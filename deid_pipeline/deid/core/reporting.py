"""Detection-report shaping: masking, per-source tallies, JSON writing.

Reports default to MASKED (`original`/`surrogate` replaced with "***") so real
PHI never lands in a report file unless the caller explicitly opts in with
`show_values=True`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List


def count_by_source(report: List[dict]) -> Dict[str, int]:
    """Tally detections by their `source` field (member/pattern/label/fuzzy/...)."""
    by_source: Dict[str, int] = {}
    for r in report:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    return by_source


def mask_detections(detections: List[dict], show_values: bool) -> List[dict]:
    """Mask real/surrogate values unless the caller explicitly asked for them."""
    if show_values:
        return detections
    return [{**r, "original": "***", "surrogate": "***"} for r in detections]


def write_json(path, payload: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_detection_report(path, detections: List[dict], by_source: Dict[str, int],
                           show_values: bool) -> None:
    """Write the standard per-document report: {detections, count, by_source}."""
    rep = mask_detections(detections, show_values)
    write_json(path, {"detections": rep, "count": len(rep), "by_source": by_source})
