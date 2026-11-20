"""Ingestion pipeline: raw HAPI JSON + MTSamples CSV -> clean patients, encounters and documents."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from .. import config
from .dedupe import dedupe_documents, match_patients
from .models import (
    AuditEntry,
    CleanDataset,
    Conflict,
    Document,
    Dropped,
    Encounter,
    IngestionReport,
    Patient,
)
from .readers import (
    document_from_mtsample,
    encounter_from_fhir,
    load_hapi,
    load_mtsamples,
    note_from_fhir,
    patient_for_mtsample,
    patient_from_fhir,
    report_from_fhir,
)
from .text import extract_age_sex, find_labeled_date, note_patient_header


def _flag(record, flag: str) -> None:
    if flag not in record.flags:
        record.flags.append(flag)


def _relink(record: Document | Encounter, id_map: dict[str, str], patients: dict[str, Patient]) -> None:
    """Point a record at the merged patient. Unlink it if the patient is not in the data."""
    old = record.patient_id
    if old is None:
        _flag(record, "unlinked")
    elif old in id_map:
        record.patient_id = id_map[old]
        record.audit.append(AuditEntry(field="patient_id", rule="link.patient_merged", before=old, after=record.patient_id))
    elif old not in patients:
        record.patient_id = None
        record.audit.append(AuditEntry(field="patient_id", rule="link.patient_not_found", before=old))
        _flag(record, "unlinked")


def _letters(text: str | None) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


def _age_on(birth_iso: str, on: date) -> int:
    born = date.fromisoformat(birth_iso)
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def check_note_against_patient(doc: Document, patient: Patient) -> list[Conflict]:
    """Compare the name, birth date and age written in a note with the linked patient record."""
    conflicts: list[Conflict] = []
    header = note_patient_header(doc.text)
    if header:
        name, dob = header
        name_ok = not patient.family_name or _letters(patient.family_name) in _letters(name)
        dob_ok = dob is None or patient.birth_date is None or dob == patient.birth_date
        if not (name_ok and dob_ok):
            _flag(doc, "note_patient_mismatch")
            conflicts.append(Conflict(
                kind="note_patient_mismatch", patient_ids=[patient.id],
                detail=f"{doc.id} names '{name}' (DOB {dob}); record is '{patient.display_name}' (DOB {patient.birth_date})"))

    age, _, _ = extract_age_sex(doc.text)
    if age is not None and patient.birth_date and len(patient.birth_date) == 10:
        labeled = find_labeled_date(doc.text)
        on = (date.fromisoformat(labeled.iso) if labeled and labeled.precision == "day"
              else doc.date.date() if doc.date else None)
        if on:
            expected = _age_on(patient.birth_date, on)
            if abs(expected - age) > 1:
                _flag(doc, "note_age_mismatch")
                conflicts.append(Conflict(
                    kind="note_age_mismatch", patient_ids=[patient.id],
                    detail=f"{doc.id} states age {age} on {on}; birth date {patient.birth_date} gives {expected}"))
    return conflicts


def run(hapi_path: Path = config.HAPI_EXPORT, mtsamples_path: Path = config.MTSAMPLES_CSV) -> CleanDataset:
    dropped: list[Dropped] = []
    conflicts: list[Conflict] = []

    # ---- HAPI FHIR JSON
    hapi = load_hapi(hapi_path)
    match = match_patients([patient_from_fhir(r) for r in hapi.get("Patient", [])])
    conflicts.extend(match.conflicts)
    patients = {p.id: p for p in match.patients}

    encounters = [encounter_from_fhir(r) for r in hapi.get("Encounter", [])]
    for enc in encounters:
        _relink(enc, match.id_map, patients)
    encounter_ids = {e.id for e in encounters}

    observations = {o["id"]: o for o in hapi.get("Observation", [])}
    hapi_docs: list[Document] = []
    for res in hapi.get("DocumentReference", []):
        doc = note_from_fhir(res)
        (dropped if isinstance(doc, Dropped) else hapi_docs).append(doc)
    hapi_docs += [report_from_fhir(r, observations) for r in hapi.get("DiagnosticReport", [])]

    for doc in hapi_docs:
        _relink(doc, match.id_map, patients)
        if doc.encounter_id and doc.encounter_id not in encounter_ids:
            doc.audit.append(AuditEntry(field="encounter_id", rule="link.encounter_not_found", before=doc.encounter_id))
            doc.encounter_id = None
        if doc.patient_id and doc.kind == "note":
            conflicts.extend(check_note_against_patient(doc, patients[doc.patient_id]))
    hapi_docs, hapi_dupes = dedupe_documents(hapi_docs, by_patient=True)

    # ---- MTSamples CSV
    rows = load_mtsamples(mtsamples_path)
    mts_docs: list[Document] = []
    for row in rows:
        doc = document_from_mtsample(row)
        (dropped if isinstance(doc, Dropped) else mts_docs).append(doc)
    mts_docs, mts_dupes = dedupe_documents(mts_docs, by_patient=False)
    mts_patients = []
    for doc in mts_docs:
        patient = patient_for_mtsample(doc)
        doc.patient_id = patient.id
        mts_patients.append(patient)

    all_patients = match.patients + mts_patients
    all_docs = hapi_docs + mts_docs
    records = [*all_patients, *encounters, *all_docs]

    report = IngestionReport(
        generated_at=datetime.now(timezone.utc),
        raw_counts={
            "hapi": {t: len(v) for t, v in sorted(hapi.items())},
            "mtsamples": {
                "rows": len(rows),
                "empty_transcription": sum(1 for d in dropped if d.source == "mtsamples"),
                "duplicate_copies": mts_dupes,
                "missing_keywords": sum(1 for r in rows if not (r.get("keywords") or "").strip()),
            },
        },
        clean_counts={
            "patients": len(all_patients),
            "patients_hapi": len(match.patients),
            "patients_mtsamples": len(mts_patients),
            "patients_merged_away": len(match.id_map),
            "encounters": len(encounters),
            "documents": len(all_docs),
            **{f"documents_{k}": v for k, v in sorted(Counter(d.kind for d in all_docs).items())},
            "documents_unlinked": sum(1 for d in all_docs if d.patient_id is None),
            "duplicate_documents_removed": hapi_dupes + mts_dupes,
            "records_dropped": len(dropped),
        },
        fixes_by_rule=dict(sorted(Counter(a.rule for r in records for a in r.audit).items())),
        flags_by_type=dict(sorted(Counter(f for r in records for f in r.flags).items())),
        merges=match.merges,
        conflicts=conflicts,
        unlinked_documents=[d.id for d in all_docs if d.patient_id is None],
        dropped=dropped,
    )
    return CleanDataset(patients=all_patients, encounters=encounters, documents=all_docs, report=report)


def write(dataset: CleanDataset, out_dir: Path = config.CLEAN_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, items in (("patients", dataset.patients), ("encounters", dataset.encounters),
                        ("documents", dataset.documents)):
        payload = [item.model_dump(mode="json") for item in items]
        (out_dir / f"{name}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "report.json").write_text(dataset.report.model_dump_json(indent=2), encoding="utf-8")


def load(out_dir: Path = config.CLEAN_DIR) -> tuple[list[Patient], list[Encounter], list[Document]]:
    """Read the clean output back (used by the FHIR mapping step)."""
    def read(name, model):
        return [model.model_validate(x) for x in json.loads((out_dir / f"{name}.json").read_text(encoding="utf-8"))]
    return read("patients", Patient), read("encounters", Encounter), read("documents", Document)
