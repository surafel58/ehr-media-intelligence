"""SQLite store for FHIR Bundles and their resources."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from .validate import BundleResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS bundles (
    patient_id     TEXT PRIMARY KEY,
    bundle_id      TEXT NOT NULL,
    valid          INTEGER NOT NULL,
    error_count    INTEGER NOT NULL,
    warning_count  INTEGER NOT NULL,
    resource_count INTEGER NOT NULL,
    bundle_json    TEXT NOT NULL,
    stored_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resources (
    resource_type  TEXT NOT NULL,
    resource_id    TEXT NOT NULL,
    patient_id     TEXT NOT NULL REFERENCES bundles(patient_id) ON DELETE CASCADE,
    full_url       TEXT NOT NULL,
    resource_json  TEXT NOT NULL,
    PRIMARY KEY (resource_type, resource_id)
);
CREATE INDEX IF NOT EXISTS idx_resources_patient ON resources(patient_id);
CREATE INDEX IF NOT EXISTS idx_resources_type ON resources(resource_type);
"""


class FhirStore:
    def __init__(self, path: Path = config.DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def __enter__(self) -> "FhirStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def clear(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM resources")
            self.conn.execute("DELETE FROM bundles")

    def save(self, patient_id: str, bundle: dict, result: BundleResult) -> None:
        entries = bundle.get("entry", [])
        with self.conn:
            self.conn.execute("DELETE FROM resources WHERE patient_id = ?", (patient_id,))
            self.conn.execute(
                "INSERT OR REPLACE INTO bundles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (patient_id, bundle["id"], int(result.valid), len(result.errors), len(result.warnings),
                 len(entries), json.dumps(bundle), datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO resources VALUES (?, ?, ?, ?, ?)",
                [(e["resource"]["resourceType"], e["resource"]["id"], patient_id, e["fullUrl"],
                  json.dumps(e["resource"])) for e in entries],
            )

    def bundle(self, patient_id: str) -> dict | None:
        row = self.conn.execute("SELECT bundle_json FROM bundles WHERE patient_id = ?", (patient_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def patient_ids(self, valid_only: bool = False) -> list[str]:
        sql = "SELECT patient_id FROM bundles" + (" WHERE valid = 1" if valid_only else "") + " ORDER BY patient_id"
        return [r[0] for r in self.conn.execute(sql)]

    def resource(self, resource_type: str, resource_id: str) -> dict | None:
        row = self.conn.execute("SELECT resource_json FROM resources WHERE resource_type = ? AND resource_id = ?",
                                (resource_type, resource_id)).fetchone()
        return json.loads(row[0]) if row else None

    def resources(self, resource_type: str | None = None, patient_id: str | None = None) -> list[dict]:
        sql, args = "SELECT resource_json FROM resources WHERE 1 = 1", []
        if resource_type:
            sql += " AND resource_type = ?"
            args.append(resource_type)
        if patient_id:
            sql += " AND patient_id = ?"
            args.append(patient_id)
        return [json.loads(r[0]) for r in self.conn.execute(sql + " ORDER BY resource_type, resource_id", args)]
