from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ehr_platform.fhir_mapping import build_bundles
from ehr_platform.ingestion.models import Document, Identifier, LabResult, Patient
from ehr_platform.summarization import DISCLAIMER, SummaryCache, build_context, summarize_all
from ehr_platform.summarization.client import LLMResult, ClaudeSummarizer, SummaryError
from ehr_platform.summarization.context import record_hash
from ehr_platform.summarization.quality import run_checks, word_count
from ehr_platform.summarization.schema import Anomaly, ClinicalSummary, Finding, MediaRecord

WHEN = datetime(2024, 3, 16, 15, 0, tzinfo=timezone.utc)


def make_bundles(note_text="Chest pain for two days. Assessment: stable angina. Plan: stress test."):
    patients = [
        Patient(id="hapi-1", source="hapi", source_ids=["1"], family_name="Smith", given_names=["Robert"],
                birth_date="1972-11-02", gender="male", mrn=Identifier(system="urn:a", value="100002"),
                flags=["conflicting_mrn"]),
        Patient(id="hapi-2", source="hapi", source_ids=["2"], family_name="Doe", given_names=["Jane"],
                birth_date="1980-01-01", gender="female"),
    ]
    documents = [
        Document(id="hapi-doc-n1", source="hapi", source_id="n1", kind="note", patient_id="hapi-1",
                 title="Progress note", text=note_text, date=WHEN, date_precision="datetime", status="current"),
        Document(id="hapi-report-r1", source="hapi", source_id="r1", kind="lab_report", patient_id="hapi-1",
                 title="Glucose panel", status="final", date=WHEN, date_precision="datetime",
                 results=[LabResult(source_id="o1", name="Glucose in blood", value=6.3, unit="mmol/L",
                                    low=3.1, high=6.2, interpretation="high")]),
    ]
    return {b.patient_id: b.bundle for b in build_bundles(patients, [], documents)}


def good_summary(words_extra: int = 0) -> ClinicalSummary:
    return ClinicalSummary(
        overview="Chest pain assessed as stable angina, stress test planned." + " word" * words_extra,
        chief_concern="Chest pain",
        key_diagnoses=[Finding(text="Stable angina", source_ids=["hapi-doc-n1"])],
        recent_media=[MediaRecord(source_id="hapi-report-r1", kind="lab", date="2024-03-16",
                                  result="Glucose 6.3 mmol/L, high")],
        anomalies=[Anomaly(kind="clinical", text="Glucose 6.3 mmol/L above range", source_ids=["hapi-report-r1"]),
                   Anomaly(kind="data_quality", text="Merged records carried different MRNs",
                           source_ids=["hapi-doc-n1"])],
        confidence="medium", confidence_reason="Single short note.",
    )


class FakeClaude:
    def __init__(self, summaries=None, fail_on=None):
        self.calls = []
        self.summaries = list(summaries or [])
        self.fail_on = fail_on

    def __call__(self, text: str) -> LLMResult:
        self.calls.append(text)
        if self.fail_on and self.fail_on in text:
            raise SummaryError("declined by the model (test)")
        summary = self.summaries.pop(0) if self.summaries else good_summary()
        return LLMResult(summary=summary, input_tokens=1000, output_tokens=300)


@pytest.fixture
def cache(tmp_path):
    with SummaryCache(tmp_path / "cache.db") as c:
        yield c


def test_context_leaves_out_name_and_mrn():
    ctx = build_context(make_bundles()["hapi-1"])
    assert "Smith" not in ctx.text and "Robert" not in ctx.text and "100002" not in ctx.text
    assert "birth date: 1972-11-02" in ctx.text


def test_context_lists_records_with_ids_abnormal_values_and_flags():
    ctx = build_context(make_bundles()["hapi-1"])
    assert "[hapi-doc-n1] 2024-03-16 | Note: Progress note" in ctx.text
    assert "- Glucose in blood: 6.3 mmol/L (high; ref 3.1-6.2)" in ctx.text
    assert ctx.source_ids == {"hapi-doc-n1", "hapi-report-r1"}
    assert ctx.abnormal == {"hapi-report-r1": ["Glucose in blood"]}
    assert ctx.flags == ["conflicting_mrn"]


def test_record_hash_ignores_bundle_timestamp_but_not_content():
    a, b = make_bundles()["hapi-1"], make_bundles()["hapi-1"]
    b["timestamp"] = "2030-01-01T00:00:00Z"
    assert record_hash(a) == record_hash(b)
    changed = make_bundles(note_text="Different note text.")["hapi-1"]
    assert record_hash(changed) != record_hash(a)


def test_cache_hit_skips_the_api(cache):
    bundles = make_bundles()
    claude = FakeClaude()
    first = summarize_all(bundles, cache, "claude-opus-5-5", claude)
    assert sorted(first.generated) == ["hapi-1", "hapi-2"] and len(claude.calls) == 1
    second = summarize_all(bundles, cache, "claude-opus-5-5", claude)
    assert sorted(second.from_cache) == ["hapi-1", "hapi-2"] and len(claude.calls) == 1


def test_changed_records_get_a_new_summary(cache):
    claude = FakeClaude()
    summarize_all(make_bundles(), cache, "claude-opus-5-5", claude)
    run = summarize_all(make_bundles(note_text="New visit: shortness of breath."), cache, "claude-opus-5-5", claude)
    assert run.generated == ["hapi-1"] and len(claude.calls) == 2


def test_patient_without_records_needs_no_api_call(cache):
    run = summarize_all({"hapi-2": make_bundles()["hapi-2"]}, cache, "claude-opus-5-5", call=None)
    summary = run.summaries["hapi-2"]
    assert summary.model == "rule-based"
    assert summary.summary.chief_concern == "No clinical records"
    assert summary.disclaimer == DISCLAIMER


def test_long_summary_is_retried_once(cache):
    claude = FakeClaude(summaries=[good_summary(words_extra=250), good_summary()])
    run = summarize_all({"hapi-1": make_bundles()["hapi-1"]}, cache, "claude-opus-5-5", claude)
    summary = run.summaries["hapi-1"]
    assert len(claude.calls) == 2 and "over the 200-word limit" in claude.calls[1]
    assert summary.usage.attempts == 2 and summary.word_count <= 200


def test_one_failure_does_not_stop_the_run(cache):
    bundles = make_bundles()
    bundles["hapi-3"] = make_bundles(note_text="FAIL ME please")["hapi-1"]
    run = summarize_all(bundles, cache, "claude-opus-5-5", FakeClaude(fail_on="FAIL ME"))
    assert list(run.failed) == ["hapi-3"] and "hapi-1" in run.summaries


def test_quality_checks_pass_for_a_grounded_summary():
    ctx = build_context(make_bundles()["hapi-1"])
    checks = {c.name: c.passed for c in run_checks(good_summary(), ctx)}
    assert all(checks.values()), checks


def test_quality_checks_catch_problems():
    ctx = build_context(make_bundles()["hapi-1"])
    bad = good_summary(words_extra=250)
    bad.key_diagnoses.append(Finding(text="Diabetes, HbA1c 9.1", source_ids=["hapi-doc-999"]))
    bad.anomalies = [Anomaly(kind="clinical", text="Something odd", source_ids=[])]
    failed = {c.name: c.detail for c in run_checks(bad, ctx) if not c.passed}
    assert set(failed) == {"under_word_limit", "citations_exist", "findings_cited", "numbers_grounded",
                           "abnormal_results_reported", "data_quality_reported"}
    assert "hapi-doc-999" in failed["citations_exist"]
    assert "9.1" in failed["numbers_grounded"]


def test_word_count_covers_every_field():
    assert word_count(good_summary()) == len(
        ("Chest pain assessed as stable angina, stress test planned. Chest pain Single short note. "
         "Stable angina Glucose 6.3 mmol/L, high Glucose 6.3 mmol/L above range "
         "Merged records carried different MRNs").split())


def fake_client(stop_reason="end_turn", parsed=None):
    captured = {}

    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed, stop_details=None,
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse))), captured


def test_client_request_shape_and_fallback_option():
    client, captured = fake_client(parsed=good_summary())
    result = ClaudeSummarizer(model="claude-opus-5-5", client=client).call("records")
    assert result.summary.chief_concern == "Chest pain"
    assert captured["output_format"] is ClinicalSummary
    assert captured["output_config"] == {"effort": "medium"}
    assert captured["fallbacks"] == "default"

    client, captured = fake_client(parsed=good_summary())
    ClaudeSummarizer(model="claude-haiku-5-5", client=client).call("records")
    assert "fallbacks" not in captured


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_client_raises_on_unusable_response(stop_reason):
    client, _ = fake_client(stop_reason=stop_reason, parsed=None)
    with pytest.raises(SummaryError):
        ClaudeSummarizer(model="claude-opus-5-5", client=client).call("records")


# ---------------------------------------------------------------- Gemini provider

from google.genai import errors as genai_errors  # noqa: E402

from ehr_platform.summarization.gemini import GeminiSummarizer  # noqa: E402


def gemini_response(parsed=None, text=None, finish="STOP"):
    return SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))], parsed=parsed, text=text,
        usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=50, thoughts_token_count=25))


def gemini_client(*responses):
    queue = list(responses)
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), calls


def test_gemini_returns_summary_and_counts_thinking_tokens():
    client, calls = gemini_client(gemini_response(parsed=good_summary()))
    result = GeminiSummarizer(model="gemini-test", client=client).call("records")
    assert result.summary.chief_concern == "Chest pain"
    assert (result.input_tokens, result.output_tokens) == (100, 75)
    assert calls[0]["config"].response_schema is ClinicalSummary


def test_gemini_reads_json_text_when_not_parsed():
    client, _ = gemini_client(gemini_response(text=good_summary().model_dump_json()))
    assert GeminiSummarizer(client=client).call("records").summary.confidence == "medium"


def test_gemini_waits_and_retries_on_rate_limit():
    limit = genai_errors.ClientError(429, {"error": {"code": 429, "message": "Please retry in 2.5s.",
                                                     "status": "RESOURCE_EXHAUSTED"}})
    client, calls = gemini_client(limit, gemini_response(parsed=good_summary()))
    waits = []
    GeminiSummarizer(client=client, sleep=waits.append).call("records")
    assert len(calls) == 2 and waits == [3.5]


def test_gemini_blocked_response_raises():
    client, _ = gemini_client(gemini_response(finish="SAFETY"))
    with pytest.raises(SummaryError, match="blocked"):
        GeminiSummarizer(client=client).call("records")


def test_age_worked_out_from_birth_date_counts_as_grounded():
    ctx = build_context(make_bundles()["hapi-1"])  # born 1972-11-02, records dated 2024-03-16
    summary = good_summary()
    summary.overview = "A 51-year-old man with chest pain assessed as stable angina."
    checks = {c.name: c for c in run_checks(summary, ctx)}
    assert checks["numbers_grounded"].passed
    summary.overview = "A 49-year-old man with chest pain."
    assert not {c.name: c for c in run_checks(summary, ctx)}["numbers_grounded"].passed


def test_cached_only_makes_no_calls_and_lists_pending(cache):
    run = summarize_all(make_bundles(), cache, "claude-opus-5-5", call=None, cached_only=True)
    assert run.pending == ["hapi-1"]
    assert list(run.summaries) == ["hapi-2"]  # no records, needs no call


def test_demographic_data_quality_note_needs_no_citation():
    ctx = build_context(make_bundles()["hapi-1"])
    summary = good_summary()
    summary.anomalies.append(Anomaly(kind="data_quality", text="Sex recorded as unknown", source_ids=[]))
    assert {c.name: c.passed for c in run_checks(summary, ctx)}["findings_cited"]
