import base64
import csv
import json
from datetime import datetime, timezone

import pytest

from ehr_platform.ingestion import run, write


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def note(nid, text, subject=None, encounter=None, date=None, content_type="text/plain", type_text=None):
    res = {"resourceType": "DocumentReference", "id": nid, "status": "current",
           "content": [{"attachment": {"contentType": content_type, "data": b64(text)}}]}
    if subject:
        res["subject"] = {"reference": f"Patient/{subject}"}
    if encounter:
        res["context"] = {"encounter": [{"reference": f"Encounter/{encounter}"}]}
    if date:
        res["date"] = date
    if type_text:
        res["type"] = {"text": type_text}
    return res


NOTE_TEXT = "Progress note, Sunrise\nPatient: Robert Smith (DOB 1972-11-02)\nChest pain for two days, improving."


@pytest.fixture
def raw_files(tmp_path):
    resources = [
        {"resourceType": "Patient", "id": "1", "name": [{"family": "smith", "given": ["robert"]}],
         "birthDate": "1972-11-02", "gender": "male",
         "identifier": [{"system": "urn:oid:1", "value": "MRN-100002"}]},
        {"resourceType": "Patient", "id": "2", "name": [{"family": "Smith", "given": ["Robert"]}],
         "birthDate": "1972-11-02", "gender": "male",
         "identifier": [{"system": "urn:acme:mrn", "value": "MRN-3001"}]},
        {"resourceType": "Patient", "id": "3", "name": [{"family": "Doe", "given": ["Jane"]}],
         "birthDate": "1980-01-01"},
        {"resourceType": "Encounter", "id": "e1", "status": "finished", "subject": {"reference": "Patient/2"},
         "period": {"start": "2024-03-16T10:00:00-05:00"}},
        note("n1", NOTE_TEXT, subject="2", encounter="e1", date="2024-03-16T10:00:00-05:00"),
        note("n2", NOTE_TEXT, subject="1", date="2024-03-16T10:00:00-05:00"),           # copy, after merge
        note("n3", "Clinical note about a fall at home with hip pain.", date="2024-01-02"),  # no patient
        note("n4", "<html><body><h1>Pre-visit report</h1><p>Headache for a week.</p></body></html>",
             subject="3", content_type="text/html"),
        note("n5", "problemCode,detail\ngcs,eye", content_type="text/csv"),
        {"resourceType": "Observation", "id": "o1", "status": "final",
         "code": {"coding": [{"system": "http://loinc.org", "code": "6690-2"}]},
         "valueQuantity": {"value": 15, "unit": "10*3/uL"},
         "referenceRange": [{"low": {"value": 4}, "high": {"value": 11}}]},
        {"resourceType": "DiagnosticReport", "id": "r1", "status": "final", "subject": {"reference": "Patient/3"},
         "code": {"text": "CBC panel"}, "effectiveDateTime": "2024-02-01", "result": [{"reference": "Observation/o1"}]},
    ]
    hapi = tmp_path / "hapi.json"
    hapi.write_text(json.dumps({"resourceType": "Bundle", "type": "collection",
                                "entry": [{"resource": r} for r in resources]}))

    mts = tmp_path / "mtsamples.csv"
    with mts.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["", "description", "medical_specialty", "sample_name", "transcription", "keywords"])
        w.writerow(["7", "Chest x-ray.", " Cardiovascular / Pulmonary", " Chest X-ray ",
                    "FINDINGS:,  Normal heart size.,IMPRESSION:,  No acute disease.", ""])
        w.writerow(["9", "Chest x-ray.", " Radiology", " Chest X-ray ",
                    "FINDINGS:,  Normal heart size.,IMPRESSION:,  No acute disease.", "radiology, chest"])
        w.writerow(["12", "Empty.", " Surgery", " Empty ", "", ""])
        w.writerow(["15", "Discharge.", " Discharge Summary", " Discharge Summary - 1 ",
                    "DATE OF ADMISSION:,  03/04/2008.,HISTORY:,  A 45-year-old male with pneumonia.", "pneumonia"])
    return hapi, mts


@pytest.fixture
def dataset(raw_files):
    return run(*raw_files)


def by_id(items):
    return {i.id: i for i in items}


def test_duplicate_patients_merge_with_audit(dataset):
    patients = by_id(dataset.patients)
    merged = patients["hapi-1"]
    assert "hapi-2" not in patients
    assert merged.source_ids == ["1", "2"]
    assert merged.family_name == "Smith" and merged.given_names == ["Robert"]
    assert "conflicting_mrn" in merged.flags
    rules = [a.rule for a in merged.audit]
    assert "patient.merged" in rules and "name.normalized" in rules and "mrn.format" in rules


def test_missing_gender_becomes_unknown_and_is_flagged(dataset):
    jane = by_id(dataset.patients)["hapi-3"]
    assert jane.gender == "unknown"
    assert "missing_gender" in jane.flags
    assert any(a.rule == "gender.missing" for a in jane.audit)


def test_notes_follow_merged_patient_and_copies_are_removed(dataset):
    docs = by_id(dataset.documents)
    kept = docs["hapi-doc-n1"]
    assert "hapi-doc-n2" not in docs
    assert kept.patient_id == "hapi-1"
    assert kept.encounter_id == "hapi-enc-e1"
    assert kept.date == datetime(2024, 3, 16, 15, 0, tzinfo=timezone.utc)
    assert kept.source_ids == ["n1", "n2"]


def test_note_without_patient_is_unlinked(dataset):
    assert dataset.report.unlinked_documents == ["hapi-doc-n3"]
    assert "unlinked" in by_id(dataset.documents)["hapi-doc-n3"].flags


def test_html_note_is_stripped_and_title_inferred(dataset):
    doc = by_id(dataset.documents)["hapi-doc-n4"]
    assert doc.text == "Pre-visit report\n\nHeadache for a week."
    assert doc.title == "Pre-visit report"


def test_lab_result_named_from_code_and_flagged_high(dataset):
    report = by_id(dataset.documents)["hapi-report-r1"]
    result = report.results[0]
    assert result.name == "White blood cell count"
    assert result.interpretation == "high"
    assert {a.rule for a in report.audit} >= {"lab.name_from_code", "lab.interpretation_computed"}


def test_mtsamples_duplicates_empty_rows_and_demographics(dataset):
    mts = [d for d in dataset.documents if d.source == "mtsamples"]
    assert len(mts) == 2
    xray = by_id(mts)["mts-doc-0007"]
    assert xray.kind == "imaging_report"
    assert xray.specialties == ["Cardiovascular / Pulmonary", "Radiology"]
    assert xray.text == "FINDINGS: Normal heart size.\nIMPRESSION: No acute disease."

    discharge = by_id(mts)["mts-doc-0015"]
    assert discharge.date.date().isoformat() == "2008-03-04"
    patient = by_id(dataset.patients)["mts-0015"]
    assert (patient.age_years, patient.gender) == (45, "male")
    assert "identity_unknown" in patient.flags

    reasons = sorted(d.reason for d in dataset.report.dropped)
    assert reasons == ["empty transcription", "no note text (attachment type: text/csv)"]


def test_outputs_are_written(dataset, tmp_path):
    out = tmp_path / "clean"
    write(dataset, out)
    for name in ("patients", "encounters", "documents", "report"):
        assert json.loads((out / f"{name}.json").read_text(encoding="utf-8"))
