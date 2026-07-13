"""Find the known member's PHI (and generic always-PHI identifiers) in free text.

Matching strategies, all returning Detection spans:
  * exact  — the member's name parts / member id / DOB (every textual variant)
  * fuzzy  — edit-distance name matches for OCR noise ('Hernandez' -> 'Hemandez')
  * labels — patient-only labels ('Patient:', line-leading 'Name:') that discover
             a patient name even when the CSV identity doesn't match the chart
  * generic — SSN/phone/email/address/ZIP/URL/IP/VIN/labeled-id/labeled-MRN regexes
"""
from __future__ import annotations

import re
from typing import List, Set

from deid.core.dates import MONTH_NAMES, parse_date
from deid.core.models import Detection, MemberPHI
from deid.core.text import WORD_RE, bounded_levenshtein, bounded_literal
from deid.detection.patterns import LABEL_ID_RE, PATTERNS, VALUE
from deid.surrogate.clinical_vocab import KEEP_WORDS

# Generic direct-identifier types we surrogate in free text — a guard so a
# future PATTERNS addition (e.g. dates, NPI) isn't silently unioned here.
# MRN is caught only when explicitly labeled (see MRN_RE) to stay precise.
GENERIC_TYPES = {
    "US_SSN", "PHONE_NUMBER", "EMAIL_ADDRESS", "URL", "IP_ADDRESS",
    "STREET_ADDRESS", "ZIP", "VIN",
}
MRN_RE = re.compile(r"\bMRN\s*#?\s*:?\s*(?P<value>[A-Z0-9][A-Z0-9\-]{4,})", re.I)

# identifier types that can wholly contain a name token (surrogate the whole thing
# as that identifier rather than letting the inner name collapse it)
CONTAINER_TYPES = {"EMAIL_ADDRESS", "URL", "IP_ADDRESS", "STREET_ADDRESS"}

# Patient-only name labels (NOT provider labels — providers are kept). The value
# after such a label is treated as a patient name even when it isn't in the CSV,
# so the PDF/OCR path still removes the patient when the manifest identity differs.
# The LABEL is case-insensitive ((?i:…) — OCR renders headers as "PATIENT NAME:")
# but the VALUE stays case-sensitive: requiring a capitalized name is what keeps
# prose like "the patient denied…" from being captured.
_PATIENT_LABELS = (r"Patient\s+Name|Patient|Member\s+Name|Subscriber|Insured|"
                   r"Beneficiary|Guarantor")
PATIENT_LABEL_RE = re.compile(rf"\b(?i:{_PATIENT_LABELS})\b\s*:?\s*(?P<value>{VALUE})")
# a bare, line-leading "Name:" is almost always the patient in a progress note
NAME_LINE_RE = re.compile(rf"(?m)^\s*(?i:Name)\s*:?\s*(?P<value>{VALUE})")
# honorifics / role words never kept as a discovered patient-name token
NAME_STOPWORDS = {"mr", "mrs", "ms", "dr", "md", "do", "rn", "np", "pa", "jr", "sr",
                  "ii", "iii", "iv", "the", "name", "patient", "member"}

# Labeled date-of-birth (NOT date-of-service) — the birth date after a DOB label is
# the patient's own DOB, the one date we're allowed to change, so surrogate it even
# when it doesn't match the member CSV's DOB. "DOS"/service dates are never matched.
_DATE_VALUE = (rf"(?P<value>\d{{1,2}}/\d{{1,2}}/\d{{2,4}}|\d{{4}}-\d{{2}}-\d{{2}}"
               rf"|(?:{MONTH_NAMES})\.?\s+\d{{1,2}},?\s+\d{{4}}"
               rf"|\d{{1,2}}\s+(?:{MONTH_NAMES})\.?\s+\d{{4}}|\d{{8}}(?![\d/]))")
DOB_LABEL_RE = re.compile(
    rf"\b(?:D\.?O\.?B\.?|Date\s+of\s+Birth|Birth\s*Date|Born)\b\s*:?\s*{_DATE_VALUE}", re.I)


def date_variants(value: str) -> List[str]:
    """Every plausible textual rendering of a date value (for matching in text).

    Parses the CSV DOB once, then emits it in the common chart formats
    (MM/DD/YYYY, M/D/YY, ISO, HL7, spelled-out) so an occurrence in any format
    is caught. Returns [] if the value doesn't parse as a date.
    """
    dt = parse_date(value)
    if dt is None:
        return []
    out = {
        dt.strftime("%m/%d/%Y"), dt.strftime("%m/%d/%y"),
        dt.strftime("%Y-%m-%d"), dt.strftime("%Y%m%d"),
        dt.strftime("%B %d, %Y"), dt.strftime("%b %d, %Y"),
        dt.strftime("%d %B %Y"), dt.strftime("%d %b %Y"),
        # non-zero-padded month/day (strftime %-m is platform-specific, so build them)
        f"{dt.month}/{dt.day}/{dt.year}", f"{dt.month}/{dt.day}/{str(dt.year)[2:]}",
    }
    return sorted(out, key=len, reverse=True)  # longest first so merge keeps the fullest


def _fuzzy_name_spans(text: str, phi: MemberPHI) -> List[Detection]:
    """Edit-distance name matches for OCR noise (e.g. 'Hernandez' -> 'Hemandez').

    Only for name parts of length >= 5; requires the same first letter and a small
    length delta, and skips clinical/structural words — so false positives stay rare.
    """
    targets = [(v.strip(), k) for v, k in
               ((phi.first, "FIRST"), (phi.middle, "MIDDLE"), (phi.last, "LAST"))
               if len((v or "").strip()) >= 5 and (v or "").strip().isalpha()]
    if not targets:
        return []
    out: List[Detection] = []
    for m in WORD_RE.finditer(text):
        tl = m.group(0).lower()
        if tl in KEEP_WORDS:
            continue
        for v, k in targets:
            vl = v.lower()
            if tl == vl or tl[0] != vl[0] or abs(len(tl) - len(vl)) > 2:
                continue
            thr = 1 if len(vl) < 8 else 2
            if bounded_levenshtein(tl, vl, thr) <= thr:
                out.append(Detection(f"MEMBER_{k}", m.start(), m.end(), 0.9, "fuzzy"))
                break
    return out


def label_name_tokens(text: str) -> Set[str]:
    """Patient-name tokens discovered from patient-only labels (`Patient:`, `Name:`
    at line start, `Subscriber:`, …). Used to catch the patient even when the CSV
    identity doesn't match the chart. Provider labels are intentionally excluded."""
    toks = set()
    for rx in (PATIENT_LABEL_RE, NAME_LINE_RE):
        for m in rx.finditer(text):
            for w in WORD_RE.findall(m.group("value")):
                if len(w) >= 2 and w.lower() not in NAME_STOPWORDS and w.lower() not in KEEP_WORDS:
                    toks.add(w)
    return toks


def member_spans(text: str, phi: MemberPHI, fuzzy: bool = False) -> List[Detection]:
    """Spans for the known member's PHI in `text` (high score so they win merges)."""
    dets: List[Detection] = []

    def _add(pattern: str, etype: str, flags=re.I):
        for m in re.finditer(pattern, text, flags):
            dets.append(Detection(etype, m.start(), m.end(), 0.99, "member"))

    for kind, value in (("FIRST", phi.first), ("MIDDLE", phi.middle), ("LAST", phi.last)):
        v = (value or "").strip()
        # skip initials / single chars — matching a bare 'Q' in narrative is noise
        if len(v.rstrip(".")) >= 2 and any(c.isalpha() for c in v):
            _add(bounded_literal(v), f"MEMBER_{kind}")

    mid = (phi.member_id or "").strip()
    if len(mid) >= 4:
        _add(bounded_literal(mid), "MEMBER_ID")

    for variant in date_variants(phi.dob):
        _add(bounded_literal(variant), "MEMBER_DOB", flags=re.I)

    if fuzzy:
        dets += _fuzzy_name_spans(text, phi)
    return dets


def generic_spans(text: str) -> List[Detection]:
    """Generic always-PHI identifiers via the regex layer (no dates, no NPI/MRN#)."""
    out: List[Detection] = []
    for etype, rx, score in PATTERNS:
        if etype not in GENERIC_TYPES:
            continue
        for m in rx.finditer(text):
            out.append(Detection(etype, m.start(), m.end(), score, "pattern"))
    for m in LABEL_ID_RE.finditer(text):
        out.append(Detection("ID_NUMBER", m.start("value"), m.end("value"), 0.55, "label"))
    for m in MRN_RE.finditer(text):
        out.append(Detection("MRN", m.start("value"), m.end("value"), 0.6, "label"))
    return out
