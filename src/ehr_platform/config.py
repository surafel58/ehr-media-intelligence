"""Project paths."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
VAR_DIR = ROOT / "var"
CLEAN_DIR = VAR_DIR / "clean"
FHIR_DIR = VAR_DIR / "fhir"
DB_PATH = VAR_DIR / "ehr.db"

HAPI_EXPORT = RAW_DIR / "hapi_export.json"
MTSAMPLES_CSV = RAW_DIR / "mtsamples.csv"
