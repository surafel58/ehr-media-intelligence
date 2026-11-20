import base64
import copy
from datetime import datetime, timezone

import pytest

from ehr_platform.fhir_mapping import FhirStore, build_bundles, check, validate_bundle
from ehr_platform.fhir_mapping.mapper import conclusion_of, patient_resource, urn
from ehr_platform.ingestion.models import Document, Encounter, Identifier, LabResult, Patient

WHEN = datetime(2024, 3, 16, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def records():
    patients = [
        Patient(id="hapi-1", source="hapi", source_ids=["1", "2"], family_name="Smith", given_names=["Robert"],
                birth_date="1972-11-02", gender="male", mrn=Identifier(system="urn:oid:1", value="100002"),
                other_identifiers=[Identifier(system="urn:acme", value="3001")], flags=["conflicting_mrn"]),
        Patient(id="mts-0015", source="mtsamples", source_ids=["15"], gender="male", age_years=45,
                flags=["identity_unknown"]),
    ]
    encounters = [Encounter(id="hapi-enc-e1", source="hapi", source_id="e1", patient_id="hapi-1",
                            status="finished", class_code="AMB", start=WHEN)]
    documents = [
        Document(id="hapi-doc-n1", source="hapi", source_id="n1", kind="note", patient_id="hapi-1",
                 encounter_id="hapi-enc-e1", title="Progress note", text="Chest pain, improving.",
                 date=WHEN, date_precision="datetime", status=None),
        Document(id="hapi-report-r1", source="hapi", source_id="r1", kind="lab_report", patient_id="hapi-1",
                 title="CBC panel", status="final", date=WHEN, date_precision="datetime",
                 results=[LabResult(source_id="o1", name="White blood cell count", loinc="6690-2", value=15.0,
                                    unit="10*3/uL", low=4, high=11, interpretation="high")]),
        Document(id="mts-doc-0015", source="mtsamples", source_id="15", kind="imaging_report",
                 patient_id="mts-0015", title="Chest X-ray", specialties=["Radiology"],
                 text="FINDINGS: Clear lungs.\nIMPRESSION: No acute disease."),
    ]
    return patients, encounters, documents


@pytest.fixture
def bundles(records):
    return {b.patient_id: b for b in build_bundles(*records)}


def resources(bundle, rtype=None):
    return [e["resource"] for e in bundle["entry"] if rtype is None or e["resource"]["resourceType"] == rtype]


def test_patient_mapping_keeps_mrn_type_and_flags(records):
    res = patient_resource(records[0][0])
    mrn = res["identifier"][0]
    assert mrn["type"]["coding"][0]["code"] == "MR" and mrn["value"] == "100002"
    assert res["identifier"][1] == {"value": "3001", "system": "urn:acme"}
    assert res["meta"]["tag"][0]["code"] == "conflicting_mrn"
    assert res["birthDate"] == "1972-11-02"


def test_stated_age_goes_to_an_extension(records):
    res = patient_resource(records[0][1])
    assert "birthDate" not in res and "name" not in res
    assert res["extension"][0]["valueAge"]["value"] == 45


def test_bundle_per_patient_with_resolving_references(bundles):
    bundle = bundles["hapi-1"].bundle
    types = [r["resourceType"] for r in resources(bundle)]
    assert types == ["Patient", "Encounter", "DocumentReference", "DiagnosticReport", "Observation"]
    note = resources(bundle, "DocumentReference")[0]
    assert note["subject"]["reference"] == urn("Patient", "hapi-1")
    assert note["context"]["encounter"][0]["reference"] == urn("Encounter", "hapi-enc-e1")
    assert base64.b64decode(note["content"][0]["attachment"]["data"]).decode() == "Chest pain, improving."
    errors, _ = validate_bundle(bundle)
    assert errors == []


def test_missing_status_gets_default_and_is_reported(bundles):
    note = resources(bundles["hapi-1"].bundle, "DocumentReference")[0]
    assert note["status"] == "current"
    assert bundles["hapi-1"].notes.items == ["DocumentReference/hapi-doc-n1: status missing, set to 'current'"]


def test_lab_report_links_observations(bundles):
    bundle = bundles["hapi-1"].bundle
    report = resources(bundle, "DiagnosticReport")[0]
    obs = resources(bundle, "Observation")[0]
    assert report["category"][0]["coding"][0]["code"] == "LAB"
    assert report["result"][0]["reference"] == urn("Observation", obs["id"])
    assert obs["interpretation"][0]["coding"][0]["code"] == "H"
    assert obs["valueQuantity"] == {"value": 15.0, "unit": "10*3/uL"}


def test_mtsamples_report_gets_derived_encounter_and_conclusion(bundles):
    bundle = bundles["mts-0015"].bundle
    enc = resources(bundle, "Encounter")[0]
    report = resources(bundle, "DiagnosticReport")[0]
    assert {"system": "urn:ehr-media-intelligence:data-quality", "code": "derived"} in enc["meta"]["tag"]
    assert report["encounter"]["reference"] == urn("Encounter", enc["id"])
    assert report["category"][0]["coding"][0]["code"] == "RAD"
    assert report["conclusion"] == "No acute disease."
    assert validate_bundle(bundle)[0] == []


@pytest.mark.parametrize(
    ("breakage", "expected"),
    [
        (lambda b: resources(b, "Patient")[0].update(gender="M"), "'M' is not an allowed code"),
        (lambda b: resources(b, "Encounter")[0].pop("class"), "required field missing"),
        (lambda b: resources(b, "Patient")[0].update(birthDate="03/16/2024"), "does not match spec regex"),
        (lambda b: resources(b, "DocumentReference")[0]["subject"].update(reference="urn:uuid:missing"),
         "does not resolve inside the Bundle"),
        (lambda b: b["entry"].append(copy.deepcopy(b["entry"][0])), "used 2 times"),
    ],
)
def test_validator_catches_errors(bundles, breakage, expected):
    bundle = copy.deepcopy(bundles["hapi-1"].bundle)
    breakage(bundle)
    errors, _ = validate_bundle(bundle)
    assert any(expected in e.message for e in errors), errors


def test_relative_references_resolve_against_restful_full_urls():
    def encounter(eid, patient):
        return {"fullUrl": f"https://example.org/fhir/Encounter/{eid}",
                "resource": {"resourceType": "Encounter", "id": eid, "status": "finished", "class": {"code": "AMB"},
                             "subject": {"reference": f"Patient/{patient}"}}}
    bundle = {"resourceType": "Bundle", "type": "collection", "entry": [
        {"fullUrl": "https://example.org/fhir/Patient/1", "resource": {"resourceType": "Patient", "id": "1"}},
        encounter("e1", "1"),
        encounter("e2", "2"),
    ]}
    errors, _ = validate_bundle(bundle)
    assert [(e.resource, e.path) for e in errors] == [("Encounter/e2", "subject")]


def test_warnings_for_gaps(bundles):
    _, warnings = validate_bundle(bundles["mts-0015"].bundle)
    messages = {w.message for w in warnings}
    assert {"no identifier", "no name", "no birth date", "no date"} <= messages


def test_conclusion_header_order():
    text = "FINDINGS: Mass.\nFINAL DIAGNOSIS: Adenocarcinoma.\nCOMMENT: Discussed."
    assert conclusion_of(text) == "Adenocarcinoma."
    assert conclusion_of("FINDINGS: Normal.") is None


def test_store_round_trip(bundles, tmp_path):
    with FhirStore(tmp_path / "test.db") as store:
        for pb in bundles.values():
            store.save(pb.patient_id, pb.bundle, check(pb.bundle, pb.patient_id))
        assert store.patient_ids() == ["hapi-1", "mts-0015"]
        assert store.bundle("hapi-1")["id"] == "bundle-hapi-1"
        assert len(store.resources("Observation")) == 1
        assert len(store.resources(patient_id="mts-0015")) == 3
        assert store.resource("Patient", "hapi-1")["name"][0]["family"] == "Smith"
        store.clear()
        assert store.patient_ids() == []
