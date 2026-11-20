"""Chroma vector index over record chunks, with filters on resource type and date."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import chromadb

from .. import config
from .documents import Record
from .embedder import Embedder

COLLECTION = "ehr_records"


SEMANTIC_WEIGHT = 0.75  # the rest is keyword overlap
STOPWORDS = {"a", "an", "and", "the", "of", "in", "on", "for", "with", "to", "or", "is", "are", "was", "were", "be",
             "by", "at", "as", "from", "this", "that", "patient", "patients", "any", "all", "who", "has", "have"}


def terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 1}


def keyword_score(query_terms: set[str], text: str) -> float:
    """Share of the query's words that appear in the text (plurals and simple suffixes included)."""
    if not query_terms:
        return 0.0
    words = terms(text)
    stems = {w[:5] for w in words}
    found = sum(1 for t in query_terms if t in words or (len(t) > 5 and t[:5] in stems))
    return found / len(query_terms)


def date_int(iso: str | None) -> int:
    """YYYY-MM-DD to YYYYMMDD. Chroma filters only compare numbers. Undated records get 0."""
    return int(iso.replace("-", "")) if iso else 0


@dataclass
class Hit:
    record_id: str
    patient_id: str
    resource_type: str
    kind: str
    title: str
    date: str | None
    score: float
    snippet: str
    similar: list[str] = field(default_factory=list)  # other records of this patient with the same matching text


class SearchIndex:
    def __init__(self, embedder: Embedder, path: Path = config.VAR_DIR / "chroma"):
        self.embedder = embedder
        self.client = chromadb.PersistentClient(path=str(path))
        self.collection = self.client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    def count(self) -> int:
        return self.collection.count()

    def rebuild(self, records: list[Record], batch: int = 256) -> int:
        """Replace the whole index with these records. Returns the number of chunks."""
        self.client.delete_collection(COLLECTION)
        self.collection = self.client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
        ids, texts, metas = [], [], []
        for r in records:
            for i, text in enumerate(r.chunks):
                ids.append(f"{r.record_id}#{i}")
                texts.append(text)
                metas.append({"record_id": r.record_id, "patient_id": r.patient_id,
                              "resource_type": r.resource_type, "kind": r.kind, "title": r.title,
                              "date": r.date or "", "date_int": date_int(r.date)})
        for start in range(0, len(ids), batch):
            end = start + batch
            self.collection.add(ids=ids[start:end], documents=texts[start:end], metadatas=metas[start:end],
                                embeddings=self.embedder.encode(texts[start:end]))
        return len(ids)

    def search(self, query: str, limit: int = 5, resource_type: str | None = None,
               date_from: date | None = None, date_to: date | None = None, per_patient: int = 2) -> list[Hit]:
        """Top `limit` records. Score = 75% cosine similarity + 25% keyword overlap, best chunk per record.

        At most `per_patient` records per patient (0 = no cap), so one long chart does not fill every slot.
        """
        conditions = []
        if resource_type:
            conditions.append({"resource_type": {"$eq": resource_type}})
        if date_from:
            conditions.append({"date_int": {"$gte": date_int(date_from.isoformat())}})
        if date_to:
            conditions.append({"date_int": {"$lte": date_int(date_to.isoformat())}})
        where = conditions[0] if len(conditions) == 1 else {"$and": conditions} if conditions else None

        total = self.collection.count()
        if total == 0:
            return []
        result = self.collection.query(query_embeddings=self.embedder.encode([query]),
                                       n_results=min(total, limit * 40), where=where,
                                       include=["documents", "metadatas", "distances"])
        best: dict[str, Hit] = {}
        query_terms = terms(query)
        for text, meta, dist in zip(result["documents"][0], result["metadatas"][0], result["distances"][0]):
            semantic = max(0.0, min(1.0, 1.0 - dist))
            score = round(SEMANTIC_WEIGHT * semantic + (1 - SEMANTIC_WEIGHT) * keyword_score(query_terms, text), 4)
            rid = meta["record_id"]
            if rid not in best or score > best[rid].score:
                best[rid] = Hit(record_id=rid, patient_id=meta["patient_id"], resource_type=meta["resource_type"],
                                kind=meta["kind"], title=meta["title"], date=meta["date"] or None, score=score,
                                snippet=text)
        # Copies of the same note (same patient, same matching text) show once, with the others listed as similar.
        ranked: list[Hit] = []
        seen: dict[tuple[str, str], Hit] = {}
        per: dict[str, int] = {}
        for hit in sorted(best.values(), key=lambda h: (-h.score, h.record_id)):
            key = (hit.patient_id, re.sub(r"\W+", "", hit.snippet.lower()))
            if key in seen:
                seen[key].similar.append(hit.record_id)
                continue
            seen[key] = hit
            if per_patient and per.get(hit.patient_id, 0) >= per_patient:
                continue
            per[hit.patient_id] = per.get(hit.patient_id, 0) + 1
            ranked.append(hit)
        return ranked[:limit]
