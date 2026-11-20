"""Turn FHIR resources and AI summaries into searchable text chunks."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

from ..summarization.schema import PatientSummary

CHUNK_WORDS = 150   # all-MiniLM-L6-v2 reads at most 256 word pieces
OVERLAP_WORDS = 30


@dataclass
class Record:
    """One searchable record: a note, a report, or a patient's AI summary."""

    record_id: str
    patient_id: str
    resource_type: str          # DocumentReference, DiagnosticReport, or Patient (for AI summaries)
    kind: str                   # note, lab_report, imaging_report, summary
    title: str
    date: str | None            # YYYY-MM-DD
    text: str
    chunks: list[str] = field(default_factory=list)


def chunk(text: str, title: str, size: int = CHUNK_WORDS, overlap: int = OVERLAP_WORDS) -> list[str]:
    """Split into overlapping word windows. Each chunk starts with the title for context."""
    words = text.split()
    if not words:
        return [title]
    step = max(1, size - overlap)
    pieces = []
    for start in range(0, len(words), step):
        pieces.append(f"{title}. {' '.join(words[start:start + size])}")
        if start + size >= len(words):
            break
    return pieces


def _attachment_text(resource: dict) -> str:
    attachments = [c["attachment"] for c in resource.get("content", [])] + resource.get("presentedForm", [])
    for att in attachments:
        if att.get("data"):
            return base64.b64decode(att["data"]).decode("utf-8", "replace")
    return ""


def _date(resource: dict) -> str | None:
    value = (resource.get("date") or resource.get("effectiveDateTime")
             or resource.get("context", {}).get("period", {}).get("start"))
    return value[:10] if value and len(value) >= 10 else None


def _result_lines(report: dict, by_url: dict[str, dict]) -> list[str]:
    lines = []
    for ref in report.get("result", []):
        obs = by_url.get(ref.get("reference", ""))
        if not obs:
            continue
        name = obs.get("code", {}).get("text", "Result")
        if "valueQuantity" in obs:
            q = obs["valueQuantity"]
            value = f"{q.get('value')} {q.get('unit', '')}".strip()
        else:
            value = obs.get("valueString", "")
        flag = next((c.get("code") for i in obs.get("interpretation", []) for c in i.get("coding", [])), None)
        word = {"H": "high, abnormal", "L": "low, abnormal", "A": "abnormal", "N": "normal"}.get(flag or "", "")
        lines.append(f"{name}: {value}" + (f" ({word})" if word else ""))
    return sorted(set(lines), key=lines.index)


def records_from_bundle(bundle: dict, patient_id: str) -> list[Record]:
    by_url = {e["fullUrl"]: e["resource"] for e in bundle.get("entry", [])}
    records = []
    for res in by_url.values():
        rtype = res["resourceType"]
        if rtype == "DocumentReference":
            title = res.get("type", {}).get("text") or "Clinical note"
            records.append(Record(res["id"], patient_id, rtype, "note", title, _date(res), _attachment_text(res)))
        elif rtype == "DiagnosticReport":
            cat = res.get("category", [{}])[0].get("coding", [{}])[0].get("code")
            kind = "imaging_report" if cat == "RAD" else "lab_report"
            title = res.get("code", {}).get("text") or "Report"
            parts = [res.get("conclusion", ""), *_result_lines(res, by_url), _attachment_text(res)]
            records.append(Record(res["id"], patient_id, rtype, kind, title, _date(res),
                                  "\n".join(p for p in parts if p)))
    for r in records:
        r.chunks = chunk(r.text, r.title)
    return records


def summary_record(summary: PatientSummary, latest_date: str | None) -> Record:
    s = summary.summary
    parts = [s.overview, f"Chief concern: {s.chief_concern}",
             "Diagnoses: " + "; ".join(d.text for d in s.key_diagnoses),
             "Recent media: " + "; ".join(m.result for m in s.recent_media),
             "Anomalies: " + "; ".join(a.text for a in s.anomalies)]
    record = Record(record_id=f"summary-{summary.patient_id}", patient_id=summary.patient_id,
                    resource_type="Patient", kind="summary", title="AI summary", date=latest_date,
                    text="\n".join(parts))
    record.chunks = chunk(record.text, record.title)
    return record
