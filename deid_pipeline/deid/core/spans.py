"""Character-span operations shared by every detector and scrubber."""
from __future__ import annotations

from typing import Callable, List, Tuple

from deid.core.models import Detection, ENTITY_PRIORITY


def tighten(text: str, start: int, end: int) -> Tuple[int, int]:
    """Strip leading/trailing whitespace inside a span."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def merge_detections(dets: List[Detection]) -> List[Detection]:
    """Collapse overlapping detections into one span each.

    Each cluster of overlapping spans becomes a single Detection covering their
    union, typed/scored by the cluster's best member (see _representative).
    """
    if not dets:
        return []
    dets = sorted(dets, key=lambda d: (d.start, d.end))
    merged: List[Detection] = []
    cluster: List[Detection] = [dets[0]]
    cl_start, cl_end = dets[0].start, dets[0].end
    for d in dets[1:]:
        if d.start < cl_end:  # overlaps current cluster
            cluster.append(d)
            cl_end = max(cl_end, d.end)
        else:
            merged.append(_representative(cluster, cl_start, cl_end))
            cluster = [d]
            cl_start, cl_end = d.start, d.end
    merged.append(_representative(cluster, cl_start, cl_end))
    return merged


def _representative(cluster: List[Detection], start: int, end: int) -> Detection:
    best = max(
        cluster,
        key=lambda d: (d.score, ENTITY_PRIORITY.get(d.entity_type, 0), d.end - d.start),
    )
    return Detection(best.entity_type, start, end, best.score, best.source)


def replace_spans(text: str, dets: List[Detection],
                  replacement: Callable[[Detection, str], str]) -> str:
    """Replace each (non-overlapping) detected span, back to front so the
    remaining spans' offsets stay valid. `replacement(det, original)` supplies
    the substitute string."""
    for d in sorted(dets, key=lambda x: x.start, reverse=True):
        original = text[d.start:d.end]
        text = text[:d.start] + replacement(d, original) + text[d.end:]
    return text
