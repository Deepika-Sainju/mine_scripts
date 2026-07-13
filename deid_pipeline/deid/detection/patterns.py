"""Direct-identifier regexes: fixed-format patterns + label-anchored captures.

Only identifiers that are PHI regardless of whose they are. Deliberately absent:
dates (all non-DOB dates are kept), NPI / bare-numeric MRN (would eat provider
NPIs and clinical codes), and claim numbers (ambiguous in narrative) — the
member scrubber keeps those unless they belong to the member.
"""
from __future__ import annotations

import re
from typing import List, Tuple

# value: an initial-capitalised name of up to 4 tokens (allowing middle initials)
VALUE = r"[A-Z][A-Za-z.'\-]+(?:,?\s+(?:[A-Z][A-Za-z.'\-]+|[A-Z]\.)){0,3}"

# label-driven identifier capture (license / vehicle / device / certificate /
# account / policy / health-plan numbers — no fixed format, so anchor on the label)
ID_LABELS = (
    r"License\s+Plate|Licen[cs]e|Serial\s+(?:No\.?|Number|#)|Serial|"
    r"Device\s+(?:ID|Identifier|Serial)|Certificate|Cert\.?|VIN|"
    r"Vehicle\s+(?:Identification\s+Number|ID)|Plate|Policy\s+(?:No\.?|Number)|Policy|"
    r"Account\s+(?:No\.?|Number)|Account|Member\s+(?:ID|No\.?|Number)|Beneficiary"
)
# value: an alphanumeric code of length >=4 that contains at least one digit
ID_VALUE = r"(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{3,}"

# label case-insensitive (OCR renders "POLICY NUMBER:"); the VALUE stays
# uppercase-only — that asymmetry is what keeps prose matches precise
LABEL_ID_RE = re.compile(rf"\b(?i:{ID_LABELS})\b\s*#?\s*:?\s*(?P<value>{ID_VALUE})")

# (entity_type, compiled_regex, score)
PATTERNS: List[Tuple[str, "re.Pattern", float]] = [
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), 0.70),
    ("PHONE_NUMBER", re.compile(r"(?:\(\d{3}\)\s*|\b\d{3}[-.\s])\d{3}[-.\s]\d{4}\b"), 0.55),
    ("EMAIL_ADDRESS", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), 0.75),
    # web URLs
    ("URL", re.compile(r"\bhttps?://[^\s<>\"')]+", re.I), 0.65),
    ("URL", re.compile(r"\bwww\.[A-Za-z0-9.\-]+\.[A-Za-z]{2,}[^\s<>\"')]*", re.I), 0.55),
    # IP addresses — IPv4 (guarded so it isn't part of a longer dotted OID) + IPv6
    ("IP_ADDRESS", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), 0.75),
    ("IP_ADDRESS", re.compile(
        r"(?<![:\w])(?:"
        r"(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}|"
        r"(?:[A-Fa-f0-9]{1,4}:){1,7}:|"
        r"(?:[A-Fa-f0-9]{1,4}:){1,6}:[A-Fa-f0-9]{1,4}|"
        r"(?:[A-Fa-f0-9]{1,4}:){1,5}(?::[A-Fa-f0-9]{1,4}){1,2}|"
        r"(?:[A-Fa-f0-9]{1,4}:){1,4}(?::[A-Fa-f0-9]{1,4}){1,3}|"
        r"(?:[A-Fa-f0-9]{1,4}:){1,3}(?::[A-Fa-f0-9]{1,4}){1,4}|"
        r"(?:[A-Fa-f0-9]{1,4}:){1,2}(?::[A-Fa-f0-9]{1,4}){1,5}|"
        r"[A-Fa-f0-9]{1,4}:(?::[A-Fa-f0-9]{1,4}){1,6}"
        r")(?![:\w])"), 0.6),
    # VIN — 17 chars, excludes I/O/Q (HL7/ISO vehicle id)
    ("VIN", re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b"), 0.5),
    ("STREET_ADDRESS", re.compile(
        r"\b\d{1,6}\s+(?:[A-Z][A-Za-z.]+\s+){1,3}"
        r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Parkway|Pkwy|Lane|Ln|"
        r"Drive|Dr|Court|Ct|Way|Place|Pl|Circle|Cir|Terrace|Ter|Highway|Hwy)\b\.?"
    ), 0.55),
    # NB: no bare \d{5} ZIP pattern — it would eat 5-digit CPT/lab codes.
    ("ZIP", re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b"), 0.45),
]
