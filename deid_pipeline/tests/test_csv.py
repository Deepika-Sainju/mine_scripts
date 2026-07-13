import csv
import logging

from deid.documents.csv_file import deidentify_csv
from deid.surrogate.generator import SurrogateGenerator


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_member_csv_surrogates_phi_and_keeps_the_rest(fixtures, tmp_path):
    out = tmp_path / "member.csv"
    res = deidentify_csv(str(fixtures / "Member/synthetic_member.csv"), str(out))
    assert res["type"] == "member"  # auto-detected
    assert set(res["surrogated_columns"]) == {
        "InputMemberID", "MemberDOB", "MemberFirstName",
        "MemberLastName", "MemberMiddleName",
    }
    orig = read_rows(fixtures / "Member/synthetic_member.csv")
    deid = read_rows(out)
    for o, d in zip(orig, deid):
        assert d["MemberFirstName"] != o["MemberFirstName"]
        assert d["InputMemberID"] != o["InputMemberID"]
        # kept columns are untouched
        for col in ("MemberGender", "MemberMetalLevel", "EnrollmentEffDate", "FilePath"):
            assert d[col] == o[col]


def test_claims_csv_keeps_provider_and_service_columns(fixtures, tmp_path):
    out = tmp_path / "claims.csv"
    res = deidentify_csv(str(fixtures / "Chart/synthetic_claims.csv"), str(out))
    assert res["type"] == "claims"
    orig = read_rows(fixtures / "Chart/synthetic_claims.csv")
    deid = read_rows(out)
    for o, d in zip(orig, deid):
        assert d["mbi"] != o["mbi"]  # Medicare id surrogated
        for col in ("rendering_providerid", "rendering_providerlastname",
                    "servicestartdate", "code"):
            assert d[col] == o[col]


def test_member_and_claims_files_still_join(fixtures, tmp_path):
    deidentify_csv(str(fixtures / "Member/synthetic_member.csv"),
                   str(tmp_path / "m.csv"), seed=1234)
    deidentify_csv(str(fixtures / "Chart/synthetic_claims.csv"),
                   str(tmp_path / "c.csv"), seed=1234)
    member = {r["InputMemberID"]: r for r in read_rows(tmp_path / "m.csv")}
    for claim in read_rows(tmp_path / "c.csv"):
        assert claim["memberid"] in member  # fake ids still join
        m = member[claim["memberid"]]
        # and the fake names agree across the two files
        assert claim["MemberFirstName"] == m["MemberFirstName"]
        assert claim["MemberLastName"] == m["MemberLastName"]


def _write_claims_without_gender(path):
    """A claims schema with NO gender column (the consistency-risk case)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "claimno", "memberid", "MemberFirstName", "MemberLastName", "dateofbirth"])
        w.writeheader()
        w.writerow({"claimno": "C900001", "memberid": "H900000001",
                    "MemberFirstName": "Marcus", "MemberLastName": "Delgado",
                    "dateofbirth": "03/14/1958"})


def test_gender_lookup_keeps_first_names_consistent_without_gender_column(tmp_path):
    src = tmp_path / "claims_nogender.csv"
    _write_claims_without_gender(src)
    out = tmp_path / "out.csv"
    deidentify_csv(str(src), str(out), csv_type="claims", seed=1234,
                   gender_lookup={"H900000001": "M"})
    row = read_rows(out)[0]
    # must match the member file's gender-matched fake, not a neutral one
    assert row["MemberFirstName"] == SurrogateGenerator(seed=1234).name_part(
        "Marcus", "first", "M")


def test_missing_gender_warns_and_falls_back_to_neutral(tmp_path, caplog):
    src = tmp_path / "claims_nogender.csv"
    _write_claims_without_gender(src)
    out = tmp_path / "out.csv"
    with caplog.at_level(logging.WARNING):
        deidentify_csv(str(src), str(out), csv_type="claims", seed=1234)
    assert any("gender" in r.message.lower() for r in caplog.records)
    row = read_rows(out)[0]
    assert row["MemberFirstName"] == SurrogateGenerator(seed=1234).name_part(
        "Marcus", "first", "")
