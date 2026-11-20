from datetime import datetime, timezone

import pytest

from ehr_platform.ingestion.normalize import (
    normalize_gender,
    normalize_mrn,
    normalize_name_part,
    normalize_unit,
    parse_date,
    parse_datetime,
    split_identifiers,
)


@pytest.mark.parametrize(
    ("raw", "iso"),
    [
        ("2024-03-16", "2024-03-16"),
        ("03/16/2024", "2024-03-16"),
        ("3/4/2020", "2020-03-04"),
        ("2/11/92", "1992-02-11"),          # two-digit year, last century
        ("1/1/30", "1930-01-01"),           # a 2030 birth date would be in the future
        ("January 16, 2008", "2008-01-16"),
        ("Nov 11, 2026", "2026-11-11"),
        ("1 October 2026", "2026-10-01"),
        ("16.03.2024", "2024-03-16"),       # European dotted, day first
        ("25/12/2020", "2020-12-25"),       # 25 cannot be a month, so day first
        ("20240316", "2024-03-16"),         # HL7 v2 style
        ("7/2008", "2008-07"),              # month precision kept
        ("1972", "1972"),                   # year precision kept
    ],
)
def test_parse_date_formats(raw, iso):
    assert parse_date(raw, ref_year=2026).iso == iso


@pytest.mark.parametrize("raw", ["13/45/2020", "2024-02-30", "not a date", "", None])
def test_parse_date_rejects_invalid(raw):
    assert parse_date(raw) is None


def test_parse_datetime_converts_offsets_to_utc():
    parsed = parse_datetime("2026-11-11T11:03:00-07:00")
    assert parsed.value == datetime(2026, 11, 11, 18, 3, tzinfo=timezone.utc)
    assert not parsed.assumed_utc


def test_parse_datetime_without_offset_is_marked_as_assumed():
    parsed = parse_datetime("2026-01-05T09:30:00.123456")
    assert parsed.assumed_utc
    assert parsed.value.tzinfo == timezone.utc


@pytest.mark.parametrize(
    ("raw", "gender"),
    [("M", "male"), ("f", "female"), ("Female", "female"), ("2", "female"), ("1", "male"),
     ("other", "other"), ("U", "unknown"), (None, "unknown"), ("garbage", "unknown")],
)
def test_gender_codes(raw, gender):
    assert normalize_gender(raw) == gender


@pytest.mark.parametrize(
    ("raw", "clean"),
    [("smith", "Smith"), ("FRED RICK", "Fred Rick"), ("o'brien", "O'Brien"), ("mary-jane", "Mary-Jane"),
     ("McDonald", "McDonald"), ("van de Heuvel", "van de Heuvel"), ("SMART JR", "Smart Jr"),
     ("SANDY II", "Sandy II"), ("Abe604", "Abe"), ("  Nora  ", "Nora"), ("", None)],
)
def test_name_normalization(raw, clean):
    assert normalize_name_part(raw) == clean


@pytest.mark.parametrize(
    ("raw", "clean"),
    [("MRN-100002", "100002"), (" mrn: 3001 ", "3001"), ("MRN100001", "100001"),
     ("JS-2026-0042", "JS20260042"), ("12345", "12345")],
)
def test_mrn_format(raw, clean):
    assert normalize_mrn(raw) == clean


def test_mrn_choice_skips_uuid_and_ssn():
    idents = [
        {"system": "urn:ietf:rfc:3986", "value": "urn:uuid:9f5d3612"},
        {"system": "http://hl7.org/fhir/sid/us-ssn", "value": "999-99-9999"},
        {"system": "http://hospital.example.org/mrn", "value": "MRN-77"},
    ]
    mrn, others = split_identifiers(idents)
    assert mrn.value == "MRN-77"
    assert len(others) == 2


def test_mrn_prefers_typed_identifier():
    idents = [
        {"system": "http://hospital.example.org/patients", "value": "555"},
        {"type": {"coding": [{"code": "MR"}]}, "system": "urn:x", "value": "A-1"},
    ]
    mrn, _ = split_identifiers(idents)
    assert mrn.value == "A-1"


def test_unit_case():
    assert normalize_unit("mmol/l") == "mmol/L"
    assert normalize_unit("cells/uL") == "cells/uL"
