"""SQLite cache for summaries, keyed by patient id + record hash (+ model and prompt version)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .. import config
from .schema import PatientSummary

SCHEMA = """
CREATE TABLE IF NOT EXISTS summaries (
    patient_id      TEXT NOT NULL,
    record_hash     TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    summary_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (patient_id, record_hash, model, prompt_version)
);
"""


class SummaryCache:
    def __init__(self, path: Path = config.DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(SCHEMA)

    def __enter__(self) -> "SummaryCache":
        return self

    def __exit__(self, *exc) -> None:
        self.conn.close()

    def get(self, patient_id: str, record_hash: str, model: str, prompt_version: str) -> PatientSummary | None:
        row = self.conn.execute(
            "SELECT summary_json FROM summaries WHERE patient_id = ? AND record_hash = ? AND model = ? "
            "AND prompt_version = ?", (patient_id, record_hash, model, prompt_version)).fetchone()
        return PatientSummary.model_validate_json(row[0]) if row else None

    def put(self, summary: PatientSummary) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO summaries VALUES (?, ?, ?, ?, ?, ?)",
                (summary.patient_id, summary.record_hash, summary.model, summary.prompt_version,
                 summary.model_dump_json(), summary.generated_at.isoformat()))

    def latest(self, patient_id: str, record_hash: str | None = None) -> PatientSummary | None:
        """Newest summary for a patient. With record_hash, only one written for exactly these records."""
        sql, args = "SELECT summary_json FROM summaries WHERE patient_id = ?", [patient_id]
        if record_hash:
            sql += " AND record_hash = ?"
            args.append(record_hash)
        row = self.conn.execute(sql + " ORDER BY created_at DESC LIMIT 1", args).fetchone()
        return PatientSummary.model_validate_json(row[0]) if row else None

    def export(self, path: Path, record_hashes: dict[str, str]) -> int:
        """Write the AI summaries that match the current records to a JSON file. Returns how many."""
        out = []
        for pid, rhash in sorted(record_hashes.items()):
            summary = self.latest(pid, rhash)
            if summary and summary.model != "rule-based":
                out.append(summary.model_dump(mode="json"))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        return len(out)

    def import_file(self, path: Path) -> int:
        """Add saved summaries that are not in the cache yet. Never overwrites a local one. Returns how many."""
        if not Path(path).exists():
            return 0
        added = 0
        with self.conn:
            for item in json.loads(Path(path).read_text(encoding="utf-8")):
                s = PatientSummary.model_validate(item)
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO summaries VALUES (?, ?, ?, ?, ?, ?)",
                    (s.patient_id, s.record_hash, s.model, s.prompt_version, s.model_dump_json(),
                     s.generated_at.isoformat()))
                added += cur.rowcount
        return added
