"""Parse the member CSV (the manifest + PHI source) into MemberPHI rows."""
from __future__ import annotations

import csv
from typing import List, Tuple

from deid.core.models import MemberPHI

# MemberPHI field -> member CSV header (matched case-insensitively)
_PHI_COLUMNS = {
    "first": "memberfirstname",
    "middle": "membermiddlename",
    "last": "memberlastname",
    "dob": "memberdob",
    "member_id": "inputmemberid",
    "gender": "membergender",
}
# mislabeled column that holds the chart filename
_CHART_COLUMN = "membermetallevel"


def parse_member_rows(csv_path: str) -> List[Tuple[MemberPHI, str]]:
    """(MemberPHI, chart_filename) per member row. Parses strictly by header."""
    out = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        lut = {(h or "").strip().lower(): h for h in (reader.fieldnames or [])}
        for row in reader:
            def g(key):
                col = lut.get(key)
                return (row.get(col) or "").strip() if col else ""
            phi = MemberPHI(**{field: g(col) for field, col in _PHI_COLUMNS.items()})
            out.append((phi, g(_CHART_COLUMN)))
    return out
