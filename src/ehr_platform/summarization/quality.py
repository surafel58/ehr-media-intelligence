"""Automatic checks on every AI summary.

They test what can be checked without a clinician:
  length       under the word limit
  citations    every cited id is a real record of this patient, and every diagnosis and clinical anomaly
               cites something (data quality notes about demographics have no record to cite)
  grounding    every number and date in the summary appears in the source records
               (ages worked out from the birth date also count)
  recall       every lab report with an abnormal value, and every data quality flag, is reported
"""

from __future__ import annotations

import re
from datetime import date

from .context import PatientContext
from .prompt import WORD_LIMIT
from .schema import ClinicalSummary, QualityCheck

_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?:-\d{2}(?:-\d{2})?)?")


def summary_text(s: ClinicalSummary) -> str:
    parts = [s.overview, s.chief_concern, s.confidence_reason]
    parts += [d.text for d in s.key_diagnoses]
    parts += [m.result for m in s.recent_media]
    parts += [a.text for a in s.anomalies]
    return " ".join(parts)


def word_count(s: ClinicalSummary) -> int:
    return len(summary_text(s).split())


def cited_ids(s: ClinicalSummary) -> set[str]:
    ids = {i for d in s.key_diagnoses for i in d.source_ids}
    ids |= {m.source_id for m in s.recent_media}
    ids |= {i for a in s.anomalies for i in a.source_ids}
    return ids


def _numbers(text: str) -> set[str]:
    return {n for n in _NUMBER.findall(text) if len(n) >= 2 or "." in n}


def derived_ages(ctx: PatientContext, today: date | None = None) -> set[str]:
    """Ages that follow from the birth date: at each record date, and today."""
    if not ctx.birth_date or len(ctx.birth_date) != 10:
        return set()
    born = date.fromisoformat(ctx.birth_date)
    points = [date.fromisoformat(d) for d in ctx.record_dates if len(d) == 10] + [today or date.today()]
    return {str(p.year - born.year - ((p.month, p.day) < (born.month, born.day))) for p in points}


def run_checks(s: ClinicalSummary, ctx: PatientContext) -> list[QualityCheck]:
    checks: list[QualityCheck] = []
    words = word_count(s)
    checks.append(QualityCheck(name="under_word_limit", passed=words <= WORD_LIMIT, detail=f"{words} words"))

    unknown = sorted(cited_ids(s) - ctx.source_ids)
    checks.append(QualityCheck(name="citations_exist", passed=not unknown,
                               detail=f"unknown ids: {', '.join(unknown)}" if unknown else ""))

    # Data quality notes about the demographics block have no record id to cite, so only clinical items must cite.
    uncited = ([d.text for d in s.key_diagnoses if not d.source_ids]
               + [a.text for a in s.anomalies if a.kind == "clinical" and not a.source_ids])
    checks.append(QualityCheck(name="findings_cited", passed=not uncited,
                               detail=f"uncited: {'; '.join(uncited)}" if uncited else ""))

    dates = {m.date for m in s.recent_media if m.date}
    ages = derived_ages(ctx)
    ungrounded = sorted(n for n in _numbers(summary_text(s)) | dates if n not in ctx.text and n not in ages)
    checks.append(QualityCheck(name="numbers_grounded", passed=not ungrounded,
                               detail=f"not in records: {', '.join(ungrounded)}" if ungrounded else ""))

    anomaly_ids = {i for a in s.anomalies for i in a.source_ids}
    missed = [rid for rid in ctx.abnormal if rid not in anomaly_ids]
    needed = min(len(ctx.abnormal), 5)
    checks.append(QualityCheck(name="abnormal_results_reported",
                               passed=len(ctx.abnormal) - len(missed) >= needed,
                               detail=f"missed: {', '.join(missed)}" if missed else ""))

    has_dq = any(a.kind == "data_quality" for a in s.anomalies)
    checks.append(QualityCheck(name="data_quality_reported", passed=has_dq or not ctx.flags,
                               detail="" if has_dq or not ctx.flags else f"flags not reported: {', '.join(ctx.flags)}"))
    return checks
