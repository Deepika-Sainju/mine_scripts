from deid.core.models import MemberPHI
from deid.member.matching import (
    date_variants, generic_spans, label_name_tokens, member_spans,
)

PHI = MemberPHI(first="Marcus", middle="J", last="Delgado",
                dob="03/14/1958", member_id="H900000001", gender="M")


def types_at(dets, text):
    return {(d.entity_type, text[d.start:d.end]) for d in dets}


def test_member_spans_exact_matches():
    text = "Marcus Delgado, id H900000001, born 03/14/1958"
    found = types_at(member_spans(text, PHI), text)
    assert ("MEMBER_FIRST", "Marcus") in found
    assert ("MEMBER_LAST", "Delgado") in found
    assert ("MEMBER_ID", "H900000001") in found
    assert ("MEMBER_DOB", "03/14/1958") in found


def test_member_spans_skips_bare_initials():
    # middle initial "J" must not flag every J in the narrative
    dets = member_spans("J codes were billed. J1885.", PHI)
    assert not any(d.entity_type == "MEMBER_MIDDLE" for d in dets)


def test_member_spans_bounded_names():
    phi = MemberPHI(last="Ng")
    text = "Dr. Ngo saw Ng today"
    found = types_at(member_spans(text, phi), text)
    assert found == {("MEMBER_LAST", "Ng")}


def test_dob_matched_in_other_formats():
    for rendering in ("3/14/58", "March 14, 1958", "19580314"):
        dets = member_spans(f"DOB {rendering} noted", PHI)
        assert any(d.entity_type == "MEMBER_DOB" for d in dets), rendering


def test_date_variants_non_date():
    assert date_variants("unknown") == []


def test_fuzzy_catches_ocr_mangled_name():
    dets = member_spans("Patient Hemandez returns", MemberPHI(last="Hernandez"),
                        fuzzy=True)
    assert any(d.entity_type == "MEMBER_LAST" and d.source == "fuzzy" for d in dets)


def test_fuzzy_skips_clinical_words():
    # "lumbar" must not fuzzy-match a member named "Lumbar"-ish
    dets = member_spans("lumbar spine intact", MemberPHI(last="Lumber"), fuzzy=True)
    assert dets == []


def test_label_name_tokens_basic():
    assert label_name_tokens("Patient: John Smith\n") >= {"John", "Smith"}
    assert label_name_tokens("Name: Jane Doe\n") >= {"Jane", "Doe"}


def test_label_name_tokens_filters_stopwords():
    toks = label_name_tokens("Patient: Mr. Smith")
    assert "Smith" in toks and "Mr" not in toks


def test_label_name_tokens_all_caps_ocr_headers():
    # OCR commonly renders headers in ALL CAPS — the label must still match
    assert label_name_tokens("PATIENT NAME: SMITH, JOHN") >= {"SMITH", "JOHN"}
    assert label_name_tokens("NAME: JANE DOE") >= {"JANE", "DOE"}


def test_label_name_tokens_ignores_lowercase_prose():
    # a lowercase value after a prose "patient" is not a name capture
    assert label_name_tokens("The patient denied chest pain") == set()


def test_generic_spans_direct_identifiers():
    text = ("SSN 602-19-4471, fax (559) 555-0173, mail marcus@example.com, "
            "4821 Greenwood Avenue")
    types = {d.entity_type for d in generic_spans(text)}
    assert {"US_SSN", "PHONE_NUMBER", "EMAIL_ADDRESS", "STREET_ADDRESS"} <= types


def test_generic_spans_labeled_ids():
    text = "Policy Number: AB-12345 and MRN: CV-7741902"
    found = types_at(generic_spans(text), text)
    assert ("ID_NUMBER", "AB-12345") in found
    assert ("MRN", "CV-7741902") in found


def test_generic_spans_all_caps_labeled_id():
    # ALL-CAPS label (OCR) must still anchor the id capture
    text = "POLICY NUMBER: AB-12345"
    found = types_at(generic_spans(text), text)
    assert ("ID_NUMBER", "AB-12345") in found


def test_generic_spans_no_dates_no_npi():
    # dates and 10-digit NPIs are deliberately NOT generic identifiers
    dets = generic_spans("seen 03/03/2025, NPI 1023456789")
    assert not any(d.entity_type in ("DATE_TIME", "NPI") for d in dets)
