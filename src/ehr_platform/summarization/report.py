"""Write summaries and the quality report to var/summaries/."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .pipeline import RunResult
from .schema import PatientSummary

# USD per million tokens (input, output)
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}


def cost(summaries: list[PatientSummary]) -> float:
    total = 0.0
    for s in summaries:
        price_in, price_out = PRICES.get(s.model, (0.0, 0.0))
        total += s.usage.input_tokens / 1e6 * price_in + s.usage.output_tokens / 1e6 * price_out
    return total


def cost_label(summaries: list[PatientSummary]) -> str:
    if summaries and all(s.model.startswith("gemini") for s in summaries):
        return "free tier"
    return f"${cost(summaries):.2f}"


def write_outputs(run: RunResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.json"):
        old.unlink()
    summaries = list(run.summaries.values())
    for s in summaries:
        (out_dir / f"{s.patient_id}.json").write_text(s.model_dump_json(indent=2), encoding="utf-8")

    ai = [s for s in summaries if s.model != "rule-based"]
    check_names = [c.name for c in summaries[0].quality] if summaries else []
    passed = Counter(c.name for s in ai for c in s.quality if c.passed)
    words = sorted(s.word_count for s in ai)
    confidence = Counter(s.summary.confidence for s in ai)

    models = sorted({s.model for s in ai})
    lines = [
        "# Summary quality report", "",
        f"Model: {', '.join(f'`{m}`' for m in models) or 'none'}", "",
        "| Patients | AI summaries | No records | From cache | Failed | Pending | Retries for length |",
        "|---|---|---|---|---|---|---|",
        f"| {len(run.summaries) + len(run.failed) + len(run.pending)} | {len(ai)} | {len(summaries) - len(ai)} "
        f"| {len(run.from_cache)} | {len(run.failed)} | {len(run.pending)} "
        f"| {sum(1 for s in ai if s.usage.attempts > 1)} |", "",
    ]
    if ai:
        lines += [
            "| Words min | Words median | Words max | Input tokens | Output tokens | Cost of these calls |",
            "|---|---|---|---|---|---|",
            f"| {words[0]} | {words[len(words) // 2]} | {words[-1]} | {sum(s.usage.input_tokens for s in ai):,} "
            f"| {sum(s.usage.output_tokens for s in ai):,} | {cost_label(ai)} |", "",
            "## Automatic checks", "", "| Check | Passed |", "|---|---|",
            *[f"| `{name}` | {passed[name]} of {len(ai)} |" for name in check_names], "",
            "## Confidence", "", "| Level | Summaries |", "|---|---|",
            *[f"| {level} | {confidence[level]} |" for level in ("high", "medium", "low")], "",
        ]
    failures = [(s.patient_id, c) for s in ai for c in s.quality if not c.passed]
    lines += ["## Failed checks", ""]
    lines += (["| Patient | Check | Detail |", "|---|---|---|",
               *[f"| {pid} | `{c.name}` | {c.detail} |" for pid, c in failures]] if failures else ["None."])
    if run.failed:
        lines += ["", "## Failed patients", "", "| Patient | Error |", "|---|---|",
                  *[f"| {pid} | {err} |" for pid, err in run.failed.items()]]
    path = out_dir / "quality_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "quality_report.json").write_text(json.dumps({
        "failed": run.failed,
        "checks": {s.patient_id: [c.model_dump() for c in s.quality] for s in summaries},
    }, indent=2), encoding="utf-8")
    return path
