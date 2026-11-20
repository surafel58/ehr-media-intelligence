"""Names for LOINC codes that arrive without display text.

Covers the codes found in the sample data. A production system would call a
terminology server instead.
"""

LOINC = {
    "69548-6": "Genetic variant assessment",
    "58410-2": "Complete blood count panel",
    "6690-2": "White blood cell count",
    "15074-8": "Glucose in blood",
    "11555-0": "Base excess in blood",
    "2345-7": "Glucose in serum or plasma",
    "2093-3": "Cholesterol in serum or plasma",
    "718-7": "Hemoglobin in blood",
    "4548-4": "Hemoglobin A1c",
}

LOINC_ANSWERS = {
    "LA9633-4": "Present",
    "LA9634-2": "Absent",
    "LA11884-6": "Indeterminate",
    "LA18198-4": "No call",
}


def code_name(concept: dict | None) -> tuple[str | None, bool]:
    """Readable name for a CodeableConcept. Returns (name, looked_up)."""
    concept = concept or {}
    if concept.get("text"):
        return concept["text"].strip(), False
    for coding in concept.get("coding", []):
        if coding.get("display"):
            return coding["display"].strip(), False
    for coding in concept.get("coding", []):
        code = coding.get("code") or ""
        if code in LOINC:
            return LOINC[code], True
        if code in LOINC_ANSWERS:
            return LOINC_ANSWERS[code], True
    return None, False
