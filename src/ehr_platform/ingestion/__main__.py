"""Run ingestion: uv run python -m ehr_platform.ingestion"""

import argparse
from pathlib import Path

from .. import config
from .pipeline import run, write


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean raw EHR data into an intermediate format.")
    parser.add_argument("--hapi", type=Path, default=config.HAPI_EXPORT)
    parser.add_argument("--mtsamples", type=Path, default=config.MTSAMPLES_CSV)
    parser.add_argument("--out", type=Path, default=config.CLEAN_DIR)
    args = parser.parse_args()

    dataset = run(args.hapi, args.mtsamples)
    write(dataset, args.out)
    r = dataset.report

    print(f"Clean data written to {args.out}\n")
    print(f"{'Count':<32}{'Value':>8}")
    for key, value in r.clean_counts.items():
        print(f"{key:<32}{value:>8}")
    print(f"\n{'Fix applied':<40}{'Times':>8}")
    for key, value in sorted(r.fixes_by_rule.items(), key=lambda kv: -kv[1]):
        print(f"{key:<40}{value:>8}")
    print(f"\n{'Flag':<32}{'Records':>8}")
    for key, value in sorted(r.flags_by_type.items(), key=lambda kv: -kv[1]):
        print(f"{key:<32}{value:>8}")
    print(f"\nMerges: {len(r.merges)}  Conflicts: {len(r.conflicts)}  Dropped: {len(r.dropped)}")


if __name__ == "__main__":
    main()
