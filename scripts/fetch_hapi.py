"""Download a snapshot of test data from the public HAPI FHIR R4 server.

The server is reset from time to time, so the snapshot is saved to
data/raw/hapi_export.json and committed. Records are saved as they are on the
server, except that fields not needed by the pipeline (address, phone, SSN and
similar) are removed before saving.

Usage:
  uv run python scripts/fetch_hapi.py              download a new snapshot
  uv run python scripts/fetch_hapi.py --rescreen   re-apply the field screen to the saved snapshot
  uv run python scripts/fetch_hapi.py --add-orphans   add unlinked notes to the saved snapshot
"""

from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://hapi.fhir.org/baseR4"
OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "hapi_export.json"

NOTE_PAGES = 10            # pages of recent notes to scan (50 per page)
NOTE_PATIENTS = 16         # patients picked for their clinical notes
NOTES_PER_PATIENT = 10
RICH_NOTE_WORDS = 80
ORPHAN_NOTES = 6
ORPHAN_MIN_WORDS = 30
DUPLICATE_GROUPS = 6       # same name + birth date groups from a common surname
RECORDS_PER_GROUP = 4
NO_DOB_RECORDS = 3
LAB_PATIENTS = 6
REPORTS_PER_LAB_PATIENT = 3

KEEP = {
    "Patient": {"resourceType", "id", "identifier", "name", "gender", "birthDate", "deceasedBoolean", "deceasedDateTime"},
    "DocumentReference": {"resourceType", "id", "status", "docStatus", "type", "category", "subject", "date", "description", "content", "context"},
    "DiagnosticReport": {"resourceType", "id", "status", "category", "code", "subject", "encounter", "effectiveDateTime", "effectivePeriod", "issued", "result", "conclusion", "presentedForm"},
    "Observation": {"resourceType", "id", "status", "category", "code", "subject", "encounter", "effectiveDateTime", "issued", "valueQuantity", "valueString", "valueCodeableConcept", "interpretation", "referenceRange"},
    "Encounter": {"resourceType", "id", "status", "class", "type", "subject", "period", "reasonCode"},
}
DROP_ID_SYSTEMS = ("us-ssn", "ssn", "driver", "passport", "license")


def get(path: str, tries: int = 5) -> dict:
    url = path if path.startswith("http") else f"{BASE}/{path}"
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/fhir+json"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # network errors on a busy public server
            print(f"  retry {attempt}/{tries} {url[:100]}: {type(exc).__name__}", flush=True)
            time.sleep(2 * attempt)
    raise RuntimeError(f"failed: {url}")


def entries(bundle: dict) -> list[dict]:
    return [e["resource"] for e in bundle.get("entry", []) if "resource" in e]


REDACT = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED]"),                          # SSN
    (re.compile(r"(Medicare MBI:?\s*)[A-Z0-9-]{8,}", re.I), r"\1[REDACTED]"),       # Medicare ID
]


def redact_attachment(att: dict) -> dict:
    """Mask SSN and Medicare numbers inside base64 text attachments."""
    if not att.get("data") or not (att.get("contentType") or "").startswith("text/"):
        return att
    try:
        text = base64.b64decode(att["data"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return att
    for pattern, repl in REDACT:
        text = pattern.sub(repl, text)
    return {**att, "data": base64.b64encode(text.encode("utf-8")).decode("ascii")}


def screen(resource: dict) -> dict:
    """Keep only the fields the pipeline uses. Drop SSN-like identifiers and mask them in text."""
    keep = KEEP.get(resource["resourceType"], set(resource))
    out = {k: v for k, v in resource.items() if k in keep}
    if "identifier" in out:
        out["identifier"] = [
            i for i in out["identifier"]
            if not any(s in (i.get("system") or "").lower() for s in DROP_ID_SYSTEMS)
        ]
    if resource["resourceType"] == "DocumentReference":
        if "context" in out:
            out["context"] = {k: v for k, v in out["context"].items() if k in ("encounter", "period")}
        out["content"] = [{**c, "attachment": redact_attachment(c.get("attachment", {}))} for c in out.get("content", [])]
    if resource["resourceType"] == "DiagnosticReport" and "presentedForm" in out:
        out["presentedForm"] = [redact_attachment(a) for a in out["presentedForm"]]
    return out


def note_words(doc: dict) -> int:
    for content in doc.get("content", []):
        att = content.get("attachment", {})
        if att.get("data") and (att.get("contentType") or "").startswith("text/"):
            try:
                return len(base64.b64decode(att["data"]).decode("utf-8", "replace").split())
            except ValueError:
                return 0
    return 0


def fetch_orphans() -> list[dict]:
    """Notes that are not linked to any patient."""
    print("Fetching notes with no patient...")
    found = []
    for doc in entries(get("DocumentReference?subject:missing=true&_count=100&_sort=-_lastUpdated")):
        if note_words(doc) >= ORPHAN_MIN_WORDS:
            found.append(doc)
        if len(found) == ORPHAN_NOTES:
            break
    return found


def main() -> None:
    collected: dict[str, dict] = {}

    def add(resource: dict) -> None:
        collected[f"{resource['resourceType']}/{resource['id']}"] = screen(resource)

    print("Scanning recent notes...")
    notes, url = [], "DocumentReference?_count=50&_sort=-_lastUpdated"
    for _ in range(NOTE_PAGES):
        bundle = get(url)
        notes += entries(bundle)
        nxt = [link["url"] for link in bundle.get("link", []) if link["relation"] == "next"]
        if not nxt:
            break
        url = nxt[0]

    by_patient: dict[str, list[dict]] = defaultdict(list)
    for doc in notes:
        ref = (doc.get("subject") or {}).get("reference")
        if ref and ref.startswith("Patient/"):
            by_patient[ref].append(doc)

    rich = {ref: docs for ref, docs in by_patient.items()
            if any(note_words(d) >= RICH_NOTE_WORDS for d in docs)}
    picked = sorted(rich, key=lambda r: -sum(note_words(d) >= RICH_NOTE_WORDS for d in rich[r]))[:NOTE_PATIENTS]
    print(f"Notes scanned: {len(notes)}. Patients with clinical notes: {len(rich)}. Picked: {len(picked)}.")

    encounter_refs = set()
    for ref in picked:
        add(get(ref))
        for doc in rich[ref][:NOTES_PER_PATIENT]:
            add(doc)
            for enc in (doc.get("context") or {}).get("encounter", []):
                if enc.get("reference", "").startswith("Encounter/"):
                    encounter_refs.add(enc["reference"])
    for doc in fetch_orphans():
        add(doc)

    print(f"Fetching {len(encounter_refs)} encounters...")
    for ref in sorted(encounter_refs):
        try:
            add(get(ref))
        except RuntimeError as exc:
            print(f"  skipped {ref}: {exc}")

    print("Fetching duplicate patient groups...")
    groups: dict[tuple, list[dict]] = defaultdict(list)
    no_dob = []
    for p in entries(get("Patient?family=Smith&_count=200")):
        name = (p.get("name") or [{}])[0]
        key = ((name.get("family") or "").strip().lower(),
               " ".join(name.get("given") or []).strip().lower(),
               p.get("birthDate"))
        if key[2]:
            groups[key].append(p)
        elif key[1] and len(no_dob) < NO_DOB_RECORDS:
            no_dob.append(p)
    dup_groups = [g for g in groups.values() if len(g) > 1][:DUPLICATE_GROUPS]
    for group in dup_groups:
        for p in group[:RECORDS_PER_GROUP]:
            add(p)
    for p in no_dob:
        add(p)

    print("Fetching lab report patients...")
    lab_subjects = []
    for rep in entries(get("DiagnosticReport?_count=100&_sort=-_lastUpdated")):
        ref = (rep.get("subject") or {}).get("reference", "")
        if ref.startswith("Patient/") and ref not in lab_subjects:
            lab_subjects.append(ref)
    for ref in lab_subjects[:LAB_PATIENTS]:
        try:
            add(get(ref))
        except RuntimeError as exc:
            print(f"  skipped {ref}: {exc}")
            continue
        reports = entries(get(f"DiagnosticReport?subject={ref}&_count={REPORTS_PER_LAB_PATIENT}&_sort=-_lastUpdated"))
        obs_ids = []
        for rep in reports:
            add(rep)
            obs_ids += [r["reference"].split("/")[-1] for r in rep.get("result", [])
                        if r.get("reference", "").startswith("Observation/")]
        for i in range(0, len(obs_ids), 20):
            for obs in entries(get(f"Observation?_id={','.join(obs_ids[i:i + 20])}&_count=20")):
                add(obs)

    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entry": [{"fullUrl": f"{BASE}/{key}", "resource": res} for key, res in collected.items()],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(bundle, indent=1, ensure_ascii=False), encoding="utf-8")
    counts = defaultdict(int)
    for res in collected.values():
        counts[res["resourceType"]] += 1
    print(f"Saved {len(collected)} resources to {OUT}: {dict(counts)}")


def rescreen(add_orphans: bool = False) -> None:
    """Re-apply the screen to the saved snapshot. Optionally add unlinked notes to it."""
    bundle = json.loads(OUT.read_text(encoding="utf-8"))
    if add_orphans:
        have = {e["resource"]["id"] for e in bundle["entry"] if e["resource"]["resourceType"] == "DocumentReference"}
        new = [d for d in fetch_orphans() if d["id"] not in have]
        bundle["entry"] += [{"fullUrl": f"{BASE}/DocumentReference/{d['id']}", "resource": d} for d in new]
        print(f"Added {len(new)} notes with no patient")
    for entry in bundle["entry"]:
        entry["resource"] = screen(entry["resource"])
    OUT.write_text(json.dumps(bundle, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Re-screened {len(bundle['entry'])} resources in {OUT}")


if __name__ == "__main__":
    if "--add-orphans" in sys.argv:
        sys.exit(rescreen(add_orphans=True))
    sys.exit(rescreen() if "--rescreen" in sys.argv else main())
