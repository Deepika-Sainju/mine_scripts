"""Read a chart-mapping CSV for the direct-fetch de-id flow.

The mapping IS the manifest — no S3 listing. Each row says which chart to fetch
(`file_source`, a full s3:// URL), under which member identity (`str_member_id`,
joined to the member CSV's InputMemberID), and where to write the de-identified
result (`file_dest`, a path relative to the output dir, written verbatim).

Schema (header required):

    str_member_id,file_source,file_dest[,source_file]

* `source_file` is OPTIONAL — a logical label only; it's not used to locate or
  write charts (defaults to "" when the column is absent).
* A `.none` `file_source` is a 0-byte marker (no content to de-identify); the
  caller writes an empty file at `file_dest` and processes the group's other rows.
* Rows with the same `str_member_id` are de-identified under one identity/scrubber.
"""
from __future__ import annotations

import csv
from typing import List, NamedTuple

# source_file is optional (a logical label; not used to locate or write charts).
REQUIRED_COLUMNS = ["str_member_id", "file_source", "file_dest"]


class ChartMapRow(NamedTuple):
    member_id: str
    source_file: str
    file_source: str  # full s3:// URL to fetch
    file_dest: str    # output path, relative to the output dir

    @property
    def is_none_marker(self) -> bool:
        return self.file_source.lower().endswith(".none")


def parse_chart_map(path: str) -> List[ChartMapRow]:
    """Parse the mapping CSV into rows, skipping blank/incomplete lines.

    Raises ValueError if the required columns are missing.
    """
    rows: List[ChartMapRow] = []
    # utf-8-sig strips a BOM if the CSV was exported from Excel/Windows.
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"chart-map missing required column(s) {missing}; got {reader.fieldnames}")
        for r in reader:
            member_id = (r.get("str_member_id") or "").strip()
            source_file = (r.get("source_file") or "").strip()
            file_source = (r.get("file_source") or "").strip()
            file_dest = (r.get("file_dest") or "").strip()
            if not file_source and not file_dest and not member_id:
                continue  # blank trailing line
            if not file_source or not file_dest:
                continue  # incomplete row — nothing actionable
            rows.append(ChartMapRow(member_id, source_file, file_source, file_dest))
    return rows
