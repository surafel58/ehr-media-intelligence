"""Validate Bundles against the FHIR R4 specification.

Four checks:
  1. Structure and data types: fhir.resources R4B models (cardinality, types, regex for dates, ids).
  2. Required fields and codes: fhir.resources does not check required value sets
     (it accepts gender "M"), so required bindings used here are checked directly.
  3. References: every reference must point at an entry in the same Bundle.
  4. Warnings: valid FHIR that is still missing something useful (no date, no identifier).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

from fhir.resources.R4B.bundle import Bundle
from pydantic import BaseModel, Field, ValidationError

from .mapper import DOC_STATUS, ENCOUNTER_STATUS, REPORT_STATUS

REQUIRED_FIELDS = {
    "Encounter": ["status", "class"],
    "DocumentReference": ["status", "content"],
    "DiagnosticReport": ["status", "code"],
    "Observation": ["status", "code"],
}
REQUIRED_CODES = {
    "Patient": {"gender": {"male", "female", "other", "unknown"}},
    "Encounter": {"status": ENCOUNTER_STATUS},
    "DocumentReference": {"status": DOC_STATUS},
    "DiagnosticReport": {"status": REPORT_STATUS},
    "Observation": {"status": {"registered", "preliminary", "final", "amended", "corrected", "cancelled",
                               "entered-in-error", "unknown"}},
}


class Issue(BaseModel):
    severity: Literal["error", "warning"]
    resource: str
    path: str
    message: str


class BundleResult(BaseModel):
    bundle_id: str
    patient_id: str
    resource_counts: dict[str, int]
    errors: list[Issue] = Field(default_factory=list)
    warnings: list[Issue] = Field(default_factory=list)
    mapping_notes: list[str] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def _references(node, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        if isinstance(node.get("reference"), str):
            yield path, node["reference"]
        for key, value in node.items():
            yield from _references(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _references(value, f"{path}[{i}]")


def _schema_issues(bundle: dict, labels: list[str]) -> list[Issue]:
    try:
        Bundle.model_validate(bundle)
        return []
    except ValidationError as exc:
        issues = []
        for err in exc.errors():
            loc = list(err["loc"])
            resource = "Bundle"
            if len(loc) >= 2 and loc[0] == "entry" and isinstance(loc[1], int) and loc[1] < len(labels):
                resource = labels[loc[1]]
                loc = loc[3:] if len(loc) > 2 and loc[2] == "resource" else loc[2:]
            issues.append(Issue(severity="error", resource=resource,
                                path=".".join(str(p) for p in loc) or "(root)", message=err["msg"]))
        return issues


def _warnings(label: str, res: dict) -> list[Issue]:
    rtype = res["resourceType"]
    found: list[tuple[str, str]] = []
    if rtype == "Patient":
        if not res.get("identifier"):
            found.append(("identifier", "no identifier"))
        if not res.get("name"):
            found.append(("name", "no name"))
        if not res.get("birthDate"):
            found.append(("birthDate", "no birth date"))
        if res.get("gender") == "unknown":
            found.append(("gender", "gender unknown"))
    elif rtype == "DocumentReference":
        if "subject" not in res:
            found.append(("subject", "not linked to a patient"))
        if "date" not in res and "period" not in res.get("context", {}):
            found.append(("date", "no date"))
    elif rtype == "DiagnosticReport":
        if "effectiveDateTime" not in res:
            found.append(("effectiveDateTime", "no date"))
    elif rtype == "Observation":
        if not any(k.startswith("value") for k in res):
            found.append(("value", "no value"))
    return [Issue(severity="warning", resource=label, path=p, message=m) for p, m in found]


def validate_bundle(bundle: dict) -> tuple[list[Issue], list[Issue]]:
    entries = bundle.get("entry", [])
    labels = [f"{e['resource']['resourceType']}/{e['resource'].get('id')}" for e in entries]
    errors = _schema_issues(bundle, labels)
    warnings: list[Issue] = []

    full_urls = [e.get("fullUrl") for e in entries]
    # Relative references ("Patient/123") resolve against entries with a RESTful fullUrl.
    relative = {label for label, url in zip(labels, full_urls) if url and url.startswith("http")}

    def resolves(reference: str) -> bool:
        return reference in full_urls or reference in relative or reference.startswith("#")

    for url, n in Counter(full_urls).items():
        if n > 1:
            errors.append(Issue(severity="error", resource="Bundle", path="entry.fullUrl",
                                message=f"fullUrl {url} used {n} times"))

    for label, entry in zip(labels, entries):
        res = entry["resource"]
        rtype = res["resourceType"]
        for field in REQUIRED_FIELDS.get(rtype, []):
            if field not in res:
                errors.append(Issue(severity="error", resource=label, path=field, message="required field missing"))
        for field, allowed in REQUIRED_CODES.get(rtype, {}).items():
            if field in res and res[field] not in allowed:
                errors.append(Issue(severity="error", resource=label, path=field,
                                    message=f"'{res[field]}' is not an allowed code ({', '.join(sorted(allowed))})"))
        for path, reference in _references(res):
            if not resolves(reference):
                errors.append(Issue(severity="error", resource=label, path=path,
                                    message=f"reference {reference} does not resolve inside the Bundle"))
        warnings.extend(_warnings(label, res))
    return errors, warnings


def check(bundle: dict, patient_id: str, mapping_notes: list[str] | None = None) -> BundleResult:
    errors, warnings = validate_bundle(bundle)
    counts = Counter(e["resource"]["resourceType"] for e in bundle.get("entry", []))
    return BundleResult(bundle_id=bundle.get("id", "?"), patient_id=patient_id, resource_counts=dict(counts),
                        errors=errors, warnings=warnings, mapping_notes=mapping_notes or [])


# ---------------------------------------------------------------- report


def raw_baseline(raw_bundle: dict) -> dict:
    """Run the same checks on the raw input snapshot, for comparison."""
    errors, warnings = validate_bundle(raw_bundle)
    return {
        "resources": len(raw_bundle.get("entry", [])),
        "errors": len(errors),
        "warnings": len(warnings),
        "warnings_by_type": Counter(f"{w.resource.split('/')[0]}: {w.message}" for w in warnings).most_common(),
    }


def write_report(results: list[BundleResult], out_dir: Path, baseline: dict | None = None) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    totals = Counter()
    for r in results:
        totals.update(r.resource_counts)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bundles": len(results),
        "valid": sum(r.valid for r in results),
        "with_errors": sum(not r.valid for r in results),
        "resources": dict(sorted(totals.items())),
        "errors": sum(len(r.errors) for r in results),
        "warnings": sum(len(r.warnings) for r in results),
        "mapping_notes": sum(len(r.mapping_notes) for r in results),
    }
    json_path = out_dir / "validation_report.json"
    json_path.write_text(json.dumps({"summary": summary, "raw_input": baseline,
                                     "bundles": [r.model_dump() for r in results]}, indent=2), encoding="utf-8")

    lines = [
        "# FHIR validation report", "",
        f"Generated {summary['generated_at']}. FHIR R4B models from fhir.resources, plus checks for required codes "
        "and references.", "",
        "| Bundles | Valid | With errors | Resources | Errors | Warnings |",
        "|---|---|---|---|---|---|",
        f"| {summary['bundles']} | {summary['valid']} | {summary['with_errors']} | {sum(totals.values())} "
        f"| {summary['errors']} | {summary['warnings']} |", "",
        "## Resources", "", "| Type | Count |", "|---|---|",
        *[f"| {t} | {n} |" for t, n in sorted(totals.items())], "",
        "## Errors", "",
    ]
    errors = [(r.bundle_id, e) for r in results for e in r.errors]
    if errors:
        lines += ["| Bundle | Resource | Path | Message |", "|---|---|---|---|",
                  *[f"| {b} | {e.resource} | `{e.path}` | {e.message} |" for b, e in errors]]
    else:
        lines.append("No errors.")
    lines += ["", "## Warnings", ""]
    grouped = Counter((w.resource.split("/")[0], w.message) for r in results for w in r.warnings)
    if grouped:
        lines += ["| Resource type | Warning | Count |", "|---|---|---|",
                  *[f"| {t} | {m} | {n} |" for (t, m), n in sorted(grouped.items(), key=lambda kv: -kv[1])]]
    else:
        lines.append("No warnings.")
    notes = [(r.bundle_id, n) for r in results for n in r.mapping_notes]
    lines += ["", "## Defaults applied during mapping", ""]
    if notes:
        grouped_notes = Counter(n.split(": ", 1)[1] for _, n in notes)
        lines += ["| Default | Count |", "|---|---|", *[f"| {m} | {c} |" for m, c in grouped_notes.most_common()]]
    else:
        lines.append("None.")
    if baseline:
        lines += ["", "## Raw input check", "",
                  "The same checks run on the raw HAPI snapshot before cleaning.", "",
                  "| Resources | Errors | Warnings |", "|---|---|---|",
                  f"| {baseline['resources']} | {baseline['errors']} | {baseline['warnings']} |", ""]
        if baseline["warnings_by_type"]:
            lines += ["| Warning | Count |", "|---|---|", *[f"| {w} | {n} |" for w, n in baseline["warnings_by_type"]]]
    md_path = out_dir / "validation_report.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
