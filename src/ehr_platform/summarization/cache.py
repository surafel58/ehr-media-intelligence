"""SQLite cache for summaries, keyed by patient id + record hash (+ model and prompt version)."""

from __future__ import annotations

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

    def latest(self, patient_id: str) -> PatientSummary | None:
        row = self.conn.execute("SELECT summary_json FROM summaries WHERE patient_id = ? "
                                "ORDER BY created_at DESC LIMIT 1", (patient_id,)).fetchone()
        return PatientSummary.model_validate_json(row[0]) if row else None
