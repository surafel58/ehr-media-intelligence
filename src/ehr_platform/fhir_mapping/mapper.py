"""Map clean records to FHIR R4 resources and build one Bundle per patient.

Resources are plain dicts in FHIR JSON form. They are validated against the
fhir.resources R4B models in validate.py.

Entries use urn:uuid fullUrls and all references inside a Bundle point at those
urns, so every Bundle is self-contained.
"""

from __future__ import annotations

import base64
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..ingestion.models import Document, Encounter, LabResult, Patient

LOINC = "http://loinc.org"
V2_0203 = "http://terminology.hl7.org/CodeSystem/v2-0203"
V2_0074 = "http://terminology.hl7.org/CodeSystem/v2-0074"
V3_ACTCODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
V3_NULLFLAVOR = "http://terminology.hl7.org/CodeSystem/v3-NullFlavor"
V3_INTERP = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
OBS_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
DOC_CATEGORY = "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category"
UCUM = "http://unitsofmeasure.org"
DATA_QUALITY = "urn:ehr-media-intelligence:data-quality"
STATED_AGE = "urn:ehr-media-intelligence:extension:stated-age"
SOURCES = {"hapi": "https://hapi.fhir.org/baseR4", "mtsamples": "https://github.com/aneuraz/mtsamplesFR"}

_NS = uuid.UUID("6f1b6a52-6d1f-4c39-9b0e-2b8a8f6e0c11")

NOTE_TYPES = [  # (pattern in title or specialty, LOINC code, display)
    (r"discharge", "18842-5", "Discharge summary"),
    (r"history and phy|h&p", "34117-2", "History and physical note"),
    (r"emergency", "34111-1", "Emergency department note"),
    (r"consult", "11488-4", "Consult note"),
    (r"progress|soap|follow[- ]?up|visit note", "11506-3", "Progress note"),
]
GENERIC_NOTE = ("34109-9", "Note")
IMAGING_STUDY = ("18748-4", "Diagnostic imaging study")
PATHOLOGY_STUDY = ("11526-1", "Pathology study")
INTERP_CODES = {"high": "H", "low": "L", "normal": "N", "abnormal": "A"}

ENCOUNTER_STATUS = {"planned", "arrived", "triaged", "in-progress", "onleave", "finished", "cancelled",
                    "entered-in-error", "unknown"}
DOC_STATUS = {"current", "superseded", "entered-in-error"}
REPORT_STATUS = {"registered", "partial", "preliminary", "final", "amended", "corrected", "appended",
                 "cancelled", "entered-in-error", "unknown"}


def urn(resource_type: str, rid: str) -> str:
    return f"urn:uuid:{uuid.uuid5(_NS, f'{resource_type}/{rid}')}"


def ref(resource_type: str, rid: str, display: str | None = None) -> dict:
    out = {"reference": urn(resource_type, rid)}
    if display:
        out["display"] = display
    return out


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _meta(source: str, flags: list[str], derived: bool = False) -> dict:
    tags = [{"system": DATA_QUALITY, "code": f} for f in flags]
    if derived:
        tags.append({"system": DATA_QUALITY, "code": "derived"})
    meta = {"source": SOURCES[source]}
    if tags:
        meta["tag"] = tags
    return meta


def _fhir_datetime(dt: datetime | None, precision: str | None) -> str | None:
    """FHIR dateTime keeping the known precision."""
    if dt is None:
        return None
    if precision == "year":
        return f"{dt.year:04d}"
    if precision == "month":
        return f"{dt.year:04d}-{dt.month:02d}"
    if precision == "day":
        return dt.date().isoformat()
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class MappingNotes:
    """Defaults the mapper had to apply, reported next to the validation results."""
    items: list[str] = field(default_factory=list)

    def status_default(self, resource: str, old: str | None, new: str) -> None:
        self.items.append(_default_note(resource, old, new))


# ---------------------------------------------------------------- resources


def patient_resource(p: Patient) -> dict:
    res: dict = {"resourceType": "Patient", "id": p.id, "meta": _meta(p.source, p.flags)}
    identifiers = []
    if p.mrn:
        mrn = {"use": "usual",
               "type": {"coding": [{"system": V2_0203, "code": "MR", "display": "Medical record number"}]},
               "value": p.mrn.value}
        if p.mrn.system:
            mrn["system"] = p.mrn.system
        identifiers.append(mrn)
    for other in p.other_identifiers:
        ident = {"value": other.value}
        if other.system:
            ident["system"] = other.system
        identifiers.append(ident)
    if identifiers:
        res["identifier"] = identifiers
    if p.family_name or p.given_names:
        name: dict = {"use": "official"}
        if p.family_name:
            name["family"] = p.family_name
        if p.given_names:
            name["given"] = p.given_names
        res["name"] = [name]
    res["gender"] = p.gender
    if p.birth_date:
        res["birthDate"] = p.birth_date
    if p.age_years is not None:
        res["extension"] = [{"url": STATED_AGE,
                             "valueAge": {"value": p.age_years, "unit": "years", "system": UCUM, "code": "a"}}]
    return res


def encounter_resource(e: Encounter, notes: MappingNotes, derived: bool = False) -> dict:
    status = e.status if e.status in ENCOUNTER_STATUS else "unknown"
    if status != e.status:
        notes.status_default(f"Encounter/{e.id}", e.status, status)
    res: dict = {
        "resourceType": "Encounter", "id": e.id, "meta": _meta(e.source, e.flags, derived=derived),
        "status": status,
        "class": ({"system": V3_ACTCODE, "code": e.class_code} if e.class_code
                  else {"system": V3_NULLFLAVOR, "code": "UNK", "display": "unknown"}),
    }
    if e.type:
        res["type"] = [{"text": e.type}]
    if e.patient_id:
        res["subject"] = ref("Patient", e.patient_id)
    period = {k: _fhir_datetime(v, "datetime") for k, v in (("start", e.start), ("end", e.end)) if v}
    if period:
        res["period"] = period
    if e.reason:
        res["reasonCode"] = [{"text": e.reason}]
    return res


def derived_encounter(doc: Document) -> Encounter:
    """MTSamples reports carry no visit record. Each report documents one visit, so one is derived."""
    return Encounter(id=doc.id.replace("-doc-", "-enc-"), source=doc.source, source_id=doc.source_id,
                     patient_id=doc.patient_id, status="unknown",
                     type=doc.specialties[0] if doc.specialties else None,
                     start=doc.date if doc.date_precision == "datetime" else None)


def _note_type(doc: Document) -> dict:
    haystack = " ".join([doc.title, *doc.specialties]).lower()
    code, display = next(((c, d) for pat, c, d in NOTE_TYPES if re.search(pat, haystack)), GENERIC_NOTE)
    return {"coding": [{"system": LOINC, "code": code, "display": display}], "text": doc.title}


def document_reference(doc: Document, notes: MappingNotes) -> dict:
    status = doc.status if doc.status in DOC_STATUS else "current"
    if status != doc.status:
        notes.status_default(f"DocumentReference/{doc.id}", doc.status, status)
    res: dict = {
        "resourceType": "DocumentReference", "id": doc.id, "meta": _meta(doc.source, doc.flags),
        "status": status,
        "type": _note_type(doc),
        "category": [{"coding": [{"system": DOC_CATEGORY, "code": "clinical-note", "display": "Clinical Note"}]}],
    }
    if doc.patient_id:
        res["subject"] = ref("Patient", doc.patient_id)
    if doc.date and doc.date_precision == "datetime":
        res["date"] = _fhir_datetime(doc.date, "datetime")
    if doc.description:
        res["description"] = doc.description
    res["content"] = [{"attachment": {"contentType": "text/plain", "language": "en",
                                      "data": b64(doc.text), "title": doc.title}}]
    context: dict = {}
    if doc.encounter_id:
        context["encounter"] = [ref("Encounter", doc.encounter_id)]
    when = _fhir_datetime(doc.date, doc.date_precision)
    if when:
        context["period"] = {"start": when}
    if context:
        res["context"] = context
    return res


_CONCLUSION_HEADERS = ["IMPRESSIONS?", "CONCLUSIONS?", "FINAL DIAGNOS[IE]S", "DIAGNOS[IE]S", "DIAGNOSTIC OPINION",
                       "SUMMARY"]


def conclusion_of(text: str) -> str | None:
    """Text of the first conclusion-like section, in order of preference."""
    for header in _CONCLUSION_HEADERS:
        m = re.search(rf"^{header}:\s*(.+?)(?=\n[A-Z][A-Z0-9 /&()'-]{{2,60}}:|\Z)", text, re.M | re.S)
        if m:
            return " ".join(m.group(1).split())
    return None


def _default_note(resource: str, old: str | None, new: str) -> str:
    return f"{resource}: status {'missing' if old is None else repr(old)}, set to '{new}'"


def _observation(r: LabResult, doc: Document, status: str) -> dict:
    res: dict = {
        "resourceType": "Observation", "id": f"{doc.source}-obs-{r.source_id}",
        "status": status if status in REPORT_STATUS - {"partial", "appended"} else "unknown",
        "category": [{"coding": [{"system": OBS_CATEGORY, "code": "laboratory"}]}],
        "code": {"text": r.name},
    }
    if r.loinc:
        res["code"]["coding"] = [{"system": LOINC, "code": r.loinc, "display": r.name}]
    if doc.patient_id:
        res["subject"] = ref("Patient", doc.patient_id)
    when = _fhir_datetime(doc.date, doc.date_precision)
    if when:
        res["effectiveDateTime"] = when
    if isinstance(r.value, (int, float)):
        res["valueQuantity"] = {"value": r.value, **({"unit": r.unit} if r.unit else {})}
    elif r.value is not None:
        res["valueString"] = str(r.value)
    if r.low is not None or r.high is not None:
        rng = {}
        if r.low is not None:
            rng["low"] = {"value": r.low}
        if r.high is not None:
            rng["high"] = {"value": r.high}
        res["referenceRange"] = [rng]
    if r.interpretation in INTERP_CODES:
        res["interpretation"] = [{"coding": [{"system": V3_INTERP, "code": INTERP_CODES[r.interpretation]}]}]
    return res


def diagnostic_report(doc: Document, notes: MappingNotes) -> tuple[dict, list[dict]]:
    status = doc.status if doc.status in REPORT_STATUS else "unknown"
    if status != doc.status:
        notes.status_default(f"DiagnosticReport/{doc.id}", doc.status, status)
    imaging = doc.kind == "imaging_report"
    code = {"text": doc.title}
    if imaging:
        code["coding"] = [{"system": LOINC, "code": IMAGING_STUDY[0], "display": IMAGING_STUDY[1]}]
    elif doc.source == "mtsamples":
        code["coding"] = [{"system": LOINC, "code": PATHOLOGY_STUDY[0], "display": PATHOLOGY_STUDY[1]}]
    res: dict = {
        "resourceType": "DiagnosticReport", "id": doc.id, "meta": _meta(doc.source, doc.flags),
        "status": status,
        "category": [{"coding": [{"system": V2_0074, "code": "RAD" if imaging else "LAB",
                                  "display": "Radiology" if imaging else "Laboratory"}]}],
        "code": code,
    }
    if doc.patient_id:
        res["subject"] = ref("Patient", doc.patient_id)
    if doc.encounter_id:
        res["encounter"] = ref("Encounter", doc.encounter_id)
    when = _fhir_datetime(doc.date, doc.date_precision)
    if when:
        res["effectiveDateTime"] = when
    observations = [_observation(r, doc, status) for r in doc.results]
    if observations:
        res["result"] = [ref("Observation", o["id"], o["code"]["text"]) for o in observations]
    conclusion = conclusion_of(doc.text)
    if conclusion:
        res["conclusion"] = conclusion
    if doc.text:
        res["presentedForm"] = [{"contentType": "text/plain", "language": "en", "data": b64(doc.text),
                                 "title": doc.title}]
    return res, observations


# ---------------------------------------------------------------- bundles


@dataclass
class PatientBundle:
    patient_id: str
    bundle: dict
    notes: MappingNotes


def _entry(resource: dict) -> dict:
    return {"fullUrl": urn(resource["resourceType"], resource["id"]), "resource": resource}


def build_bundles(patients: list[Patient], encounters: list[Encounter],
                  documents: list[Document]) -> list[PatientBundle]:
    """One collection Bundle per patient, plus one for documents with no patient."""
    enc_by_patient: dict[str | None, list[Encounter]] = defaultdict(list)
    for e in encounters:
        enc_by_patient[e.patient_id].append(e)
    docs_by_patient: dict[str | None, list[Document]] = defaultdict(list)
    for d in documents:
        docs_by_patient[d.patient_id].append(d)

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    out: list[PatientBundle] = []
    groups: list[tuple[str, Patient | None]] = [(p.id, p) for p in patients]
    if docs_by_patient.get(None):
        groups.append(("unlinked", None))

    for pid, patient in groups:
        notes = MappingNotes()
        key = patient.id if patient else None
        resources: list[dict] = [patient_resource(patient)] if patient else []
        resources += [encounter_resource(e, notes) for e in enc_by_patient.get(key, [])]

        docs = []
        for d in docs_by_patient.get(key, []):
            if d.source == "mtsamples" and patient and not d.encounter_id:
                enc = derived_encounter(d)
                resources.append(encounter_resource(enc, notes, derived=True))
                d = d.model_copy(update={"encounter_id": enc.id})
            docs.append(d)

        for d in docs:
            if d.kind == "note":
                resources.append(document_reference(d, notes))
            else:
                report, observations = diagnostic_report(d, notes)
                resources.append(report)
                resources += observations
        bundle = {
            "resourceType": "Bundle", "id": f"bundle-{pid}", "type": "collection", "timestamp": stamp,
            "entry": [_entry(r) for r in resources],
        }
        out.append(PatientBundle(patient_id=pid, bundle=bundle, notes=notes))
    return out
