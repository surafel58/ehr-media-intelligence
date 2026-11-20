# EHR Media Intelligence Platform

Turns messy EHR exports into clean FHIR R4 data, writes AI clinical summaries, and lets clinicians search all records in plain language.

## Build steps

Built in the same order as the assessment. Each step is one commit.

| Step | What it does | Code |
|---|---|---|
| 1. Ingestion & cleaning | Reads JSON and CSV/text records, fixes data quality issues, logs every change | `src/ehr_platform/ingestion/` |
| 2. FHIR R4 mapping | Builds Patient, DocumentReference and DiagnosticReport resources into one Bundle per patient | `src/ehr_platform/fhir_mapping/` |
| 3. AI summaries | Summarizes each patient with Claude, cached by patient + record hash | `src/ehr_platform/summarization/` |
| 4. Semantic search | Embeds records and summaries, serves `POST /search` | `src/ehr_platform/search/`, `src/ehr_platform/api/` |
| 5. Frontend | Clinician search and summary UI | `frontend/` |

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # add your ANTHROPIC_API_KEY
uv run pytest
```

More setup steps are added as each build step lands.

> All data in this repo is synthetic. No real patient data is used.
