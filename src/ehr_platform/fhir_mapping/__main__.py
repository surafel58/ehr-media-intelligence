"""Build, validate and store FHIR Bundles: uv run python -m ehr_platform.fhir_mapping"""

import argparse
import json
from collections import Counter
from pathlib import Path

from .. import config
from ..ingestion import load
from .mapper import build_bundles
from .store import FhirStore
from .validate import check, raw_baseline, write_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Map clean records to FHIR R4 Bundles.")
    parser.add_argument("--clean", type=Path, default=config.CLEAN_DIR, help="output folder of step 1")
    parser.add_argument("--out", type=Path, default=config.FHIR_DIR)
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    args = parser.parse_args()

    patients, encounters, documents = load(args.clean)
    built = build_bundles(patients, encounters, documents)

    bundle_dir = args.out / "bundles"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for old in bundle_dir.glob("*.json"):
        old.unlink()

    results = []
    with FhirStore(args.db) as store:
        store.clear()
        for pb in built:
            result = check(pb.bundle, pb.patient_id, pb.notes.items)
            store.save(pb.patient_id, pb.bundle, result)
            (bundle_dir / f"{pb.patient_id}.json").write_text(json.dumps(pb.bundle, indent=2), encoding="utf-8")
            results.append(result)
    baseline = None
    if config.HAPI_EXPORT.exists():
        baseline = raw_baseline(json.loads(config.HAPI_EXPORT.read_text(encoding="utf-8")))
    json_path, md_path = write_report(results, args.out, baseline)

    totals = Counter()
    for r in results:
        totals.update(r.resource_counts)
    print(f"Bundles: {len(results)}  valid: {sum(r.valid for r in results)}  "
          f"with errors: {sum(not r.valid for r in results)}")
    print("Resources: " + ", ".join(f"{t} {n}" for t, n in sorted(totals.items())))
    print(f"Errors: {sum(len(r.errors) for r in results)}  Warnings: {sum(len(r.warnings) for r in results)}  "
          f"Mapping defaults: {sum(len(r.mapping_notes) for r in results)}")
    print(f"Stored in {args.db}\nReport: {md_path}")


if __name__ == "__main__":
    main()
