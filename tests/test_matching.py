from ehr_platform.ingestion.dedupe import dedupe_documents, match_patients
from ehr_platform.ingestion.models import Document, Identifier, Patient


def patient(pid, family, given, dob, mrn=None, system="urn:a", gender="male"):
    return Patient(id=f"hapi-{pid}", source="hapi", source_ids=[pid], family_name=family, given_names=given,
                   birth_date=dob, gender=gender, mrn=Identifier(system=system, value=mrn) if mrn else None)


def test_same_name_and_dob_merge_and_flag_conflicting_mrns():
    result = match_patients([
        patient("1", "Smith", ["Robert"], "1972-11-02", "100002", "urn:oid:1"),
        patient("2", "Smith", ["Robert"], "1972-11-02", "3001", "urn:acme"),
        patient("3", "Smith", ["Robert"], "1972-11-02", "100002", "urn:oid:1"),
    ])
    assert len(result.patients) == 1
    merged = result.patients[0]
    assert merged.source_ids == ["1", "2", "3"]
    assert merged.mrn.value == "100002"                       # the MRN most records agree on
    assert [i.value for i in merged.other_identifiers] == ["3001"]
    assert "conflicting_mrn" in merged.flags
    assert result.id_map == {"hapi-2": "hapi-1", "hapi-3": "hapi-1"}


def test_same_mrn_different_person_is_not_merged():
    result = match_patients([
        patient("1", "Smith", ["Jane"], "1985-04-12", "12345"),
        patient("2", "Doe", ["Ann"], "1990-02-20", "12345"),
    ])
    assert len(result.patients) == 2
    assert all("shared_mrn" in p.flags for p in result.patients)


def test_missing_birth_date_is_flagged_not_merged():
    result = match_patients([
        patient("1", "Smith", ["John"], None),
        patient("2", "Smith", ["John"], None),
        patient("3", "Smith", ["John"], "1980-01-15"),
    ])
    assert len(result.patients) == 3
    assert all("possible_duplicate" in p.flags for p in result.patients)


def test_middle_name_difference_is_flagged_not_merged():
    result = match_patients([
        patient("1", "Smith", ["John"], "1980-01-15"),
        patient("2", "Smith", ["John", "Developer"], "1980-01-15"),
    ])
    assert len(result.patients) == 2
    assert all("possible_duplicate" in p.flags for p in result.patients)


def test_merge_fills_gender_and_flags_disagreement():
    result = match_patients([
        patient("1", "Smart", ["Freddie"], "1946-08-22", "7055", gender="unknown"),
        patient("2", "Smart", ["Freddie"], "1946-08-22", "7055", gender="male"),
        patient("3", "Smart", ["Freddie"], "1946-08-22", "7055", gender="female"),
    ])
    merged = result.patients[0]
    assert merged.gender in ("male", "female")
    assert "conflicting_gender" in merged.flags


def doc(did, text, specialty, kind="note"):
    return Document(id=did, source="mtsamples", source_id=did, source_ids=[did], kind=kind, title=did,
                    specialties=[specialty], text=text)


def test_duplicate_reports_across_specialties_become_one():
    docs = [
        doc("1", "FINDINGS: Normal chest x-ray.", "Cardiovascular / Pulmonary"),
        doc("2", "FINDINGS:  normal chest X-ray", "Radiology", kind="imaging_report"),
        doc("3", "Different report.", "Radiology", kind="imaging_report"),
    ]
    kept, removed = dedupe_documents(docs, by_patient=False)
    assert removed == 1
    first = kept[0]
    assert first.source_ids == ["1", "2"]
    assert first.specialties == ["Cardiovascular / Pulmonary", "Radiology"]
    assert first.kind == "imaging_report"
