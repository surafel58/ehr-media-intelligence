import time
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ehr_platform.api.app import create_app
from ehr_platform.fhir_mapping import FhirStore, build_bundles, check
from ehr_platform.ingestion.models import Document, Identifier, LabResult, Patient
from ehr_platform.search import HashEmbedder, SearchIndex, chunk, collect_records, records_from_bundle
from ehr_platform.summarization import SummaryCache
from ehr_platform.summarization.schema import ClinicalSummary, Finding, PatientSummary


def at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T10:00:00+00:00")


def note(nid, pid, text, day=None, title="Progress note"):
    return Document(id=nid, source="hapi", source_id=nid, kind="note", patient_id=pid, title=title, text=text,
                    status="current", date=at(day) if day else None, date_precision="datetime" if day else None)


ANGINA = "Chest pain on exertion for two weeks. Assessment: stable angina. Plan: exercise stress test."


@pytest.fixture
def env(tmp_path):
    patients = [
        Patient(id="hapi-1", source="hapi", source_ids=["1"], family_name="Smith", given_names=["Robert"],
                birth_date="1972-11-02", gender="male", mrn=Identifier(system="urn:a", value="100002")),
        Patient(id="hapi-2", source="hapi", source_ids=["2"], family_name="Brooks", given_names=["Hannah"],
                birth_date="1992-12-15", gender="female"),
        Patient(id="mts-0015", source="mtsamples", source_ids=["15"], flags=["identity_unknown"]),
    ]
    documents = [
        note("hapi-doc-a", "hapi-1", ANGINA, "2024-03-16"),
        note("hapi-doc-b", "hapi-1", ANGINA, "2024-03-17"),  # same text, another day
        Document(id="hapi-report-r1", source="hapi", source_id="r1", kind="lab_report", patient_id="hapi-1",
                 title="Glucose panel", status="final", date=at("2023-05-01"), date_precision="datetime",
                 results=[LabResult(source_id="o1", name="Glucose in blood", value=6.3, unit="mmol/L",
                                    low=3.1, high=6.2, interpretation="high")]),
        note("hapi-doc-c", "hapi-2", "New headaches with visual aura on oral contraceptives. Migraine with aura.",
             "2025-01-10"),
        Document(id="mts-doc-0015", source="mtsamples", source_id="15", kind="imaging_report",
                 patient_id="mts-0015", title="Chest X-ray", specialties=["Radiology"],
                 text="FINDINGS: Clear lungs, normal heart size.\nIMPRESSION: No acute disease."),
    ]
    db = tmp_path / "ehr.db"
    with FhirStore(db) as store, SummaryCache(db) as cache:
        for pb in build_bundles(patients, [], documents):
            store.save(pb.patient_id, pb.bundle, check(pb.bundle, pb.patient_id))
        cache.put(PatientSummary(
            patient_id="hapi-1", record_hash="h", model="test-model", prompt_version="v1",
            generated_at=datetime.now(timezone.utc), word_count=20,
            summary=ClinicalSummary(overview="Stable angina with a stress test planned. Glucose high.",
                                    chief_concern="Chest pain",
                                    key_diagnoses=[Finding(text="Stable angina", source_ids=["hapi-doc-a"])],
                                    recent_media=[], anomalies=[], confidence="medium", confidence_reason="Short.")))
        records = collect_records(store, cache)
    index = SearchIndex(HashEmbedder(), path=tmp_path / "chroma")
    index.rebuild(records)
    return index, db, records


@pytest.fixture
def client(env):
    index, db, _ = env
    with TestClient(create_app(index=index, db_path=db)) as c:
        yield c


def search(client, query, **params):
    response = client.post("/search", params=params, json={"query": query})
    return response.status_code, response.json()


# ---------------------------------------------------------------- records and chunks


def test_chunks_overlap_and_start_with_the_title():
    text = " ".join(f"w{i}" for i in range(300))
    pieces = chunk(text, "Note", size=150, overlap=30)
    assert len(pieces) == 3
    assert all(p.startswith("Note. ") for p in pieces)
    assert pieces[1].split()[1] == "w120"  # second window starts 30 words before the first one ends


def test_records_cover_notes_reports_and_summaries(env):
    _, _, records = env
    kinds = sorted((r.record_id, r.kind) for r in records)
    assert ("hapi-report-r1", "lab_report") in kinds and ("summary-hapi-1", "summary") in kinds
    lab = next(r for r in records if r.record_id == "hapi-report-r1")
    assert "Glucose in blood: 6.3 mmol/L (high, abnormal)" in lab.text
    xray = next(r for r in records if r.record_id == "mts-doc-0015")
    assert xray.kind == "imaging_report" and xray.date is None


# ---------------------------------------------------------------- index


def test_best_match_first(env):
    index, _, _ = env
    hits = index.search("migraine with visual aura")
    assert hits[0].record_id == "hapi-doc-c"


def test_resource_type_filter(env):
    index, _, _ = env
    hits = index.search("glucose chest x-ray", resource_type="DiagnosticReport")
    assert hits and all(h.resource_type == "DiagnosticReport" for h in hits)


def test_date_filter_excludes_out_of_range_and_undated(env):
    index, _, _ = env
    hits = index.search("chest pain headache x-ray", date_from=date(2025, 1, 1))
    assert [h.record_id for h in hits] == ["hapi-doc-c"]


def test_copies_of_a_note_show_once(env):
    index, _, _ = env
    hits = index.search("stable angina stress test", per_patient=0)
    angina = [h for h in hits if h.record_id in ("hapi-doc-a", "hapi-doc-b")]
    assert len(angina) == 1 and len(angina[0].similar) == 1


def test_per_patient_cap(env):
    index, _, _ = env
    hits = index.search("chest pain angina glucose", per_patient=1)
    patients = [h.patient_id for h in hits]
    assert len(patients) == len(set(patients))


def test_search_under_two_seconds_for_50_records(tmp_path):
    from ehr_platform.search import Record
    records = [Record(f"doc-{i}", f"p-{i % 10}", "DocumentReference", "note", "Note", "2024-01-01",
                      f"note {i} about cough fever rash fracture glucose {i}") for i in range(50)]
    for r in records:
        r.chunks = chunk(r.text, r.title)
    index = SearchIndex(HashEmbedder(), path=tmp_path / "chroma")
    index.rebuild(records)
    started = time.perf_counter()
    for _ in range(20):
        assert len(index.search("fever and cough")) == 5
    assert (time.perf_counter() - started) / 20 < 2.0


# ---------------------------------------------------------------- API


def test_search_endpoint_returns_ranked_results_with_patient_and_summary(client):
    status, body = search(client, "stable angina chest pain")
    assert status == 200 and len(body["results"]) <= 5
    top = body["results"][0]
    assert top["patient"] == {"id": "hapi-1", "name": "Robert Smith", "mrn": "100002", "gender": "male",
                              "birth_date": "1972-11-02", "age": None}
    assert top["summary_snippet"].startswith("Stable angina")
    assert 0 <= top["score"] <= 1 and body["took_ms"] < 2000
    scores = [r["score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)


def test_search_endpoint_filters(client):
    _, body = search(client, "glucose", resource_type="DiagnosticReport", date_from="2023-01-01",
                     date_to="2023-12-31")
    assert [r["record_id"] for r in body["results"]] == ["hapi-report-r1"]


@pytest.mark.parametrize(
    ("query", "params"),
    [("chest pain", {"resource_type": "Banana"}),
     ("chest pain", {"date_from": "2025-01-01", "date_to": "2024-01-01"}),
     ("x", {}),
     ("chest pain", {"limit": 0})],
)
def test_search_endpoint_rejects_bad_input(client, query, params):
    status, _ = search(client, query, **params)
    assert status == 422


def test_patient_endpoint(client):
    body = client.get("/patients/hapi-1").json()
    assert body["patient"]["name"] == "Robert Smith"
    assert body["summary"]["summary"]["chief_concern"] == "Chest pain"
    assert "AI-generated" in body["summary"]["disclaimer"]
    assert {r["id"] for r in body["resources"]} >= {"hapi-doc-a", "hapi-report-r1"}
    assert client.get("/patients/nobody").status_code == 404


def test_patient_without_summary_and_resource_text(client):
    body = client.get("/patients/mts-0015").json()
    assert body["summary"] is None and body["patient"]["name"] == "Unidentified patient"
    res = client.get("/resources/DiagnosticReport/mts-doc-0015").json()
    assert "No acute disease" in res["text"]


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["indexed_chunks"] > 0
