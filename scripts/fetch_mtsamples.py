"""Build the MTSamples subset used by the pipeline.

Downloads the full MTSamples CSV (or reads a local copy) and writes a subset to
data/raw/mtsamples.csv. Rows are copied unchanged. For every report picked, all
of its duplicate copies in the full file are included too, plus a few rows with
an empty transcription, so the subset keeps the problems of the full dataset.

Usage:
  uv run python scripts/fetch_mtsamples.py
  uv run python scripts/fetch_mtsamples.py --source path/to/mtsamples.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import urllib.request
from collections import defaultdict
from pathlib import Path

URL = "https://raw.githubusercontent.com/aneuraz/mtsamplesFR/master/data/mtsamples.csv"
OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "mtsamples.csv"

PICK = {
    "Radiology": 8,
    "Discharge Summary": 6,
    "SOAP / Chart / Progress Notes": 6,
    "Consult - History and Phy.": 5,
    "Emergency Room Reports": 4,
    "Lab Medicine - Pathology": 4,
}
EMPTY_ROWS = 3


def load(source: str | None) -> str:
    if source:
        return Path(source).read_text(encoding="utf-8")
    with urllib.request.urlopen(URL, timeout=600) as resp:
        return resp.read().decode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", help="local copy of the full mtsamples.csv")
    args = parser.parse_args()

    csv.field_size_limit(10**8)
    reader = csv.reader(io.StringIO(load(args.source), newline=""))
    header, rows = next(reader), list(reader)
    col = {name: i for i, name in enumerate(header)}
    spec_i, text_i = col["medical_specialty"], col["transcription"]

    by_text: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        if row[text_i].strip():
            by_text[row[text_i].strip()].append(i)

    chosen: set[int] = set()
    taken: dict[str, int] = defaultdict(int)
    for i, row in enumerate(rows):
        spec, text = row[spec_i].strip(), row[text_i].strip()
        if spec in PICK and text and taken[spec] < PICK[spec] and i not in chosen:
            taken[spec] += 1
            chosen.update(by_text[text])  # the report and all of its duplicate copies
    empties = [i for i, row in enumerate(rows) if not row[text_i].strip()][:EMPTY_ROWS]
    chosen.update(empties)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for i in sorted(chosen):
            writer.writerow(rows[i])
    print(f"Full file: {len(rows)} rows. Subset: {len(chosen)} rows written to {OUT}")


if __name__ == "__main__":
    main()
