"""Generate realistic synthetic SURROGATES for detected PHI.

Not redaction: a name becomes a different but plausible name, a date is shifted by
one consistent per-document offset, IDs are format-preserved. Clinical content is
never touched (it is never detected as PHI).
"""
from __future__ import annotations

import re
import zlib
from datetime import timedelta
from random import Random
from typing import Dict, Optional, Tuple

from faker import Faker

from deid.core.dates import MONTH_NAMES, parse_date_with_format, shift_date
from deid.core.text import ALPHA_TOKEN_RE, apply_case
from deid.surrogate.clinical_vocab import KEEP_WORDS

# Module-shared Faker for content-derived name parts: it is reseeded with
# seed_instance() before every draw, so sharing one instance is deterministic
# and keeps SurrogateGenerator construction cheap (one per CSV row is fine).
_NAME_FAKER = Faker()

# One token: a (possibly multi-word) date, OR an alphabetic word, OR a number/id/time.
# Date alternatives come first so "January 9, 1989" is captured as a single unit.
_TOKEN = re.compile(
    rf"(?:{MONTH_NAMES})\.?\s+\d{{1,2}},?\s+\d{{4}}"   # February 6, 2025
    rf"|\d{{1,2}}\s+(?:{MONTH_NAMES})\.?\s+\d{{4}}"     # 6 February 2025
    rf"|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}"                 # 02/19/1958
    rf"|\d{{4}}-\d{{2}}-\d{{2}}"                        # 1958-02-19
    rf"|[A-Za-z]+"                                      # a word
    rf"|\d[\d.\-:]*",                                   # a number / id / time
    re.I,
)


class SurrogateGenerator:
    """Deterministic fake-value generation.

    known_tokens arms the collision guard AT CONSTRUCTION: a generated fake
    never reproduces one of the member's real tokens. There is deliberately no
    way to arm it later — callers that could forget a second step did forget it
    (see MemberScrubber, which owns construction).
    """

    def __init__(self, seed: int = 1234, known_tokens=None) -> None:
        self._seed = seed
        self._rng = Random(seed)
        self._faker_instance: Faker | None = None  # lazy; only document paths use it
        self._name_faker = _NAME_FAKER
        self._cache: Dict[Tuple[str, str], str] = {}
        self._person_map: Dict[str, str] = {}  # real-token.lower() -> fake last name
        self._part_map: Dict[Tuple[str, str], str] = {}  # (kind, value.lower()) -> fake
        self._known_tokens = {t.lower() for t in (known_tokens or ())}
        # one consistent date offset for the whole document (±15..364 days, nonzero)
        mag = self._rng.randint(15, 364)
        self._date_offset = mag if self._rng.random() < 0.5 else -mag

    @property
    def _faker(self) -> Faker:
        # Same sequence as an eagerly seeded instance; deferred because CSV-row
        # generators never touch it and Faker construction is the expensive part.
        if self._faker_instance is None:
            f = Faker()
            f.seed_instance(self._seed)
            self._faker_instance = f
        return self._faker_instance

    def _collides(self, surrogate: str) -> bool:
        return any(
            m.group(0).lower() in self._known_tokens
            for m in ALPHA_TOKEN_RE.finditer(surrogate)
        )

    def _guarded(self, gen) -> str:
        cand = gen()
        for _ in range(24):  # give up after retries; better an odd name than a crash
            if not self._collides(cand):
                break
            cand = gen()
        return cand

    # -- public --------------------------------------------------------------
    def surrogate(self, entity_type: str, value: str) -> str:
        key = (entity_type, value)
        if key in self._cache:
            return self._cache[key]
        out = self._generate(entity_type, value)
        self._cache[key] = out
        return out

    # -- content-derived field surrogates (for CSV / structured data) --------
    # These map a real value to a fake deterministically from the *value itself*
    # (+ seed), so the same value → same fake across files and runs (member ↔
    # claims referential integrity), independent of processing order.

    @staticmethod
    def _norm_gender(g: str) -> str:
        g = (g or "").strip().upper()
        if g in ("M", "MALE"):
            return "M"
        if g in ("F", "FEMALE"):
            return "F"
        return ""

    def name_part(self, value: str, kind: str = "last", gender: str = "") -> str:
        """Type-appropriate fake name part. kind in {"first","middle","last"}.

        A single-character middle name is treated as an initial (fake letter).
        For a first name, `gender` ("M"/"F") picks a gender-matching fake so the
        surrogate agrees with the kept gender; last names are gender-neutral.
        Casing of the original is preserved. Deterministic per (kind, gender, value).
        """
        v = (value or "").strip()
        if not v:
            return value
        if kind == "middle" and len(v.rstrip(".")) == 1:
            base = (self._seed + zlib.crc32(("mid:" + v.lower()).encode())) & 0x7FFFFFFF
            return chr(ord("A") + base % 26) + ("." if v.endswith(".") else "")
        g = self._norm_gender(gender) if kind == "first" else ""
        cache_key = (kind, g, v.lower())
        fake = self._part_map.get(cache_key)
        if fake is None:
            base = (self._seed + zlib.crc32((kind + ":" + g + ":" + v.lower()).encode())) & 0x7FFFFFFF
            fake = self._name_faker.last_name()
            for attempt in range(25):
                self._name_faker.seed_instance(base + attempt)
                if kind == "first" and g == "M":
                    fake = self._name_faker.first_name_male()
                elif kind == "first" and g == "F":
                    fake = self._name_faker.first_name_female()
                elif kind == "first":
                    fake = self._name_faker.first_name()
                else:
                    fake = self._name_faker.last_name()
                if fake.lower() not in self._known_tokens:
                    break
            self._part_map[cache_key] = fake
        return apply_case(v, fake)

    def fake_dob(self, value: str) -> str:
        """A realistic fake DOB: shift the real DOB by a value-derived offset
        (±≈1yr, nonzero), keeping the original format. Deterministic per date, so
        the same DOB → same fake in the member and claims files."""
        parsed = parse_date_with_format(value)
        if parsed is None:
            return value  # not a parseable date — leave as-is
        dt, fmt = parsed
        canon = dt.strftime("%Y%m%d")
        off = ((self._seed + zlib.crc32(("dob:" + canon).encode())) % 731) - 365
        off = off or 17
        return (dt + timedelta(days=off)).strftime(fmt)

    def identifier(self, value: str) -> str:
        """Content-derived, format-preserving fake id (digit→digit, letter→letter,
        separators kept). Deterministic per value+seed → consistent across files."""
        if not value:
            return value
        rng = Random((self._seed + zlib.crc32(value.encode())) & 0x7FFFFFFF)
        out = []
        for ch in value:
            if ch.isdigit():
                out.append(str(rng.randint(0, 9)))
            elif ch.isupper():
                out.append(chr(rng.randint(ord("A"), ord("Z"))))
            elif ch.islower():
                out.append(chr(rng.randint(ord("a"), ord("z"))))
            else:
                out.append(ch)
        return "".join(out)

    # -- generators ----------------------------------------------------------
    def _generate(self, etype: str, value: str) -> str:
        if etype in ("LOCATION", "GPE"):
            return self._guarded(self._faker.city)
        if etype == "ORG":
            return self._guarded(self._faker.company)
        if etype == "EMAIL_ADDRESS":
            return self._guarded(self._faker.ascii_email)
        if etype == "STREET_ADDRESS":
            return self._guarded(self._faker.street_address)
        if etype == "URL":
            return self._guarded(self._faker.url)
        if etype == "IP_ADDRESS":
            return self._guarded(self._faker.ipv6 if ":" in value else self._faker.ipv4)
        if etype == "DATE_TIME":
            return self._date(value)
        # identifiers: keep the exact shape (digits/letters/separators), no name-ifying
        if etype in ("MRN", "NPI", "CLAIM_NUMBER", "US_SSN", "PHONE_NUMBER", "ZIP",
                     "VIN", "ID_NUMBER"):
            return self._format_preserve(value)
        # PERSON / PHI / default: token-aware, structure-preserving surrogation.
        return self._smart(value)

    def _name_for_token(self, token: str) -> str:
        """A fake last name derived from the real token's content (not its position).

        Same real name -> same fake everywhere (within a doc and across documents),
        independent of the order names appear. Cached per token.
        """
        key = token.lower()
        cached = self._person_map.get(key)
        if cached is not None:
            return cached
        base = (self._seed + zlib.crc32(key.encode())) & 0x7FFFFFFF
        name = self._faker.last_name()
        for attempt in range(25):  # avoid colliding with a real PHI token
            self._name_faker.seed_instance(base + attempt)
            name = self._name_faker.last_name()
            if name.lower() not in self._known_tokens:
                break
        self._person_map[key] = name
        return name

    def try_shift_date(self, value: str) -> Optional[str]:
        """Shift a value by the document offset if it parses as a date, else None."""
        return shift_date(value, self._date_offset)

    def _date(self, value: str) -> str:
        shifted = self.try_shift_date(value)
        # A DATE_TIME span that doesn't parse as a pure date is really a longer,
        # mis-merged span (e.g. a whole line) — surrogate it structurally instead
        # of scrambling the whole thing.
        return shifted if shifted is not None else self._smart(value)

    def _smart(self, value: str) -> str:
        """Token-aware, structure-preserving surrogate.

        Walks the value token by token: shift embedded dates, replace name/word
        tokens with consistent fakes, format-preserve numbers/IDs, and keep
        structural/clinical words (KEEP_WORDS), single-letter initials, and all
        punctuation/whitespace untouched — so labels and clinical text stay readable
        while PHI is replaced.
        """
        out, i = [], 0
        for m in _TOKEN.finditer(value):
            out.append(value[i:m.start()])  # gaps (punctuation/whitespace) kept
            tok = m.group(0)
            shifted = self.try_shift_date(tok)
            if shifted is not None:
                out.append(shifted)
            elif len(tok) == 1 or tok.lower() in KEEP_WORDS:
                out.append(tok)  # initial, structural label, or clinical term
            elif tok.isalpha():
                out.append(apply_case(tok, self._name_for_token(tok)))
            else:
                out.append(self._format_preserve(tok))  # numbers / IDs / times
            i = m.end()
        out.append(value[i:])
        return "".join(out)

    def _format_preserve(self, value: str) -> str:
        out = []
        for ch in value:
            if ch.isdigit():
                out.append(str(self._rng.randint(0, 9)))
            elif ch.isupper():
                out.append(chr(self._rng.randint(ord("A"), ord("Z"))))
            elif ch.islower():
                out.append(chr(self._rng.randint(ord("a"), ord("z"))))
            else:
                out.append(ch)  # keep separators / punctuation
        return "".join(out)
