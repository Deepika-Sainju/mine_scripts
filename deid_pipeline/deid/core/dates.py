"""Date parsing and shifting shared by surrogates, member matching, and C-CDA."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple

# Every date format the pipeline understands, in match-priority order.
DATE_FORMATS = [
    "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%m-%Y",
    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",  # ISO datetime
    "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",  # February 6, 2025
    "%d %B %Y", "%d %b %Y",                              # 6 February 2025
    # HL7 / C-CDA — %Y%m%d first so an 8-digit date isn't mis-parsed by the
    # flexible-width time formats (strptime would read 19731214 as 1973-01-02 01:04).
    "%Y%m%d", "%Y%m%d%H%M", "%Y%m%d%H%M%S", "%Y%m%d%H%M%S%z",
]

# Month-name alternation reused by the date regexes in detection/, surrogate/,
# and member/ (one definition so they can never drift apart).
MONTH_NAMES = (r"January|February|March|April|May|June|July|August|September|October|"
               r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec")


def parse_date_with_format(value: str) -> Optional[Tuple[datetime, str]]:
    """Parse `value` against DATE_FORMATS; return (datetime, matched_format) or None."""
    v = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt), fmt
        except ValueError:
            continue
    return None


def parse_date(value: str) -> Optional[datetime]:
    """Parse `value` against DATE_FORMATS; None if it isn't a date."""
    parsed = parse_date_with_format(value)
    return parsed[0] if parsed else None


def shift_date(value: str, days: int) -> Optional[str]:
    """Shift a date by `days`, preserving its original format; None if not a date."""
    parsed = parse_date_with_format(value)
    if parsed is None:
        return None
    dt, fmt = parsed
    return (dt + timedelta(days=days)).strftime(fmt)
