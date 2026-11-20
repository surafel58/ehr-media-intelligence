"""Turn raw source records into clean records (before merging and linking)."""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from .models import AuditEntry, Document, Dropped, Encounter, Identifier, LabResult, Patient
from .normalize import (
    normalize_gender,
    normalize_interpretation,
    normalize_mrn,
    normalize_name_part,
    normalize_unit,
    parse_date,
    parse_datetime,
    split_identifiers,
)
from .text import (
    decode_attachment,
    extract_age_sex,
    find_date_mentions,
    find_labeled_date,
    html_to_text,
    looks_like_html,
    repair_transcription,
    tidy_whitespace,
)
from .terminology import code_name


def _audit(record, field: str, rule: str, before=None, after=None) -> None:
    record.audit.append(AuditEntry(
        field=field, rule=rule,
        before=None if before is None else str(before),
        after=None if after is None else str(after),
    ))


def _ref_id(ref: dict | None, rtype: str) -> str | None:
    value = (ref or {}).get("reference") or ""
    return value.split("/", 1)[1] if value.startswith(f"{rtype}/") else None


def _concept_text(concept: dict | None) -> str | None:
    concept = concept or {}
    if concept.get("text"):
        return concept["text"].strip()
    for coding in concept.get("coding", []):
        if coding.get("display"):
            return coding["display"].strip()
    return None


# ================================================================ HAPI FHIR JSON


def load_hapi(path: Path) -> dict[str, list[dict]]:
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    by_type: dict[str, list[dict]] = defaultdict(list)
    for entry in bundle.get("entry", []):
        res = entry.get("resource") or {}
        if res.get("resourceType"):
            by_type[res["resourceType"]].append(res)
    return by_type


def patient_from_fhir(res: dict) -> Patient:
    p = Patient(id=f"hapi-{res['id']}", source="hapi", source_ids=[res["id"]])

    names = res.get("name") or []
    name = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    family_raw = name.get("family")
    p.family_name = normalize_name_part(family_raw)
    if family_raw is not None and p.family_name != family_raw:
        _audit(p, "family_name", "name.normalized", family_raw, p.family_name)
    for g in name.get("given") or []:
        clean = normalize_name_part(g)
        if clean:
            p.given_names.append(clean)
        if clean != g:
            _audit(p, "given_names", "name.normalized", g, clean)
    if not p.family_name and not p.given_names:
        p.flags.append("missing_name")

    raw_dob = res.get("birthDate")
    parsed = parse_date(raw_dob)
    if raw_dob and parsed:
        p.birth_date = parsed.iso
        if parsed.iso != raw_dob:
            _audit(p, "birth_date", "date.format", raw_dob, parsed.iso)
    elif raw_dob:
        _audit(p, "birth_date", "date.invalid", raw_dob, None)
        p.flags.append("missing_birth_date")
    else:
        p.flags.append("missing_birth_date")

    raw_gender = res.get("gender")
    p.gender = normalize_gender(raw_gender)
    if raw_gender is None:
        _audit(p, "gender", "gender.missing", None, p.gender)
        p.flags.append("missing_gender")
    elif p.gender != raw_gender:
        _audit(p, "gender", "gender.code", raw_gender, p.gender)

    mrn, others = split_identifiers(res.get("identifier") or [])
    p.other_identifiers = others
    if mrn:
        clean = normalize_mrn(mrn.value)
        if clean != mrn.value:
            _audit(p, "mrn", "mrn.format", mrn.value, clean)
        p.mrn = Identifier(system=mrn.system, value=clean) if clean else None
    if p.mrn is None:
        p.flags.append("missing_mrn")
    return p


def _set_date(doc, raw: str | None, field: str = "date") -> bool:
    parsed = parse_datetime(raw)
    if parsed is None:
        if raw:
            _audit(doc, field, "date.invalid", raw, None)
        return False
    doc.date, doc.date_precision = parsed.value, parsed.precision
    iso = parsed.value.isoformat()
    if parsed.assumed_utc:
        _audit(doc, field, "date.assumed_utc", raw, iso)
    elif parsed.precision == "datetime" and not raw.endswith(("Z", "+00:00")):
        _audit(doc, field, "date.to_utc", raw, iso)
    return True


def _text_from_attachments(doc, attachments: list[dict]) -> bool:
    """Fill doc.text from the best text attachment. Returns False if none is readable."""
    order = ("text/plain", "text/markdown", "text/html", "application/xhtml+xml")
    usable = [a for a in attachments if a.get("data") and any((a.get("contentType") or "").startswith(t) for t in order)]
    usable.sort(key=lambda a: next(i for i, t in enumerate(order) if (a.get("contentType") or "").startswith(t)))
    for att in usable:
        try:
            text, rules = decode_attachment(att["data"])
        except ValueError:
            _audit(doc, "text", "text.bad_base64")
            continue
        for rule in rules:
            _audit(doc, "text", rule)
        doc.content_type = att.get("contentType", "text/plain").split(";")[0]
        if "html" in doc.content_type or looks_like_html(text):
            text = html_to_text(text)
            _audit(doc, "text", "text.html_stripped")
        doc.text = tidy_whitespace(text)
        return bool(doc.text)
    return False


MIN_NOTE_WORDS = 5
_TITLE_LINE = re.compile(r"^\s*#*\s*([A-Za-z][A-Za-z /-]{2,40}?)\s*(?:[,:\n—–]| - |$)")


def note_from_fhir(res: dict) -> Document | Dropped:
    doc = Document(id=f"hapi-doc-{res['id']}", source="hapi", source_id=res["id"], source_ids=[res["id"]],
                   kind="note", title="", status=res.get("status"))
    pid = _ref_id(res.get("subject"), "Patient")
    doc.patient_id = f"hapi-{pid}" if pid else None
    encs = (res.get("context") or {}).get("encounter") or []
    eid = _ref_id(encs[0], "Encounter") if encs else None
    doc.encounter_id = f"hapi-enc-{eid}" if eid else None

    attachments = [c.get("attachment", {}) for c in res.get("content", [])]
    if not _text_from_attachments(doc, attachments):
        types = sorted({a.get("contentType") or "none" for a in attachments}) or ["none"]
        return Dropped(source="hapi", source_id=res["id"],
                       reason=f"no note text (attachment type: {', '.join(types)})")
    if len(doc.text.split()) < MIN_NOTE_WORDS:
        return Dropped(source="hapi", source_id=res["id"], reason=f"too short to be a clinical note: '{doc.text[:40]}'")

    doc.title = _concept_text(res.get("type")) or ""
    if not doc.title:
        m = _TITLE_LINE.match(doc.text)
        doc.title = m.group(1).strip().capitalize() if m else "Clinical note"
        _audit(doc, "title", "document.title_inferred", None, doc.title)
    doc.description = res.get("description")

    if not _set_date(doc, res.get("date")):
        period = (res.get("context") or {}).get("period") or {}
        if _set_date(doc, period.get("start")):
            _audit(doc, "date", "date.from_period", None, doc.date.isoformat())
        else:
            labeled = find_labeled_date(doc.text)
            if labeled:
                _set_date(doc, labeled.iso)
                _audit(doc, "date", "date.from_text", labeled.raw, labeled.iso)
            else:
                doc.flags.append("missing_date")
    doc.date_mentions = find_date_mentions(doc.text)
    return doc


_IMAGING = re.compile(r"x-?ray|\bct\b|\bmri\b|ultrasound|radiolog|imaging|echocardio|mammo|\bscan\b", re.I)


def _lab_result(obs: dict) -> tuple[LabResult, list[str]]:
    rules: list[str] = []
    code = obs.get("code") or {}
    loinc = next((c.get("code") for c in code.get("coding", []) if "loinc" in (c.get("system") or "")), None)
    name, looked_up = code_name(code)
    if looked_up:
        rules.append("lab.name_from_code")
    elif name is None:
        name = f"LOINC {loinc}" if loinc else "Unnamed result"
    result = LabResult(source_id=obs["id"], name=name, loinc=loinc)
    if "valueQuantity" in obs:
        q = obs["valueQuantity"]
        result.value = float(q["value"]) if isinstance(q.get("value"), (int, float)) else q.get("value")
        result.unit = normalize_unit(q.get("unit") or q.get("code"))
        if result.unit != (q.get("unit") or q.get("code")):
            rules.append("lab.unit")
    elif "valueString" in obs:
        result.value = obs["valueString"]
    elif "valueCodeableConcept" in obs:
        value, looked_up = code_name(obs["valueCodeableConcept"])
        if looked_up:
            rules.append("lab.value_from_code")
        result.value = value or next((c.get("code") for c in obs["valueCodeableConcept"].get("coding", [])), None)
    ranges = obs.get("referenceRange") or [{}]
    result.low = (ranges[0].get("low") or {}).get("value")
    result.high = (ranges[0].get("high") or {}).get("value")
    interp = None
    for concept in obs.get("interpretation") or []:
        for coding in concept.get("coding", []):
            interp = normalize_interpretation(coding.get("code")) or interp
    if interp is None and isinstance(result.value, (int, float)) and (result.low is not None or result.high is not None):
        if result.low is not None and result.value < result.low:
            interp = "low"
        elif result.high is not None and result.value > result.high:
            interp = "high"
        else:
            interp = "normal"
        rules.append("lab.interpretation_computed")
    result.interpretation = interp or "unknown"
    return result, rules


def report_from_fhir(res: dict, observations: dict[str, dict]) -> Document:
    title, looked_up = code_name(res.get("code"))
    doc = Document(id=f"hapi-report-{res['id']}", source="hapi", source_id=res["id"], source_ids=[res["id"]],
                   kind="lab_report", title=title or "", status=res.get("status"))
    if looked_up:
        _audit(doc, "title", "report.title_from_code", None, title)
    elif not title:
        codes = [c.get("code") for c in (res.get("code") or {}).get("coding", []) if c.get("code")]
        doc.title = f"Lab panel {codes[0]}" if codes else "Diagnostic report"
        _audit(doc, "title", "report.title_inferred", None, doc.title)
    pid = _ref_id(res.get("subject"), "Patient")
    doc.patient_id = f"hapi-{pid}" if pid else None
    eid = _ref_id(res.get("encounter"), "Encounter")
    doc.encounter_id = f"hapi-enc-{eid}" if eid else None

    codes = {c.get("code") for cat in res.get("category") or [] for c in cat.get("coding", [])}
    if codes & {"RAD", "IMG", "imaging"}:
        doc.kind = "imaging_report"
    elif not codes & {"LAB"}:
        doc.kind = "imaging_report" if _IMAGING.search(doc.title) else "lab_report"
        _audit(doc, "kind", "report.category_inferred", None, doc.kind)

    if not _set_date(doc, res.get("effectiveDateTime") or (res.get("effectivePeriod") or {}).get("start")):
        if _set_date(doc, res.get("issued")):
            _audit(doc, "date", "date.from_issued", None, doc.date.isoformat())
        else:
            doc.flags.append("missing_date")

    texts = []
    if res.get("conclusion"):
        texts.append(res["conclusion"].strip())
    holder = Document(id="tmp", source="hapi", source_id="tmp", kind="note", title="")
    if _text_from_attachments(holder, res.get("presentedForm") or []):
        texts.append(holder.text)
        doc.audit.extend(holder.audit)
    doc.text = "\n\n".join(texts)

    missing = 0
    for ref in res.get("result") or []:
        oid = _ref_id(ref, "Observation")
        obs = observations.get(oid or "")
        if obs is None:
            missing += 1
            continue
        result, rules = _lab_result(obs)
        doc.results.append(result)
        after = {"lab.unit": result.unit, "lab.interpretation_computed": result.interpretation,
                 "lab.name_from_code": result.name, "lab.value_from_code": result.value}
        for rule in rules:
            _audit(doc, f"results.{result.name}", rule, None, after.get(rule))
    if missing:
        doc.flags.append("missing_results")
        _audit(doc, "results", "lab.result_not_found", str(missing), None)
    doc.date_mentions = find_date_mentions(doc.text)
    return doc


def encounter_from_fhir(res: dict) -> Encounter:
    enc = Encounter(id=f"hapi-enc-{res['id']}", source="hapi", source_id=res["id"], status=res.get("status"))
    pid = _ref_id(res.get("subject"), "Patient")
    enc.patient_id = f"hapi-{pid}" if pid else None
    cls = res.get("class") or {}
    enc.class_code = cls.get("code") if isinstance(cls, dict) else None
    enc.type = next((t for t in (_concept_text(c) for c in res.get("type") or []) if t), None)
    enc.reason = next((t for t in (_concept_text(c) for c in res.get("reasonCode") or []) if t), None)
    period = res.get("period") or {}
    for field in ("start", "end"):
        parsed = parse_datetime(period.get(field))
        if parsed:
            setattr(enc, field, parsed.value)
            if parsed.precision == "datetime" and not period[field].endswith(("Z", "+00:00")):
                _audit(enc, field, "date.assumed_utc" if parsed.assumed_utc else "date.to_utc",
                       period[field], parsed.value.isoformat())
    return enc


# ================================================================ MTSamples CSV


def load_mtsamples(path: Path) -> list[dict]:
    csv.field_size_limit(10**8)
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def document_from_mtsample(row: dict) -> Document | Dropped:
    row_id = (row.get("") or "").strip()
    raw_text = row.get("transcription") or ""
    if not raw_text.strip():
        return Dropped(source="mtsamples", source_id=row_id, reason="empty transcription")

    spec_raw = row.get("medical_specialty") or ""
    spec = " ".join(spec_raw.split())
    title_raw = row.get("sample_name") or ""
    title = " ".join(title_raw.split()) or "Transcribed report"
    kind = "imaging_report" if spec == "Radiology" else "lab_report" if spec == "Lab Medicine - Pathology" else "note"
    doc = Document(id=f"mts-doc-{int(row_id):04d}", source="mtsamples", source_id=row_id, source_ids=[row_id],
                   kind=kind, title=title, specialties=[spec] if spec else [],
                   description=" ".join((row.get("description") or "").split()) or None)
    if spec != spec_raw:
        _audit(doc, "specialties", "text.whitespace", spec_raw, spec)
    if title != title_raw:
        _audit(doc, "title", "text.whitespace", title_raw, title)

    doc.text = repair_transcription(raw_text)
    if doc.text != tidy_whitespace(raw_text):
        _audit(doc, "text", "text.section_breaks_restored", raw_text[:60], doc.text[:60])

    seen: set[str] = set()
    for kw in (row.get("keywords") or "").split(","):
        kw = kw.strip().lower()
        if kw and kw not in seen:
            seen.add(kw)
            doc.keywords.append(kw)

    labeled = find_labeled_date(doc.text)
    if labeled:
        doc.date, doc.date_precision = parse_datetime(labeled.iso).value, labeled.precision
        _audit(doc, "date", "date.from_text", labeled.raw, labeled.iso)
    else:
        doc.flags.append("missing_date")
    doc.date_mentions = find_date_mentions(doc.text)
    return doc


def patient_for_mtsample(doc: Document) -> Patient:
    """MTSamples reports carry no identity. Each report gets its own patient."""
    p = Patient(id=f"mts-{int(doc.source_id):04d}", source="mtsamples", source_ids=[doc.source_id],
                flags=["identity_unknown"])
    age, gender, rule = extract_age_sex(doc.text)
    if age is not None:
        p.age_years = age
        _audit(p, "age_years", "age.from_text", None, age)
    p.gender = gender
    _audit(p, "gender", rule or "gender.missing", None, gender)
    if gender == "unknown":
        p.flags.append("missing_gender")
    return p
