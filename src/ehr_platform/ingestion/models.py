"""Clean intermediate records produced by the ingestion pipeline.

Every change made while cleaning a record is written to that record's `audit`
list. Problems that need a human look (conflicts, possible duplicates) go to
`flags`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["hapi", "mtsamples"]
Gender = Literal["male", "female", "other", "unknown"]
DocKind = Literal["note", "lab_report", "imaging_report"]
Precision = Literal["datetime", "day", "month", "year"]


class AuditEntry(BaseModel):
    field: str
    rule: str
    before: str | None = None
    after: str | None = None


class Identifier(BaseModel):
    system: str | None = None
    value: str


class DateMention(BaseModel):
    """A date found inside free text, normalized to ISO format."""

    raw: str
    iso: str
    precision: Precision


class LabResult(BaseModel):
    source_id: str
    name: str
    loinc: str | None = None
    value: float | str | None = None
    unit: str | None = None
    low: float | None = None
    high: float | None = None
    interpretation: Literal["high", "low", "normal", "abnormal", "unknown"] = "unknown"


class Patient(BaseModel):
    id: str
    source: Source
    source_ids: list[str]
    mrn: Identifier | None = None
    other_identifiers: list[Identifier] = Field(default_factory=list)
    family_name: str | None = None
    given_names: list[str] = Field(default_factory=list)
    birth_date: str | None = Field(default=None, description="ISO date, may be partial: YYYY or YYYY-MM")
    gender: Gender = "unknown"
    age_years: int | None = Field(default=None, description="Only when stated in a note and no birth date is known")
    flags: list[str] = Field(default_factory=list)
    audit: list[AuditEntry] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        name = " ".join([*self.given_names, self.family_name or ""]).strip()
        return name or "Unidentified patient"


class Encounter(BaseModel):
    id: str
    source: Source
    source_id: str
    patient_id: str | None = None
    status: str | None = None
    class_code: str | None = None
    type: str | None = None
    reason: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    flags: list[str] = Field(default_factory=list)
    audit: list[AuditEntry] = Field(default_factory=list)


class Document(BaseModel):
    id: str
    source: Source
    source_id: str
    source_ids: list[str] = Field(default_factory=list, description="All raw copies merged into this record")
    kind: DocKind
    patient_id: str | None = None
    encounter_id: str | None = None
    title: str
    description: str | None = None
    specialties: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    date: datetime | None = None
    date_precision: Precision | None = None
    status: str | None = None
    content_type: str = "text/plain"
    text: str = ""
    date_mentions: list[DateMention] = Field(default_factory=list)
    results: list[LabResult] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    audit: list[AuditEntry] = Field(default_factory=list)


class Dropped(BaseModel):
    source: Source
    source_id: str
    reason: str


class Conflict(BaseModel):
    kind: str
    patient_ids: list[str]
    detail: str


class Merge(BaseModel):
    into: str
    merged: list[str]
    reason: str


class IngestionReport(BaseModel):
    generated_at: datetime
    raw_counts: dict[str, dict[str, int]]
    clean_counts: dict[str, int]
    fixes_by_rule: dict[str, int]
    flags_by_type: dict[str, int]
    merges: list[Merge]
    conflicts: list[Conflict]
    unlinked_documents: list[str]
    dropped: list[Dropped]


class CleanDataset(BaseModel):
    patients: list[Patient]
    encounters: list[Encounter]
    documents: list[Document]
    report: IngestionReport
