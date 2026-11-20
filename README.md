# EHR Media Intelligence Platform

Cleans raw EHR exports, maps them to FHIR R4, writes AI summaries per patient, and makes every record searchable in plain language.

## Progress

| Step | Status | Code |
|---|---|---|
| 1. Ingestion and cleaning | Done | `src/ehr_platform/ingestion/` |
| 2. FHIR R4 mapping | Done | `src/ehr_platform/fhir_mapping/` |
| 3. AI summaries | Done (sample run in progress, see below) | `src/ehr_platform/summarization/` |
| 4. Semantic search | Done | `src/ehr_platform/search/`, `src/ehr_platform/api/` |
| 5. Frontend | Not started | `frontend/` |

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). Plain pip also works with `pip install -r requirements.txt` (it pulls the CPU-only PyTorch build).

```bash
uv sync
cp .env.example .env                         # add ANTHROPIC_API_KEY (or GEMINI_API_KEY)
uv run python -m ehr_platform.ingestion      # step 1
uv run python -m ehr_platform.fhir_mapping   # step 2
uv run python -m ehr_platform.summarization  # step 3
uv run python -m ehr_platform.search         # step 4: build the index
uv run python -m ehr_platform.api            # step 4: API on http://127.0.0.1:8000/docs
uv run pytest
```

## Data

All data is synthetic or de-identified sample data. No real patients.

### Sources used

| Source | Format | Contents | Why |
|---|---|---|---|
| [HAPI FHIR public test server](https://hapi.fhir.org/) | FHIR JSON | Patients, clinical notes, visits, lab reports | Open server where many developers upload test data with no review, so the data is messy on its own. Notes are linked to patients. |
| [MTSamples](https://github.com/aneuraz/mtsamplesFR) | CSV | Transcribed reports: radiology, discharge, progress notes and more | 4,999 reports with detailed clinical text. Public domain (CC0). |

### Sources checked and not used

| Source | Reason |
|---|---|
| Oracle Health (Cerner) open sandbox | Strong duplicate and ID problems, but 0 of 29 sampled notes had real clinical text. Most were PDFs only, placeholder text or test junk. |
| [Synthetic OMOP dataset (Zenodo)](https://zenodo.org/records/23913660) | PostgreSQL dump with no notes. Covers only placeholder birth dates. |
| Synthea | Clean data. Nothing to clean. |
| MIMIC-IV | Real patient data. |

### Problems found in the raw data

| Problem | HAPI snapshot | MTSamples |
|---|---|---|
| Duplicates | 11 extra patient records in 6 groups. "Robert Smith, 1972-11-02" stored 3 times. | 2,609 duplicate copies in the full file (34 in our subset). The same report is filed under several specialties. |
| Conflicting IDs | "John Smith, 1980-01-15" stored under 4 different MRNs | None. Reports have no patient ID. |
| Missing fields | 21 of 42 patients without gender, 8 without birth date, 4 without a name | 33 empty reports in the full file (3 in our subset) |
| Mixed date formats | Times with offsets `-04:00` and `-05:00`. In note text: `2024-03-16`, `03/16/2024`, `Nov 9, 2026`, `12 Apr 2024` | `2/11/92`, `07/24/2008`, `January 16, 2008`, `7/2008` |
| Inconsistent values | MRNs like `MRN-100002`, `MRN100001`, `CLR-12345`. Names like `smith`, `SMITH`, `Abe604`. 120 lab results with codes but no names. | Stray spaces in every specialty value. Line breaks replaced by commas. |
| Wrong data | A note states age 92 for a patient whose birth date makes her 82 | |
| Not clinical | All notes with no patient link were CSV code lists | |

### How patients are handled

| Case | Rule |
|---|---|
| Same full name and birth date | Merge into one patient. Keep all old IDs. Flag if MRNs differ. |
| Same MRN, different name or birth date | Do not merge. Flag the conflict. |
| Same first and last name, middle name differs | Do not merge. Flag as possible duplicate. |
| Same name, no birth date | Do not merge. Flag as possible duplicate. |
| Note with no patient link | Kept out of patient records. Listed as unlinked in the report. |
| MTSamples report | One patient per report after removing duplicates. Age and sex taken from the text. Name and birth date left empty. |

## Step 1: Ingestion and cleaning

```bash
uv run python -m ehr_platform.ingestion
```

Reads `data/raw/` and writes `var/clean/`: `patients.json`, `encounters.json`, `documents.json` and `report.json`.

### Input

| File | Contents | Rebuild with |
|---|---|---|
| `data/raw/hapi_export.json` | Snapshot from 2026-11-20: 42 patients, 137 notes, 114 visits, 6 lab reports, 123 lab results | `scripts/fetch_hapi.py` |
| `data/raw/mtsamples.csv` | 70 rows: 33 reports from 6 specialties, every duplicate copy of them, and 3 empty rows | `scripts/fetch_mtsamples.py` |

The HAPI snapshot keeps only the fields the pipeline uses. Addresses, phone fields and SSN identifiers are removed. SSN and Medicare numbers inside note text are masked.

### Cleaning rules

| Problem | Rule |
|---|---|
| Date formats | 12 formats parsed to ISO 8601. Slash dates read as month/day unless the first number is over 12. Two digit years never land in the future. Partial dates keep their precision (`2008-07`). |
| Time zones | All times converted to UTC. Times with no offset are marked as assumed UTC. |
| Gender | `M`, `F`, `1`, `2`, `woman`, `gentleman` and similar mapped to `male`, `female`, `other`, `unknown`. Missing becomes `unknown`. |
| Names | Spaces trimmed, all caps and all lower case fixed, generator digits removed (`Abe604` to `Abe`). |
| MRNs | Best MRN picked from all identifiers, skipping UUIDs and SSNs. Prefix and separators removed (`MRN-100002` to `100002`). |
| Duplicate documents | Same text when case, spacing and punctuation are ignored. First copy kept. Ids and specialties of the other copies added to it. |
| Note text | Base64 decoded (twice when needed). HTML stripped. MTSamples line breaks restored. Missing titles taken from the first line. |
| Lab results | Missing names filled from LOINC codes. Units fixed (`mmol/l` to `mmol/L`). High or low worked out from the reference range when not given. |
| Note vs record | Name, birth date and age written in a note are checked against the linked patient. |
| Unusable records | Empty reports and non-note attachments are dropped with a reason. |

### Result

| | Raw | Clean |
|---|---|---|
| Patients | 42 HAPI records, 70 MTSamples rows | 64 (31 HAPI, 33 MTSamples) |
| Documents | 143 HAPI, 70 MTSamples | 172 (154 notes, 10 lab reports, 8 imaging reports) |
| Duplicate patients merged | | 11 records into 6 patients |
| Duplicate documents removed | | 34 |
| Dropped | | 7 (3 empty reports, 4 CSV code lists) |

### Flags for review

| Flag | Records | Meaning |
|---|---|---|
| `missing_gender` | 39 | No gender in the source |
| `missing_date` | 34 | No document date found |
| `identity_unknown` | 33 | MTSamples report with no name or MRN |
| `missing_mrn` | 21 | No identifier that looks like an MRN |
| `missing_birth_date` | 8 | |
| `possible_duplicate` | 5 | Same name, but birth date missing or middle name differs |
| `missing_name` | 4 | |
| `conflicting_mrn` | 2 | Merged records carried different MRNs |
| `note_age_mismatch` | 1 | Note says age 92, birth date gives 82 |

Every record has an `audit` list. Each entry holds the field, the rule applied, and the value before and after. `report.json` lists totals, merges, conflicts and dropped records.

### Tests

71 tests: date formats, gender codes, names, MRNs, text repair, patient matching, document de-duplication, and a full run on a small hand-made dataset.

## Step 2: FHIR R4 mapping

```bash
uv run python -m ehr_platform.fhir_mapping
```

Reads `var/clean/`. Writes one Bundle per patient to `var/fhir/bundles/` and `var/ehr.db`, and the validation report to `var/fhir/validation_report.md`.

### Mapping

| Clean record | FHIR resource | Details |
|---|---|---|
| Patient | `Patient` | MRN typed as `MR`, other IDs kept. Flags from step 1 kept as `meta.tag`. Age stated in a note kept as an extension. |
| Visit | `Encounter` | Each MTSamples report gets one derived visit, tagged `derived`. |
| Note | `DocumentReference` | LOINC type from the title: progress note, discharge summary, history and physical, consult, ED note. Text stored as an attachment. |
| Lab or imaging report | `DiagnosticReport` | Category `LAB` or `RAD`. Conclusion taken from the IMPRESSION, CONCLUSION or DIAGNOSIS section. |
| Lab result | `Observation` | LOINC code, value, unit, reference range, and `H` or `L` flag. |

Each Bundle is a `collection`. Entries use `urn:uuid` addresses, and every `subject`, `encounter` and `result` reference points to an entry in the same Bundle.

### Validation

| Check | How |
|---|---|
| Structure and data types | fhir.resources R4B models |
| Required codes | Gender and status values checked against the allowed codes. fhir.resources does not check these: it accepts gender `M`. |
| Required fields | `status`, `class`, `code`, `content` |
| References | Every reference must resolve to an entry in the Bundle |
| Warnings | Valid but incomplete: no date, name, identifier or birth date |

### Result

| Bundles | Valid | Resources | Errors | Warnings |
|---|---|---|---|---|
| 64 | 64 | 506 | 0 | 188 |

| Resource | Count |
|---|---|
| Patient | 64 |
| Encounter | 147 (114 from HAPI, 33 derived) |
| DocumentReference | 154 |
| DiagnosticReport | 18 |
| Observation | 123 |

The raw HAPI snapshot also passes the same checks: 422 resources, 0 errors. Its problems are in the content (duplicates, conflicting IDs, missing values), not the structure, so cleaning has to run before mapping.

Most warnings come from MTSamples patients, which have no name, identifier or birth date. MTSamples has no status field, so its notes are set to `current` and its reports to `unknown`. The report lists each of these 33 defaults.

### FHIR version

fhir.resources 8, the release built on Pydantic v2, ships R5 and R4B models but not R4. Bundles are validated with the R4B models. R4B changed subscriptions, medication definitions and evidence resources. The five resources used here are the same in R4 and R4B.

### Storage

SQLite at `var/ehr.db`:

| Table | Holds |
|---|---|
| `bundles` | One row per patient: Bundle JSON, valid flag, error and warning counts |
| `resources` | Every resource by type, id and patient, for direct lookups |

### Tests

15 tests: resource mapping, references, status defaults, lab results, derived visits, the validator catching bad codes, missing fields, broken references, bad dates and duplicate entries, and the SQLite store.

## Step 3: AI summaries

```bash
uv run python -m ehr_platform.summarization                       # Claude (default)
uv run python -m ehr_platform.summarization --provider gemini     # Gemini
uv run python -m ehr_platform.summarization --dry-run             # what would be sent, rough cost
uv run python -m ehr_platform.summarization --cached-only         # saved summaries only, no API calls
```

Reads the Bundles from `var/ehr.db`. Writes one summary per patient to `var/summaries/` and the SQLite cache, plus `var/summaries/quality_report.md`.

### Summary contents

| Field | Content |
|---|---|
| `overview` | Two or three sentences for a search result |
| `chief_concern` | Main reason for the most recent care |
| `key_diagnoses` | Up to 5, each with the ids of the records that support it |
| `recent_media` | Up to 5 recent imaging, lab and pathology records, one line each |
| `anomalies` | Up to 5 abnormal results or record contradictions, marked `clinical` or `data_quality` |
| `confidence` | `high`, `medium` or `low`, with a one-sentence reason |
| `disclaimer` | Fixed text added by code, not by the model: AI-generated, not a clinical decision |

### How the prompt keeps summaries short and accurate

| Rule | How |
|---|---|
| Fixed format | Structured output: the model must return the schema above |
| Grounded | Only facts written in the records. Thin or non-clinical records are called out, not filled in. |
| Traceable | Every record in the prompt carries its id. Diagnoses, media and anomalies must cite ids. |
| Short | Target 180 words. A summary over 200 words is regenerated once with a request to shorten it. |
| Minimum data | Names and MRNs are not sent. Sex, birth date and the records are enough. |
| No wasted calls | Patients with no notes or reports get a fixed "no clinical records" summary without an API call. |

### Cache

SQLite table `summaries`, keyed by patient id, record hash, model and prompt version. The record hash covers every resource in the patient's Bundle, so a summary is only regenerated when a record changes or the prompt changes.

### Quality checks

Every summary is checked automatically. These checks are how summary quality was validated.

| Check | Fails when |
|---|---|
| `under_word_limit` | Over 200 words |
| `citations_exist` | A cited id is not a record of this patient |
| `findings_cited` | A diagnosis or clinical anomaly cites nothing |
| `numbers_grounded` | A number or date is not in the records (ages worked out from the birth date are allowed) |
| `abnormal_results_reported` | A lab report with a high or low value is missing from the anomalies |
| `data_quality_reported` | A data quality flag from step 1 is not mentioned |

### Providers

| Provider | Default model | Notes |
|---|---|---|
| Claude | `claude-opus-5-5` | Default. Server-side refusal fallback turned on. |
| Gemini | `gemini-3.5-flash` | Free tier: 20 requests per day per model. Rate limits are retried with a wait. |

The sample run uses Gemini because no paid Claude credit was available. Both providers use the same prompt, schema, cache and checks. The Gemini free tier may use prompts to improve Google products, which is fine for synthetic data but not for real patient data.

### Result so far

The free tier allows 20 summaries per day, so the sample run finishes over the next few days.

| Patients | AI summaries | No records (no call) | Pending |
|---|---|---|---|
| 64 | 15 | 9 | 40 |

| Check | Passed |
|---|---|
| All 6 checks | 15 of 15 summaries |
| Words | 53 to 120, median 102 |
| Confidence | 1 high, 12 medium, 2 low |

The summaries also caught problems step 1 missed, such as a patient with eight near-identical progress notes and a note dated 2026 for a 2024 care episode.

### Tests

21 tests with a fake model client, so no API key is needed: context building, cache hits and misses, the length retry, every quality check, refusals and blocked responses, and rate-limit retries.

## Step 4: Semantic search

```bash
uv run python -m ehr_platform.search   # build the index, print search timings
uv run python -m ehr_platform.api      # serve the API
```

```bash
curl -X POST "http://127.0.0.1:8000/search?resource_type=DiagnosticReport&date_from=2013-01-01&date_to=2013-12-31" \
  -H "Content-Type: application/json" -d '{"query": "abnormal blood glucose"}'
```

### Index

| Part | Choice |
|---|---|
| Embeddings | `all-MiniLM-L6-v2` (sentence-transformers), run locally on CPU. Record text never leaves the machine. |
| Vector store | Chroma, saved in `var/chroma`, cosine distance |
| What is indexed | Every note and report (text, conclusion, lab results as text) and every AI summary |
| Chunks | 150 words with a 30-word overlap, title in front. The model reads at most 256 word pieces. |
| Filter fields | Resource type and date stored on every chunk. Dates as `YYYYMMDD` numbers, since Chroma filters only compare numbers. |

### Ranking

| Rule | Why |
|---|---|
| Score = 75% cosine similarity + 25% keyword overlap | Embeddings miss exact clinical words. "Abnormal blood glucose" ranked a genetics panel above the high glucose result until keywords were added. |
| Best chunk per record | A long note matches on its most relevant part |
| Copies collapsed | Notes of one patient with the same matching text show once. The others are listed in `similar_records`. |
| At most 2 records per patient (`per_patient`) | One long chart cannot fill all 5 results |

### API

| Endpoint | Purpose |
|---|---|
| `POST /search` | Body `{"query": "..."}`. Query parameters: `resource_type` (`DocumentReference`, `DiagnosticReport`, `Patient` for AI summaries), `date_from`, `date_to`, `limit` (default 5), `per_patient` (default 2). Returns ranked records with score, snippet, patient name and MRN, and the patient's AI summary overview. |
| `GET /patients/{id}` | Patient details, full AI summary with disclaimer and quality checks, and every linked FHIR resource |
| `GET /resources/{type}/{id}` | One FHIR resource with its attachment text decoded |
| `GET /health` | Index size and model |

Interactive docs at `http://127.0.0.1:8000/docs`.

### Result

187 records (154 notes, 10 lab reports, 8 imaging reports, 15 AI summaries) indexed as 744 chunks.

| Measure | Median | Slowest |
|---|---|---|
| Search inside the index (30 queries) | 12 ms | 31 ms |
| Full API request over HTTP (20 queries) | 95 ms | 123 ms |

The model loads once at startup (about 6 seconds), not per request.

### Known limits

| Limit | Example |
|---|---|
| Negation | "Shortness of breath and chest pain" also matches an ER note that says the patient denies both |
| Near-duplicate notes | Notes with small differences are not collapsed, only exact matching text |

### Tests

17 tests: chunking, records, ranking, both filters, collapsing copies, the per-patient cap, a 50-record speed test, and every endpoint including bad input.
