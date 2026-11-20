"""Step 4: semantic search over records and AI summaries."""

from .build import collect_records
from .documents import Record, chunk, records_from_bundle, summary_record
from .embedder import HashEmbedder, SentenceTransformerEmbedder
from .index import Hit, SearchIndex

__all__ = ["Hit", "HashEmbedder", "Record", "SearchIndex", "SentenceTransformerEmbedder", "chunk",
           "collect_records", "records_from_bundle", "summary_record"]
