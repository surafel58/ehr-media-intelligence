"""Patient matching and document de-duplication.

Matching rules:
  exact full name + birth date      -> merge into one patient
  same MRN, different name or DOB   -> keep apart, flag "shared_mrn"
  same family + first name + DOB    -> keep apart, flag "possible_duplicate" (middle names differ)
  same name, birth date missing     -> keep apart, flag "possible_duplicate"
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .models import AuditEntry, Conflict, Document, Identifier, Merge, Patient


def _letters(text: str | None) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


def full_name_key(p: Patient) -> str:
    return "|".join([_letters(p.family_name), *(_letters(g) for g in p.given_names)])


def short_name_key(p: Patient) -> str:
    return f"{_letters(p.family_name)}|{_letters(p.given_names[0]) if p.given_names else ''}"


def _sort_id(p: Patient):
    sid = p.source_ids[0]
    return (0, int(sid), sid) if sid.isdigit() else (1, 0, sid)


def _completeness(p: Patient) -> int:
    return sum([p.birth_date is not None, p.gender != "unknown", p.mrn is not None, bool(p.given_names)])


def _flag(p: Patient, flag: str) -> None:
    if flag not in p.flags:
        p.flags.append(flag)


@dataclass
class MatchResult:
    patients: list[Patient]
    merges: list[Merge] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    id_map: dict[str, str] = field(default_factory=dict)


def _merge_group(group: list[Patient], result: MatchResult) -> Patient:
    group = sorted(group, key=lambda p: (-_completeness(p), _sort_id(p)))
    keep, rest = group[0], group[1:]
    for other in rest:
        keep.source_ids.extend(other.source_ids)
        keep.audit.append(AuditEntry(field="patient", rule="patient.merged", before=other.id, after=keep.id))
        keep.audit.extend(other.audit)
        result.id_map[other.id] = keep.id
    result.merges.append(Merge(into=keep.id, merged=[o.id for o in rest], reason="same full name and birth date"))

    # MRNs: most common one wins, the others are kept and flagged
    mrns = [(p.mrn.system, p.mrn.value) for p in group if p.mrn]
    if len(set(mrns)) > 1:
        top = Counter(mrns).most_common(1)[0][0]
        if keep.mrn is None or (keep.mrn.system, keep.mrn.value) != top:
            keep.audit.append(AuditEntry(field="mrn", rule="mrn.majority",
                                         before=keep.mrn.value if keep.mrn else None, after=top[1]))
            keep.mrn = Identifier(system=top[0], value=top[1])
        seen = {(i.system, i.value) for i in keep.other_identifiers}
        for system, value in sorted(set(mrns) - {top}, key=str):
            if (system, value) not in seen:
                keep.other_identifiers.append(Identifier(system=system, value=value))
        _flag(keep, "conflicting_mrn")
        result.conflicts.append(Conflict(kind="conflicting_mrn", patient_ids=[keep.id],
                                         detail=f"merged records carry {len(set(mrns))} different MRNs: "
                                                + ", ".join(sorted(v for _, v in set(mrns)))))
    elif keep.mrn is None and mrns:
        keep.mrn = Identifier(system=mrns[0][0], value=mrns[0][1])
        keep.audit.append(AuditEntry(field="mrn", rule="patient.filled_from_duplicate", after=mrns[0][1]))
    if keep.mrn is not None and "missing_mrn" in keep.flags:
        keep.flags.remove("missing_mrn")

    # Gender: fill unknown from duplicates, flag real disagreement
    known = [p.gender for p in group if p.gender != "unknown"]
    if len(set(known)) > 1:
        _flag(keep, "conflicting_gender")
        result.conflicts.append(Conflict(kind="conflicting_gender", patient_ids=[keep.id],
                                         detail="merged records disagree: " + ", ".join(sorted(set(known)))))
    if keep.gender == "unknown" and known:
        keep.gender = Counter(known).most_common(1)[0][0]
        keep.audit.append(AuditEntry(field="gender", rule="patient.filled_from_duplicate", after=keep.gender))
        if "missing_gender" in keep.flags:
            keep.flags.remove("missing_gender")

    seen_ids = {(i.system, i.value) for i in keep.other_identifiers}
    for other in rest:
        for ident in other.other_identifiers:
            if (ident.system, ident.value) not in seen_ids:
                keep.other_identifiers.append(ident)
                seen_ids.add((ident.system, ident.value))
    return keep


def match_patients(patients: list[Patient]) -> MatchResult:
    result = MatchResult(patients=[])

    exact: dict[tuple, list[Patient]] = defaultdict(list)
    singles: list[Patient] = []
    for p in patients:
        if p.birth_date and p.family_name and p.given_names:
            exact[(full_name_key(p), p.birth_date)].append(p)
        else:
            singles.append(p)
    for group in exact.values():
        result.patients.append(_merge_group(group, result) if len(group) > 1 else group[0])
    result.patients.extend(singles)
    result.patients.sort(key=_sort_id)

    # Same MRN on people who did not match
    by_mrn: dict[tuple, list[Patient]] = defaultdict(list)
    for p in result.patients:
        if p.mrn:
            by_mrn[(p.mrn.system, p.mrn.value)].append(p)
    for (_, value), group in by_mrn.items():
        if len(group) > 1:
            for p in group:
                _flag(p, "shared_mrn")
            result.conflicts.append(Conflict(kind="shared_mrn", patient_ids=[p.id for p in group],
                                             detail=f"MRN {value} is used by records with different names or birth dates"))

    # Close matches that were not merged
    by_short: dict[str, list[Patient]] = defaultdict(list)
    for p in result.patients:
        if p.family_name and p.given_names:
            by_short[short_name_key(p)].append(p)
    for group in by_short.values():
        if len(group) < 2:
            continue
        dobs = [p.birth_date for p in group]
        if any(d is None for d in dobs):
            suspects = group  # without a birth date any of them could be the same person
        else:
            suspects = [p for p in group if dobs.count(p.birth_date) > 1]
        if len(suspects) < 2:
            continue
        for p in suspects:
            _flag(p, "possible_duplicate")
        result.conflicts.append(Conflict(
            kind="possible_duplicate", patient_ids=[p.id for p in suspects],
            detail="same family and first name; middle name differs or birth date is missing"))
    return result


# ---------------------------------------------------------------- documents


def text_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def dedupe_documents(docs: list[Document], by_patient: bool = True) -> tuple[list[Document], int]:
    """Drop exact copies (same text, ignoring case, spacing and punctuation).

    The first copy is kept; ids, specialties and keywords of the others are added to it.
    """
    kept: dict[tuple, Document] = {}
    removed = 0
    for doc in docs:
        if doc.text:
            key = (doc.patient_id if by_patient else None, text_key(doc.text))
        else:
            key = (doc.patient_id, doc.kind, doc.title, doc.date,
                   tuple((r.name, r.value) for r in doc.results))
        first = kept.get(key)
        if first is None:
            kept[key] = doc
            continue
        removed += 1
        first.source_ids.extend(doc.source_ids)
        first.audit.append(AuditEntry(field="document", rule="document.duplicate_removed",
                                      before=doc.id, after=first.id))
        for spec in doc.specialties:
            if spec not in first.specialties:
                first.specialties.append(spec)
                first.audit.append(AuditEntry(field="specialties", rule="document.specialty_from_duplicate", after=spec))
        for kw in doc.keywords:
            if kw not in first.keywords:
                first.keywords.append(kw)
        if first.kind == "note" and doc.kind != "note":
            first.kind = doc.kind
    return list(kept.values()), removed
