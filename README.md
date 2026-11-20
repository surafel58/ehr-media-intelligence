# EHR Media Intelligence Platform

Cleans raw EHR exports, maps them to FHIR R4, writes AI summaries per patient, and makes every record searchable in plain language.

## Progress

| Step | Status | Code |
|---|---|---|
| 1. Ingestion and cleaning | Done | `src/ehr_platform/ingestion/` |
| 2. FHIR R4 mapping | Not started | `src/ehr_platform/fhir_mapping/` |
| 3. AI summaries | Not started | `src/ehr_platform/summarization/` |
| 4. Semantic search | Not started | `src/ehr_platform/search/` |
| 5. Frontend | Not started | `frontend/` |

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). Plain pip also works with `pip install -r requirements.txt`.

```bash
uv sync
cp .env.example .env                      # add ANTHROPIC_API_KEY
uv run python -m ehr_platform.ingestion   # step 1
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

71 tests in `tests/`: date formats, gender codes, names, MRNs, text repair, patient matching, document de-duplication, and a full run on a small hand-made dataset.
