"""Summary formats: what Claude returns, and what is stored."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DISCLAIMER = ("AI-generated summary for information only. It is not a clinical decision and must be checked "
              "against the source records by a qualified clinician.")


class Finding(BaseModel):
    text: str = Field(description="Short phrase, e.g. 'Type 2 diabetes, poorly controlled'")
    source_ids: list[str] = Field(description="Ids of the records that support this finding")


class MediaRecord(BaseModel):
    source_id: str = Field(description="Id of the imaging or lab record")
    kind: Literal["imaging", "lab", "pathology"]
    date: str | None = Field(description="ISO date of the record, or null if not stated")
    result: str = Field(description="One-line result")


class Anomaly(BaseModel):
    kind: Literal["clinical", "data_quality"]
    text: str = Field(description="What is abnormal or contradictory")
    source_ids: list[str]


class ClinicalSummary(BaseModel):
    """The structured output requested from Claude."""

    overview: str = Field(description="Two or three sentences for a clinician skimming a search result")
    chief_concern: str = Field(description="Main reason for the most recent care, one short phrase")
    key_diagnoses: list[Finding] = Field(description="Documented diagnoses or problems, most important first, at most 5")
    recent_media: list[MediaRecord] = Field(description="Most recent imaging and lab records, at most 5")
    anomalies: list[Anomaly] = Field(description="Abnormal results and record contradictions, at most 5")
    confidence: Literal["high", "medium", "low"]
    confidence_reason: str = Field(description="One sentence")


class QualityCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 0


class PatientSummary(BaseModel):
    """What is cached and shown in the UI."""

    patient_id: str
    record_hash: str
    model: str
    prompt_version: str
    generated_at: datetime
    summary: ClinicalSummary
    disclaimer: str = DISCLAIMER
    word_count: int
    quality: list[QualityCheck] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)

    @property
    def quality_passed(self) -> bool:
        return all(c.passed for c in self.quality)
