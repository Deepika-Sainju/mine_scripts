#!/usr/bin/env python3
"""
deid_txt.py — standalone, zero-dependency text de-identifier.

Redacts pattern-based PII from plain-text files using only the Python
standard library. Each distinct real value maps to a stable pseudonym token
(e.g. the same email always becomes [EMAIL_00001]), so relationships in the
data are preserved while the identifier is hidden.

Scope / limitations:
  * This is PATTERN-based only. It catches things with a recognizable shape
    (emails, phone numbers, SSNs, credit cards, IPs, URLs, dates, ZIPs, etc.).
  * It does NOT catch arbitrary person names, place names, or org names — that
    needs an NER model. For that, use the full `deidentify` package in this repo.
  * No PII detector is perfect. Review the audit log; do not treat output as
    certified de-identified.

Usage:
  python3 deid_txt.py input.txt                 # writes input.deid.txt
  python3 deid_txt.py input.txt -o out.txt
  python3 deid_txt.py input.txt --audit audit.json
  python3 deid_txt.py input.txt --mapping map.json   # persist tokens across runs
  python3 deid_txt.py input.txt --static             # one fixed value per PII type
  cat input.txt | python3 deid_txt.py -            # read stdin, write stdout
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Patterns, ordered most-specific first. Order matters: earlier patterns win
# on overlapping matches (e.g. SSN before a generic 9-digit number).
# ---------------------------------------------------------------------------
PATTERNS: list[tuple[str, re.Pattern]] = [
    # Emails
    ("EMAIL", re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),

    # URLs
    ("URL", re.compile(
        r"\bhttps?://[^\s<>\"')]+", re.IGNORECASE)),

    # Credit card numbers (13-16 digits, optional spaces/dashes as separators)
    ("CREDIT_CARD", re.compile(
        r"\b(?:\d[ -]?){12,15}\d\b")),

    # US SSN  (123-45-6789)
    ("SSN", re.compile(
        r"\b\d{3}-\d{2}-\d{4}\b")),

    # US ITIN (9xx-xx-xxxx, starts with 9) — checked via SSN shape + context
    # (kept simple; falls under SSN pattern above if formatted the same way)

    # IPv4 addresses
    ("IP_ADDRESS", re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")),

    # Phone numbers (US-ish: optional +1, area code, separators)
    ("PHONE", re.compile(
        r"(?<!\w)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)")),

    # Dates: 01/02/2023, 2023-01-02, Jan 2, 2023, 2 January 2023
    ("DATE", re.compile(
        r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
        r"|\d{4}[/-]\d{1,2}[/-]\d{1,2}"
        r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
        r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
        re.IGNORECASE)),

    # US ZIP (5 or 5+4) — kept after phone/ssn to avoid stealing their digits.
    ("ZIP", re.compile(
        r"(?<!\w)\d{5}(?:-\d{4})?(?!\w)")),
]


# ---------------------------------------------------------------------------
# Static replacements: in --static mode, EVERY detection of a type collapses to
# the single fixed value below (all emails become the same string, etc.).
# Edit these to whatever placeholder your downstream process expects.
# ---------------------------------------------------------------------------
STATIC_VALUES: dict[str, str] = {
    "EMAIL":       "redacted@example.com",
    "URL":         "https://example.com",
    "CREDIT_CARD": "0000000000000000",
    "SSN":         "000-00-0000",
    "IP_ADDRESS":  "0.0.0.0",
    "PHONE":       "000-000-0000",
    "DATE":        "1900-01-01",
    "ZIP":         "00000",
}


def _normalize(value: str) -> str:
    """Collapse whitespace so 'John  Doe' and 'John Doe' share a token."""
    return re.sub(r"\s+", " ", value).strip().lower()


class Deidentifier:
    def __init__(self, mapping: dict | None = None, static: bool = False):
        # mapping: { "EMAIL": { normalized_value: token, ... }, ... }
        self.mapping: dict[str, dict[str, str]] = mapping or {}
        # static=True -> one fixed replacement per type (see STATIC_VALUES)
        self.static = static
        # counters seeded from any pre-loaded mapping so tokens don't collide
        self.counters: dict[str, int] = {
            etype: len(vals) for etype, vals in self.mapping.items()
        }
        self.findings: list[dict] = []

    def _token_for(self, etype: str, value: str) -> str:
        if self.static:
            # Fixed value per type; fall back to a bracket token for any type
            # that has no static entry defined.
            return STATIC_VALUES.get(etype, f"[{etype}]")
        norm = _normalize(value)
        bucket = self.mapping.setdefault(etype, {})
        if norm not in bucket:
            self.counters[etype] = self.counters.get(etype, 0) + 1
            bucket[norm] = f"[{etype}_{self.counters[etype]:05d}]"
        return bucket[norm]

    def deidentify(self, text: str) -> str:
        # 1. Collect all non-overlapping matches across all patterns.
        spans: list[tuple[int, int, str, str]] = []  # (start, end, etype, value)
        claimed: list[tuple[int, int]] = []

        def overlaps(s: int, e: int) -> bool:
            return any(s < ce and cs < e for cs, ce in claimed)

        for etype, pattern in PATTERNS:
            for m in pattern.finditer(text):
                s, e = m.start(), m.end()
                if overlaps(s, e):
                    continue
                spans.append((s, e, etype, m.group()))
                claimed.append((s, e))

        # 2. Replace right-to-left so earlier offsets stay valid.
        spans.sort(key=lambda x: x[0], reverse=True)
        out = text
        for s, e, etype, value in spans:
            token = self._token_for(etype, value)
            self.findings.append(
                {"entity_type": etype, "start": s, "end": e, "token": token}
            )
            out = out[:s] + token + out[e:]

        self.findings.sort(key=lambda f: f["start"])
        return out

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f["entity_type"]] = counts.get(f["entity_type"], 0) + 1
        return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Standalone regex-based text de-identifier (stdlib only).")
    ap.add_argument("input", help="Input .txt file, or '-' for stdin.")
    ap.add_argument("-o", "--output",
                    help="Output file (default: <input>.deid.txt; '-' for stdout).")
    ap.add_argument("--audit", help="Write a JSON audit log to this path.")
    ap.add_argument("--mapping",
                    help="JSON file to load/save the value->token map "
                         "(persists consistent tokens across runs). "
                         "Ignored in --static mode.")
    ap.add_argument("--static", action="store_true",
                    help="Replace every detection of a type with one fixed "
                         "value (e.g. all emails -> redacted@example.com). "
                         "See STATIC_VALUES in the script to customize.")
    args = ap.parse_args(argv)

    # Read input
    if args.input == "-":
        text = sys.stdin.read()
        default_out = "-"
    else:
        in_path = Path(args.input)
        if not in_path.is_file():
            print(f"error: input file not found: {in_path}", file=sys.stderr)
            return 1
        text = in_path.read_text(encoding="utf-8", errors="replace")
        default_out = str(in_path.with_suffix(".deid" + in_path.suffix))

    # Load persistent mapping if requested (not used in static mode)
    mapping = None
    if args.mapping and not args.static and Path(args.mapping).is_file():
        mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))

    deid = Deidentifier(mapping=mapping, static=args.static)
    result = deid.deidentify(text)

    # Write output
    out_target = args.output or default_out
    if out_target == "-":
        sys.stdout.write(result)
    else:
        Path(out_target).write_text(result, encoding="utf-8")

    # Persist mapping (skipped in static mode — there is no per-value table)
    if args.mapping and not args.static:
        Path(args.mapping).write_text(
            json.dumps(deid.mapping, indent=2, ensure_ascii=False),
            encoding="utf-8")

    # Audit log
    if args.audit:
        Path(args.audit).write_text(
            json.dumps(
                {
                    "input": args.input,
                    "output": out_target,
                    "counts": deid.summary(),
                    "findings": deid.findings,
                },
                indent=2, ensure_ascii=False),
            encoding="utf-8")

    # Report to stderr so stdout stays clean when piping
    if out_target != "-":
        counts = deid.summary()
        total = sum(counts.values())
        print(f"de-identified {total} item(s) -> {out_target}", file=sys.stderr)
        for etype, n in sorted(counts.items()):
            print(f"  {etype:12} {n}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
