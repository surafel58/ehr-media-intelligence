"""FastAPI service: semantic search and patient detail.

Run: uv run python -m ehr_platform.api   (http://127.0.0.1:8000/docs)
"""

from __future__ import annotations

import base64
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import config
from ..fhir_mapping import FhirStore
from ..summarization import SummaryCache
from ..summarization.schema import DISCLAIMER, ClinicalSummary, QualityCheck
from ..search import SearchIndex

ResourceType = Literal["DocumentReference", "DiagnosticReport", "Patient"]
FRONTEND_DIST = config.ROOT / "frontend" / "dist"


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500, examples=["abnormal blood glucose"])


class PatientInfo(BaseModel):
    id: str
    name: str
    mrn: str | None = None
    gender: str | None = None
    birth_date: str | None = None
    age: int | None = None


class SearchResult(BaseModel):
    record_id: str
    resource_type: str
    kind: str
    title: str
    date: str | None
    score: float
    snippet: str
    patient: PatientInfo
    summary_snippet: str | None = None
    similar_records: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: str
    resource_type: str | None
    date_from: date | None
    date_to: date | None
    took_ms: float
    results: list[SearchResult]


class LinkedResource(BaseModel):
    resource_type: str
    id: str
    title: str
    date: str | None = None
    status: str | None = None


class SummaryOut(BaseModel):
    summary: ClinicalSummary
    disclaimer: str = DISCLAIMER
    model: str
    generated_at: datetime
    word_count: int
    quality: list[QualityCheck]


class PatientDetail(BaseModel):
    patient: PatientInfo
    flags: list[str]
    summary: SummaryOut | None
    resources: list[LinkedResource]


# ---------------------------------------------------------------- helpers


def patient_info(resource: dict | None, patient_id: str) -> PatientInfo:
    if not resource:
        return PatientInfo(id=patient_id, name="Unidentified patient")
    name = (resource.get("name") or [{}])[0]
    full = " ".join([*name.get("given", []), name.get("family", "")]).strip()
    mrn = next((i.get("value") for i in resource.get("identifier", [])
                if any(c.get("code") == "MR" for c in i.get("type", {}).get("coding", []))), None)
    age = next((e["valueAge"]["value"] for e in resource.get("extension", []) if "valueAge" in e), None)
    return PatientInfo(id=patient_id, name=full or "Unidentified patient", mrn=mrn,
                       gender=resource.get("gender"), birth_date=resource.get("birthDate"), age=age)


def _title(res: dict) -> str:
    if res["resourceType"] == "DocumentReference":
        return res.get("type", {}).get("text") or "Clinical note"
    if res["resourceType"] in ("DiagnosticReport", "Observation"):
        return res.get("code", {}).get("text") or res["resourceType"]
    if res["resourceType"] == "Encounter":
        return (res.get("type") or [{}])[0].get("text") or "Visit"
    return res["resourceType"]


def _date(res: dict) -> str | None:
    value = (res.get("date") or res.get("effectiveDateTime") or res.get("period", {}).get("start")
             or res.get("context", {}).get("period", {}).get("start"))
    return value[:10] if value else None


def _snippet(text: str, title: str, size: int = 280) -> str:
    body = text[len(title) + 2:] if text.startswith(f"{title}. ") else text
    return body if len(body) <= size else body[:size].rsplit(" ", 1)[0] + "..."


# ---------------------------------------------------------------- app


def create_app(index: SearchIndex | None = None, db_path: Path = config.DB_PATH) -> FastAPI:
    state: dict = {"index": index}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["index"] is None:  # load the model once, not per request
            from ..search import SentenceTransformerEmbedder
            state["index"] = SearchIndex(SentenceTransformerEmbedder())
        yield

    app = FastAPI(title="EHR Media Intelligence API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["GET", "POST"], allow_headers=["*"])

    @app.get("/health")
    def health() -> dict:
        idx: SearchIndex = state["index"]
        return {"status": "ok", "indexed_chunks": idx.count(), "embedding_model": idx.embedder.name}

    @app.post("/search", response_model=SearchResponse)
    def search(body: SearchRequest,
               resource_type: ResourceType | None = Query(None, description="FHIR resource type"),
               date_from: date | None = Query(None, description="YYYY-MM-DD, inclusive"),
               date_to: date | None = Query(None, description="YYYY-MM-DD, inclusive"),
               limit: int = Query(5, ge=1, le=20),
               per_patient: int = Query(2, ge=0, le=20, description="max results per patient, 0 = no cap")
               ) -> SearchResponse:
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=422, detail="date_from is after date_to")
        started = time.perf_counter()
        hits = state["index"].search(body.query, limit=limit, resource_type=resource_type,
                                     date_from=date_from, date_to=date_to, per_patient=per_patient)
        results = []
        with FhirStore(db_path) as store, SummaryCache(db_path) as cache:
            people: dict[str, PatientInfo] = {}
            overviews: dict[str, str | None] = {}
            for hit in hits:
                if hit.patient_id not in people:
                    people[hit.patient_id] = patient_info(store.resource("Patient", hit.patient_id), hit.patient_id)
                    summary = cache.latest(hit.patient_id)
                    overviews[hit.patient_id] = summary.summary.overview if summary else None
                results.append(SearchResult(
                    record_id=hit.record_id, resource_type=hit.resource_type, kind=hit.kind, title=hit.title,
                    date=hit.date, score=hit.score, snippet=_snippet(hit.snippet, hit.title),
                    patient=people[hit.patient_id], summary_snippet=overviews[hit.patient_id],
                    similar_records=hit.similar))
        took = round((time.perf_counter() - started) * 1000, 1)
        return SearchResponse(query=body.query, resource_type=resource_type, date_from=date_from, date_to=date_to,
                              took_ms=took, results=results)

    @app.get("/patients/{patient_id}", response_model=PatientDetail)
    def patient(patient_id: str) -> PatientDetail:
        with FhirStore(db_path) as store, SummaryCache(db_path) as cache:
            bundle = store.bundle(patient_id)
            if bundle is None:
                raise HTTPException(status_code=404, detail="patient not found")
            resources = [e["resource"] for e in bundle["entry"]]
            pres = next((r for r in resources if r["resourceType"] == "Patient"), None)
            summary = cache.latest(patient_id)
        linked = [LinkedResource(resource_type=r["resourceType"], id=r["id"], title=_title(r), date=_date(r),
                                 status=r.get("status"))
                  for r in resources if r["resourceType"] != "Patient"]
        linked.sort(key=lambda r: (r.date or "", r.resource_type), reverse=True)
        flags = [t["code"] for t in (pres or {}).get("meta", {}).get("tag", [])]
        out = None
        if summary:
            out = SummaryOut(summary=summary.summary, disclaimer=summary.disclaimer, model=summary.model,
                             generated_at=summary.generated_at, word_count=summary.word_count,
                             quality=summary.quality)
        return PatientDetail(patient=patient_info(pres, patient_id), flags=flags, summary=out, resources=linked)

    @app.get("/resources/{resource_type}/{resource_id}")
    def resource(resource_type: str, resource_id: str) -> dict:
        """The stored FHIR resource, with attachment text decoded for reading."""
        with FhirStore(db_path) as store:
            res = store.resource(resource_type, resource_id)
        if res is None:
            raise HTTPException(status_code=404, detail="resource not found")
        for att in [c.get("attachment", {}) for c in res.get("content", [])] + res.get("presentedForm", []):
            if att.get("data"):
                return {"resource": res, "text": base64.b64decode(att["data"]).decode("utf-8", "replace")}
        return {"resource": res, "text": None}

    if FRONTEND_DIST.exists():  # serve the built UI from the same port
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return app


app = create_app()
