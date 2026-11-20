"""Build the search index and time some queries: uv run python -m ehr_platform.search"""

import argparse
import statistics
import time
from collections import Counter

from ..fhir_mapping import FhirStore
from ..summarization import SummaryCache
from .build import collect_records
from .embedder import SentenceTransformerEmbedder
from .index import SearchIndex

SAMPLE_QUERIES = [
    "chest pain with shortness of breath",
    "abnormal blood glucose",
    "heart failure home health visit",
    "fracture on x-ray",
    "patient with migraine and visual aura",
    "wound healing after surgery",
    "prostate cancer bone scan",
    "elevated white blood cell count",
    "discharge summary after pneumonia",
    "duplicate records or conflicting identifiers",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the semantic search index.")
    parser.add_argument("--query", help="run one query after building")
    args = parser.parse_args()

    t0 = time.perf_counter()
    embedder = SentenceTransformerEmbedder()
    print(f"Model {embedder.name} loaded in {time.perf_counter() - t0:.1f}s")

    with FhirStore() as store, SummaryCache() as cache:
        records = collect_records(store, cache)
    index = SearchIndex(embedder)
    t0 = time.perf_counter()
    chunks = index.rebuild(records)
    print(f"Indexed {len(records)} records as {chunks} chunks in {time.perf_counter() - t0:.1f}s")
    print("Records: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(r.kind for r in records).items())))

    timings = []
    for q in SAMPLE_QUERIES * 3:
        t0 = time.perf_counter()
        index.search(q)
        timings.append((time.perf_counter() - t0) * 1000)
    timings.sort()
    print(f"Search time over {len(timings)} queries: median {statistics.median(timings):.0f} ms, "
          f"p95 {timings[int(len(timings) * 0.95) - 1]:.0f} ms, max {timings[-1]:.0f} ms")

    if args.query:
        for hit in index.search(args.query):
            print(f"  {hit.score:.3f}  {hit.resource_type:<18} {hit.title[:40]:<40} {hit.patient_id}")


if __name__ == "__main__":
    main()
