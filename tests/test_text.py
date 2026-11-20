import base64

from ehr_platform.ingestion.text import (
    decode_attachment,
    extract_age_sex,
    find_date_mentions,
    find_labeled_date,
    html_to_text,
    note_patient_header,
    repair_transcription,
)


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def test_repair_mtsamples_section_breaks():
    raw = ("SUBJECTIVE:,  This 23-year-old white female presents with allergies.,"
           "MEDICATIONS: , Her only medication is Allegra.,ASSESSMENT:,1.  Allergic rhinitis.,2.  Asthma.")
    fixed = repair_transcription(raw)
    assert fixed.splitlines() == [
        "SUBJECTIVE: This 23-year-old white female presents with allergies.",
        "MEDICATIONS: Her only medication is Allegra.",
        "ASSESSMENT: 1. Allergic rhinitis.",
        "2. Asthma.",
    ]


def test_repair_keeps_normal_commas():
    text = "Patient has cough, fever, and chills."
    assert repair_transcription(text) == text


def test_double_base64_attachment_is_decoded():
    html = "<html><body><p>Chest pain since Monday.</p></body></html>"
    text, rules = decode_attachment(b64(b64(html)))
    assert text == html
    assert rules == ["text.double_base64"]


def test_plain_base64_attachment():
    text, rules = decode_attachment(b64("Progress note, follow up in 2 weeks."))
    assert text.startswith("Progress note")
    assert rules == []


def test_html_to_text_drops_tags_and_styles():
    html = "<html><head><style>p{color:red}</style></head><body><h1>Pre-visit</h1><p>BP 140/90</p></body></html>"
    assert html_to_text(html) == "Pre-visit\n\nBP 140/90"


def test_age_and_sex_from_text():
    assert extract_age_sex("A 23-year-old white female presents with...")[:2] == (23, "female")
    assert extract_age_sex("This 45 year old gentleman was admitted")[:2] == (45, "male")
    assert extract_age_sex("Mrs. Jones returns for review.")[:2] == (None, "female")
    assert extract_age_sex("Chest x-ray, two views.")[:2] == (None, "unknown")


def test_date_mentions_are_normalized():
    text = "Seen 07/24/2008 and again January 16, 2008. Surgery 2/11/92. Last scan 7/2008. BP 120/80."
    isos = [m.iso for m in find_date_mentions(text)]
    assert isos == ["2008-07-24", "2008-01-16", "1992-02-11", "2008-07"]


def test_labeled_date():
    text = "DISCHARGE SUMMARY\nDATE OF ADMISSION: 03/04/2008\nHistory: seen 01/01/2001"
    assert find_labeled_date(text).iso == "2008-03-04"


def test_note_header_two_layouts():
    assert note_patient_header("Progress note, Sunrise\nPatient: Marcus Johnson (DOB 1971-11-30)\n") == (
        "Marcus Johnson", "1971-11-30")
    referral = "Patient Name:      Margaret Ellen Torvald\nDate of Birth:     07/22/1941\n"
    assert note_patient_header(referral) == ("Margaret Ellen Torvald", "1941-07-22")
