from deid.core.models import MemberPHI
from deid.member.scrubber import MemberScrubber
from deid.surrogate.generator import SurrogateGenerator

PHI = MemberPHI(first="Marcus", middle="J", last="Delgado",
                dob="03/14/1958", member_id="H900000001", gender="M")


def scrubber() -> MemberScrubber:
    return MemberScrubber(PHI, seed=1234)


def test_scrub_removes_member_keeps_provider_and_dos():
    text = ("Marcus Delgado (DOB 03/14/1958, id H900000001) was seen by "
            "Dr. Frank Ferraro on 03/03/2025 for lumbar radiculopathy.")
    out, report = scrubber().scrub_text(text)
    for real in ("Marcus", "Delgado", "03/14/1958", "H900000001"):
        assert real not in out
    assert "Frank Ferraro" in out          # provider kept
    assert "03/03/2025" in out             # date of service kept
    assert "lumbar radiculopathy" in out   # clinical content kept


def test_scrub_uses_the_members_canonical_fakes():
    s = scrubber()
    out, _ = s.scrub_text("Marcus Delgado")
    assert out == f'{s.name_part("Marcus", "first")} {s.name_part("Delgado", "last")}'


def test_member_name_inside_email_does_not_collapse_it():
    out, report = scrubber().scrub_text("mail: marcus.delgado@example.com")
    assert "delgado" not in out.lower() and "marcus" not in out.lower()
    assert any(r["type"] == "EMAIL_ADDRESS" for r in report)
    assert "@" in out  # replaced by an email-shaped surrogate, not a bare name


def test_guard_is_armed_at_construction():
    # a fake can never equal the member's own real name — no set-up step to forget
    unguarded_fake = SurrogateGenerator(seed=1234).name_part("smith", "last")
    s = MemberScrubber(MemberPHI(first=unguarded_fake, last="smith"), seed=1234)
    assert s.name_part("smith", "last").lower() != unguarded_fake.lower()


def test_for_identity_rekeys_the_session():
    refined = scrubber().for_identity(MemberPHI(first="Priya", last="Nair"))
    assert refined.phi.last == "Nair"
    out, _ = refined.scrub_text("Priya Nair was seen.")
    assert "Priya" not in out and "Nair" not in out


def test_ocr_profile_discovers_labeled_names_across_pages():
    # the label pass runs in scrub_pages: a name on the header page is
    # scrubbed everywhere, even for an empty manifest identity
    pages = [(1, "Patient: Priya Nair\n"), (2, "Nair returns for follow-up.")]
    res = MemberScrubber(MemberPHI(), seed=1234).scrub_pages(pages)
    assert "Nair" not in res["markdown"] and "Priya" not in res["markdown"]
    assert "<!-- page 2 -->" in res["markdown"]


def test_dob_label_is_an_ocr_profile_behavior():
    text = "Date of Birth: 07/22/1971"   # NOT the member's DOB
    out_digital, _ = scrubber().scrub_text(text)
    assert "07/22/1971" in out_digital           # digital profile: kept
    res = scrubber().scrub_pages([(1, text)])
    assert "07/22/1971" not in res["markdown"]   # OCR profile: surrogated


def test_report_is_in_reading_order():
    text = "Marcus was here. Delgado signed."
    _, report = scrubber().scrub_text(text)
    positions = [text.find(r["original"]) for r in report]
    assert positions == sorted(positions)


def test_empty_text_passthrough():
    assert scrubber().scrub_text("") == ("", [])
