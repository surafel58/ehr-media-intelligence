"""Collect searchable records from the FHIR store and the summary cache."""

from __future__ import annotations

from ..fhir_mapping import FhirStore
from ..summarization import SummaryCache
from .documents import Record, records_from_bundle, summary_record


def collect_records(store: FhirStore, cache: SummaryCache) -> list[Record]:
    records: list[Record] = []
    for pid in store.patient_ids():
        patient_records = records_from_bundle(store.bundle(pid), pid)
        records += patient_records
        summary = cache.latest(pid)
        if summary and summary.model != "rule-based":
            latest = max((r.date for r in patient_records if r.date), default=None)
            records.append(summary_record(summary, latest))
    return records
