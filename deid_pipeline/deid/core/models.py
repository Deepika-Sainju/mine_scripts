"""Data types shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Detection:
    """One detected PHI span in a text: [start, end) with a type, score, source."""
    entity_type: str
    start: int
    end: int
    score: float
    source: str


# Entity-type priority used to pick the representative of merged overlapping
# spans (higher wins ties after score).
ENTITY_PRIORITY = {
    "DATE_TIME": 5, "US_SSN": 5, "NPI": 5, "MRN": 5, "CLAIM_NUMBER": 5,
    "PHONE_NUMBER": 4, "EMAIL_ADDRESS": 4, "STREET_ADDRESS": 4,
    "IP_ADDRESS": 5, "URL": 4, "VIN": 4,
    "PERSON": 3, "PHI": 3, "ID_NUMBER": 3,
    "ORG": 2, "LOCATION": 2, "GPE": 2,
    "ZIP": 1,
}


@dataclass
class MemberPHI:
    """A single member's known identifiers (from the member CSV row)."""
    first: str = ""
    middle: str = ""
    last: str = ""
    dob: str = ""
    member_id: str = ""
    gender: str = ""   # "M"/"F" — used to pick a gender-matching fake first name
