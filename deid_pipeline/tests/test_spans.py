from deid.core.models import Detection
from deid.core.spans import merge_detections, replace_spans


def d(etype, start, end, score=0.5, source="test"):
    return Detection(etype, start, end, score, source)


def test_merge_collapses_overlaps_to_union():
    merged = merge_detections([d("PERSON", 0, 5), d("PERSON", 3, 10)])
    assert len(merged) == 1
    assert (merged[0].start, merged[0].end) == (0, 10)


def test_merge_keeps_disjoint_spans():
    merged = merge_detections([d("PERSON", 0, 5), d("PERSON", 6, 10)])
    assert len(merged) == 2


def test_merge_representative_prefers_higher_score():
    merged = merge_detections([d("PERSON", 0, 5, 0.6), d("EMAIL_ADDRESS", 2, 8, 0.75)])
    assert merged[0].entity_type == "EMAIL_ADDRESS"


def test_merge_representative_breaks_score_ties_by_priority():
    # DATE_TIME (priority 5) beats PERSON (priority 3) at equal score
    merged = merge_detections([d("PERSON", 0, 5, 0.6), d("DATE_TIME", 2, 8, 0.6)])
    assert merged[0].entity_type == "DATE_TIME"


def test_merge_empty():
    assert merge_detections([]) == []


def test_replace_spans_back_to_front_keeps_offsets():
    text = "aaa bbb ccc"
    dets = [d("X", 0, 3), d("Y", 8, 11)]
    out = replace_spans(text, dets, lambda det, orig: "<" + det.entity_type + ">")
    assert out == "<X> bbb <Y>"
