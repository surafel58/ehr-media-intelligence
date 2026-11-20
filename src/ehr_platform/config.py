"""Project paths and settings."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
VAR_DIR = ROOT / "var"
CLEAN_DIR = VAR_DIR / "clean"
FHIR_DIR = VAR_DIR / "fhir"
SUMMARY_DIR = VAR_DIR / "summaries"
DB_PATH = VAR_DIR / "ehr.db"
SAVED_SUMMARIES = ROOT / "data" / "summaries.json"  # AI summaries shipped with the repo

HAPI_EXPORT = RAW_DIR / "hapi_export.json"
MTSAMPLES_CSV = RAW_DIR / "mtsamples.csv"


def load_env(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env into the environment (existing variables win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "claude")  # claude or gemini
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
