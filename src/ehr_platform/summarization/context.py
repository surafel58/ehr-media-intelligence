"""Turn a patient's FHIR Bundle into the text Claude reads.

Names and MRNs are not sent: the summary does not need them (minimum necessary).
Each record is tagged with its id so every statement in the summary can cite its source.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field

DOC_CHAR_LIMIT = 8000
PATIENT_CHAR_LIMIT = 60000

FLAG_TEXT = {
    "conflicting_mrn": "merged duplicate records carried different MRNs",
    "conflicting_gender": "merged duplicate records disagree on gender",
    "shared_mrn": "the same MRN is used by another patient record",
    "possible_duplicate": "another record with the same name may be the same person",
    "note_age_mismatch": "the age written in this note does not match the birth date",
    "note_patient_mismatch": "the name or birth date in this note does not match the patient record",
}
INTERP = {"H": "high", "L": "low", "A": "abnormal", "N": "normal"}


@dataclass
class PatientContext:
    patient_id: str
    text: str
    record_hash: str
    source_ids: set[str] = field(default_factory=set)
    abnormal: dict[str, list[str]] = field(default_factory=dict)  # report id -> abnormal result names
    flags: list[str] = field(default_factory=list)                 # data quality flags worth reporting
    has_records: bool = False
    omitted: int = 0


def record_hash(bundle: dict) -> str:
    """Hash of the records in a Bundle. Ignores the Bundle timestamp, so re-running step 2 keeps the cache."""
    resources = sorted((e["resource"] for e in bundle.get("entry", [])), key=lambda r: (r["resourceType"], r["id"]))
    canonical = json.dumps(resources, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _tags(resource: dict) -> list[str]:
    return [t["code"] for t in resource.get("meta", {}).get("tag", [])]


def _text(resource: dict) -> str:
    attachments = [c["attachment"] for c in resource.get("content", [])] + resource.get("presentedForm", [])
    for att in attachments:
        if att.get("data"):
            return base64.b64decode(att["data"]).decode("utf-8", "replace").strip()
    return ""


def _date(resource: dict) -> str | None:
    value = (resource.get("date") or resource.get("effectiveDateTime")
             or resource.get("context", {}).get("period", {}).get("start"))
    return value[:10] if value else None


def _result_line(obs: dict) -> tuple[str, str | None]:
    name = obs.get("code", {}).get("text") or "Result"
    if "valueQuantity" in obs:
        q = obs["valueQuantity"]
        value = f"{q.get('value')} {q.get('unit', '')}".strip()
    else:
        value = obs.get("valueString", "no value")
    interp = None
    for concept in obs.get("interpretation", []):
        for coding in concept.get("coding", []):
            interp = INTERP.get(coding.get("code"), interp)
    extras = []
    if interp:
        extras.append(interp)
    rng = (obs.get("referenceRange") or [{}])[0]
    if rng:
        extras.append(f"ref {rng.get('low', {}).get('value', '')}-{rng.get('high', {}).get('value', '')}")
    return f"{name}: {value}" + (f" ({'; '.join(extras)})" if extras else ""), interp


def build_context(bundle: dict) -> PatientContext:
    resources = [e["resource"] for e in bundle.get("entry", [])]
    by_url = {e["fullUrl"]: e["resource"] for e in bundle.get("entry", [])}
    patient = next((r for r in resources if r["resourceType"] == "Patient"), None)
    pid = patient["id"] if patient else bundle.get("id", "unknown").removeprefix("bundle-")
    ctx = PatientContext(patient_id=pid, text="", record_hash=record_hash(bundle))

    lines = ["PATIENT"]
    if patient:
        lines.append(f"sex: {patient.get('gender', 'unknown')}")
        lines.append(f"birth date: {patient.get('birthDate', 'unknown')}")
        for ext in patient.get("extension", []):
            if "valueAge" in ext:
                lines.append(f"age stated in the report: {ext['valueAge']['value']}")
        ctx.flags = [t for t in _tags(patient) if t in FLAG_TEXT]

    records = [r for r in resources if r["resourceType"] in ("DocumentReference", "DiagnosticReport")]
    records.sort(key=lambda r: _date(r) or "", reverse=True)
    ctx.has_records = bool(records)

    body: list[str] = []
    used = 0
    for res in records:
        rid = res["id"]
        if res["resourceType"] == "DocumentReference":
            label = f"Note: {res.get('type', {}).get('text') or 'Clinical note'}"
        else:
            cat = res.get("category", [{}])[0].get("coding", [{}])[0].get("code")
            kind = "Imaging report" if cat == "RAD" else "Pathology report" if "Pathology" in str(res.get("code")) else "Lab report"
            label = f"{kind}: {res.get('code', {}).get('text', 'Report')}"
        header = f"[{rid}] {_date(res) or 'date unknown'} | {label}"
        enc_ref = (res.get("encounter") or (res.get("context", {}).get("encounter") or [{}])[0]).get("reference")
        enc = by_url.get(enc_ref or "")
        if enc and enc.get("type"):
            header += f" | visit: {enc['type'][0].get('text')}"
        record_flags = [t for t in _tags(res) if t in FLAG_TEXT]
        for flag in record_flags:
            header += f"\n(data quality: {FLAG_TEXT[flag]})"
            if flag not in ctx.flags:
                ctx.flags.append(flag)

        parts = [header]
        if res.get("conclusion"):
            parts.append(f"Conclusion: {res['conclusion']}")
        results = [by_url[r["reference"]] for r in res.get("result", []) if r.get("reference") in by_url]
        if results:
            counted = Counter()
            for obs in results:
                line, interp = _result_line(obs)
                counted[line] += 1
                if interp in ("high", "low", "abnormal"):
                    ctx.abnormal.setdefault(rid, []).append(obs.get("code", {}).get("text", "Result"))
            parts += [f"- {line}" + (f" (x{n})" if n > 1 else "") for line, n in counted.items()]
        text = _text(res)
        if text:
            parts.append(text if len(text) <= DOC_CHAR_LIMIT else text[:DOC_CHAR_LIMIT] + " [...truncated]")
        block = "\n".join(parts)
        if used + len(block) > PATIENT_CHAR_LIMIT and body:
            ctx.omitted += 1
            continue
        body.append(block)
        used += len(block)
        ctx.source_ids.add(rid)

    if ctx.flags:
        lines.append("data quality flags: " + "; ".join(FLAG_TEXT[f] for f in ctx.flags))
    lines.append("")
    lines.append(f"RECORDS (newest first, {len(body)} shown"
                 + (f", {ctx.omitted} older not shown)" if ctx.omitted else ")"))
    ctx.text = "\n".join(lines) + "\n\n" + "\n\n---\n\n".join(body)
    return ctx
