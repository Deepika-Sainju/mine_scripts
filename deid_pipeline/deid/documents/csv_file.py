"""De-identify member / claims CSV files (tabular, column-based).

Surrogates the member's PHI columns with realistic, consistent fakes and keeps
provider, service-date, and clinical columns. Referential integrity: the same
member/claim value maps to the same fake across the member and claims files
(content-derived surrogates in deid.surrogate), so de-identified files still join.

No OCR, no NER, no DB — CPU-only. Parses by header name (schema/order varies).

    member : surrogate first/middle/last name, DOB, InputMemberID
    claims : surrogate first/last name, dateofbirth, memberid, claimno
    kept   : provider names/ids, service dates, clinical codes, gender, metal, etc.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from deid.core.models import MemberPHI
from deid.core.reporting import mask_detections, write_json
from deid.member.scrubber import MemberScrubber

log = logging.getLogger(__name__)

# column holding the member id per CSV type (keys gender_lookup)
_ID_COLUMNS = {"member": "inputmemberid", "claims": "memberid"}

# column (lower-cased header) -> (action, name-kind). Anything not listed is KEPT.
#   action "name" -> gen.name_part(value, kind);  "dob" -> gen.fake_dob;  "id" -> gen.identifier
_MEMBER_RULES = {
    "memberfirstname":  ("name", "first"),
    "membermiddlename": ("name", "middle"),
    "memberlastname":   ("name", "last"),
    "memberdob":        ("dob", None),
    "inputmemberid":    ("id", None),
}
_CLAIMS_RULES = {
    "memberfirstname": ("name", "first"),
    "memberlastname":  ("name", "last"),
    "dateofbirth":     ("dob", None),
    "memberid":        ("id", None),
    "claimno":         ("id", None),
    "mbi":             ("id", None),  # Medicare Beneficiary Identifier — direct PHI
}


def _detect_type(headers) -> str:
    h = {c.strip().lower() for c in headers}
    if "claimno" in h or "rendering_providerid" in h:
        return "claims"
    if "inputmemberid" in h or "membermetallevel" in h:
        return "member"
    raise ValueError("cannot detect CSV type (no member/claims marker columns); "
                     "pass csv_type / --type explicitly")


def _apply(scrubber: MemberScrubber, action: str, kind, value: str) -> str:
    if action == "name":
        return scrubber.name_part(value, kind)
    if action == "dob":
        return scrubber.fake_dob(value)
    if action == "id":
        return scrubber.identifier(value)
    return value


def _row_phi(row, col_rule, id_col, gender: str) -> MemberPHI:
    """The member identity carried by one CSV row (arms the row's collision guard)."""
    phi = MemberPHI(gender=gender)
    for col, (action, kind) in col_rule.items():
        v = (row.get(col) or "").strip()
        if action == "name":
            setattr(phi, kind, v)
        elif action == "dob":
            phi.dob = v
    phi.member_id = (row.get(id_col) or "").strip() if id_col else ""
    return phi


def deidentify_csv(in_path, out_path, *, csv_type=None, seed=1234,
                   report_path=None, show_values=False, gender_lookup=None) -> dict:
    """De-identify one member/claims CSV.

    gender_lookup — optional {real member id: "M"/"F"} fallback (built from the
    member manifest) for files without a gender column. Fake first names are
    derived per (value, gender), so without a gender the fake won't match the
    member file's gender-matched fake — the lookup preserves that join.
    """
    with open(in_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)

    ctype = csv_type or _detect_type(headers)
    rules = _MEMBER_RULES if ctype == "member" else _CLAIMS_RULES

    # map each real header (any case) to its rule
    col_rule = {}
    for col in headers:
        r = rules.get(col.strip().lower())
        if r:
            col_rule[col] = r

    # gender column (for gender-matching fake first names): prefer MemberGender
    lut = {c.strip().lower(): c for c in headers}
    gender_col = lut.get("membergender") or lut.get("gender")
    id_col = lut.get(_ID_COLUMNS[ctype])
    surrogates_first_names = any(kind == "first" for _, kind in col_rule.values())

    report = []
    rows_missing_gender = 0
    for row in rows:
        gender = (row.get(gender_col) or "") if gender_col else ""
        if not gender.strip() and gender_lookup and id_col:
            gender = gender_lookup.get((row.get(id_col) or "").strip(), "")
        if surrogates_first_names and not gender.strip():
            rows_missing_gender += 1
        # one scrub session per row: the collision guard is armed with THIS
        # row's real tokens (fakes stay content-derived, so still consistent
        # with the same member's chart and other files)
        scrubber = MemberScrubber(_row_phi(row, col_rule, id_col, gender), seed=seed)
        for col, (action, kind) in col_rule.items():
            orig = row.get(col) or ""
            if not orig.strip():
                continue
            new = _apply(scrubber, action, kind, orig)
            if new != orig:
                report.append({"column": col, "original": orig, "surrogate": new})
                row[col] = new

    if rows_missing_gender:
        log.warning(
            "%s: %d row(s) have no gender (no gender column / lookup match) — their "
            "fake first names are gender-neutral and will NOT match a gender-matched "
            "fake for the same member in another file",
            Path(in_path).name, rows_missing_gender)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)

    if report_path:
        write_json(report_path, {
            "type": ctype, "rows": len(rows), "changed": len(report),
            "surrogated_columns": sorted(col_rule),
            "detections": mask_detections(report, show_values),
        })

    return {"type": ctype, "rows": len(rows), "changed": len(report),
            "surrogated_columns": sorted(col_rule), "report": report}
