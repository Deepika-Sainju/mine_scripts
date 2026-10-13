#!/usr/bin/env python3
"""
ccda_dosfirst_v5.py — Re-shape a sectional C-CDA into a DOS-first (date-of-service) view.   v5

Why v5 (what changed vs v4) — throughput only: every output file's CONTENT is byte-identical to v4's (verified
with diff -r over --json/--text/--html, nested input trees, and -j 1 / -j 4 / default).  The XML/HTML file NAMES
differ by design — they carry a _v5 suffix so a v4 and a v5 run can share an output folder.

Timings below were measured on one machine (WSL2, 14 cores, CPython 3.12) and are observations, not promises:
they are here to say which change paid and which barely did, so a later reader optimizes the right thing.
Per-phase figures are min/median of 9 runs on the 6 MB chart; batch figures come from a 56-chart corpus.

  * PARALLEL BY DEFAULT — this is the change that matters.  56 charts: 33.3 s -> 4.66 s (7.1x on 14 cores).
    Charts are independent of each other, so --jobs/-j runs them in worker processes, defaulting to one per CPU
    core (-j 1 restores v4's serial behaviour).  Workers write their own files and return only picklable summaries
    — no Element ever crosses the process boundary — and all printing stays in the parent, where ex.map replays
    results in input order, so a parallel run's stderr reads exactly like a serial one.
  * clone() REPLACES copy.deepcopy FOR ELEMENTS.  build_dosfirst_xml 317/349 ms -> 211/227 ms (-33%) on the 6 MB
    chart; deepcopy was ~228k calls there, 15% of the run by tottime and 29% cumulative.  Note the phase: the
    deepcopy cost is in build_dosfirst_xml, NOT in transform.  deepcopy reaches an Element through its generic
    memo/dispatch machinery, while an Element holds only a tag, a flat str->str attrib dict, two strings and its
    children — so the copy is a five-line recursion.
  * NARRATIVE TABLE SCANS MEMOIZED, AND THE WHITESPACE REGEX PRECOMPILED.  Together ~10% off transform
    (397/447 ms -> 355/400 ms); they were not measured apart, so neither is credited separately.  The memo is
    structural rather than empirical: _header_cells and _heading_before answer a question about the TABLE but were
    called once per ROW, each rescanning the whole table — O(rows^2) on a long result table.  It does remove real
    work (text() calls on the 6 MB chart: 79.2k -> 59.3k).
  * cda.xsl COMPILED ONCE PER PROCESS instead of re-parsed and re-compiled for every --html file, for a result that
    never changes.  Unmeasured here — lxml is not installed in this checkout, so the --html path could not be
    benchmarked; the argument for it is that the work is provably redundant, not a recorded number.
  * RUN CLOCK OFF A MONOTONIC SOURCE, plus a per-file average, so a clock change mid-run cannot corrupt the elapsed
    time and a long batch reports its own cost per chart.

Why v4 (what changed vs v3):
  * MALFORMED SOURCE XML IS RECOVERED, NOT LOST.  v3 scrubbed XML-illegal characters on the way OUT (scrub_tree) but
    handed the input straight to ET.parse, so one bad character anywhere in a chart threw the whole chart away with
    "ParseError: not well-formed (invalid token)" before a single section was read — the failure mode behind 10 lost
    files in a 3467-chart batch.  load_source() now puts the same gate on the way IN: a well-formed file is parsed
    once and touched by nothing else (the fast path costs the good charts nothing), and only a ParseError re-reads the
    bytes and retries on a repaired copy.  Repaired: raw C0 control characters, HTML named entities XML never defined
    (&nbsp;), bare '&', character references to unrepresentable codepoints, junk ahead of the XML declaration, and a
    wrong or missing encoding declaration (BOM -> declared -> utf-8 -> cp1252).  Every repair is recorded as a
    document warning ahead of all others, so a recovered chart is never passed off as a clean one.
    NOT repaired, because the fix would be a guess at clinical content: a raw '<' in text, a mismatched or unclosed
    tag, a truncated file.  Those still fail, now reporting both the original and the post-repair error.

Why v2 (what changed vs ccda_dosfirst.py):
  * NO SILENT DROPS.  Every section and every entry is processed: sections without a dedicated parser go through a
    generic parser (bucket named after the section); entry children a dedicated parser does not understand fall back to
    the generic parser too; nested sections are visited; narrative-only sections are carried over as text.
    Every drop that still happens is counted per section, printed to stderr and returned in the JSON summary;
    --strict turns any drop into a non-zero exit.
  * NARRATIVE-ONLY TEXT IS KEPT (v2.1).  Entry coverage is not narrative coverage: a section can parse perfectly and
    still hold facts that live only in its <text> block, because nothing outside an ID-bearing element is reachable
    from an entry.  So each item now resolves its own #reference back to the narrative ROW that holds it, plus the
    loose text heading that row's table, and the leftover — dates stripped, cells that merely repeat the label
    dropped — is appended to the item's existing "Date basis / notes" cell.  No new section and no new column:
    the text lands on the row it belongs to.  This recovers allergy "Deactivated",
    medication / problem "Active", results "No observation recorded." and vitals panel headings such as
    "07/01/2015 02:00PM PHYSICAL EXAM", all of which v2.0 discarded silently.
    The "Source narratives" fallback keeps v2.0 behaviour: a section's <text> is carried verbatim only when it has
    no structured entries, lost entries, or went through the generic parser.  Sections a dedicated parser handled
    are not repeated, so any narrative-only cell those parsers do not surface is not in the output.
    Narrative anchors per section, and any source reference pointing at a non-existent ID, are counted, shown in the
    overview table and returned in the JSON summary.
  * DOS MEANS A VISIT.  Only performed encounters (moodCode EVN, Encounters section or componentOf/encompassingEncounter)
    create "DOS yyyy-mm-dd" sections.  Other dated items are attached to the encounter whose date/window contains them;
    dates with no encounter are still listed chronologically but headed "no encounter on this date" and every table
    says what the date means (onset / start / collected / recorded / planned).  Planned items (moodCode INT/RQO/PRP/ARQ)
    never create a DOS: they attach to the visit on which they were ordered or go under "Planned yyyy-mm-dd".
  * DATE RULES.  effectiveTime/high (stop date) is never a DOS; stopped medications with unknown start are listed
    under the visit on which they were reconciled (if the stop date is a visit) or in Undated with the stop date shown.
    Results/vitals use the earliest of organizer and component dates (import timestamps on the organizer are ignored and
    flagged).  Bulk author/time stamps (same second on >= 20 items) are recognised as import stamps and never used as dates.
    Partial dates (YYYY, YYYYMM) are kept as partial, not thrown away.  The local calendar date is used as written
    (no timezone conversion — intentional).
  * VALUES.  One formatter for every value type: PQ/REAL/INT/MO, CD/CE, ST, BL, TS, IVL, ED; numbers hidden in
    <translation value=..> under nullFlavor=OTH are used; Python None never reaches the page.
  * LABELS.  displayName -> originalText -> translation -> narrative text -> product name -> "(uncoded ...)"; codes from
    translations are surfaced; long labels are truncated in tables (full text kept in JSON).
  * MORE CONTENT.  Reference ranges, result comments, medication indications / fills / patient instructions, allergy
    comments, encounter reasons, nested screening scores, all performers.
  * ROBUSTNESS.  UTF-8 everywhere (no cp1252 crash), no Element truthiness bugs, no KeyError on missing header parts,
    batch mode over files/directories with per-file error isolation, one-step --html, --year / --dos-from / --dos-to filter.

Usage:
    python ccda_dosfirst_v5.py charts/                            -> charts/<stem>_dosfirst_v5.xml for every .xml found
    python ccda_dosfirst_v5.py charts/ -o out/                    -> same, written to out/ (created if missing)
    python ccda_dosfirst_v5.py charts/ -o out/ --json --text      -> JSON and text as well as the XML
    python ccda_dosfirst_v5.py charts/ -o out/ --html             -> rendered HTML as well (needs lxml)
    python ccda_dosfirst_v5.py a.xml b.xml 'charts/*.xml' -o out/ -> files, folders and globs can be mixed
    python ccda_dosfirst_v5.py input.xml --year 2025              -> only DOS in 2025 (overview stays complete)
    python ccda_dosfirst_v5.py charts/ --strict                   -> exit 2 if anything was dropped
    python ccda_dosfirst_v5.py charts/ -j 8                       -> 8 worker processes (default: one per CPU core)
    python ccda_dosfirst_v5.py charts/ -j 1                       -> serial, i.e. v4's behaviour
    python ccda_dosfirst_v5.py charts/ --timezone Asia/Kathmandu  -> report the run clock in that zone, not the machine's

A batch runs in parallel by default -- one worker process per CPU core -- because charts are independent of each
other.  -j/--jobs sets the count, -j 1 forces serial.  Output content and stderr ordering are identical either way.

Sub-folders are scanned by default; --no-recursive limits the scan to the top level (-r is accepted but is now
a no-op).  Input may be any mix of files, folders and globs; a folder is scanned for *.xml and this tool's own
outputs (*_dosfirst*.xml) are skipped, so re-running over the same folder is safe.  DOS-first XML is always
written unless --no-xml is given; --html/--json/--text add further formats on top of it.  Only the standard
library is needed for --xml/--json/--text; --html needs lxml (render_html.py).

Outputs, per input chart, mirrored under the output folder -- -o if given, otherwise ./output -- with the top
folder of the mirrored tree stamped with the run date.  Nothing is ever written among the inputs:
    <stem>_dosfirst_v5.xml  the DOS-first document.  Carries <?xml-stylesheet href="CCDA.xsl" type="text/xsl"?>
                            ahead of the root, so it renders as a chart in a browser when a stylesheet of that
                            name sits next to it -- the .xsl in this repo is xsl/cda.xsl, so copy/rename it there.
                            The _v5 suffix lets a v4 and a v5 run share one output folder
    <stem>_dosfirst.json    full structured summary, including per-item date basis      (--json)
    <stem>_dosfirst.txt     plain-text rendering                                        (--text)
    <stem>_dosfirst_v5.html cda.xsl rendering of the XML                                (--html)

Failures do not stop the batch, and never take a worker down with them.  Every failed chart is recorded in ONE
consolidated report, error.txt, at the top of that same output folder:
an index of what failed, then the input path, intended output path and full traceback for each.  Workers hand
their tracebacks back to the parent rather than writing files, so the report is written once, in input order.
A clean run writes no report and deletes the one its own previous run left, so a stale file cannot be mistaken
for the current result.  Exit code is 1 if anything failed, 2 with --strict if any entry was dropped, 0 otherwise.

Timestamps (the started/completed lines, the error report, the run-date folder stamp) come off one clock: the
machine's local zone, or --timezone ZONE.  Worth knowing under WSL, where Linux commonly sits on UTC while the
host is on your real zone.  This never touches a clinical date, which is always read exactly as written.
"""
from __future__ import annotations

import argparse
import base64
import codecs
import glob
import json
import os
import re
import sys
import time
import traceback
from collections import Counter, OrderedDict, defaultdict
from datetime import date as _date, datetime as _datetime, timezone as _timezone
from html.entities import name2codepoint
from xml.etree import ElementTree as ET

__version__ = "2.1.0"

NS = {"h": "urn:hl7-org:v3", "xsi": "http://www.w3.org/2001/XMLSchema-instance", "sdtc": "urn:hl7-org:sdtc"}
H = "{urn:hl7-org:v3}"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
NPI_ROOT = "2.16.840.1.113883.4.6"
SSN_ROOT = "2.16.840.1.113883.4.1"
LOINC_OID = "2.16.840.1.113883.6.1"

# templateId root -> section key (dedicated parser).  Anything else -> generic parser (never dropped).
SECTION_TEMPLATES = {
    "2.16.840.1.113883.10.20.22.2.6.1": "allergies", "2.16.840.1.113883.10.20.22.2.6": "allergies",
    "2.16.840.1.113883.10.20.22.2.1.1": "medications", "2.16.840.1.113883.10.20.22.2.1": "medications",
    "2.16.840.1.113883.10.20.22.2.38": "medications",  # medications administered
    "2.16.840.1.113883.10.20.22.2.5.1": "problems", "2.16.840.1.113883.10.20.22.2.5": "problems",
    "2.16.840.1.113883.10.20.22.2.7.1": "procedures", "2.16.840.1.113883.10.20.22.2.7": "procedures",
    "2.16.840.1.113883.10.20.22.2.3.1": "results", "2.16.840.1.113883.10.20.22.2.3": "results",
    "2.16.840.1.113883.10.20.22.2.22.1": "encounters", "2.16.840.1.113883.10.20.22.2.22": "encounters",
    "2.16.840.1.113883.10.20.22.2.17": "social_history",
    "2.16.840.1.113883.10.20.22.2.2.1": "immunizations", "2.16.840.1.113883.10.20.22.2.2": "immunizations",
    "2.16.840.1.113883.10.20.22.2.10": "plan_of_care",
    "2.16.840.1.113883.10.20.22.2.4.1": "vitals", "2.16.840.1.113883.10.20.22.2.4": "vitals",
    "2.16.840.1.113883.10.20.22.2.65": "notes",
    "2.16.840.1.113883.10.20.22.2.14": "functional_status", "2.16.840.1.113883.10.20.22.2.56": "mental_status",
    "2.16.840.1.113883.10.20.22.2.15": "family_history",
    "2.16.840.1.113883.10.20.22.2.8": "assessment",
    # Reason for Referral is an IHE template, not an HL7 one, so its root sits outside the 2.16.840… tree.
    "1.3.6.1.4.1.19376.1.5.3.1.3.1": "referral",
    "2.16.840.1.113883.10.20.22.2.18": "payers",
    "2.16.840.1.113883.10.20.22.2.21": "advance_directives",
    "2.16.840.1.113883.10.20.22.2.58": "health_concerns",
    "2.16.840.1.113883.10.20.22.2.500": "care_team",
}
SECTION_CODES = {
    "48765-2": "allergies", "10160-0": "medications", "29549-3": "medications", "11450-4": "problems", "47519-4": "procedures",
    "30954-2": "results", "46240-8": "encounters", "29762-2": "social_history", "11369-6": "immunizations",
    "18776-5": "plan_of_care", "8716-3": "vitals", "34109-9": "notes", "11488-4": "notes", "28570-0": "notes", "51847-2": "notes",
    "47420-5": "functional_status", "10190-7": "mental_status", "10157-6": "family_history", "51848-0": "assessment",
    "42349-1": "referral", "48768-6": "payers", "42348-3": "advance_directives", "75310-3": "health_concerns",
    "85847-2": "care_team",
}
NOTE_ACTIVITY_TEMPLATE = "2.16.840.1.113883.10.20.22.4.202"
REFERRAL_ACT_TEMPLATE = "2.16.840.1.113883.10.20.22.4.140"   # Patient Referral Act
INDICATION_TEMPLATE = "2.16.840.1.113883.10.20.22.4.19"      # Indication Observation
OID_NAMES = {
    "2.16.840.1.113883.6.96": "SNOMED CT", "2.16.840.1.113883.6.1": "LOINC", "2.16.840.1.113883.6.88": "RxNorm",
    "2.16.840.1.113883.6.90": "ICD-10-CM", "2.16.840.1.113883.6.103": "ICD-9-CM", "2.16.840.1.113883.6.12": "CPT",
    "2.16.840.1.113883.6.101": "NUCC", "2.16.840.1.113883.12.292": "CVX", "2.16.840.1.113883.6.238": "CDC Race/Ethnicity",
    "2.16.840.1.113883.5.1": "HL7 AdministrativeGender", "2.16.840.1.113883.6.69": "NDC", "2.16.840.1.113883.6.14": "HCPCS",
    "2.16.840.1.113883.5.83": "HL7 ObservationInterpretation", "2.16.840.1.113883.5.111": "HL7 RoleCode",
}
PLANNED_MOODS = {"INT", "RQO", "PRP", "ARQ", "PRMS", "GOL", "APT"}
BULK_STAMP_THRESHOLD = 20      # >= this many author/time values with the identical second => import stamp
ORGANIZER_STAMP_DAYS = 3       # organizer date this many days after every component date => ignore organizer date
MAX_LABEL = 120                # table label truncation (full text kept in JSON)


# ----------------------------------------------------------------------------------------------- small helpers
def q(tag):
    return H + tag


def local(tag):
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def find(el, path):
    return el.find(path, NS) if el is not None else None


def findall(el, path):
    return el.findall(path, NS) if el is not None else []


def attr(el, name, default=None):
    return el.get(name, default) if el is not None else default


def clone(el):
    """Deep copy of an Element subtree.

    copy.deepcopy() got here through its generic memo/dispatch machinery: ~228k calls on a 6 MB chart, 15% of
    that run by tottime.  Nearly all of it is under build_dosfirst_xml (copy_entries), which this cut by a third.
    An Element holds only a tag, a flat str->str attrib dict, two strings and its children, so the copy is this
    five-line recursion and nothing is shared with the source."""
    new = ET.Element(el.tag, el.attrib.copy())
    new.text, new.tail = el.text, el.tail
    new.extend([clone(c) for c in el])
    return new


_WS_RUN = re.compile(r"\s+")  # compiled once: text() is called on every narrative cell of every chart


def text(el):
    if el is None:
        return None
    t = "".join(el.itertext())
    t = _WS_RUN.sub(" ", t).replace("\xa0", " ").strip()
    return t or None


def first(*vals):
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None


CODE_SUFFIX = re.compile(r"\s*\[[^\[\]]{1,40}\]$")


def trunc(s, n=MAX_LABEL):
    """Truncate for display, but never drop a trailing '[SYSTEM code]'.

    code_label() puts the identifier at the END of the label, so a plain tail-cut silently deletes it on long names:
    'Glomerular filtration rate … among black population [LOINC 48643-1]' is ~170 chars, and cutting at 120 threw the
    LOINC code away — leaving the concept in the table but its code nowhere in the document.
    """
    if s is None:
        return None
    s = str(s)
    if len(s) <= n:
        return s
    m = CODE_SUFFIX.search(s)
    if m and len(m.group(0)) < n - 8:
        keep = n - len(m.group(0)) - 1
        return s[:keep] + "…" + m.group(0)
    return s[: n - 1] + "…"


TS_RE = re.compile(r"^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:\.\d+)?([+-]\d{4})?$")
# ISO-8601 written where an HL7 TS belongs: "2025-04-17", "2025-04-17T09:30:00-05:00", "2025-04-17 09:30Z".
ISO_TS_RE = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?$")


def compact_ts(value):
    """Normalise an ISO-8601 timestamp to the HL7 TS digits the rest of this module expects.

    HL7 TS is 'YYYYMMDDhhmmss±zzzz' with no separators, but real exports do emit 'YYYY-MM-DD'.  Every date parser
    here reads leading digits, so a hyphen ends the match after the year: a chart whose 65 dates were all written
    '2025-04-17' collapsed to the single year '2025' and the whole document landed in one "DOS 2025" section.
    Values already in HL7 form contain no hyphen at position 4 and are returned untouched.
    """
    if not value:
        return value
    v = value.strip()
    m = ISO_TS_RE.match(v)
    if not m:
        return v
    y, mo, d, hh, mi, ss, tz = m.groups()
    out = y + mo + (d or "")
    if hh:
        out += hh + mi + (ss or "")
    if tz:
        out += "+0000" if tz == "Z" else tz.replace(":", "")
    return out


def hl7_ts(value):
    """HL7 TS 'YYYYMMDDhhmmss±zzzz' -> 'YYYY-MM-DD[ hh:mm[:ss]][ ±zz:zz]' (partial dates stay partial)."""
    if not value:
        return None
    m = TS_RE.match(compact_ts(value))
    if not m:
        return value
    y, mo, d, hh, mm, ss, tz = m.groups()
    out = y
    if mo:
        out += f"-{mo}"
    if d:
        out += f"-{d}"
    if hh:
        out += f" {hh}:{mm or '00'}"
        if ss:
            out += f":{ss}"
    if tz:
        out += f" {tz[:3]}:{tz[3:]}"
    return out


def ts_date(value):
    """Local calendar date 'YYYY-MM-DD' of an HL7 TS (first 8 digits, NO timezone conversion — intentional:
    a visit stamped 2025-04-23 00:00 -0500 must stay 2025-04-23).  Partial dates return 'YYYY' or 'YYYY-MM'."""
    if not value:
        return None
    v = compact_ts(value)
    m = re.match(r"^(\d{4})(\d{2})?(\d{2})?", v)
    if not m or not re.match(r"^\d{4}", v):
        return None
    y, mo, d = m.groups()
    if mo and d:
        try:
            _date(int(y), int(mo), int(d))
        except ValueError:
            return None
        return f"{y}-{mo}-{d}"
    if mo:
        return f"{y}-{mo}"
    return y


def is_full_date(d):
    return bool(d) and len(d) == 10


def days_between(a, b):
    if not (is_full_date(a) and is_full_date(b)):
        return None
    ya, ma, da = map(int, a.split("-"))
    yb, mb, db = map(int, b.split("-"))
    return abs((_date(ya, ma, da) - _date(yb, mb, db)).days)


def effective_time(el):
    """First IVL_TS/TS effectiveTime child (PIVL/EIVL frequency timings skipped).  Never returns None."""
    out = {"value": None, "low": None, "high": None, "raw_value": None, "raw_low": None, "raw_high": None, "nullFlavor": None, "present": False}
    if el is None:
        return out
    for et in findall(el, "h:effectiveTime"):
        xt = et.get(XSI_TYPE) or ""
        if xt.endswith("PIVL_TS") or xt.endswith("EIVL_TS") or et.get("operator"):
            continue
        low, high = find(et, "h:low"), find(et, "h:high")
        out.update({
            "value": hl7_ts(attr(et, "value")), "low": hl7_ts(attr(low, "value")), "high": hl7_ts(attr(high, "value")),
            "raw_value": attr(et, "value"), "raw_low": attr(low, "value"), "raw_high": attr(high, "value"),
            "nullFlavor": attr(et, "nullFlavor") or attr(low, "nullFlavor"), "present": True,
        })
        return out
    return out


def fmt_eff(eff):
    if not eff or not eff.get("present"):
        return "n/a"
    if eff.get("value"):
        return eff["value"]
    lo, hi = eff.get("low"), eff.get("high")
    if lo or hi:
        return f"{lo or '?'} → {hi or 'ongoing'}"
    return f"no date (nullFlavor {eff['nullFlavor']})" if eff.get("nullFlavor") else "n/a"


def pick_dos(eff, meaning_value="performed", meaning_low="start"):
    """DOS candidate from an entry's own effectiveTime: value, else low.  NEVER high (a stop date is not a service date).
    Returns (date, source, meaning) or (None, None, None)."""
    d = ts_date(eff.get("raw_value"))
    if d:
        return d, "effectiveTime/value", meaning_value
    d = ts_date(eff.get("raw_low"))
    if d:
        return d, "effectiveTime/low", meaning_low
    return None, None, None


# ----------------------------------------------------------------------------------------------- narrative + codes
# Characters XML 1.0 allows in content: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF].
# Everything else (C0 controls, lone surrogates, #xFFFE/#xFFFF) is unrepresentable — not even as &#x2;.
_XML_ILLEGAL = re.compile("[^\\x09\\x0a\\x0d\\x20-\\ud7ff\\ue000-\\ufffd\\U00010000-\\U0010ffff]")

# Magic numbers for payloads that are binary no matter how they decode.
_BINARY_MAGIC = (b"%PDF", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"II*\x00", b"MM\x00*")


def xml_safe(s):
    """Drop characters that cannot appear in a well-formed XML 1.0 document."""
    if not isinstance(s, str):
        return s
    return _XML_ILLEGAL.sub("", s)


def _looks_binary(data: bytes) -> bool:
    """True for payloads that are not text: known magic, NULs, or a heavy sprinkling of C0 control bytes."""
    if data.startswith(_BINARY_MAGIC):
        return True
    sample = data[:4096]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    ctrl = sum(1 for b in sample if b < 0x20 and b not in (0x09, 0x0A, 0x0D))
    return ctrl * 20 > len(sample)  # >5% control bytes


def decode_b64_text(el):
    """<text representation="B64" mediaType=...> -> readable text (RTF control words / XML tags stripped)."""
    raw = "".join(el.itertext()).strip()
    if not raw:
        return None
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception:
        return xml_safe(raw)
    if _looks_binary(data):
        return "(binary attachment)"
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            s = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return "(binary attachment)"
    s = xml_safe(s)
    if s.lstrip().startswith("{\\rtf"):
        s = re.sub(r"\\par[d]?\b", "\n", s)
        s = re.sub(r"\\'[0-9a-fA-F]{2}", " ", s)
        s = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", s)
        s = re.sub(r"[{}]", " ", s)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s).strip()
    return s or None


class Narrative:
    """Index every element with an ID inside the document so '#ref' pointers resolve to display text.

    Also keeps a child -> parent map, so a resolved anchor can be widened to the narrative row (<tr>) that holds it
    and to the loose text run heading the table that row sits in.  Those neighbouring cells and headings carry no ID
    of their own, so nothing else in the pipeline can see them — status columns ("Active" / "Deactivated"), panel
    headings ("03/30/2016 02:00PM Lab Work"), narrative-only dates, and so on.
    """

    def __init__(self, root):
        self.index = {}
        self.parent = {}
        self.resolved_ids = set()
        # Per-table memos.  _header_cells and _heading_before are called once per ROW but answer a question about
        # the TABLE, and each rescans the whole table -- O(rows^2), which bites on a long result table.
        # Keyed by id(): the source tree is alive and unmodified for the life of this object, so the ids are stable.
        self._hdr_memo = {}
        self._head_memo = {}
        for el in root.iter():
            for c in el:
                self.parent[id(c)] = el
            i = el.get("ID")
            if i:
                self.index[i] = el

    def resolve(self, ref):
        if not ref:
            return None
        key = ref.lstrip("#")
        el = self.index.get(key)
        if el is not None:
            self.resolved_ids.add(key)
        return text(el)

    def _ancestor(self, el, tags):
        while el is not None:
            if local(el.tag) in tags:
                return el
            el = self.parent.get(id(el))
        return None

    def _heading_before(self, tbl):
        """The loose text run immediately preceding `tbl` among its siblings, skipping <br/> separators.

        Handles the athenahealth vitals shape, where each panel is introduced by bare text before its table:
            <text>11/07/2016 02:15PM Ultrasound<br/><table>...</table>10/15/2016 12:00PM Ultrasound<br/><table>...

        Memoized: the answer is a property of the table, but it is asked once per row.
        """
        key = id(tbl)
        if key in self._head_memo:
            return self._head_memo[key]
        self._head_memo[key] = out = self._heading_before_uncached(tbl)
        return out

    def _heading_before_uncached(self, tbl):
        par = self.parent.get(id(tbl))
        if par is None:
            return None
        kids = list(par)
        try:
            i = kids.index(tbl)
        except ValueError:
            return None
        j = i - 1
        while j >= 0:
            t = (kids[j].tail or "").strip()
            if t:
                return _WS_RUN.sub(" ", t)
            if local(kids[j].tag) != "br":
                return None
            j -= 1
        return _WS_RUN.sub(" ", (par.text or "").strip()) or None

    def headers_of(self, tbl):
        """Memoized _header_cells, for the same reason: one answer per table, one question per row."""
        key = id(tbl)
        if key in self._hdr_memo:
            return self._hdr_memo[key]
        self._hdr_memo[key] = out = Narrative._header_cells(tbl)
        return out

    @staticmethod
    def _header_cells(tbl):
        """Column names of a table: the first row whose cells are all <th>.  Returns [] when there is no header row."""
        for tr in tbl.iter(q("tr")):
            cells = [c for c in tr if local(c.tag) in ("td", "th")]
            if cells and all(local(c.tag) == "th" for c in cells):
                return [text(c) or "" for c in cells]
        return []

    @staticmethod
    def _row_text(tr, headers=None):
        """Row text with cell boundaries kept, each value labelled with its column name when the widths line up.

        Column names live in a <thead> row, which is a sibling of the item's own row, so without pairing them the
        recovered values arrive anonymous — '11/03/2016 | hemoglobin A1C | 5.8 | high' instead of
        'Observation Date: 11/03/2016 | Name: hemoglobin A1C | Value: 5.8 | Abnormal Flag: high'.
        """
        cells = [c for c in tr if local(c.tag) in ("td", "th")]
        vals = [text(c) for c in cells]
        if headers and len(headers) == len(vals):
            parts = [f"{h}: {v}" if h else v for h, v in zip(headers, vals) if v]
        else:
            parts = [v for v in vals if v]
        return " | ".join(parts) if parts else text(tr)

    def row_context(self, tr, tbl):
        """(row text, panel heading, {column name: value}) for a narrative row.

        The cell map is what lets a single narrative column be pulled out on its own — e.g. rendering just
        "Diagnosis Note" in its own table column instead of the whole row.  It is empty when the table has no
        header row or its width does not match the row's.
        """
        head = self._heading_before(tbl) if tbl is not None else None
        headers = self.headers_of(tbl) if tbl is not None else []
        cells = [c for c in tr if local(c.tag) in ("td", "th")]
        if headers and cells and all(local(c.tag) == "th" for c in cells):
            return None, head, {}  # this row *is* the header row
        vals = [text(c) for c in cells]
        cmap = {h: v for h, v in zip(headers, vals) if h and v} if len(headers) == len(vals) else {}
        return self._row_text(tr, headers), head, cmap

    def context_of(self, ref):
        """(row text, panel heading, cell map) around the narrative element that #ref points at."""
        el = self.index.get((ref or "").lstrip("#"))
        if el is None:
            return None, None, {}
        tr = self._ancestor(el, ("tr",))
        tbl = self._ancestor(el, ("table",))
        if tr is None:
            return None, (self._heading_before(tbl) if tbl is not None else None), {}
        return self.row_context(tr, tbl)

    def text_of(self, el):
        """Text of <text> child: base64 payload decoded, inline text, else the resolved <reference value='#x'/>."""
        t = find(el, "h:text")
        if t is None:
            return None
        if (t.get("representation") or "").upper() == "B64":
            return decode_b64_text(t)
        inline = text(t)
        ref = find(t, "h:reference")
        resolved = self.resolve(attr(ref, "value")) if ref is not None else None
        if inline and resolved and resolved not in inline:
            return f"{resolved} — {inline}"
        return inline or resolved


def code_dict(el, nar=None):
    if el is None:
        return None
    d = {"code": attr(el, "code"), "codeSystem": attr(el, "codeSystem"), "codeSystemName": attr(el, "codeSystemName"),
         "displayName": attr(el, "displayName"), "nullFlavor": attr(el, "nullFlavor")}
    ot = find(el, "h:originalText")
    if ot is not None:
        ref = find(ot, "h:reference")
        d["originalText"] = text(ot) or (nar.resolve(attr(ref, "value")) if nar is not None and ref is not None else None)
    trans = [code_dict(t) for t in findall(el, "h:translation")]
    trans = [t for t in trans if t]
    if trans:
        d["translation"] = trans
    return {k: v for k, v in d.items() if v}


def sysname(c):
    return c.get("codeSystemName") or OID_NAMES.get(c.get("codeSystem") or "", c.get("codeSystem"))


def code_label(c, fallback=None):
    """Human label: name [SYS code].  Falls back through translations; never returns the string 'None'."""
    if not c:
        return fallback
    name = c.get("displayName") or c.get("originalText")
    code, sname = c.get("code"), sysname(c)
    if not code and c.get("translation"):
        t = c["translation"][0]
        name = name or t.get("displayName") or t.get("originalText")
        code, sname = t.get("code"), sysname(t)
    if not name and c.get("translation"):
        name = next((t.get("displayName") or t.get("originalText") for t in c["translation"] if t.get("displayName") or t.get("originalText")), None)
    if name and code:
        return f"{name} [{sname} {code}]" if sname else f"{name} [{code}]"
    if name:
        return name + (f" (code {c['nullFlavor']})" if c.get("nullFlavor") and not code else "")
    if code:
        return f"{sname} {code}" if sname else code
    return fallback


def code_only(c):
    """Best code + system (from the code itself or its first translation)."""
    if not c:
        return None, None
    if c.get("code"):
        return c["code"], sysname(c)
    for t in c.get("translation") or []:
        if t.get("code"):
            return t["code"], sysname(t)
    return None, None


def fmt_value(v, nar=None):
    """Any <value> element -> display string, or None when the value is genuinely absent."""
    if v is None:
        return None
    xt = (v.get(XSI_TYPE) or "").split(":")[-1]
    nf = v.get("nullFlavor")
    val, unit = v.get("value"), v.get("unit")
    if val is None and nf:
        # athena puts the real number under <translation value=..> when the primary value is nullFlavor=OTH
        tr = find(v, "h:translation")
        if tr is not None and tr.get("value") is not None:
            ot = text(find(tr, "h:originalText"))
            return f"{tr.get('value')} {tr.get('unit') or ot or ''}".strip()
    if xt in ("PQ", "REAL", "INT", "MO", "TS", "") and val is not None:
        return f"{val} {unit or ''}".strip() if xt != "TS" else hl7_ts(val)
    if xt in ("CD", "CE", "CO", "CV", "CS"):
        lab = code_label(code_dict(v, nar))
        return lab or (f"not reported ({nf})" if nf else None)
    if xt in ("IVL_PQ", "IVL_INT", "IVL_REAL"):
        lo, hi = find(v, "h:low"), find(v, "h:high")
        lo_s = f"{attr(lo, 'value')} {attr(lo, 'unit') or ''}".strip() if lo is not None and lo.get("value") else None
        hi_s = f"{attr(hi, 'value')} {attr(hi, 'unit') or ''}".strip() if hi is not None and hi.get("value") else None
        if lo_s or hi_s:
            return f"{lo_s or '?'} – {hi_s or '?'}"
    if xt == "BL" and val is not None:
        return val
    if xt == "ED":
        if (v.get("representation") or "").upper() == "B64":
            return decode_b64_text(v) or "(binary attachment)"
        return text(v) or "(attachment)"
    if xt == "RTO":
        n, d = find(v, "h:numerator"), find(v, "h:denominator")
        if n is not None and d is not None:
            return f"{attr(n, 'value')}/{attr(d, 'value')}"
    t = text(v)
    if t:
        return t
    if nf:
        return f"not reported ({nf})"
    return None


def name_dict(el):
    if el is None:
        return None
    given = [text(g) for g in findall(el, "h:given") if text(g)]
    fam = text(find(el, "h:family"))
    prefix = text(find(el, "h:prefix"))
    suffix = text(find(el, "h:suffix"))
    full = " ".join(p for p in [prefix, *given, fam, suffix] if p) or text(el)
    return {"given": given, "family": fam, "prefix": prefix, "suffix": suffix, "full": full}


def addr_dict(el):
    if el is None or el.get("nullFlavor"):
        return None
    lines = [text(s) for s in findall(el, "h:streetAddressLine") if text(s)]
    d = {"use": attr(el, "use"), "street": lines, "city": text(find(el, "h:city")), "state": text(find(el, "h:state")),
         "postalCode": text(find(el, "h:postalCode")), "country": text(find(el, "h:country"))}
    d["oneLine"] = ", ".join(p for p in [*lines, d["city"], d["state"], d["postalCode"]] if p) or None
    return d


def telecoms(el):
    return [{"use": t.get("use"), "value": t.get("value")} for t in findall(el, "h:telecom") if t.get("value")]


def ids(el):
    out = []
    for i in findall(el, "h:id"):
        d = {"root": i.get("root"), "extension": i.get("extension"), "nullFlavor": i.get("nullFlavor")}
        if d["root"] == NPI_ROOT:
            d["type"] = "NPI"
        elif d["root"] == SSN_ROOT:
            d["type"] = "SSN"
        out.append({k: v for k, v in d.items() if v})
    return out


def npi_of(assigned_entity):
    for i in ids(assigned_entity):
        if i.get("type") == "NPI" and i.get("extension"):
            return i["extension"]
    return None


def assigned_entity(el, nar=None):
    if el is None:
        return None
    org = find(el, "h:representedOrganization")
    person = find(el, "h:assignedPerson/h:name")
    return {
        "ids": ids(el), "npi": npi_of(el), "taxonomy": code_dict(find(el, "h:code"), nar),
        "name": name_dict(person), "addr": addr_dict(find(el, "h:addr")), "telecom": telecoms(el),
        "organization": {"ids": ids(org), "name": text(find(org, "h:name")), "addr": addr_dict(find(org, "h:addr"))} if org is not None else None,
    }


def participant_role(prole, nar=None):
    """A <participantRole> in the shape assigned_entity() returns, so provider_label() renders both the same.

    Used for payer covered parties / policy holders, care team members and referral targets: the role names its
    person or organization through playingEntity rather than assignedPerson, and carries the member or policy
    number in its own id/@extension rather than as an NPI.
    """
    if prole is None:
        return None
    scoping = find(prole, "h:scopingEntity")
    return {
        "ids": ids(prole), "npi": npi_of(prole), "taxonomy": code_dict(find(prole, "h:code"), nar),
        "name": name_dict(find(prole, "h:playingEntity/h:name")), "addr": addr_dict(find(prole, "h:addr")),
        "telecom": telecoms(prole),
        "organization": {"ids": ids(scoping), "name": first(text(find(scoping, "h:desc")), text(find(scoping, "h:name"))),
                         "addr": addr_dict(find(scoping, "h:addr"))} if scoping is not None else None,
    }


def role_id(role, *roots_to_skip):
    """The first non-NPI id/@extension of a role -- the member number, policy number or payer id."""
    for i in (role or {}).get("ids") or []:
        if i.get("extension") and i.get("type") != "NPI" and i.get("root") not in roots_to_skip:
            return i["extension"]
    return None


def org_name(ae):
    return ((ae or {}).get("organization") or {}).get("name")


def person_name(ae):
    """Full name of a role.  The "name" key is always present but is None whenever the role names no person,
    so ae.get("name", {}) is not a safe default here -- it returns that None."""
    return ((ae or {}).get("name") or {}).get("full")


def provider_label(ae):
    if not ae:
        return "n/a"
    nm = (ae.get("name") or {}).get("full")
    parts = [nm, f"NPI {ae['npi']}" if ae.get("npi") else None]
    tax = ae.get("taxonomy") or {}
    if tax.get("displayName"):
        parts.append(f"{tax['displayName']} ({tax.get('code')})")
    if not nm and ae.get("organization") and ae["organization"].get("name"):
        parts.insert(0, ae["organization"]["name"])
    return " | ".join(p for p in parts if p) or "n/a"


def author_time_raw(el):
    for a in findall(el, "h:author"):
        t = find(a, "h:time")
        if t is not None and t.get("value"):
            return t.get("value")
    return None


def author_of(el, nar=None):
    a = find(el, "h:author/h:assignedAuthor")
    if a is None:
        return None
    dev = find(a, "h:assignedAuthoringDevice")
    ae = assigned_entity(a, nar)
    if ae and not (ae.get("name") or {}).get("full") and dev is not None:
        ae["name"] = {"full": text(find(dev, "h:softwareName")) or text(find(dev, "h:manufacturerModelName"))}
    return ae


def template_ids(el):
    return [t.get("root") for t in findall(el, "h:templateId") if t.get("root")]


# ----------------------------------------------------------------------------------------------- header
def parse_header(root, nar):
    pr = find(root, "h:recordTarget/h:patientRole")
    pt = find(pr, "h:patient")
    patient = {
        "ids": ids(pr), "name": name_dict(find(pt, "h:name")) or {"full": "n/a"},
        "gender": code_dict(find(pt, "h:administrativeGenderCode")),
        "birthTime": hl7_ts(attr(find(pt, "h:birthTime"), "value")),
        "maritalStatus": code_dict(find(pt, "h:maritalStatusCode")),
        "race": code_dict(find(pt, "h:raceCode")), "ethnicity": code_dict(find(pt, "h:ethnicGroupCode")),
        "language": [attr(find(lc, "h:languageCode"), "code") for lc in findall(pt, "h:languageCommunication")],
        "addr": [a for a in (addr_dict(x) for x in findall(pr, "h:addr")) if a], "telecom": telecoms(pr),
    }
    authors = []
    for a in findall(root, "h:author"):
        aa = find(a, "h:assignedAuthor")
        dev = find(aa, "h:assignedAuthoringDevice")
        authors.append({
            "time": hl7_ts(attr(find(a, "h:time"), "value")), "ids": ids(aa), "npi": npi_of(aa),
            "person": name_dict(find(aa, "h:assignedPerson/h:name")),
            "device": {"manufacturerModelName": text(find(dev, "h:manufacturerModelName")), "softwareName": text(find(dev, "h:softwareName"))} if dev is not None else None,
            "addr": addr_dict(find(aa, "h:addr")), "telecom": telecoms(aa), "organization": text(find(aa, "h:representedOrganization/h:name")),
        })
    cust = find(root, "h:custodian/h:assignedCustodian/h:representedCustodianOrganization")
    custodian = {"ids": ids(cust), "name": text(find(cust, "h:name")), "telecom": telecoms(cust), "addr": addr_dict(find(cust, "h:addr"))} if cust is not None else None
    service_events = []
    for se in findall(root, "h:documentationOf/h:serviceEvent"):
        service_events.append({
            "classCode": se.get("classCode"), "code": code_dict(find(se, "h:code")), "effectiveTime": effective_time(se),
            "performers": [{"typeCode": p.get("typeCode"), "functionCode": code_dict(find(p, "h:functionCode")),
                            "assignedEntity": assigned_entity(find(p, "h:assignedEntity"), nar)} for p in findall(se, "h:performer")],
        })
    enc = find(root, "h:componentOf/h:encompassingEncounter")
    encompassing = None
    if enc is not None:
        loc = find(enc, "h:location/h:healthCareFacility")
        dd = find(enc, "sdtc:dischargeDispositionCode")
        if dd is None:
            dd = find(enc, "h:dischargeDispositionCode")
        encompassing = {
            "ids": ids(enc), "code": code_dict(find(enc, "h:code"), nar), "effectiveTime": effective_time(enc),
            "dischargeDisposition": code_dict(dd),
            "responsibleParty": assigned_entity(find(enc, "h:responsibleParty/h:assignedEntity"), nar),
            "encounterParticipants": [{"typeCode": ep.get("typeCode"), "time": effective_time(ep), "assignedEntity": assigned_entity(find(ep, "h:assignedEntity"), nar)} for ep in findall(enc, "h:encounterParticipant")],
            "location": {"ids": ids(loc), "code": code_dict(find(loc, "h:code")), "name": text(find(loc, "h:location/h:name")),
                         "addr": addr_dict(find(loc, "h:location/h:addr")), "serviceProviderOrganization": text(find(loc, "h:serviceProviderOrganization/h:name"))} if loc is not None else None,
        }
    doc_ids = ids(root)
    return {
        "document": {"id": doc_ids, "templateIds": template_ids(root), "code": code_dict(find(root, "h:code")),
                     "title": text(find(root, "h:title")), "effectiveTime": hl7_ts(attr(find(root, "h:effectiveTime"), "value")),
                     "effectiveDate": ts_date(attr(find(root, "h:effectiveTime"), "value")),
                     "confidentiality": attr(find(root, "h:confidentialityCode"), "code"), "language": attr(find(root, "h:languageCode"), "code")},
        "patient": patient, "authors": authors, "custodian": custodian,
        "documentationOf_serviceEvent": service_events, "componentOf_encompassingEncounter": encompassing,
    }


# ----------------------------------------------------------------------------------------------- parse context
class Ctx:
    """Per-document parsing context: narrative resolver, bulk author stamps, warnings."""

    def __init__(self, root):
        self.root = root
        self.nar = Narrative(root)
        stamps = Counter(t.get("value") for t in root.iter(q("time")) if t.get("value"))
        self.bulk_stamps = {v for v, n in stamps.items() if n >= BULK_STAMP_THRESHOLD}
        self.warnings = []
        self.doc_date = ts_date(attr(find(root, "h:effectiveTime"), "value"))
        # An ISO-8601 timestamp is not a legal HL7 TS.  compact_ts() reads them anyway, but say so: it means the
        # source is non-conformant, and a reader that does not normalise will see only the year.
        iso = sum(1 for et in root.iter(q("effectiveTime"))
                  for v in [et.get("value")] + [c.get("value") for c in et]
                  if v and ISO_TS_RE.match(v.strip()))
        if iso:
            self.warn(f"{iso} timestamp(s) are ISO-8601 ('2025-04-17') where HL7 TS ('20250417') is required; "
                      "read as dates anyway, but the source is non-conformant")

    def warn(self, msg):
        self.warnings.append(msg)

    def recorded(self, el):
        """author/time as (formatted, date, is_bulk_stamp)."""
        raw = author_time_raw(el)
        return hl7_ts(raw), ts_date(raw), (raw in self.bulk_stamps) if raw else False


def narrative_source(el, ctx):
    """(row, panel heading) of the source narrative behind `el`, via the first resolvable #ref in its subtree.

    Recovers the narrative cells that carry no ID of their own and would otherwise be discarded: the allergy
    'Deactivated' column, the medication / problem 'Active' column, narrative-only dates, and the vitals panel
    headings ('07/01/2015 02:00PM PHYSICAL EXAM').
    """
    for r in el.iter(q("reference")):
        v = r.get("value") or ""
        if v.startswith("#") and v.lstrip("#") in ctx.nar.index:
            row, head, cmap = ctx.nar.context_of(v)
            if row or head:
                return row, head, cmap
    return None, None, {}


def _label_candidates(it):
    """Strings that identify an item, for matching it against a narrative row when it has no #reference."""
    out = [it.get("label"), it.get("value_text")]
    for c in (it.get("code"), it.get("value")):
        if isinstance(c, dict):
            out.append(c.get("displayName"))
            out.append(c.get("originalText"))
            for t in c.get("translation") or []:
                out.append((t or {}).get("displayName"))
    return [s for s in out if s]


def _code_values(it):
    """Code values that identify an item, e.g. the SNOMED code printed in a narrative row next to the diagnosis."""
    out = [it.get("problem_code"), it.get("code_value")]
    c = it.get("code")
    if isinstance(c, dict):
        out.append(c.get("code"))
        for t in c.get("translation") or []:
            out.append((t or {}).get("code"))
    return [str(v) for v in out if v]


def _row_match_score(it, row):
    """How strongly a narrative row looks like this item: a code hit is worth 3, a date hit 2, distinctive words 1 each.

    The code is the strongest signal — in a one-row-per-diagnosis table every row repeats the same encounter dates
    and performer, so only the diagnosis code / name tells the rows apart.
    """
    score = 0
    for cv in _code_values(it):
        if len(cv) >= 4 and cv in row:
            score += 3
            break
    d = it.get("dos")
    if d and is_full_date(d):
        y, m, dd = d[:4], d[5:7], d[8:10]
        if f"{m}/{dd}/{y}" in row or f"{int(m)}/{int(dd)}/{y}" in row:
            score += 2
    words = {w for cand in _label_candidates(it) for w in re.findall(r"[A-Za-z]{4,}", cand.lower())}
    if words:
        low = row.lower()
        score += min(sum(1 for w in words if w in low), 3)
    return score


def _match_pass(targets, rows, used):
    """Give each unattached target its best unclaimed row.  Rows are claimed one-to-one, minimum score 2."""
    for it in targets:
        if it.get("nar_row") or it.get("nar_head"):
            continue
        best = (0, None, None, {})
        for rt, head, cmap in rows:
            if rt in used:
                continue
            sc = _row_match_score(it, rt)
            if sc > best[0]:
                best = (sc, rt, head, cmap)
        if best[0] >= 2:
            it["nar_row"], it["nar_head"], it["nar_cells"] = best[1], best[2], best[3]
            used.add(best[1])


def attach_narrative_by_match(items, narrative, ctx):
    """Attach narrative rows to items that their own #reference could not reach.

    Two passes, each claiming rows one-to-one but with independent claim sets:
      1. top-level items — covers entries carrying no <reference> at all (results / social-history organizers);
      2. encounter diagnoses — these are nested inside their encounter item, so they are never in `items`, and in a
         one-row-per-(encounter, diagnosis) narrative each of them owns a row carrying its own ICD-10 code and
         "Diagnosis Note".  The claim set is separate from pass 1 because the row an encounter resolved to via its
         own #reference is also the row of one of its diagnoses, and both are entitled to it.
    """
    rows, starts = [], []
    for tbl in narrative.iter(q("table")):
        for tr in tbl.iter(q("tr")):
            rt, head, cmap = ctx.nar.row_context(tr, tbl)
            if rt:
                cells = [text(c) for c in tr if local(c.tag) in ("td", "th")]
                rows.append((rt, head, cmap))
                starts.append(bool(cells and cells[0]))  # a filled first cell opens a new group
    if not rows:
        return
    _match_pass(items, rows, {it["nar_row"] for it in items if it.get("nar_row")})
    for it in items:
        dxs = it.get("diagnoses") or []
        if not dxs:
            continue
        # Scope this encounter's diagnoses to its own group of rows.  These tables repeat the encounter columns only
        # on the group's first row and leave them blank on continuation rows, so without scoping a diagnosis can
        # match an identically-named diagnosis under a different encounter.
        scope = rows
        if it.get("nar_row") in {r[0] for r in rows}:
            i = next(k for k, r in enumerate(rows) if r[0] == it["nar_row"])
            j = i + 1
            while j < len(rows) and not starts[j]:
                j += 1
            scope = rows[i:j]
        _match_pass(dxs, scope, {d["nar_row"] for d in dxs if d.get("nar_row")})


def unclaimed_narrative_rows(narrative, items, ctx):
    """How many narrative rows of a section no item was matched to.

    This is the test for "carry the source narrative as well as the parsed table": when every row reached an item
    the blob is pure duplication, when some row reached nothing it is the only place that content survives.
    """
    if narrative is None:
        return 0
    # Both ways an item can already hold a row: the row matcher gave it one (nar_row), or the entry's own
    # #reference resolved into the narrative (text).  Counting only the first marked an advance directive whose
    # text came through "#directive1" as unreached, and carried a second undated copy of it.
    claimed = [c for c in ([it.get("nar_row") for it in items] + [it.get("text") for it in items]) if c]

    def covered(rt):
        return any(rt == c or rt in c or c in rt for c in claimed)

    rows = set()
    for tbl in narrative.iter(q("table")):
        for tr in tbl.iter(q("tr")):
            rt = ctx.nar.row_context(tr, tbl)[0]
            if rt:
                rows.add(rt)
    if not rows:  # no table at all -- the narrative is one blob, claimed only if an item quotes it
        blob = text(narrative)
        return 0 if not blob or covered(blob) else 1
    return sum(1 for rt in rows if not covered(rt))


NAR_CELL_TAGS = ("td", "th", "content", "paragraph", "item", "caption")
NAR_MAX = 4000  # recovered narrative runs long (lab interpretation paragraphs); do not clip it to a label-sized cell
_TRIVIAL_CELL = re.compile(r"^[\d/:\-.,%<>= ]*$")


def base_item(kind, el, ctx, label, code=None):
    eff = effective_time(el)
    rec_fmt, rec_date, bulk = ctx.recorded(el)
    mood = el.get("moodCode") or "EVN"
    nar_row, nar_head, nar_cells = narrative_source(el, ctx)
    return {
        "kind": kind, "_el": el, "id": ids(el), "moodCode": mood, "planned": mood in PLANNED_MOODS,
        "label": label, "code": code, "status": attr(find(el, "h:statusCode"), "code"),
        "negated": el.get("negationInd") == "true", "effectiveTime": eff,
        "recorded": rec_fmt, "recorded_date": rec_date, "recorded_is_import_stamp": bulk,
        "dos": None, "dos_source": None, "dos_meaning": None, "flags": [], "text": ctx.nar.text_of(el),
        "nar_row": nar_row, "nar_head": nar_head, "nar_cells": nar_cells,
    }


def set_dos(it, d, source, meaning):
    it["dos"], it["dos_source"], it["dos_meaning"] = d, source, meaning
    if d and not is_full_date(d):
        it["flags"].append(f"partial date {d}")


def label_of(el, ctx, *extra):
    """Label fallback chain: code -> value -> narrative text -> extras -> uncoded marker."""
    c = code_dict(find(el, "h:code"), ctx.nar)
    v = find(el, "h:value")
    cands = [code_label(c)]
    if v is not None and (v.get(XSI_TYPE) or "").split(":")[-1] in ("CD", "CE", "CO", "CV"):
        cands.append(code_label(code_dict(v, ctx.nar)))
    cands.append(ctx.nar.text_of(el))
    cands.extend(extra)
    lab = first(*cands)
    if not lab:
        nf = attr(find(el, "h:code"), "nullFlavor") or attr(v, "nullFlavor")
        lab = f"(uncoded {local(el.tag)}" + (f", nullFlavor={nf})" if nf else ")")
    return lab, c


# ----------------------------------------------------------------------------------------------- dedicated parsers
# Every parser receives the statement elements of a section (entry children, plus fallbacks for odd nesting), returns items,
# and adds every element it handled to `consumed` (by id) so the generic parser can pick up the rest.

def statements_of(sec):
    """(entry, statement) pairs for a section: every element child of <entry> that is a clinical statement."""
    out = []
    for entry in findall(sec, "h:entry"):
        for child in entry:
            if local(child.tag) in ("act", "observation", "organizer", "procedure", "substanceAdministration", "encounter", "supply", "regionOfInterest"):
                out.append((entry, child))
    return out


def problem_obs_item(obs, ctx, concern=None):
    v = code_dict(find(obs, "h:value"), ctx.nar)
    lab = code_label(v) or ctx.nar.text_of(obs) or (ctx.nar.text_of(concern) if concern is not None else None)
    it = base_item("problem", obs, ctx, lab or "(uncoded problem)", v)
    code, system = code_only(v)
    it.update({"problem_code": code, "problem_system": system, "value": v})
    d, src, meaning = pick_dos(it["effectiveTime"], "onset", "onset")
    if not d and concern is not None:
        ce = effective_time(concern)
        d, src, _ = pick_dos(ce, "concern", "concern")
        src = ("concernAct." + src) if src else None
        meaning = "concern opened"
    set_dos(it, d, src, meaning)
    it["resolved"] = it["effectiveTime"].get("high") or (effective_time(concern).get("high") if concern is not None else None)
    if concern is not None:
        it["concern"] = {"status": attr(find(concern, "h:statusCode"), "code"), "effectiveTime": effective_time(concern), "id": ids(concern)}
    if not it["label"] or it["label"].startswith("(uncoded"):
        it["flags"].append("no coded problem name in source")
    return it


def parse_problems(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        if local(st.tag) == "act":
            obs_list = findall(st, "h:entryRelationship/h:observation")
            if not obs_list:
                continue
            for obs in obs_list:
                items.append(problem_obs_item(obs, ctx, st))
            consumed.add(id(st))
        elif local(st.tag) == "observation":
            items.append(problem_obs_item(st, ctx))
            consumed.add(id(st))
    return items


def medication_item(sa, ctx, kind="medication"):
    mat = find(sa, "h:consumable/h:manufacturedProduct/h:manufacturedMaterial")
    pc = code_dict(find(mat, "h:code"), ctx.nar)
    pname = text(find(mat, "h:name"))
    lab = first(pname, (pc or {}).get("displayName"), (pc or {}).get("originalText"), code_label(pc), ctx.nar.text_of(sa), "(uncoded medication)")
    it = base_item(kind, sa, ctx, lab, pc)
    code, system = code_only(pc)
    it.update({"product_code": code, "product_system": system, "product_name": pname, "sig": ctx.nar.text_of(sa)})
    dose = find(sa, "h:doseQuantity")
    it["dose"] = fmt_value(dose) if dose is not None and dose.get("value") else None
    it["route"] = code_label(code_dict(find(sa, "h:routeCode")))
    # administration site: approachSiteCode on immunizations ("Structure of left deltoid muscle"), targetSiteCode elsewhere
    it["site"] = code_label(code_dict(find(sa, "h:approachSiteCode"))) or code_label(code_dict(find(sa, "h:targetSiteCode")))
    freq = None
    for et in findall(sa, "h:effectiveTime"):
        per = find(et, "h:period")
        if per is not None and per.get("value"):
            freq = f"every {per.get('value')} {per.get('unit') or ''}".strip()
    it["frequency"] = freq
    eff = it["effectiveTime"]
    it["start"], it["stop"] = eff.get("low") or (eff.get("value") if eff.get("value") else None), eff.get("high")
    # sub-entries
    it["indications"] = [code_label(code_dict(find(o, "h:value"), ctx.nar)) or ctx.nar.text_of(o) for o in findall(sa, "h:entryRelationship[@typeCode='RSON']/h:observation")]
    it["indications"] = [x for x in it["indications"] if x]
    fills = []
    for sup in findall(sa, "h:entryRelationship/h:supply"):
        qty = find(sup, "h:quantity")
        rn = find(sup, "h:repeatNumber")
        seff = effective_time(sup)
        bits = [f"{sup.get('moodCode') or ''}".lower() or None, f"qty {qty.get('value')}" if qty is not None and qty.get("value") else None,
                f"refills {rn.get('value')}" if rn is not None and rn.get("value") is not None else None, fmt_eff(seff) if seff.get("present") else None]
        s_ = " ".join(b for b in bits if b)
        if s_:
            fills.append(s_)
    it["fills"] = fills
    it["instructions"] = [t for t in (ctx.nar.text_of(a) for a in findall(sa, "h:entryRelationship[@typeCode='SUBJ']/h:act")) if t]
    # DOS: start date only.  Stop date is NEVER a DOS.
    d, src, meaning = pick_dos(eff, "administered", "start")
    set_dos(it, d, src, meaning)
    if not d and it["stop"]:
        it["flags"].append("no start date in the source, only a stop date")
        it["stop_date"] = ts_date(eff.get("raw_high"))
    return it


def parse_medications(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        if local(st.tag) == "substanceAdministration":
            items.append(medication_item(st, ctx))
            consumed.add(id(st))
        elif local(st.tag) == "act":
            inner = findall(st, "h:entryRelationship/h:substanceAdministration")
            if inner:
                for sa in inner:
                    items.append(medication_item(sa, ctx))
                consumed.add(id(st))
    return items


def allergy_obs_item(obs, ctx, concern=None):
    allergen = code_dict(find(obs, "h:participant/h:participantRole/h:playingEntity/h:code"), ctx.nar)
    lab = first(code_label(allergen), ctx.nar.text_of(obs), (ctx.nar.text_of(concern) if concern is not None else None), "(uncoded allergen)")
    it = base_item("allergy", obs, ctx, lab, allergen)
    it["type"] = code_label(code_dict(find(obs, "h:value"), ctx.nar))
    reactions, severity, comments = [], None, []
    for er in findall(obs, "h:entryRelationship"):
        o = find(er, "h:observation")
        a = find(er, "h:act")
        if o is not None:
            tid = template_ids(o)
            c = attr(find(o, "h:code"), "code")
            if c == "SEV" or "2.16.840.1.113883.10.20.22.4.8" in tid:
                severity = code_label(code_dict(find(o, "h:value"))) or ctx.nar.text_of(o)
            elif "2.16.840.1.113883.10.20.22.4.9" in tid or er.get("typeCode") == "MFST":
                r = code_label(code_dict(find(o, "h:value"))) or ctx.nar.text_of(o)
                if r:
                    reactions.append(r)
            elif "2.16.840.1.113883.10.20.22.4.28" in tid:
                it["allergy_status"] = code_label(code_dict(find(o, "h:value")))
            else:
                t = ctx.nar.text_of(o) or code_label(code_dict(find(o, "h:value")))
                if t:
                    comments.append(t)
        elif a is not None:
            t = ctx.nar.text_of(a)
            if t:
                comments.append(t)
    if concern is not None:
        for a in findall(concern, "h:entryRelationship/h:act"):
            t = ctx.nar.text_of(a)
            if t:
                comments.append(t)
    it.update({"reactions": reactions, "severity": severity, "comments": comments,
               "concernStatus": attr(find(concern, "h:statusCode"), "code") if concern is not None else None})
    d, src, meaning = pick_dos(it["effectiveTime"], "onset", "onset")
    if not d and concern is not None:
        ce = effective_time(concern)
        d, src, _ = pick_dos(ce, "concern", "concern")
        src = ("concernAct." + src) if src else None
        meaning = "concern opened"
        raw = ce.get("raw_low") or ce.get("raw_value")
        if raw and raw in ctx.bulk_stamps:
            d, src, meaning = None, None, None
            it["flags"].append("concern date is an import stamp; treated as undated")
    set_dos(it, d, src, meaning)
    return it


def parse_allergies(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        if local(st.tag) == "act":
            obs_list = findall(st, "h:entryRelationship/h:observation")
            if not obs_list:
                continue
            for obs in obs_list:
                items.append(allergy_obs_item(obs, ctx, st))
            consumed.add(id(st))
        elif local(st.tag) == "observation":
            items.append(allergy_obs_item(st, ctx))
            consumed.add(id(st))
    return items


def procedure_item(el, ctx):
    lab, c = label_of(el, ctx, f"{local(el.tag)} (not coded)")
    it = base_item("procedure", el, ctx, lab, c)
    it["entryClass"] = local(el.tag)
    it["code_value"], it["code_system"] = code_only(c)
    it["performers"] = [assigned_entity(find(p, "h:assignedEntity"), ctx.nar) for p in findall(el, "h:performer")]
    it["body_site"] = code_label(code_dict(find(el, "h:targetSiteCode")))
    it["value_text"] = fmt_value(find(el, "h:value"), ctx.nar)
    it["reason"] = [code_label(code_dict(find(o, "h:value"), ctx.nar)) or ctx.nar.text_of(o) for o in findall(el, "h:entryRelationship[@typeCode='RSON']/h:observation")]
    d, src, meaning = pick_dos(it["effectiveTime"], "performed", "performed")
    set_dos(it, d, src, meaning)
    return it


def parse_procedures(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        if local(st.tag) in ("procedure", "observation", "act"):
            if local(st.tag) == "act" and NOTE_ACTIVITY_TEMPLATE in template_ids(st):
                items.append(note_item(st, ctx))
            else:
                items.append(procedure_item(st, ctx))
            consumed.add(id(st))
    return items


def component_item(obs, ctx):
    c = code_dict(find(obs, "h:code"), ctx.nar)
    v = find(obs, "h:value")
    rr = find(obs, "h:referenceRange/h:observationRange")
    ref = None
    if rr is not None:
        ref = text(find(rr, "h:text")) or fmt_value(find(rr, "h:value"))
    comments = [t for t in (ctx.nar.text_of(a) for a in findall(obs, "h:entryRelationship/h:act")) if t]
    return {
        "code": c, "label": code_label(c) or ctx.nar.text_of(obs) or "(uncoded component)", "value": fmt_value(v, ctx.nar),
        "value_type": (v.get(XSI_TYPE) or "").split(":")[-1] if v is not None else None,
        "negated": obs.get("negationInd") == "true", "interpretation": code_label(code_dict(find(obs, "h:interpretationCode"))),
        "referenceRange": ref, "effectiveTime": effective_time(obs), "status": attr(find(obs, "h:statusCode"), "code"),
        "comments": comments, "method": code_label(code_dict(find(obs, "h:methodCode"))),
    }


def organizer_item(org, ctx, kind, default_label):
    c = code_dict(find(org, "h:code"), ctx.nar)
    comps = [component_item(o, ctx) for o in findall(org, "h:component/h:observation")]
    # organizers nested in organizers (rare) -> flatten
    for sub in findall(org, "h:component/h:organizer"):
        comps.extend(component_item(o, ctx) for o in findall(sub, "h:component/h:observation"))
    lab = code_label(c) or ctx.nar.text_of(org) or default_label
    it = base_item(kind, org, ctx, lab, c)
    it["components"] = comps
    eff = it["effectiveTime"]
    org_d, org_src, _ = pick_dos(eff, "collected", "collected")
    comp_dates = sorted({d for d in (pick_dos(cp["effectiveTime"], "x", "x")[0] for cp in comps) if d})
    full_comp = [d for d in comp_dates if is_full_date(d)]
    d, src, meaning = org_d, ("organizer." + org_src) if org_src else None, "collected"
    if full_comp:
        earliest = min(full_comp)
        if not (org_d and is_full_date(org_d)):
            d, src, meaning = earliest, "component.effectiveTime", "resulted"
        else:
            gap = days_between(org_d, earliest)
            if org_d > earliest and gap is not None and gap > ORGANIZER_STAMP_DAYS:
                it["flags"].append(f"organizer effectiveTime {org_d} is {gap} days after the result date(s) — treated as an import stamp; grouped by result date {earliest}")
                d, src, meaning = earliest, "component.effectiveTime", "resulted"
    set_dos(it, d, src, meaning)
    it["component_dates"] = comp_dates
    return it


def note_item(act, ctx):
    c = code_dict(find(act, "h:code"), ctx.nar)
    typ = None
    if c:
        typ = (c.get("translation") or [{}])[0].get("displayName") if c.get("translation") else None
        typ = typ or c.get("displayName")
    it = base_item("note", act, ctx, typ or "Note", c)
    it["note_type"] = typ or "Note"
    it["author"] = author_of(act, ctx.nar)
    d, src, meaning = pick_dos(it["effectiveTime"], "written", "written")
    if not d and it["recorded_date"] and not it["recorded_is_import_stamp"]:
        d, src, meaning = it["recorded_date"], "author/time", "recorded"
    set_dos(it, d, src, meaning)
    return it


def parse_results(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        tag = local(st.tag)
        if tag == "organizer":
            items.append(organizer_item(st, ctx, "result", "Result panel"))
        elif tag == "act" and NOTE_ACTIVITY_TEMPLATE in template_ids(st):
            it = note_item(st, ctx)
            it["kind"] = "result_note"
            items.append(it)
        elif tag == "observation":
            # a result observation outside an organizer: wrap as a one-component panel
            it = organizer_item(st, ctx, "result", "Result")
            it["components"] = [component_item(st, ctx)]
            d, src, meaning = pick_dos(it["effectiveTime"], "resulted", "resulted")
            set_dos(it, d, src, meaning)
            items.append(it)
        else:
            continue
        consumed.add(id(st))
    return items


def parse_vitals(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        tag = local(st.tag)
        if tag == "organizer":
            it = organizer_item(st, ctx, "vitals", "Vital signs")
            it["label"] = "Vital signs"
            items.append(it)
        elif tag == "observation":
            it = organizer_item(st, ctx, "vitals", "Vital signs")
            it["label"] = "Vital signs"
            it["components"] = [component_item(st, ctx)]
            items.append(it)
        else:
            continue
        consumed.add(id(st))
    return items


def parse_immunizations(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        if local(st.tag) != "substanceAdministration":
            continue
        it = medication_item(st, ctx, kind="immunization")
        mat = find(st, "h:consumable/h:manufacturedProduct/h:manufacturedMaterial")
        pc = it["code"] or {}
        cvx = pc.get("code") if (pc.get("codeSystem") == "2.16.840.1.113883.12.292" or (pc.get("codeSystemName") or "").upper() == "CVX") else None
        rx = next((t.get("code") for t in pc.get("translation") or [] if (t.get("codeSystem") == "2.16.840.1.113883.6.88" or (t.get("codeSystemName") or "").upper() == "RXNORM")), None)
        it.update({"cvx": cvx, "rxnorm": rx, "lot": text(find(mat, "h:lotNumberText")),
                   "manufacturer": text(find(st, "h:consumable/h:manufacturedProduct/h:manufacturerOrganization/h:name"))})
        # short product label: name > displayName > bracketed brand in RxNorm string > translation display
        lab = first(it["product_name"], pc.get("displayName"), pc.get("originalText"), ctx.nar.text_of(st))
        if not lab:
            tdn = next((t.get("displayName") for t in pc.get("translation") or [] if t.get("displayName")), None)
            if tdn:
                m = re.search(r"\[([^\]]+)\]\s*$", tdn)
                lab = m.group(1) if m else tdn
        it["label"] = lab or "(uncoded vaccine)"
        it["refusal_reason"] = next((code_label(code_dict(find(o, "h:value"))) for o in findall(st, "h:entryRelationship[@typeCode='RSON']/h:observation")), None)
        d, src, meaning = pick_dos(it["effectiveTime"], "administered", "administered")
        set_dos(it, d, src, meaning)
        items.append(it)
        consumed.add(id(st))
    return items


def social_item(obs, ctx):
    lab, c = label_of(obs, ctx)
    it = base_item("social_history", obs, ctx, code_label(c) or lab, c)
    it["value_text"] = fmt_value(find(obs, "h:value"), ctx.nar) or ctx.nar.text_of(obs)
    d, src, meaning = pick_dos(it["effectiveTime"], "observed", "start")
    raw = it["effectiveTime"].get("raw_value") or it["effectiveTime"].get("raw_low")
    if d and raw and (raw in ctx.bulk_stamps or d == ctx.doc_date):
        # social-history facts stamped with the document date are an 'as of this export' snapshot, not a service date
        it["flags"].append(f"date {d} is the document-generation/import stamp; treated as undated")
        d, src, meaning = None, None, None
    if not d and it["recorded_date"] and not it["recorded_is_import_stamp"]:
        d, src, meaning = it["recorded_date"], "author/time", "recorded"
    set_dos(it, d, src, meaning)
    return it


def parse_social(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        tag = local(st.tag)
        if tag == "observation":
            items.append(social_item(st, ctx))
        elif tag == "organizer":
            for o in findall(st, "h:component/h:observation"):
                items.append(social_item(o, ctx))
        elif tag == "act":
            inner = findall(st, "h:entryRelationship/h:observation")
            if not inner:
                continue
            for o in inner:
                items.append(social_item(o, ctx))
        else:
            continue
        consumed.add(id(st))
    return items


def parse_notes(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        if local(st.tag) in ("act", "observation"):
            items.append(note_item(st, ctx))
            consumed.add(id(st))
    return items


def encounter_item(enc, ctx):
    c = code_dict(find(enc, "h:code"), ctx.nar)
    lab = code_label(c) or ctx.nar.text_of(enc) or "Visit (type not coded)"
    it = base_item("encounter", enc, ctx, lab, c)
    it["performers"] = [assigned_entity(find(p, "h:assignedEntity"), ctx.nar) for p in findall(enc, "h:performer")]
    locs = []
    for pr in findall(enc, "h:participant[@typeCode='LOC']/h:participantRole"):
        locs.append({"code": code_dict(find(pr, "h:code")), "name": text(find(pr, "h:playingEntity/h:name")), "addr": addr_dict(find(pr, "h:addr")), "telecom": telecoms(pr)})
    dxs, reasons, notes = [], [], []
    for er in findall(enc, "h:entryRelationship"):
        for act in findall(er, "h:act"):
            if NOTE_ACTIVITY_TEMPLATE in template_ids(act):
                notes.append(note_item(act, ctx))
                continue
            inner = findall(act, "h:entryRelationship/h:observation")
            if inner:
                for obs in inner:
                    dxs.append(problem_obs_item(obs, ctx))
            elif er.get("typeCode") == "RSON":
                t = ctx.nar.text_of(act) or code_label(code_dict(find(act, "h:code"), ctx.nar))
                if t:
                    reasons.append(t)
        for obs in findall(er, "h:observation"):
            if er.get("typeCode") == "RSON" and not findall(obs, "h:value"):
                t = ctx.nar.text_of(obs) or code_label(code_dict(find(obs, "h:code"), ctx.nar))
                if t:
                    reasons.append(t)
            else:
                dxs.append(problem_obs_item(obs, ctx))
    dd = find(enc, "sdtc:dischargeDispositionCode")
    if dd is None:
        dd = find(enc, "h:dischargeDispositionCode")
    eff = it["effectiveTime"]
    d, src, meaning = pick_dos(eff, "visit", "visit")
    set_dos(it, d, src, meaning)
    it.update({
        "type": lab, "locations": locs, "location_name": locs[0]["name"] if locs else None,
        "pos_code": (locs[0]["code"] or {}).get("code") if locs and locs[0].get("code") else None,
        "diagnoses": dxs, "reasons": reasons, "notes": notes, "dischargeDisposition": code_label(code_dict(dd)),
        "end_date": ts_date(eff.get("raw_high")),
    })
    if it["end_date"] and d and is_full_date(it["end_date"]) and it["end_date"] != d:
        it["flags"].append(f"multi-day visit {d} → {it['end_date']}")
    return it


def parse_encounters(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        tag = local(st.tag)
        if tag == "encounter":
            items.append(encounter_item(st, ctx))
        elif tag == "act":
            inner = findall(st, "h:entryRelationship/h:encounter")
            if not inner:
                continue
            for e in inner:
                items.append(encounter_item(e, ctx))
        else:
            continue
        consumed.add(id(st))
    return items


def plan_item(el, ctx):
    tag = local(el.tag)
    if tag == "substanceAdministration":
        it = medication_item(el, ctx, kind="plan")
    elif tag == "encounter":
        it = encounter_item(el, ctx)
        it["kind"] = "plan"
    else:
        lab, c = label_of(el, ctx)
        it = base_item("plan", el, ctx, lab, c)
        it["value_text"] = fmt_value(find(el, "h:value"), ctx.nar)
        d, src, meaning = pick_dos(it["effectiveTime"], "planned", "planned")
        set_dos(it, d, src, meaning)
    it["entryClass"] = tag
    it["planned"] = it["planned"] or (it["moodCode"] in PLANNED_MOODS)
    if it["planned"]:
        it["dos_meaning"] = "planned / ordered" if it["dos"] else None
    it["code_value"], it["code_system"] = code_only(it["code"])
    it["instructions"] = it.get("instructions") or [t for t in (ctx.nar.text_of(a) for a in findall(el, "h:entryRelationship/h:act")) if t]
    if not it["dos"] and it["recorded_date"] and not it["recorded_is_import_stamp"]:
        set_dos(it, it["recorded_date"], "author/time", "recorded")
    return it


def parse_plan(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        items.append(plan_item(st, ctx))
        consumed.add(id(st))
    return items


# ---------------------------------------------------------------------------------------- Reason for Referral
# The Patient Referral Act carries only what the referral is FOR (its code) and the indication that justifies it.
# Who referred, who is being referred to, and which encounter the referral came out of are narrative-only in every
# chart seen so far -- the act has no author, no performer and no participant.  So the structured fields are read
# from the act and the rest is mined back out of the section narrative once the row has been matched to the item.
REFERRAL_NAR_KEYS = (
    "Referring Physician", "Referring Provider", "Referring Doctor", "Referred By", "Referred From",
    "Referred To", "Referral To", "Referred To Provider", "Provider Name", "Organization Details",
    "Encounter Date", "Referral Date", "Order Date", "Submit Date", "Priority", "Reason for Referral", "Reason",
)
_REFERRAL_KEY_CANON = {k.lower(): k for k in REFERRAL_NAR_KEYS}
# No left boundary: these rows separate their fields with <br/>, and text() drops the tag without leaving a space,
# so the flattened row reads "…bilateral earsReferring Physician: …".  Requiring whitespace or a pipe before the
# label found nothing in exactly the charts this exists for.  Longest label first so "Reason for Referral" is not
# matched as "Reason", and the labels are specific enough that matching mid-word costs at most a stray prefix.
_REFERRAL_KEY_RE = re.compile(r"(" + "|".join(re.escape(k) for k in
                                              sorted(REFERRAL_NAR_KEYS, key=len, reverse=True)) + r")\s*:\s*", re.I)
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def us_date(s):
    """'MM/DD/YYYY' (optionally followed by a time) -> 'YYYY-MM-DD'.  Narrative dates are written this way."""
    m = _US_DATE.search(s or "")
    if not m:
        return None
    mo, d, y = m.groups()
    try:
        _date(int(y), int(mo), int(d))
    except ValueError:
        return None
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def labelled_fields(row, key_re, canon):
    """Split a flattened narrative row into (leading free text, {canonical label: value}).

    Longest label wins (sorted by length in the pattern) so "Referred To Provider" is not matched as
    "Referred To" with a stray "Provider" left in the value.
    """
    if not row:
        return None, {}
    ms = list(key_re.finditer(row))
    if not ms:
        return row.strip(" |") or None, {}
    out = {}
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(row)
        val = row[m.end():end].strip(" |")
        if val:
            out.setdefault(canon[m.group(1).lower()], val)
    return row[:ms[0].start()].strip(" |") or None, out


def referral_item(el, ctx):
    """One statement from a Reason for Referral section, normally a Patient Referral Act (…22.4.140).

    The act is moodCode INT by definition, so it is always 'planned'; its date is when the referral was placed,
    which is a real event on the timeline and not a scheduled future service.  dos_meaning says so rather than
    reusing the plan bucket's 'planned / ordered'.
    """
    lab, c = label_of(el, ctx, "Referral (not coded)")
    it = base_item("referral", el, ctx, lab, c)
    it["entryClass"] = local(el.tag)
    it["code_value"], it["code_system"] = code_only(c)
    it["is_referral_act"] = REFERRAL_ACT_TEMPLATE in template_ids(el)
    it["priority"] = code_label(code_dict(find(el, "h:priorityCode"), ctx.nar))
    # Indication V3 (…22.4.19): the finding that justifies the referral.  Its <code> is only the "Clinical finding"
    # wrapper -- the finding itself is in <value> -- so the value is read first and the wrapper is the last resort.
    inds = []
    for o in findall(el, "h:entryRelationship[@typeCode='RSON']/h:observation"):
        inds.append(first(code_label(code_dict(find(o, "h:value"), ctx.nar)), ctx.nar.text_of(o),
                          code_label(code_dict(find(o, "h:code"), ctx.nar))))
    it["indications"] = [x for x in inds if x]
    it["referred_by"] = author_of(el, ctx.nar)
    it["referred_to"] = [x for x in (assigned_entity(find(p, "h:assignedEntity"), ctx.nar) for p in findall(el, "h:performer")) if x]
    for prole in findall(el, "h:participant/h:participantRole"):
        pr = participant_role(prole, ctx.nar)
        if (pr.get("name") or {}).get("full") or org_name(pr) or (pr.get("addr") or {}).get("oneLine"):
            it["referred_to"].append(pr)
    # Anything hung off the act that is not the indication -- instructions, a scheduled encounter, a note.
    details = []
    for er in findall(el, "h:entryRelationship"):
        if er.get("typeCode") == "RSON":
            continue
        for o in er:
            nl, nc = label_of(o, ctx)
            nv = fmt_value(find(o, "h:value"), ctx.nar)
            details.append(f"{code_label(nc) or nl}" + (f": {nv}" if nv else ""))
    it["details"] = details
    d, src, meaning = pick_dos(it["effectiveTime"], "referred", "referred")
    set_dos(it, d, src, meaning)
    if not it["dos"] and it["recorded_date"] and not it["recorded_is_import_stamp"]:
        set_dos(it, it["recorded_date"], "author/time", "recorded")
    return it


def parse_referrals(stmts, ctx, consumed):
    """Acts are referrals; anything else in the section is left for the generic parser rather than mislabelled."""
    items = []
    for entry, st in stmts:
        if local(st.tag) == "act":
            items.append(referral_item(st, ctx))
            consumed.add(id(st))
    return items


def referral_narrative_fields(items, ctx):
    """Mine the narrative row each referral was matched to, and date the referral from it if the act had no date.

    Called after attach_narrative_by_match(), which is what gives a referral act its row: the act carries no
    <reference> into the narrative, so at parse time nar_row is still empty.
    """
    for it in items:
        if it["kind"] != "referral":
            continue
        lead, kv = labelled_fields(it.get("nar_row"), _REFERRAL_KEY_RE, _REFERRAL_KEY_CANON)
        it["referral_text"] = lead
        it["nar_fields"] = kv or None
        it["referred_by_text"] = first(kv.get("Referring Physician"), kv.get("Referring Provider"),
                                       kv.get("Referring Doctor"), kv.get("Referred By"), kv.get("Referred From"))
        it["referred_to_text"] = first(kv.get("Referred To Provider"), kv.get("Referred To"), kv.get("Referral To"),
                                       kv.get("Provider Name"), kv.get("Organization Details"))
        it["encounter_date"] = first(kv.get("Encounter Date"), kv.get("Referral Date"), kv.get("Order Date"))
        it["priority"] = it.get("priority") or kv.get("Priority")
        if not it["indications"] and kv.get("Reason for Referral"):
            it["indications"] = [kv["Reason for Referral"]]
        if not it["dos"]:
            d = us_date(it["encounter_date"])
            if d:
                set_dos(it, d, "narrative Encounter Date", "referred")
                ctx.warn(f"referral '{trunc(it['label'], 60)}' carried no date in its entry; dated {d} from the "
                         "narrative 'Encounter Date' instead")


# ---------------------------------------------------------------------------------------------------- payers
# Coverage Activity (…22.4.60) is a wrapper: one act per payment source, each holding one or more Policy
# Activities (…22.4.61) in sequence order.  The policy is what carries the money -- the payer organisation, the
# group and member numbers, the guarantor and the covered party -- so one item is emitted per POLICY, not per
# coverage act, and a coverage act with no policy still emits one item so the entry is never silently empty.
POLICY_ACTIVITY_TEMPLATE = "2.16.840.1.113883.10.20.22.4.61"
PAYER_PERFORMER_TEMPLATE = "2.16.840.1.113883.10.20.22.4.87"
GUARANTOR_PERFORMER_TEMPLATE = "2.16.840.1.113883.10.20.22.4.88"


PAYERS_SECTION_CODE = "48768-6"
_OID_LIKE = re.compile(r"^[\d.]+$")


def _performer_by_template(el, template):
    """The assignedEntity of the performer carrying `template`."""
    for p in findall(el, "h:performer"):
        if template in template_ids(p):
            return find(p, "h:assignedEntity")
    return None


def _payer_entity(pol):
    """The payer's assignedEntity.

    C-CDA marks it with the Payer Performer template (…22.4.87), but plenty of charts write a plain <performer>,
    sometimes nested inside a subscriber observation rather than sitting on the policy itself.  So: the template
    when it is there, otherwise any performer that names an organisation and is not the guarantor.
    """
    ae = _performer_by_template(pol, PAYER_PERFORMER_TEMPLATE)
    if ae is not None:
        return ae
    for p in pol.iter(q("performer")):
        if GUARANTOR_PERFORMER_TEMPLATE in template_ids(p):
            continue
        cand = find(p, "h:assignedEntity")
        if cand is not None and text(find(cand, "h:representedOrganization/h:name")):
            return cand
    return None


def non_oid_id(el):
    """An id that is a human identifier rather than a namespace: @extension, else a @root that is neither a dotted
    OID nor a UUID.  Charts that hang the subscriber off a COMP observation put the member number in @root."""
    for i in ids(el):
        if i.get("extension"):
            return i["extension"]
        r = i.get("root")
        if r and not _OID_LIKE.match(r) and "-" not in r:
            return r
    return None


def policy_item(pol, ctx, coverage=None, sequence=None):
    lab, c = label_of(pol, ctx, "Insurance policy (not coded)")
    it = base_item("payer", pol, ctx, lab, c)
    it["entryClass"] = local(pol.tag)
    it["code_value"], it["code_system"] = code_only(c)
    # The payer's own code is often nullFlavor with the real value in <translation> (x12N-1336), so the plan type
    # is read from whichever of the two actually carries a displayName.
    tr = find(pol, "h:code/h:translation")
    # When there is no Policy Activity the coverage act stands in for it, and its code is the Payers SECTION code
    # -- "Payers [LOINC 48768-6]" is the name of the section, not the name of a plan, so it is not a plan type.
    own_code_is_section = (c or {}).get("code") == PAYERS_SECTION_CODE
    it["plan_type"] = None if own_code_is_section and tr is None else first(
        None if own_code_is_section else code_label(c), code_label(code_dict(tr, ctx.nar)))
    it["plan_type_name"] = first(None if own_code_is_section else (c or {}).get("displayName"), attr(tr, "displayName"))
    it["plan_type_code"] = attr(tr, "code") or (None if own_code_is_section else (c or {}).get("code"))
    it["subscriber_id"] = None  # narrative-only in these charts; filled by payer_narrative_fields()
    # The sequenceNumber is a sibling of the policy inside its own entryRelationship, so it has to be handed in by
    # the caller: reading it off the coverage act finds the FIRST one and stamps every policy with sequence 1.
    it["sequence"] = sequence or attr(find(pol, "h:sequenceNumber"), "value")
    payer_el = _payer_entity(pol)
    payer = assigned_entity(payer_el, ctx.nar)
    guar = assigned_entity(_performer_by_template(pol, GUARANTOR_PERFORMER_TEMPLATE), ctx.nar)
    covered = participant_role(find(pol, "h:participant[@typeCode='COV']/h:participantRole"), ctx.nar)
    holder = participant_role(find(pol, "h:participant[@typeCode='HLD']/h:participantRole"), ctx.nar)
    it["payer"], it["guarantor"], it["covered_party"], it["policy_holder"] = payer, guar, covered, holder
    it["payer_name"] = first(org_name(payer), person_name(payer))
    it["payer_id"] = role_id(payer)
    it["guarantor_name"] = first(person_name(guar), org_name(guar))
    it["member_id"] = role_id(covered)
    it["group_number"] = role_id({"ids": ids(pol)})
    # A representedOrganization may carry two names -- the carrier and the plan ("MEDICARE" / "MCR Medicare").
    org_names = [text(n) for n in findall(find(payer_el, "h:representedOrganization") if payer_el is not None else None, "h:name") if text(n)]
    if len(org_names) > 1 and not it["plan_type_name"]:
        it["plan_type_name"] = org_names[1]
    # Some charts hang the subscriber on a COMP observation (code SUBSC) instead of a Covered Party participant,
    # and put the member number in that observation's id/@root rather than @extension.  ElementTree's XPath subset
    # has no nested-path predicate, so the SUBSC test is a loop rather than "observation[h:code/@code='SUBSC']".
    if not it["member_id"]:
        for o in findall(pol, "h:entryRelationship/h:observation"):
            if attr(find(o, "h:code"), "code") == "SUBSC":
                it["member_id"] = non_oid_id(o)
                break
    it["relationship"] = code_label(code_dict(find(pol, "h:participant[@typeCode='COV']/h:participantRole/h:code"), ctx.nar))
    it["policy_holder_name"] = first(person_name(holder), org_name(holder))
    if it["payer_name"]:
        it["label"] = it["payer_name"] + (f" — {it['plan_type_name']}" if it["plan_type_name"] else "")
    # A policy usually has no date of its own; the coverage act's effectiveTime is when the coverage applies.
    # coverage_eff is what the table prints, so a policy dated only by its parent does not read "n/a" in a
    # section that is filed under that very date.
    it["coverage_eff"] = it["effectiveTime"] if it["effectiveTime"].get("present") else (
        effective_time(coverage) if coverage is not None else it["effectiveTime"])
    d, src, meaning = pick_dos(it["effectiveTime"], "coverage", "coverage from")
    if not d and coverage is not None:
        d, src, meaning = pick_dos(effective_time(coverage), "coverage", "coverage from")
        src = ("coverageActivity." + src) if src else None
    set_dos(it, d, src, meaning)
    return it


# Narrative column header -> item field.  The Policy Activity names the payer organisation but leaves the
# guarantor and policy-holder roles with a code and an address and no name at all, so those names exist only in
# the narrative table.  Structured values are never overwritten; these only fill what the entry left empty.
PAYER_NAR_KEYS = {
    "guarantor_name": ("Guarantor Name", "Guarantor"),
    "policy_holder_name": ("Policy Holder", "Policy Holder Name", "Subscriber Name"),
    "subscriber_id": ("Subscriber Identifier", "Subscriber ID"),
    "member_id": ("Covered Member ID", "Member ID"),
    "group_number": ("Group Identifier", "Group Number"),
    "payer_id": ("Payer Identifier", "Payer ID"),
    "payer_name": ("Insurance Name", "Payer Name"),
    "sequence": ("Sequence",),
}


def payer_narrative_fields(items):
    for it in items:
        if it["kind"] != "payer":
            continue
        cells = {k.strip().lower(): v for k, v in (it.get("nar_cells") or {}).items() if v}
        if not cells:
            continue
        for field, labels in PAYER_NAR_KEYS.items():
            if it.get(field):
                continue
            for lb in labels:
                if cells.get(lb.lower()):
                    it[field] = cells[lb.lower()]
                    break
        if it["payer_name"] and it["label"].startswith("("):
            it["label"] = it["payer_name"] + (f" — {it['plan_type_name']}" if it.get("plan_type_name") else "")


def parse_payers(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        if local(st.tag) != "act":
            continue
        found = False
        for er in findall(st, "h:entryRelationship"):
            seq = attr(find(er, "h:sequenceNumber"), "value")
            for pol in er:
                if local(pol.tag) == "act" and POLICY_ACTIVITY_TEMPLATE in template_ids(pol):
                    items.append(policy_item(pol, ctx, coverage=st, sequence=seq))
                    found = True
        if not found:
            items.append(policy_item(st, ctx))
        consumed.add(id(st))
    return items


# ----------------------------------------------------------------------------------------- advance directives
def advance_directive_item(el, ctx):
    """Advance Directive Observation (…22.4.48).

    The directive itself is usually free text pointing into the narrative ("Y: SEE MOLST 1/07/2020") rather than a
    coded value, so the label chain runs value -> narrative text -> code.  effectiveTime/low is when the directive
    took effect and high is when it lapses; a nullFlavor='NA' high means "no end", which is not an end date.
    """
    v = find(el, "h:value")
    lab = first(code_label(code_dict(v, ctx.nar)), fmt_value(v, ctx.nar), ctx.nar.text_of(el),
                code_label(code_dict(find(el, "h:code"), ctx.nar)))
    it = base_item("advance_directive", el, ctx, lab or "(uncoded advance directive)", code_dict(find(el, "h:code"), ctx.nar))
    it["entryClass"] = local(el.tag)
    it["code_value"], it["code_system"] = code_only(it["code"])
    it["directive_type"] = code_label(code_dict(find(el, "h:code"), ctx.nar))
    it["value_text"] = fmt_value(v, ctx.nar)
    it["verifiers"] = [provider_label(participant_role(p, ctx.nar)) for p in findall(el, "h:participant[@typeCode='VRF']/h:participantRole")]
    it["custodians"] = [provider_label(participant_role(p, ctx.nar)) for p in findall(el, "h:participant[@typeCode='CST']/h:participantRole")]
    it["document_ref"] = first(text(find(el, "h:reference/h:externalDocument/h:text")),
                               attr(find(el, "h:reference/h:externalDocument/h:id"), "extension"))
    # high nullFlavor NA means the directive has no end -- a real fact.  UNK / NI mean the end is simply not
    # recorded, which is not the same thing and must not be printed as "no end date".
    hi_nf = attr(find(el, "h:effectiveTime/h:high"), "nullFlavor")
    it["in_effect_until"] = it["effectiveTime"].get("high")
    it["no_end_date"] = hi_nf == "NA"
    it["end_unknown"] = hi_nf in ("UNK", "NI", "NASK", "ASKU")
    d, src, meaning = pick_dos(it["effectiveTime"], "directive in effect", "directive in effect from")
    set_dos(it, d, src, meaning)
    if not it["dos"] and it["recorded_date"] and not it["recorded_is_import_stamp"]:
        set_dos(it, it["recorded_date"], "author/time", "recorded")
    return it


def parse_advance_directives(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        if local(st.tag) in ("observation", "act", "organizer"):
            items.append(advance_directive_item(st, ctx))
            consumed.add(id(st))
    return items


# --------------------------------------------------------------------------------------------- health concerns
# Codes that name the CONTAINER rather than the concern: the section's own LOINC and the HL7 "Concern" ActClass.
# Neither says what the concern is, so when one of them is the act's code the referenced statement supplies the label.
HEALTH_CONCERN_WRAPPER_CODES = {"75310-3", "CONC"}


def health_concern_item(el, ctx):
    """Health Concern Act (…22.4.132).

    The act is a container: the concern itself is whatever it points at through entryRelationship -- a problem
    observation, an allergy, a result.  Those referenced statements stay where they already live in the document,
    so they are summarised here as text rather than emitted a second time as their own dated items.
    """
    lab, c = label_of(el, ctx, "Health concern (not coded)")
    it = base_item("health_concern", el, ctx, lab, c)
    it["entryClass"] = local(el.tag)
    it["code_value"], it["code_system"] = code_only(c)
    it["concern_status"] = attr(find(el, "h:statusCode"), "code")
    related = []
    for er in findall(el, "h:entryRelationship"):
        for o in er:
            if local(o.tag) not in ("act", "observation", "organizer", "procedure", "substanceAdministration", "encounter", "supply"):
                continue
            nl, nc = label_of(o, ctx)
            nv = first(code_label(code_dict(find(o, "h:value"), ctx.nar)), fmt_value(find(o, "h:value"), ctx.nar))
            related.append(first(nv, code_label(nc), nl))
    it["related"] = [x for x in related if x]
    it["details"] = it["related"]
    it["author_org"] = org_name(author_of(el, ctx.nar))
    if it["related"] and (c or {}).get("code") in (None, *HEALTH_CONCERN_WRAPPER_CODES):
        it["label"] = it["related"][0]
    d, src, meaning = pick_dos(it["effectiveTime"], "concern", "concern raised")
    if not d:
        for o in findall(el, "h:entryRelationship/h:observation"):
            d, src, meaning = pick_dos(effective_time(o), "concern", "concern raised")
            if d:
                src = "nested." + src
                break
    set_dos(it, d, src, meaning)
    if not it["dos"] and it["recorded_date"] and not it["recorded_is_import_stamp"]:
        set_dos(it, it["recorded_date"], "author/time", "recorded")
    return it


def parse_health_concerns(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        if local(st.tag) in ("act", "observation", "organizer"):
            items.append(health_concern_item(st, ctx))
            consumed.add(id(st))
    return items


# --------------------------------------------------------------------------------------------------- care team
CARE_TEAM_ORGANIZER_TEMPLATE = "2.16.840.1.113883.10.20.22.4.500"


def care_team_member_item(el, ctx, team=None, team_name=None):
    """One Care Team Member Act (…22.4.500.1), or the organizer itself when it lists no member."""
    ae = first(assigned_entity(find(el, "h:performer/h:assignedEntity"), ctx.nar),
               participant_role(find(el, "h:participant/h:participantRole"), ctx.nar),
               assigned_entity(find(el, "h:assignedEntity"), ctx.nar))
    role = code_dict(find(el, "h:code"), ctx.nar)
    nm = person_name(ae)
    lab = first(nm, org_name(ae), code_label(role), ctx.nar.text_of(el), "Care team member (not named)")
    it = base_item("care_team", el, ctx, lab, role)
    it["entryClass"] = local(el.tag)
    it["code_value"], it["code_system"] = code_only(role)
    it["team"] = team_name
    it["member"] = ae
    it["member_name"] = nm
    it["member_org"] = org_name(ae)
    it["npi"] = (ae or {}).get("npi")
    it["role"] = code_label(role)
    it["specialty"] = code_label((ae or {}).get("taxonomy")) if ae else None
    it["addr"] = ((ae or {}).get("addr") or {}).get("oneLine") if ae else None
    it["phone"] = "; ".join(t["value"] for t in ((ae or {}).get("telecom") or [])) or None
    # A member act often carries no period of its own; the organizer's is the team's period.  `period` is what the
    # table prints so a member dated only by its team does not read "n/a" under that team's own date.
    it["period"] = it["effectiveTime"] if it["effectiveTime"].get("present") else (
        effective_time(team) if team is not None else it["effectiveTime"])
    d, src, meaning = pick_dos(it["effectiveTime"], "care team", "on the care team from")
    if not d and team is not None:
        d, src, meaning = pick_dos(effective_time(team), "care team", "on the care team from")
        src = ("careTeamOrganizer." + src) if src else None
    set_dos(it, d, src, meaning)
    if not it["dos"] and it["recorded_date"] and not it["recorded_is_import_stamp"]:
        set_dos(it, it["recorded_date"], "author/time", "recorded")
    return it


def parse_care_team(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        if local(st.tag) == "organizer":
            team_name = first(ctx.nar.text_of(st), code_label(code_dict(find(st, "h:code"), ctx.nar)))
            members = [m for c in findall(st, "h:component") for m in c
                       if local(m.tag) in ("act", "observation", "encounter", "procedure")]
            items.extend(care_team_member_item(m, ctx, team=st, team_name=team_name) for m in members)
            if not members:
                items.append(care_team_member_item(st, ctx, team_name=team_name))
            consumed.add(id(st))
        elif local(st.tag) in ("act", "observation"):
            items.append(care_team_member_item(st, ctx))
            consumed.add(id(st))
    return items


def assessment_item(el, ctx):
    """One Assessment section statement, built on the same shape as plan_item().

    The C-CDA Assessment section (2.16.840.1.113883.10.20.22.2.8 / LOINC 51848-0) defines no entry template, so
    a conforming section carries nothing here and narrative_assessment_section_items() does the work instead.
    Vendors do occasionally hang an observation off it anyway, and an entry that exists must not be dropped, so
    this handles that case with the section's own date chain: its effectiveTime, then a nested observation's,
    then author/time when that is not an import stamp.
    """
    lab, c = label_of(el, ctx)
    it = base_item("assessment", el, ctx, lab, c)
    it["value_text"] = fmt_value(find(el, "h:value"), ctx.nar)
    d, src, meaning = pick_dos(it["effectiveTime"], "assessed", "assessed")
    if not d:
        for o in findall(el, "h:entryRelationship/h:observation"):
            d, src, meaning = pick_dos(effective_time(o), "assessed", "assessed")
            if d:
                src = "nested." + src
                break
    set_dos(it, d, src, meaning)
    it["entryClass"] = local(el.tag)
    it["code_value"], it["code_system"] = code_only(it["code"])
    if not it["dos"] and it["recorded_date"] and not it["recorded_is_import_stamp"]:
        set_dos(it, it["recorded_date"], "author/time", "recorded")
    return it


def parse_assessment(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        items.append(assessment_item(st, ctx))
        consumed.add(id(st))
    return items


def screening_item(obs, ctx, kind):
    """Functional / Mental status observations (scores with nested component observations)."""
    lab, c = label_of(obs, ctx)
    it = base_item(kind, obs, ctx, code_label(c) or lab, c)
    it["value_text"] = fmt_value(find(obs, "h:value"), ctx.nar)
    details = []
    for o in findall(obs, "h:entryRelationship/h:observation") + findall(obs, "h:component/h:observation"):
        oc = code_dict(find(o, "h:code"), ctx.nar)
        details.append(f"{code_label(oc) or ctx.nar.text_of(o) or 'item'}: {fmt_value(find(o, 'h:value'), ctx.nar) or 'n/a'}")
    it["details"] = details
    d, src, meaning = pick_dos(it["effectiveTime"], "assessed", "assessed")
    if not d:
        for o in findall(obs, "h:entryRelationship/h:observation") + findall(obs, "h:component/h:observation"):
            d, src, meaning = pick_dos(effective_time(o), "assessed", "assessed")
            if d:
                src = "nested." + src
                break
    set_dos(it, d, src, meaning)
    return it


def parse_screening(kind):
    def _parse(stmts, ctx, consumed):
        items = []
        for entry, st in stmts:
            tag = local(st.tag)
            if tag == "observation":
                items.append(screening_item(st, ctx, kind))
            elif tag == "organizer":
                for o in findall(st, "h:component/h:observation"):
                    items.append(screening_item(o, ctx, kind))
            elif tag == "act":
                inner = findall(st, "h:entryRelationship/h:observation")
                if not inner:
                    continue
                for o in inner:
                    items.append(screening_item(o, ctx, kind))
            else:
                continue
            consumed.add(id(st))
        return items
    return _parse


US_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})")


def _nar_date(cell):
    """'10/25/2025' or '2025-10-25' (optionally with a time after it) -> 'YYYY-MM-DD'."""
    if not cell:
        return None
    m = US_DATE_RE.match(cell)
    if m:
        mo, d, y = m.groups()
        return ts_date(f"{y}{int(mo):02d}{int(d):02d}")
    return ts_date(cell.strip().split()[0]) if cell.strip() else None


def narrative_assessment_items(sec, ctx, kind, title):
    """Build dated items from a screening section's narrative table.

    athenahealth writes functional and mental status as a table -- Date | Assessment | Value | LastModified by |
    Organization Details | LastModified Time -- and wires only the panel rows to entries.  The item-level rows
    ("Feeling down, depressed, or hopeless" / "Several days" on 10/25/2025) are the target of no usable reference:
    Functional Status points its entries at "#disabilityassessment7745227" while the anchor is
    "assessment7745227", and Mental Status references only its seven "#screener..." rows out of 77.

    Carrying the section's <text> verbatim rescued the words but stranded them in one blob at the top of the
    document, which is the opposite of what this tool is for -- every one of those rows states its own date and
    belongs in that date's section.  So they are mined into real items here instead.

    A row whose anchor is already the target of a resolvable reference from an entry is skipped, so the panel
    scores the dedicated parser produced are not duplicated.
    """
    txt = find(sec, "h:text")
    if txt is None:
        return []
    claimed = {(r.get("value") or "").lstrip("#") for e in findall(sec, "h:entry") for r in e.iter(q("reference"))
               if (r.get("value") or "").startswith("#") and (r.get("value") or "").lstrip("#") in ctx.nar.index}
    out = []
    for tbl in txt.iter(q("table")):
        headers = [h.lower() for h in Narrative._header_cells(tbl)]
        if not headers:
            continue
        def col(*names):
            for n in names:
                for i, h in enumerate(headers):
                    if n in h:
                        return i
            return None
        i_date, i_lab = col("date"), col("assessment", "question")
        i_val, i_note = col("value", "answer"), col("note")
        i_by, i_time = col("lastmodified by", "modified by"), col("lastmodified time", "modified time")
        if i_lab is None or i_val is None:
            continue
        for tr in tbl.iter(q("tr")):
            cells = [c for c in tr if local(c.tag) in ("td", "th")]
            if not cells or all(local(c.tag) == "th" for c in cells):
                continue
            vals = [text(c) for c in cells]
            if len(vals) != len(headers):
                continue
            rid = next((e.get("ID") for e in tr.iter() if e.get("ID")), None)
            if rid and rid in claimed:
                continue  # the dedicated parser already rendered this row
            lab = vals[i_lab] if i_lab < len(vals) else None
            val = vals[i_val] if i_val < len(vals) else None
            if not lab and not val:
                continue
            # "Not Available" is athenahealth's empty cell, not a finding
            def real(v):
                return v if v and v.strip().lower() not in ("not available", "n/a", "-") else None
            when = _nar_date(vals[i_date]) if i_date is not None and i_date < len(vals) else None
            recorded = real(vals[i_time]) if i_time is not None and i_time < len(vals) else None
            note = real(vals[i_note]) if i_note is not None and i_note < len(vals) else None
            by = real(vals[i_by]) if i_by is not None and i_by < len(vals) else None
            eff = {"value": when, "low": None, "high": None, "raw_value": when.replace("-", "") if when else None,
                   "raw_low": None, "raw_high": None, "nullFlavor": None, "present": bool(when)}
            it = {
                "kind": kind, "_el": None, "id": [], "moodCode": "EVN", "planned": False,
                "label": lab or "(assessment)", "code": None, "status": None, "negated": False,
                "effectiveTime": eff, "recorded": recorded, "recorded_date": _nar_date(recorded),
                "recorded_is_import_stamp": False, "dos": None, "dos_source": None, "dos_meaning": None,
                "flags": [], "text": None, "nar_row": None, "nar_head": None,
                "nar_cells": {"Note": note} if note else {},
                "value_text": real(val), "details": [f"recorded by {by}"] if by else [],
                "section_title": title, "section_key": kind, "from_narrative": True,
            }
            set_dos(it, when, "narrative table Date column", "assessed")
            if not when:
                it["flags"].append("no date in the narrative row; only a last-modified timestamp")
            out.append(it)
    return out


def _header_index(headers, exact, *contains):
    """Column index by header name: an exact match first, a substring match only as a fallback.

    Substring-only matching is what breaks on the Assessment table.  Its headers are
    "Encounter Id | Assessment Date | Assessment | ...", so a scan for "assessment" hits "Assessment Date"
    first and the label column resolves to the date.  Asking for the exact name up front settles it, and the
    substring pass still covers vendors who spell the column differently.
    """
    low = [h.lower().strip() for h in headers]
    if exact and exact in low:
        return low.index(exact)
    for n in contains:
        for i, h in enumerate(low):
            if n in h:
                return i
    return None


def narrative_assessment_section_items(sec, ctx, title):
    """Build dated items from the Assessment section's narrative table.

    The Assessment section (2.16.840.1.113883.10.20.22.2.8 / LOINC 51848-0) is narrative-only by design -- the
    C-CDA template defines no entry -- so there is no effectiveTime, no entryRelationship and no author/time to
    read a date from.  athenahealth writes it as a table whose every row states its own date:

        Encounter Id | Assessment Date | Assessment | LastModified by | Organization Details | LastModified Time

    Carrying that table verbatim into "Source narratives" left three dated assessments sitting undated at the top
    of a DOS-first document, reachable from no date section, which is the same failure
    narrative_assessment_items() was written to fix for Functional and Mental Status.  That function is not reused
    here: it is gated to those two section keys, it skips any table with no Value/Answer column (this table has
    none -- the finding IS the "Assessment" column), and its substring column scan resolves the label to
    "Assessment Date".  Kept separate so its verified output for those two sections cannot regress.

    A section with no table -- athenahealth's empty state is "<paragraph>No assessment recorded.</paragraph>" --
    yields nothing, and the section is then carried as narrative exactly as before.
    """
    txt = find(sec, "h:text")
    if txt is None:
        return []
    out = []
    for tbl in txt.iter(q("table")):
        headers = Narrative._header_cells(tbl)
        if not headers:
            continue
        i_date = _header_index(headers, "assessment date", "date")
        i_lab = _header_index(headers, "assessment", "assessment", "finding", "description")
        if i_lab == i_date:  # only "Assessment Date" matched; the payload column is spelled some other way
            i_lab = next((i for i, h in enumerate(headers) if i != i_date and "assessment" in h.lower()), None)
        if i_lab is None:
            continue
        i_enc = _header_index(headers, "encounter id", "encounter")
        i_by = _header_index(headers, "lastmodified by", "lastmodified by", "modified by")
        i_org = _header_index(headers, "organization details", "organization")
        i_time = _header_index(headers, "lastmodified time", "lastmodified time", "modified time")

        def cell(vals, i):
            """One cell, with athenahealth's empty markers treated as empty."""
            if i is None or i >= len(vals):
                return None
            v = vals[i]
            return v if v and v.strip().lower() not in ("not available", "n/a", "-") else None

        for tr in tbl.iter(q("tr")):
            cells = [c for c in tr if local(c.tag) in ("td", "th")]
            if not cells or all(local(c.tag) == "th" for c in cells):
                continue
            vals = [text(c) for c in cells]
            if len(vals) != len(headers):
                continue
            lab = cell(vals, i_lab)
            if not lab:
                continue
            when = _nar_date(vals[i_date]) if i_date is not None and i_date < len(vals) else None
            recorded = cell(vals, i_time)
            enc_id, by, org = cell(vals, i_enc), cell(vals, i_by), cell(vals, i_org)
            eff = {"value": when, "low": None, "high": None, "raw_value": when.replace("-", "") if when else None,
                   "raw_low": None, "raw_high": None, "nullFlavor": None, "present": bool(when)}
            details = []
            if enc_id:
                details.append(f"source encounter id {enc_id}")
            if by:
                details.append(f"recorded by {by}")
            if org:
                details.append(org)
            it = {
                "kind": "assessment", "_el": None, "id": [], "moodCode": "EVN", "planned": False,
                "label": lab, "code": None, "status": None, "negated": False,
                "effectiveTime": eff, "recorded": recorded, "recorded_date": _nar_date(recorded),
                "recorded_is_import_stamp": False, "dos": None, "dos_source": None, "dos_meaning": None,
                "flags": [], "text": None, "nar_row": None, "nar_head": None, "nar_cells": {},
                "value_text": None, "details": details, "encounter_id": enc_id, "recorded_by": by,
                "organization": org, "entryClass": None, "code_value": None, "code_system": None,
                "section_title": title, "section_key": "assessment", "from_narrative": True,
            }
            set_dos(it, when, "narrative table Date column", "assessed")
            if not when:
                it["flags"].append("no date in the narrative row; only a last-modified timestamp")
            out.append(it)
    return out


def parse_family_history(stmts, ctx, consumed):
    items = []
    for entry, st in stmts:
        if local(st.tag) != "organizer":
            continue
        rel = code_dict(find(st, "h:subject/h:relatedSubject/h:code"), ctx.nar)
        who = (rel or {}).get("displayName") or code_label(rel) or "relative"
        for o in findall(st, "h:component/h:observation"):
            v = code_dict(find(o, "h:value"), ctx.nar)
            lab = f"{who}: {code_label(v) or ctx.nar.text_of(o) or 'condition not coded'}"
            it = base_item("family_history", o, ctx, lab, v)
            it["relation"] = who
            it["value_text"] = code_label(v)
            death = find(o, "h:entryRelationship/h:observation[h:code/@code='ASSERTION']") if False else None
            it["details"] = [f"{code_label(code_dict(find(x, 'h:code'), ctx.nar)) or 'item'}: {fmt_value(find(x, 'h:value'), ctx.nar) or 'n/a'}" for x in findall(o, "h:entryRelationship/h:observation")]
            d, src, meaning = pick_dos(it["effectiveTime"], "recorded", "recorded")
            raw = it["effectiveTime"].get("raw_value") or it["effectiveTime"].get("raw_low")
            if d and raw and raw in ctx.bulk_stamps:
                it["flags"].append(f"date {d} is an import stamp; treated as undated")
                d, src, meaning = None, None, None
            set_dos(it, d, src, meaning)
            items.append(it)
        consumed.add(id(st))
    return items


def generic_item(el, ctx, section_title):
    """Fallback for any clinical statement anywhere: label, value, dates, nested detail."""
    tag = local(el.tag)
    if tag == "organizer":
        it = organizer_item(el, ctx, "other", section_title)
        it["value_text"] = "; ".join(f"{c['label']}: {c['value'] or 'n/a'}" for c in it["components"]) or None
    elif tag == "substanceAdministration":
        it = medication_item(el, ctx, kind="other")
    elif tag == "encounter":
        it = encounter_item(el, ctx)
        it["kind"] = "other"
    else:
        lab, c = label_of(el, ctx)
        it = base_item("other", el, ctx, lab, c)
        it["value_text"] = fmt_value(find(el, "h:value"), ctx.nar)
        nested = []
        for o in findall(el, "h:entryRelationship/h:observation") + findall(el, "h:entryRelationship/h:act") + findall(el, "h:entryRelationship/h:procedure") + findall(el, "h:entryRelationship/h:substanceAdministration"):
            nl, nc = label_of(o, ctx)
            nv = fmt_value(find(o, "h:value"), ctx.nar)
            nested.append(f"{code_label(nc) or nl}" + (f": {nv}" if nv else ""))
        it["details"] = nested
        d, src, meaning = pick_dos(it["effectiveTime"], "effective", "start")
        if not d:
            for o in findall(el, "h:entryRelationship/h:observation"):
                d, src, meaning = pick_dos(effective_time(o), "effective", "start")
                if d:
                    src = "nested." + src
                    break
        set_dos(it, d, src, meaning)
    it["code_value"], it["code_system"] = code_only(it.get("code"))
    it["section_title"] = section_title
    if it["planned"] and it["dos"]:
        it["dos_meaning"] = "planned / ordered"
    return it


def parse_generic(stmts, ctx, consumed, section_title):
    items = []
    for entry, st in stmts:
        if id(st) in consumed:
            continue
        items.append(generic_item(st, ctx, section_title))
        consumed.add(id(st))
    return items


PARSERS = {
    "problems": parse_problems, "medications": parse_medications, "allergies": parse_allergies, "procedures": parse_procedures,
    "results": parse_results, "vitals": parse_vitals, "immunizations": parse_immunizations, "social_history": parse_social,
    "notes": parse_notes, "encounters": parse_encounters, "plan_of_care": parse_plan,
    "functional_status": parse_screening("functional_status"), "mental_status": parse_screening("mental_status"),
    "family_history": parse_family_history, "assessment": parse_assessment, "referral": parse_referrals,
    "payers": parse_payers, "advance_directives": parse_advance_directives,
    "health_concerns": parse_health_concerns, "care_team": parse_care_team,
}


# Sections whose dedicated parser is newer than the generic handler that used to run them.  The generic handler
# always carried their source narrative; these keep the same guarantee through unclaimed_narrative_rows(), which
# carries the blob only when some narrative row reached no item.  Two of them also mine their narrative for fields
# the entry does not carry at all -- the referring physician, the guarantor and policy-holder names.
NARRATIVE_CHECKED_KEYS = {"referral", "payers", "advance_directives", "health_concerns", "care_team"}
NARRATIVE_ENRICHERS = {
    "referral": referral_narrative_fields,
    "payers": lambda items, ctx: payer_narrative_fields(items),
}


def section_key(sec):
    for t in template_ids(sec):
        k = SECTION_TEMPLATES.get(t)
        if k:
            return k
    return SECTION_CODES.get(attr(find(sec, "h:code"), "code"), "other")


def parse_sections(root, ctx):
    """Visit every section (nested ones too).  Returns section dicts with items, drop counts and narrative."""
    sections = []

    def visit(sec, depth, parent_title):
        key = section_key(sec)
        title = text(find(sec, "h:title")) or code_label(code_dict(find(sec, "h:code"))) or f"Section {len(sections) + 1}"
        if parent_title:
            title = f"{parent_title} › {title}"
        stmts = statements_of(sec)
        consumed = set()
        parser = PARSERS.get(key)
        items = parser(stmts, ctx, consumed) if parser else []
        mined = narrative_assessment_items(sec, ctx, key, title) if key in ("functional_status", "mental_status") else []
        # The Assessment section carries no entry at all (the C-CDA template defines none), so there is no
        # effectiveTime / entryRelationship / author-time to date it by.  Only then is its narrative table read.
        if key == "assessment" and not items:
            mined = narrative_assessment_section_items(sec, ctx, title)
        if mined:
            ctx.warn(f"section '{title}': {len(mined)} assessment row(s) recovered from the narrative table and dated "
                     "individually (no entry referenced them)")
            items += mined
        generic = parse_generic(stmts, ctx, consumed, title)
        if generic and parser:
            ctx.warn(f"section '{title}': {len(generic)} entr{'y' if len(generic) == 1 else 'ies'} not understood by the {key} parser were handled generically")
        if not parser and generic:
            ctx.warn(f"section '{title}' (LOINC {attr(find(sec, 'h:code'), 'code')}) has no dedicated parser; {len(generic)} entries handled generically")
        items += generic
        for it in items:
            it["section_title"] = title
            it["section_key"] = key
        entries = findall(sec, "h:entry")
        stmt_ids = {id(st) for _, st in stmts}
        dropped = len(stmt_ids - consumed)
        entries_without_statement = sum(1 for e in entries if not any(local(c.tag) in ("act", "observation", "organizer", "procedure", "substanceAdministration", "encounter", "supply", "regionOfInterest") for c in e))
        if dropped or entries_without_statement:
            ctx.warn(f"section '{title}': {dropped + entries_without_statement} entries DROPPED")
        narrative = find(sec, "h:text")
        if narrative is not None and items:
            attach_narrative_by_match(items, narrative, ctx)
        # These entries carry no <reference>, so their narrative row only exists after the match pass above.
        nar_unclaimed = 0
        if key in NARRATIVE_CHECKED_KEYS:
            enrich = NARRATIVE_ENRICHERS.get(key)
            if enrich:
                enrich(items, ctx)
            nar_unclaimed = unclaimed_narrative_rows(narrative, items, ctx)
        anchors = sorted({e.get("ID") for e in (narrative.iter() if narrative is not None else []) if e.get("ID")})
        broken = sorted({(r.get("value") or "").lstrip("#") for e in entries for r in e.iter(q("reference"))
                         if (r.get("value") or "").startswith("#") and (r.get("value") or "").lstrip("#") not in ctx.nar.index})
        if broken:
            ctx.warn(f"section '{title}': {len(broken)} narrative reference(s) point at IDs that do not exist in the source: " + ", ".join("#" + b for b in broken[:8]) + (" …" if len(broken) > 8 else ""))
        sections.append({
            "key": key, "title": title, "code": code_dict(find(sec, "h:code")), "templateIds": template_ids(sec), "nullFlavor": sec.get("nullFlavor"),
            "entryCount": len(entries), "items": items, "dropped": dropped + entries_without_statement, "handler": key if parser else "generic",
            "narrative_only": not entries and narrative is not None and text(narrative) is not None,
            "_text": narrative, "_el": sec, "depth": depth, "nar_anchors": anchors, "nar_broken_refs": broken,
            "nar_mined": len(mined), "nar_unclaimed": nar_unclaimed,
        })
        for sub in findall(sec, "h:component/h:section"):
            visit(sub, depth + 1, title)

    for sec in findall(root, "h:component/h:structuredBody/h:component/h:section"):
        visit(sec, 0, None)
    return sections


# ----------------------------------------------------------------------------------------------- grouping
BUCKET_OF_KIND = {
    "encounter": "encounters", "problem": "problems", "medication": "medications", "result": "results", "result_note": "notes",
    "procedure": "procedures", "vitals": "vitals", "immunization": "immunizations", "allergy": "allergies", "social_history": "social_history",
    "note": "notes", "plan": "plan", "functional_status": "functional_status", "mental_status": "mental_status", "family_history": "family_history",
    "assessment": "assessment", "referral": "referrals", "payer": "payers",
    "advance_directive": "advance_directives", "health_concern": "health_concerns", "care_team": "care_team",
    "other": "other",
}
# A Patient Referral Act is moodCode INT by definition, so the planned-item routing below would sweep every
# referral into the generic plan bucket and the Reason for Referral section would vanish as a distinct thing.
# These kinds keep their own bucket even when planned; the date still says when the referral was placed.
KEEP_BUCKET_WHEN_PLANNED = {"referral"}
BUCKET_TITLES = OrderedDict([
    ("problems", ("Problems (by onset)", "11450-4", "Problem list")),
    ("medications", ("Medications (by start date)", "10160-0", "History of Medication use")),
    ("medications_stopped", ("Medications stopped / reconciled on this date (start date unknown)", "10160-0", "History of Medication use")),
    ("results", ("Results", "30954-2", "Relevant diagnostic tests/laboratory data")),
    ("procedures", ("Procedures", "47519-4", "History of Procedures")),
    ("vitals", ("Vital signs", "8716-3", "Vital signs")),
    ("immunizations", ("Immunizations", "11369-6", "History of Immunization")),
    ("allergies", ("Allergies (by onset)", "48765-2", "Allergies and adverse reactions")),
    ("social_history", ("Social history", "29762-2", "Social history")),
    ("functional_status", ("Functional status", "47420-5", "Functional status")),
    ("mental_status", ("Mental status", "10190-7", "Mental status")),
    ("family_history", ("Family history", "10157-6", "Family history")),
    ("notes", ("Notes", "34109-9", "Note")),
    ("assessment", ("Assessments", "51848-0", "Assessments")),
    ("referrals", ("Referrals (reason for referral)", "42349-1", "Reason for referral")),
    ("health_concerns", ("Health concerns", "75310-3", "Health concerns")),
    ("care_team", ("Care team", "85847-2", "Care team")),
    ("advance_directives", ("Advance directives", "42348-3", "Advance directives")),
    ("payers", ("Payers / insurance coverage", "48768-6", "Payment sources")),
    ("plan", ("Planned / ordered (not performed)", "18776-5", "Plan of care note")),
    ("other", ("Other entries", None, None)),
])


def new_group(dos, kind):
    return {"dos": dos, "kind": kind, "encounters": [], "diagnoses": [], "buckets": defaultdict(list)}


def group_by_dos(sections, header):
    """Two-tier grouping.  kind: 'visit' (has a performed encounter), 'no_visit' (dated items only), 'planned'."""
    all_items = [it for s in sections for it in s["items"]]
    encounters = [it for it in all_items if it["kind"] == "encounter" and not it["planned"] and it["dos"]]
    # encompassingEncounter (header) counts as a visit too
    ee = header.get("componentOf_encompassingEncounter")
    if ee and ee.get("effectiveTime"):
        d, _, _ = pick_dos(ee["effectiveTime"], "visit", "visit")
        if d and d not in {e["dos"] for e in encounters}:
            encounters.append({"kind": "encounter", "planned": False, "dos": d, "label": code_label(ee.get("code")) or "Visit (document header)", "type": code_label(ee.get("code")) or "Visit (document header)",
                               "effectiveTime": ee["effectiveTime"], "performers": [ee.get("responsibleParty")] if ee.get("responsibleParty") else [], "locations": [ee["location"]] if ee.get("location") else [],
                               "location_name": (ee.get("location") or {}).get("name"), "pos_code": ((ee.get("location") or {}).get("code") or {}).get("code"), "diagnoses": [], "reasons": [], "notes": [],
                               "dischargeDisposition": code_label(ee.get("dischargeDisposition")), "end_date": ts_date(ee["effectiveTime"].get("raw_high")), "flags": ["from the document header"],
                               "status": None, "id": ee.get("ids"), "_el": None, "recorded": None, "dos_source": "encompassingEncounter", "dos_meaning": "visit", "section_title": "Document header", "moodCode": "EVN", "negated": False, "text": None, "code": ee.get("code")})
    visit_dates = {e["dos"] for e in encounters if is_full_date(e["dos"])}
    windows = [(e["dos"], e["end_date"]) for e in encounters if e.get("end_date") and is_full_date(e["dos"]) and is_full_date(e["end_date"]) and e["end_date"] != e["dos"]]

    def window_start(d):
        for lo, hi in windows:
            if lo < d <= hi:
                return lo
        return None

    groups = {}
    undated = defaultdict(list)

    def grp(dos, kind):
        g = groups.get(dos)
        if g is None:
            g = groups[dos] = new_group(dos, kind)
        elif kind == "visit":
            g["kind"] = "visit"
        return g

    for e in encounters:
        g = grp(e["dos"], "visit")
        g["encounters"].append(e)
        g["diagnoses"].extend(e.get("diagnoses") or [])
        for n in e.get("notes") or []:
            g["buckets"]["notes"].append(n)
    for it in all_items:
        if it["kind"] == "encounter" and not it["planned"]:
            continue
        bucket = BUCKET_OF_KIND.get(it["kind"], "other")
        d = it["dos"]
        if it["planned"]:
            pb = bucket if it["kind"] in KEEP_BUCKET_WHEN_PLANNED else "plan"
            if d and d in visit_dates:
                grp(d, "visit")["buckets"][pb].append(it)
                it["flags"].append("ordered / scheduled at this visit")
            elif d:
                grp(d, "planned")["buckets"][pb].append(it)
            else:
                undated[pb].append(it)
            continue
        if not d:
            if it["kind"] == "medication" and it.get("stop_date") and it["stop_date"] in visit_dates:
                grp(it["stop_date"], "visit")["buckets"]["medications_stopped"].append(it)
                # This item now sits in a dated section, so give it a real date basis.  Leaving dos None made
                # _basis() print "undated" on a row filed under 2019-07-08, contradicting its own placement, and
                # the flag then repeated "start date unknown" which medication_item() had already added.
                set_dos(it, it["stop_date"], "effectiveTime/high", "stopped / reconciled at this visit")
            else:
                undated[bucket].append(it)
            continue
        if d in visit_dates:
            grp(d, "visit")["buckets"][bucket].append(it)
        else:
            ws = window_start(d) if is_full_date(d) else None
            if ws:
                it["flags"].append(f"dated {d}, inside the visit that started {ws}")
                grp(ws, "visit")["buckets"][bucket].append(it)
            else:
                grp(d, "no_visit")["buckets"][bucket].append(it)
    ordered = OrderedDict((d, groups[d]) for d in sorted(groups))
    return ordered, dict(undated)


def filter_groups(groups, lo, hi):
    kept, dropped = OrderedDict(), 0
    for d, g in groups.items():
        key = d[:10] if len(d) >= 10 else d
        if (lo and key < lo) or (hi and key > hi):
            dropped += 1
            continue
        kept[d] = g
    return kept, dropped


# ----------------------------------------------------------------------------------------------- CDA XML emitter
def strip_private(obj):
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    return obj


def _sub(parent, tag, txt=None, **attrs):
    el = ET.SubElement(parent, q(tag), {k: v for k, v in attrs.items() if v is not None})
    if txt is not None:
        el.text = str(txt)
    return el


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v if x not in (None, ""))
    return str(v)


DATE_ONLY_CELL = re.compile(r"^(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|\d{8})$")
DATE_TIME_PREFIX = re.compile(r"^\s*(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})(?:\s+\d{1,2}:\d{2}\s*[AaPp]\.?[Mm]\.?)?\s*")


NOTE_COLUMN_HINTS = ("note", "comment", "instruction", "remark")
# bulky keys, or keys describing other rows — excluded from the "already rendered" blob
_BLOB_SKIP = {"nar_row", "nar_head", "nar_cells", "flags", "dos_meaning", "dos_source",
              "components", "diagnoses", "performers", "locations", "notes", "text"}


def _shown_blob(it):
    """Everything about this item that the tables already render, flattened and lowercased.

    Used for header-less narrative tables, where there are no column names to select by.  A narrative cell whose
    text already appears in the item's own data duplicates a column this same row prints — the RxNorm code, the
    allergen name, the reaction, the severity, the status — so echoing it into the notes cell just repeats the row
    back at itself.  A cell that does NOT appear is the interesting case: it exists nowhere in the structured entry,
    which is exactly the content worth keeping ("Deactivated", "PHYSICAL EXAM", "No observation recorded.").

    Note this is a redundancy test, not an "is it an enum" test.  "Active" and "Deactivated" are the same enum, but
    the first duplicates the rendered status column and the second contradicts it — only the second must survive.
    """
    parts = []

    def add(v, depth=0):
        if depth > 3:
            return
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, (int, float)):
            parts.append(str(v))
        elif isinstance(v, dict):
            for k, sub in v.items():
                if not k.startswith("_"):
                    add(sub, depth + 1)
        elif isinstance(v, (list, tuple)):
            for x in v:
                add(x, depth + 1)

    for k, v in it.items():
        if k.startswith("_") or k in _BLOB_SKIP:
            continue
        add(v)
    return re.sub(r"\s+", " ", " ".join(parts)).lower()


def _nar(it):
    """Narrative-only text behind an item, for the notes column.

    When the source table has a header row, take ONLY its note-like columns: the other columns (date, performer,
    location, codes) are already rendered as their own columns of this same table, and repeating the whole row there
    just duplicates the row back into one cell.

    When it has no header row there are no column names to select by, so every cell is tested for redundancy instead:
    drop pure dates (the table has its own date column) and drop anything whose text the item already carries — the
    code, the system name, the allergen, the reaction, the severity, the status.  What survives is content that
    appears nowhere in the structured entry: "Deactivated", "PHYSICAL EXAM" / "Lab Work", "No observation recorded."

    A cell dropped here is dropped from the output unless its own section narrative happens to be carried, so the
    tests are deliberately conservative: only exact duplicates of what the row already shows are removed.
    """
    cells = it.get("nar_cells") or {}
    if cells:
        notes = [v for k, v in cells.items() if any(h in k.lower() for h in NOTE_COLUMN_HINTS)]
        return "; ".join(dict.fromkeys(n.strip() for n in notes if n and n.strip()))
    blob = _shown_blob(it)
    out = []
    head = it.get("nar_head")
    if head:
        head = DATE_TIME_PREFIX.sub("", head).strip()
        if head and head.lower() not in blob:
            out.append(head)
    for cell in (it.get("nar_row") or "").split(" | "):
        cell = cell.strip()
        if not cell or DATE_ONLY_CELL.match(cell) or cell in out:
            continue
        if cell.lower() in blob:
            continue  # already shown in one of this row's own columns
        out.append(cell)
    return "; ".join(out)


def _table(text_el, headers, rows, caption=None):
    tbl = _sub(text_el, "table", border="1", width="100%")
    if caption:
        _sub(tbl, "caption", caption)
    thead = _sub(tbl, "thead")
    tr = _sub(thead, "tr")
    for h in headers:
        _sub(tr, "th", h)
    tbody = _sub(tbl, "tbody")
    for r in rows:
        tr = _sub(tbody, "tr")
        for c in r:
            _sub(tr, "td", _cell(c))
    return tbl


def _section(parent, title, loinc_code=None, loinc_name=None):
    comp = _sub(parent, "component")
    sec = _sub(comp, "section")
    if loinc_code:
        _sub(sec, "code", code=loinc_code, codeSystem=LOINC_OID, codeSystemName="LOINC", displayName=loinc_name)
    _sub(sec, "title", title)
    txt = _sub(sec, "text")
    return sec, txt


def _entry_of(el, parent_map):
    while el is not None and el.tag != q("entry"):
        el = parent_map.get(el)
    return el


def _copy_entries(sec, items, parent_map, seen):
    for it in items:
        ent = _entry_of(it.get("_el"), parent_map)
        if ent is not None and id(ent) not in seen:
            seen.add(id(ent))
            sec.append(clone(ent))



def _basis(it, *shown):
    """The "Date basis / notes" cell: why this row carries this date, then anything else worth saying.

    Order matters and so does de-duplication.  Flags are accumulated by several passes -- the parser, the grouper,
    the narrative matcher -- and they had no idea about each other, so a stopped medication came out as
    "undated; start unknown; stop date only; listed under the visit of its stop date 2019-07-08; start date
    unknown": a false "undated" on a row that is filed under a date, and the same fact stated twice.
    """
    parts = []
    b = it.get("dos_meaning") or ("undated" if not it.get("dos") else "")
    src = it.get("dos_source")
    if b:
        parts.append(f"{b} ({src})" if src else b)
    seen = {p.lower() for p in parts}
    for f in it.get("flags") or []:
        f = (f or "").strip()
        if f and f.lower() not in seen:
            seen.add(f.lower())
            parts.append(f)
    nar = trunc(_nar(it), NAR_MAX)
    if nar and nar.lower() not in seen and not _already_shown(nar, shown):
        parts.append(nar)
    return "; ".join(parts)


def _norm(v):
    return re.sub(r"\s+", " ", _cell(v) or "").strip().lower()


def _already_shown(nar, shown):
    """True when the recovered narrative is what another column of this same row already prints.

    _nar() screens against the item's own fields, but _BLOB_SKIP excludes the bulky ones -- "text", "components" --
    so a note's body and a lab's interpretation paragraph slipped past it and were printed twice in one row: 3,672
    characters in "Text" and the same 3,672 again inside "Date basis / notes".  Containment is tested both ways
    because the recovered run usually carries a little extra ("text/html; " ahead of a note body).
    """
    n = _norm(nar)
    if not n:
        return False
    # Compare on letters and digits only, and probe with a prefix rather than demanding full containment.  The two
    # renderings of the same paragraph are never byte-identical: the recovered run is prefixed with the mediaType
    # ("text/html; ") and collapses whitespace differently, so a 3,649-character narrative and the 3,672-character
    # "Text" column holding the same note shared no containment relation at all and both were printed.
    na = re.sub(r"[^a-z0-9]", "", n)
    for other in shown:
        o = _norm(other)
        if len(o) < 12:
            continue
        if n in o or o in n:
            return True
        oa = re.sub(r"[^a-z0-9]", "", o)
        if len(oa) < 40 or len(na) < 40:
            continue
        if na[:200] in oa or oa[:200] in na:
            return True
    return False


def _notes(it, *shown):
    """The "Notes" cell: narrative genuinely attached to this row, and nothing else.

    _basis() answers a different question -- why does this row carry this date -- and it belongs to whoever is
    auditing the transform, not to the table.  Printed in every row it read as noise at best and as contradiction at
    worst: "undated; date 2026-06-12 is the document-generation/import stamp; treated as undated" states the same
    fact twice and buries the one thing a reader wants, which is whether there is a note.  Downstream this is
    actively harmful, because a clinical NLP reader flattens each cell into a sentence: derivation prose becomes
    phrases competing with real clinical text.  The derivation is still in --json for every item (dos_meaning,
    dos_source, flags) and --date-basis puts it back in the narrative.
    """
    nar = trunc(_nar(it), NAR_MAX)
    return "" if (not nar or _already_shown(nar, shown)) else nar


def _nar_cell(it, *wanted):
    """One named column out of the item's narrative row, e.g. the 'Diagnosis Note' on its own.

    Matched on a lowercase substring of the source column name so vendor wording ('Diagnosis Note', 'Dx Note',
    'Note') all resolve, and the whole row is not dumped into the cell.
    """
    cells = it.get("nar_cells") or {}
    for w in wanted:
        for k, v in cells.items():
            if w in k.lower():
                return v
    return ""


def _coded(it):
    """Primary code plus any translations, e.g. '61582004 (ICD-10-CM J30.1)'.

    athenahealth hangs the ICD-10 mapping off the SNOMED <value> as a <translation>.  Only the primary code was ever
    rendered, so those ICD-10 diagnoses existed in the copied entries but appeared in no table — and because they are
    not in the source narrative either, carrying the narrative verbatim could not surface them.
    """
    primary = it.get("problem_code") or it.get("code_value") or it.get("product_code") or ""
    v = it.get("value") if isinstance(it.get("value"), dict) else it.get("code")
    others = []
    for t in ((v or {}).get("translation") or []):
        c = (t or {}).get("code")
        if not c or c == primary:
            continue
        sysname = (t or {}).get("codeSystemName") or OID_NAMES.get((t or {}).get("codeSystem")) or ""
        others.append(f"{sysname} {c}".strip())
    others = list(dict.fromkeys(others))
    return f"{primary} ({'; '.join(others)})" if primary and others else (primary or "; ".join(others))


def _dx_rows(dxs, dos):
    rows = []
    for dx in dxs:
        flag = "asserted on a different date" if dx.get("dos") and dx["dos"] != dos else ""
        rows.append([dx["label"], _coded(dx), dx.get("problem_system"), fmt_eff(dx["effectiveTime"]),
                     dx.get("status"), flag, trunc(_nar_cell(dx, "diagnosis note", "note"), NAR_MAX)])
    return rows


def _bucket_table(txt, bucket, items, date_basis=False):
    ncol = "Date basis / notes" if date_basis else "Notes"

    def basis(it, *shown):
        return _basis(it, *shown) if date_basis else _notes(it, *shown)

    if bucket == "problems":
        _table(txt, ["Problem", "Code", "System", "Onset", "Resolved", "Recorded (author time)", "Concern status", ncol],
               [[trunc(it["label"]), _coded(it), it.get("problem_system"), it["effectiveTime"].get("low") or it["effectiveTime"].get("value"), it.get("resolved"),
                 it.get("recorded"), (it.get("concern") or {}).get("status"), basis(it)] for it in items])
    elif bucket in ("medications", "medications_stopped"):
        _table(txt, ["Medication", "Sig", "Dose", "Route", "Frequency", "Start", "Stop", "Status", "Indication", "Fills / supply", "Patient instructions", "RxNorm", ncol],
               [[trunc(it["label"]), it.get("sig"), it.get("dose"), it.get("route"), it.get("frequency"), it.get("start"), it.get("stop"), it.get("status"),
                 it.get("indications"), it.get("fills"), trunc(_cell(it.get("instructions")), 200), _coded(it),
                 basis(it, it.get("sig"), it.get("instructions"), it.get("indications"))] for it in items],)
    elif bucket in ("results", "vitals"):
        rows = []
        for it in items:
            if it["kind"] == "result_note":
                rows.append([trunc(it["label"]), "Imaging / result narrative", trunc(it.get("text"), 4000), "", "", fmt_eff(it["effectiveTime"]), "", basis(it, it.get("text"))])
                continue
            for c in it["components"]:
                neg = " (negationInd=true: not performed/recorded)" if c.get("negated") else ""
                cdate = fmt_eff(c["effectiveTime"]) if c["effectiveTime"].get("present") else fmt_eff(it["effectiveTime"])
                rows.append([trunc(it["label"]), trunc(c["label"]), (c["value"] or "not reported") + neg, c.get("interpretation"), c.get("referenceRange"), cdate, c.get("comments"),
                             basis(it, c.get("comments"), it.get("text"))])
            if not it["components"]:
                rows.append([trunc(it["label"]), "", "", "", "", fmt_eff(it["effectiveTime"]), "", basis(it)])
        _table(txt, ["Panel", "Component", "Value", "Interpretation", "Reference range", "Date", "Comment", ncol], rows)
    elif bucket == "procedures":
        _table(txt, ["Procedure", "Code", "System", "Status", "Performer(s)", "Body site", "Result / value", "Date", ncol],
               [[trunc(it["label"]), _coded(it), it.get("code_system"), it.get("status"), [provider_label(p) for p in it.get("performers") or []] or "n/a",
                 it.get("body_site"), it.get("value_text"), fmt_eff(it["effectiveTime"]),
                 basis(it, it.get("value_text"), it.get("reason"), [provider_label(p) for p in it.get("performers") or []])] for it in items])
    elif bucket == "immunizations":
        _table(txt, ["Vaccine", "CVX", "RxNorm", "Lot", "Route", "Site", "Status", "Not given?", "Date", ncol],
               [[trunc(it["label"]), it.get("cvx"), it.get("rxnorm"), it.get("lot"), it.get("route"), it.get("site"), it.get("status"), ("yes" + (f" ({it['refusal_reason']})" if it.get("refusal_reason") else "")) if it.get("negated") else "",
                 fmt_eff(it["effectiveTime"]), basis(it)] for it in items])
    elif bucket == "allergies":
        _table(txt, ["Allergen", "Type", "Reaction(s)", "Severity", "Concern status", "Onset", "Recorded (author time)", "Comments", ncol],
               [[trunc(it["label"]), it.get("type"), it.get("reactions") or "n/a", it.get("severity") or "n/a", it.get("concernStatus"), fmt_eff(it["effectiveTime"]), it.get("recorded"), it.get("comments"), basis(it, it.get("comments"), it.get("reactions"))] for it in items],)
    elif bucket == "social_history":
        _table(txt, ["Observation", "Value", "effectiveTime", "Recorded (author time)", ncol],
               [[trunc(it["label"]), it.get("value_text"), fmt_eff(it["effectiveTime"]), it.get("recorded"), basis(it, it.get("value_text"))] for it in items])
    elif bucket in ("functional_status", "mental_status", "family_history"):
        _table(txt, ["Item", "Value / score", "Details", "Date", "Recorded (author time)", ncol],
               [[trunc(it["label"]), it.get("value_text"), trunc(_cell(it.get("details")), 600), fmt_eff(it["effectiveTime"]), it.get("recorded"),
                 basis(it, it.get("value_text"), it.get("details"))] for it in items])
    elif bucket == "notes":
        _table(txt, ["Note type", "Author", "Written (effectiveTime)", "Recorded (author time)", "Text", ncol],
               [[trunc(it.get("note_type") or it["label"]), provider_label(it.get("author")) if it.get("author") else "", fmt_eff(it.get("effectiveTime")), it.get("recorded"), it.get("text"),
                 basis(it, it.get("text"))] for it in items],)
    elif bucket == "assessment":
        # "Encounter Id" is carried because it is the only join key the source offers back to its own encounter
        # list.  In the chart this was built for those ids match no encounter in the document, so it is printed
        # as provenance rather than used to date the row -- the row's own date column does that.
        _table(txt, ["Assessment", "Date", "Encounter Id", "Recorded by", "Organization", "Last modified", ncol],
               [[trunc(it["label"], 4000), fmt_eff(it["effectiveTime"]), it.get("encounter_id"), it.get("recorded_by"),
                 it.get("organization"), it.get("recorded"),
                 basis(it, it["label"], it.get("encounter_id"), it.get("recorded_by"), it.get("organization"))] for it in items])
    elif bucket == "referrals":
        # "Referred by" and "Referred to" are narrative-only in every chart seen so far -- the act has no author,
        # performer or participant -- so the structured value comes first and the mined narrative value backs it up.
        rows = []
        for it in items:
            rows.append([trunc(it["label"]), _coded(it), it.get("code_system"), it.get("indications") or "not stated",
                         first(provider_label(it.get("referred_by")) if it.get("referred_by") else None, it.get("referred_by_text")) or "n/a",
                         first("; ".join(provider_label(p) for p in it.get("referred_to") or []) or None, it.get("referred_to_text")) or "n/a",
                         it.get("status"), it.get("priority"), fmt_eff(it["effectiveTime"]), it.get("encounter_date"),
                         trunc(it.get("referral_text"), NAR_MAX),
                         basis(it, it.get("indications"), it.get("referral_text"), it.get("referred_by_text"),
                               it.get("referred_to_text"), it.get("encounter_date"), it.get("details"))])
        _table(txt, ["Referral", "Code", "System", "Indication (reason)", "Referred by", "Referred to", "Status",
                     "Priority", "Referral date", "Encounter date", "Referral narrative", ncol], rows)
    elif bucket == "payers":
        _table(txt, ["Payer", "Plan type", "Seq", "Group / policy #", "Member ID", "Subscriber ID",
                     "Relationship to subscriber", "Policy holder", "Guarantor", "Payer ID", "Coverage date", ncol],
               [[trunc(it.get("payer_name") or it["label"]), it.get("plan_type"), it.get("sequence"), it.get("group_number"),
                 it.get("member_id"), it.get("subscriber_id"), it.get("relationship"), it.get("policy_holder_name"),
                 it.get("guarantor_name"), it.get("payer_id"), fmt_eff(it.get("coverage_eff") or it["effectiveTime"]),
                 basis(it, it.get("payer_name"), it.get("plan_type"), it.get("group_number"), it.get("member_id"),
                       it.get("subscriber_id"), it.get("relationship"), it.get("policy_holder_name"),
                       it.get("guarantor_name"), it.get("payer_id"), it.get("sequence"))] for it in items])
    elif bucket == "advance_directives":
        _table(txt, ["Directive", "Type", "Value", "Status", "In effect from", "Until", "Verified by", "Custodian",
                     "Document", ncol],
               [[trunc(it["label"], NAR_MAX), it.get("directive_type"), it.get("value_text"), it.get("status"),
                 fmt_eff(it["effectiveTime"]),
                 "no end date" if it.get("no_end_date") else ("not recorded" if it.get("end_unknown") else it.get("in_effect_until")),
                 it.get("verifiers") or "", it.get("custodians") or "", it.get("document_ref"),
                 basis(it, it["label"], it.get("directive_type"), it.get("value_text"), it.get("verifiers"),
                       it.get("custodians"), it.get("document_ref"))] for it in items])
    elif bucket == "health_concerns":
        _table(txt, ["Health concern", "Code", "System", "Concern status", "Related to", "Recorded by",
                     "Recorded (author time)", "Date", ncol],
               [[trunc(it["label"]), _coded(it), it.get("code_system"), it.get("concern_status"),
                 trunc(_cell(it.get("related")), 600), it.get("author_org"), it.get("recorded"),
                 fmt_eff(it["effectiveTime"]), basis(it, it.get("related"), it.get("author_org"))] for it in items])
    elif bucket == "care_team":
        _table(txt, ["Member", "Role", "Specialty", "NPI", "Organization", "Team", "Address", "Phone", "Status",
                     "Date", ncol],
               [[trunc(it.get("member_name") or it["label"]), it.get("role"), it.get("specialty"), it.get("npi"),
                 it.get("member_org"), it.get("team"), it.get("addr"), it.get("phone"), it.get("status"),
                 fmt_eff(it.get("period") or it["effectiveTime"]),
                 basis(it, it.get("member_name"), it.get("role"), it.get("specialty"), it.get("npi"),
                       it.get("member_org"), it.get("team"), it.get("addr"), it.get("phone"))] for it in items])
    elif bucket == "plan":
        rows = []
        for it in items:
            # The instruction text of a Patient Education act exists ONLY in the narrative -- the act carries a code
            # and a statusCode and nothing else -- so _nar() is the last fallback for this column.  Without it those
            # 876 characters of "We discussed your cholesterol: - Your bad cholesterol (LDL) is ..." ended up in
            # ncol, a column meant to say why the row carries its date, while the column that should
            # have held them sat empty.  Passing the value to _basis() then keeps it from being printed twice.
            val = it.get("value_text") or trunc(_cell(it.get("instructions")), 300) or it.get("sig") or trunc(_nar(it), NAR_MAX)
            rows.append([trunc(it["label"]), it.get("entryClass"), it.get("moodCode"), it.get("code_value"), val,
                         it.get("status"), fmt_eff(it["effectiveTime"]), basis(it, val)])
        _table(txt, ["Planned item", "Class", "Mood", "Code", "Value / instructions", "Status", "Planned date", ncol], rows)
    else:
        _table(txt, ["Source section", "Item", "Code", "System", "Value", "Details", "Status", "Date", "Recorded (author time)", ncol],
               [[it.get("section_title"), trunc(it["label"]), it.get("code_value"), it.get("code_system"), it.get("value_text"), trunc(_cell(it.get("details")), 600), it.get("status"), fmt_eff(it["effectiveTime"]), it.get("recorded"),
                 basis(it, it.get("value_text"), it.get("details"))] for it in items],)


def _heading(g):
    d = g["dos"]
    if g["kind"] == "visit":
        provs, locs = [], []
        for e in g["encounters"]:
            for p in e.get("performers") or []:
                nm = provider_label(p)
                if nm != "n/a" and nm not in provs:
                    provs.append(nm.split(" | ")[0])
            ln = (e.get("location_name") or "").strip().lstrip("-_ ").strip()
            if ln and ln not in locs and not ln.upper().startswith("_ATHENA_MIGRATION"):
                locs.append(ln)
        who = ""
        if provs:
            who += " - " + " / ".join(provs)
        if locs:
            who += " @ " + " / ".join(locs)
        return f"DOS {d}{who}"
    if g["kind"] == "planned":
        return f"Planned {d} — scheduled / ordered, not a visit"
    # Deliberately avoids the word "encounter": these sections contain none, and a reader scanning the document for
    # encounters counts the negation as a hit.  The word is reserved for the one section that really holds them.
    return f"DOS {d} — no visit recorded on this date (onset / start / result dates only)"


def _encounters_passthrough(body, doc):
    """Re-emit the source Encounters section unchanged: one section, LOINC 46240-8, its original narrative, and
    every original <entry> in source order.

    Scattering each encounter into the date section it belongs to is the whole point of this tool, but it also
    destroys the shape a section-scoped reader depends on — "find the encounters section, then read its
    encounter/effectiveTime".  One 46240-8 section holding 93 encounters became 85 date sections holding one or two
    apiece, so a reader written against the source CCDA found no encounters section it recognised even though every
    element was still present.  Carrying the original section through alongside the date sections restores that
    shape exactly: same single list, same order, same entries.

    Returns the ids of the entries emitted here so the date sections do NOT copy them as well — otherwise every
    encounter appears twice and a sweep over the document counts every visit twice.
    """
    srcs = [s for s in doc["sections"] if s["key"] == "encounters" and s.get("_el") is not None]
    if not srcs:
        return set()
    sec, txt = _section(body, srcs[0]["title"].split(" › ")[-1] or "Encounters", "46240-8", "History of encounters")
    if srcs[0].get("_text") is not None:
        sec.remove(txt)
        sec.append(clone(srcs[0]["_text"]))
    else:
        _sub(txt, "paragraph", "Source encounters, carried over unchanged from the input document.")
    out = set()
    for s_ in srcs:
        for ent in findall(s_["_el"], "h:entry"):
            out.add(id(ent))
            sec.append(clone(ent))
    return out


def _header_dos(new, doc, mode):
    """Put a real date of service in the CDA header, where a header-driven reader looks for it first.

    cda.xsl renders componentOf/encompassingEncounter as the "Encounter" row of the document header and
    documentationOf/serviceEvent as the "Documentation Of" row.  athenahealth exports omit encompassingEncounter
    entirely and set serviceEvent/effectiveTime/low to the export timestamp, so a reader walking
    encompassingEncounter -> serviceEvent -> encounters answers with the export date ("February 3, 2026" for a chart
    whose visits are 2026-01-16 and 2026-02-02) and never reaches the encounters at all.  Two corrections:

      * encompassingEncounter is filled in from a real performed visit -- its id, type, date, provider and facility
        copied from that encounter's own entry, nothing invented beyond the choice of which visit;
      * a serviceEvent/effectiveTime that merely repeats the document's own timestamp is replaced by the span of
        the performed visits, so the export date is not offered as a service date even if it is reached.

    mode: 'latest' (most recent performed visit), 'earliest', 'span' (low..high over all visits), 'none'.
    Returns a note describing what was asserted, for the overview and the run summary.
    """
    if mode == "none":
        return None
    visits = [(d, g) for d, g in doc["dos"].items() if g["kind"] == "visit" and is_full_date(d)]
    if not visits:
        return None
    lo, hi = visits[0][0], visits[-1][0]  # doc["dos"] is already date-ordered
    pick = hi if mode == "latest" else lo
    enc = next((e for d, g in visits if d == pick for e in g["encounters"]), None) or {}

    for old in new.findall("h:componentOf", NS):
        new.remove(old)
    ee = _sub(_sub(new, "componentOf"), "encompassingEncounter")
    for i in enc.get("id") or []:
        _sub(ee, "id", root=i.get("root"), extension=i.get("extension"))
    code = enc.get("code") or {}
    if code.get("code") or code.get("displayName"):
        _sub(ee, "code", code=code.get("code"), codeSystem=code.get("codeSystem"),
             codeSystemName=code.get("codeSystemName"), displayName=code.get("displayName"))
    et = _sub(ee, "effectiveTime")
    end = enc.get("end_date")
    if mode == "span" and lo != hi:
        _sub(et, "low", value=lo.replace("-", ""))
        _sub(et, "high", value=hi.replace("-", ""))
        shown = f"{lo} → {hi}"
    elif end and is_full_date(end) and end != pick:
        _sub(et, "low", value=pick.replace("-", ""))
        _sub(et, "high", value=end.replace("-", ""))
        shown = f"{pick} → {end}"
    else:
        et.set("value", pick.replace("-", ""))
        shown = pick
    src = enc.get("_el")
    if src is not None:
        ae = find(src, "h:performer/h:assignedEntity")
        if ae is not None:
            _sub(ee, "responsibleParty").append(clone(ae))
        pr = find(src, "h:participant[@typeCode='LOC']/h:participantRole")
        if pr is not None:
            hcf = _sub(_sub(ee, "location"), "healthCareFacility")
            c = find(pr, "h:code")
            if c is not None:
                hcf.append(clone(c))
            nm, ad = text(find(pr, "h:playingEntity/h:name")), find(pr, "h:addr")
            if nm or ad is not None:
                place = _sub(hcf, "location")
                if nm:
                    _sub(place, "name", nm)
                if ad is not None:
                    place.append(clone(ad))

    # No "--" in this text: it is emitted as an XML comment, where "--" is illegal.  The guard at the call site
    # would rewrite it, which turns the flag name into nonsense ("—header-dos"), so spell it without the dashes.
    note = (f"Header date of service asserted (header-dos={mode}): componentOf/encompassingEncounter/effectiveTime = {shown}"
            + (f", {enc.get('type') or 'encounter'}" if enc else "")
            + f". Chosen from {len(visits)} performed visit(s) spanning {lo} → {hi}.")
    doc_date = (doc["header"]["document"] or {}).get("effectiveDate")
    for se in new.findall("h:documentationOf/h:serviceEvent", NS):
        se_et = se.find("h:effectiveTime", NS)
        if se_et is None:
            continue
        cur = ts_date(attr(se_et, "value") or attr(se_et.find("h:low", NS), "value"))
        if not (cur and doc_date and cur == doc_date):
            continue
        for ch in list(se_et):
            se_et.remove(ch)
        se_et.attrib.pop("value", None)
        _sub(se_et, "low", value=lo.replace("-", ""))
        _sub(se_et, "high", value=hi.replace("-", ""))
        # Say WHAT was replaced, never print the offending value: this note is readable prose, and a date-of-service
        # reader scanning text would happily pick the export date back out of the sentence explaining it was wrong.
        # The timestamp itself stays in the header, where cda.xsl renders it as "Created On".
        note += f" documentationOf/serviceEvent/effectiveTime merely repeated the document's own export timestamp, which is not a service date; replaced with the performed-visit span {lo} → {hi}."
    return note


def build_dosfirst_xml(src_root, doc, parent_map, copy_entries=True, header_dos="latest", audit_tables=False,
                       encounters_section="inline", date_basis=False):
    ET.register_namespace("", "urn:hl7-org:v3")
    ET.register_namespace("xsi", NS["xsi"])
    ET.register_namespace("sdtc", NS["sdtc"])
    new = ET.Element(q("ClinicalDocument"))
    for child in src_root:
        if child.tag == q("component"):
            break
        new.append(clone(child))
    t = new.find("h:title", NS)
    if t is None:
        t = _sub(new, "title")
    t.text = f"{t.text or 'Clinical Document'} - DOS-first view"
    # componentOf must be built before <component>: CDA requires it to precede the body.
    dos_note = _header_dos(new, doc, header_dos)
    if dos_note:
        # Provenance goes in a comment, not in the narrative.  The encompassingEncounter above is asserted by this
        # tool and is absent from the source, so the fact has to be recorded somewhere -- but as readable prose it
        # was a third place a date of service appeared in the document, and the only one that was not a real
        # service date field.  A comment is invisible to the XSL and to any tag-driven reader, and still there for
        # anyone auditing the file.  It is also echoed to stderr on every run.
        new.append(ET.Comment(" " + dos_note.replace("--", "—") + " "))
        doc["_header_dos_note"] = dos_note
    body = _sub(_sub(new, "component"), "structuredBody")

    # --- 1. overview
    # Patient, demographics, serviceEvent, author and custodian are deliberately NOT restated here.  Earlier comments
    # in this spot claimed they "appeared nowhere outside the copied header"; that was simply wrong — cda.xsl renders
    # every one of them in the document header and again in the footer, so each paragraph was a third copy.  The
    # duplication was not merely noise: the serviceEvent paragraph reprinted the export date as readable prose, and
    # went on printing the SOURCE value after _header_dos had already replaced it in the header element, handing a
    # date-of-service reader one more chance to pick the wrong date and contradicting the document's own header.
    # What survives is only what the source header cannot say: how to read this view, and what this tool asserted.
    sec, txt = _section(body, "Document Overview (sectional summary)", "34133-9", "Summarization of Episode Note")
    if doc.get("filter_note"):
        _sub(txt, "paragraph", doc["filter_note"])
    # The section-audit table and the processing notes are OFF by default (--audit-tables turns them back on).
    # They describe the transform, not the patient, and downstream readers cannot tell the difference: a clinical NLP
    # pipeline flattens every table cell into a sentence and runs entity recognition over it, so "1. 1. 0. 1.
    # allergies." — the Entries / Items parsed / Dropped / Undated counters for the Allergies section — came back as
    # CPT 4133F, "1. 1. 0. 1. medications." as A9150 and "Plan of Care." as 5050F, each stamped with the document's
    # date of service.  Debug counters were being turned into billable charges.  The same data is in --json
    # (summary.sections, summary.warnings), in --text, and on stderr every run.
    if audit_tables:
        rows = []
        for s_ in doc["sections"]:
            dated = [it["dos"] for it in s_["items"] if it.get("dos")]
            rows.append([s_["title"], code_label(s_["code"]) or "", s_["entryCount"], len(s_["items"]), s_["dropped"], len(s_["items"]) - len(dated),
                         f"{min(dated)} to {max(dated)}" if dated else "", s_["handler"] + (" (narrative only)" if s_["narrative_only"] else "")])
        _table(txt, ["Section", "LOINC", "Entries", "Items parsed", "Entries DROPPED", "Undated items", "Date range", "Handler"], rows, caption="Sections in source document")
        if doc["warnings"]:
            _sub(txt, "paragraph", "Processing notes:")
            for w in doc["warnings"]:
                _sub(txt, "paragraph", "• " + w)
    # "Dates found" stays ON by default: it is the index of a DOS-first document -- what happened on each date and
    # how much of it -- which is exactly what this view exists to show and which no other section states.  It was
    # not the source of the spurious CPT codes; every one of those came from the section-audit table above.
    _table(txt, ["Date", "Type", "Visits", "Diagnoses", "Problems", "Meds", "Results", "Procedures", "Vitals", "Immunizations", "Allergies", "Notes", "Referrals", "Planned", "Other"],
           [[d, {"visit": "visit", "no_visit": "no visit", "planned": "planned only"}[g["kind"]], len(g["encounters"]), len(g["diagnoses"]), len(g["buckets"].get("problems", [])),
             len(g["buckets"].get("medications", [])) + len(g["buckets"].get("medications_stopped", [])), len(g["buckets"].get("results", [])), len(g["buckets"].get("procedures", [])),
             len(g["buckets"].get("vitals", [])), len(g["buckets"].get("immunizations", [])), len(g["buckets"].get("allergies", [])), len(g["buckets"].get("notes", [])),
             len(g["buckets"].get("referrals", [])), len(g["buckets"].get("plan", [])),
             sum(len(v) for k, v in g["buckets"].items() if k in ("other", "functional_status", "mental_status", "family_history", "social_history", "assessment",
                                                                  "health_concerns", "care_team", "advance_directives", "payers"))] for d, g in doc["dos"].items()],
           caption="Dates found")
    # Sections whose source narrative has to be carried because the structured entries cannot stand in for it.
    #
    # The first three cases are the original ones: no entries at all, entries that were dropped, or a section that
    # went through the generic parser.  The last two are real data loss found in athenahealth charts:
    #
    #   * nar_broken_refs -- the entries point at narrative IDs that do not exist.  Functional Status references
    #     "#disabilityassessment7745227" while the narrative anchor is "assessment7745227", so 48 of 56 references
    #     dangle and those assessment rows are reachable from no entry.
    # "nar_mined" stands the carry down: narrative_assessment_items() has already turned those rows into dated
    # items sitting in their own DOS sections, which is where they belong.  Carrying the blob as well would store
    # every PHQ-9 response twice and put one copy back at the top of the document, undated.
    #
    # That guard now covers every clause rather than only the broken-reference one, because the Assessment section
    # reaches this list through "narrative_only" instead: it has no entry at all, so once
    # narrative_assessment_section_items() has mined its table into dated items, carrying the table as well would
    # print each assessment twice and leave one copy undated at the top -- exactly the failure being fixed.
    # Functional and Mental Status are unaffected: they have entries, no drops and a dedicated handler, so they
    # already failed every clause.
    #
    # Deliberately NOT keyed on unresolved anchors in general: results, vitals and medications leave hundreds of
    # anchors unresolved (narrative_source() stops at an item's first resolvable reference) while still rendering
    # every component, so that test carries ~238 KB of narrative already covered by the tables.
    #   * nar_unclaimed -- set only for Reason for Referral, whose dedicated handler mines the narrative into the
    #     referral row (referring physician, encounter date, the free-text reason) because the act carries none of
    #     it.  Once every row has reached an item the blob would be a second, undated copy of the same referral, so
    #     it is carried only when some row reached nothing.
    narr = [s_ for s_ in doc["sections"]
            if s_.get("_text") is not None and text(s_["_text"]) and not s_.get("nar_mined")
            and (s_["narrative_only"] or s_["dropped"] or s_["handler"] == "generic" or s_["nar_broken_refs"]
                 or s_.get("nar_unclaimed"))]
    if narr:
        nsec, ntxt = _section(sec, "Source narratives (sections without structured entries or with generic handling)")
        for s_ in narr:
            ssec, _ = _section(nsec, s_["title"] + " (narrative)", (s_["code"] or {}).get("code"), (s_["code"] or {}).get("displayName"))
            ssec.remove(ssec.find("h:text", NS))
            ssec.append(clone(s_["_text"]))

    # --- 1b. the source Encounters section, carried through unchanged (only in 'separate' mode)
    separate_encounters = copy_entries and encounters_section == "separate"
    passthrough = _encounters_passthrough(body, doc) if separate_encounters else set()

    # --- 2. one section per date
    # The LOINC code depends on what the section actually holds.  Stamping every date section "History of encounters"
    # (46240-8) is what turned 1 encounters section into 222, most of them holding no encounter, and made a
    # section-scoped reader lose the list entirely.  So: a section that really does hold encounter entries is coded
    # 46240-8; a date with no visit is 34133-9, a summary of one date.  In 'separate' mode the entries live in the
    # pass-through section instead, so every date section is 34133-9.
    for dos, g in doc["dos"].items():
        seen = set(passthrough)
        lc, ln = (("46240-8", "History of encounters") if (g["encounters"] and not separate_encounters)
                  else ("34133-9", "Summarization of Episode Note"))
        sec, txt = _section(body, _heading(g), lc, ln)
        if not g["encounters"]:
            _sub(txt, "paragraph", "No visit on this date. The items below are planned, ordered or scheduled — not services rendered."
                 if g["kind"] == "planned" else
                 "No visit recorded on this date. The items below carry this date as onset / start / result / recorded date — see the 'Date basis' column.")
        if copy_entries:
            _copy_entries(sec, g["encounters"], parent_map, seen)
        # Encounters get a titled sub-section like every other bucket, named after the source section they came from
        # ("Past Encounters"), so that title shows in the body and not only in the overview table.  Built after
        # _copy_entries because CDA requires a section's entry* children to precede its component* children.
        if g["encounters"] or g["diagnoses"]:
            # Named after the source section it came from ("Past Encounters"), so the source's own heading shows in
            # the body and not only in the overview table.  This does mean the title repeats once per visit date;
            # the table captions below stay distinct ("Visit detail", "Diagnoses at this visit") so a reader
            # counting encounter lists has something unambiguous to key on.
            etitle = next((e.get("section_title") for e in g["encounters"] if e.get("section_title")), None) or "Encounters"
            _, etxt = _section(sec, etitle)
            if g["encounters"]:
                _table(etxt, ["Date", "Visit type", "POS code", "Location", "Location address", "Provider(s)", "NPI", "Reason for visit", "Discharge disposition", "Dx", "Notes"],
                       [[fmt_eff(e["effectiveTime"]), trunc(e["type"]), e.get("pos_code") or "not coded", e.get("location_name") or "n/a",
                         ((e.get("locations") or [{}])[0].get("addr") or {}).get("oneLine") if e.get("locations") and e["locations"][0] else "",
                         [provider_label(pp) for pp in e.get("performers") or []] or "n/a", [pp.get("npi") for pp in e.get("performers") or [] if pp and pp.get("npi")] or "n/a",
                         e.get("reasons") or "", e.get("dischargeDisposition") or "", len(e.get("diagnoses") or []),
                         "; ".join([x for x in ([("; ".join(e["flags"]) if e.get("flags") else "") if date_basis else "", _notes(e)]) if x])] for e in g["encounters"]],
                       caption="Visit detail")
            if g["diagnoses"]:
                _table(etxt, ["Diagnosis", "Code", "System", "Asserted (effectiveTime)", "Status", "Flag", "Diagnosis Note"], _dx_rows(g["diagnoses"], dos),
                       caption="Diagnoses at this visit")
        for bucket, (title, lc, ln) in BUCKET_TITLES.items():
            items = g["buckets"].get(bucket) or []
            if not items:
                continue
            sub, subtxt = _section(sec, title, lc, ln)
            _bucket_table(subtxt, bucket, items, date_basis=date_basis)
            if copy_entries:
                _copy_entries(sub, items, parent_map, seen)

    # --- 3. undated
    if doc["undated"]:
        sec, txt = _section(body, "Undated (no usable date in the source)")
        _sub(txt, "paragraph", "Items whose source carries no start / performed / onset date. Stop dates, import stamps and document-generation stamps are shown but are not treated as dates of service.")
        seen = set(passthrough)
        for bucket, (title, lc, ln) in BUCKET_TITLES.items():
            items = doc["undated"].get(bucket) or []
            if not items:
                continue
            sub, subtxt = _section(sec, title, lc, ln)
            _bucket_table(subtxt, bucket, items, date_basis=date_basis)
            if copy_entries:
                _copy_entries(sub, items, parent_map, seen)

    return new


def _indent(elem, level=0):
    pad = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = pad + "  "
        child = None
        for child in elem:
            _indent(child, level + 1)
        if child is not None and (not child.tail or not child.tail.strip()):
            child.tail = pad
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = pad


def scrub_tree(elem):
    """Last gate before serialization: ElementTree writes control characters through verbatim, producing a file no
    conformant reader (.NET XmlReader, libxml2, expat) will accept — 'hexadecimal value 0x02, is an invalid character'.
    Strip them from every text node, tail and attribute value.  Returns the number of characters removed."""
    removed = 0
    for el in elem.iter():
        for slot in ("text", "tail"):
            v = getattr(el, slot)
            if v:
                clean = xml_safe(v)
                if clean != v:
                    removed += len(v) - len(clean)
                    setattr(el, slot, clean)
        for k, v in list(el.attrib.items()):
            clean = xml_safe(v)
            if clean != v:
                removed += len(v) - len(clean)
                el.attrib[k] = clean
    return removed


XML_STYLESHEET_PI = '<?xml-stylesheet href="CCDA.xsl" type="text/xsl"?>'


def write_dosfirst_xml(root, path):
    """Serialize the DOS-first document with an xml-stylesheet PI ahead of the root element.

    The PI is what makes the .xml open as a rendered chart in a browser instead of a raw element tree; the href is
    relative, so it resolves only when a stylesheet of that exact name sits next to the output file.

    ElementTree.write() emits nothing above the root element, so the declaration and the PI are written by hand and
    the tree follows with xml_declaration=False.  The declaration bytes are character-for-character what
    ElementTree itself would have written, so the sole difference from a plain write() is the added PI line."""
    with open(path, "wb") as f:
        f.write(b"<?xml version='1.0' encoding='UTF-8'?>\n")
        f.write(XML_STYLESHEET_PI.encode("utf-8") + b"\n")
        ET.ElementTree(root).write(f, encoding="UTF-8", xml_declaration=False)


# ----------------------------------------------------------------------------------------------- text rendering
def render_text(doc):
    L = []
    w = L.append
    h = doc["header"]
    d = h["document"]
    w("ClinicalDocument")
    w(f"  id={(d['id'][0].get('root') if d['id'] else 'n/a')}  title={d['title']}  code={code_label(d['code'])}  effectiveTime={d['effectiveTime']}")
    p = h["patient"]
    w(f"  patient: {p['name'].get('full') or 'n/a'}  DOB={p.get('birthTime')}  sex={code_label(p.get('gender'))}")
    w("  sections")
    for s in doc["sections"]:
        w(f"    {s['title']} [{s['key']}/{s['handler']}] entries={s['entryCount']} parsed={len(s['items'])} dropped={s['dropped']}" + ("  (narrative only)" if s["narrative_only"] else ""))
    for wmsg in doc["warnings"]:
        w(f"  ! {wmsg}")
    for dos, g in doc["dos"].items():
        w(_heading(g))
        for e in g["encounters"]:
            w(f"  encounter: {e['type']}  location={e.get('location_name') or 'n/a'}  providers={[provider_label(x) for x in e.get('performers') or []] or 'n/a'}")
        if g["diagnoses"]:
            w("  diagnoses: " + "; ".join(dx["label"] for dx in g["diagnoses"]))
        for bucket, (title, _, _) in BUCKET_TITLES.items():
            items = g["buckets"].get(bucket) or []
            if not items:
                continue
            w(f"  {title}:")
            for it in items:
                w(f"    - {trunc(it['label'], 100)}  [{_basis(it)}]")
    if doc["undated"]:
        w("Undated")
        for bucket, items in doc["undated"].items():
            w(f"  {bucket}: " + "; ".join(trunc(it["label"], 60) for it in items))
    return "\n".join(L)


# ----------------------------------------------------------------------------------------------- input recovery
# scrub_tree() is the gate on the way OUT.  This is the gate on the way IN: real-world C-CDA exports carry the same
# faults their writers refuse to read back — raw C0 control characters in a narrative cell, an HTML entity (&nbsp;)
# that XML never defined, a bare '&' in a clinic name, a declared encoding the bytes do not match, junk ahead of the
# XML declaration.  expat rejects all of them with "not well-formed (invalid token)" before a single section is seen,
# so the whole chart is lost to a character.  Repair, parse, and say in the warnings what was changed.
_XML_DECL_RE = re.compile(r"^<\?xml[^>]*\?>\s*")
_DECL_ENC_RE = re.compile(rb'encoding\s*=\s*["\']([\w.\-]+)["\']', re.I)
# UTF-32 BOMs must be tested before UTF-16: BOM_UTF32_LE starts with BOM_UTF16_LE.
_BOMS = ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
         (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"))
_XML_BUILTIN_ENTS = {"amp", "lt", "gt", "apos", "quot"}
# Every '&', with a well-formed reference after it if there is one.  What is left is a bare '&' needing escape.
_REF_RE = re.compile(r"&(?:#[0-9]+;|#[xX][0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9._-]*;)?")


def _legal_cp(cp):
    """True if the codepoint may appear in a well-formed XML 1.0 document, as a character or as a &#N;."""
    try:
        return not _XML_ILLEGAL.match(chr(cp))
    except (ValueError, OverflowError):
        return False


def _decode_source(data):
    """Bytes -> (text, encoding, note).  BOM wins, then the declared encoding, then utf-8, then cp1252."""
    for bom, enc in _BOMS:
        if data.startswith(bom):
            return data.decode(enc, errors="replace"), enc, None
    declared = None
    m = re.match(rb"^\s*<\?xml[^>]*\?>", data)
    if m:
        e = _DECL_ENC_RE.search(m.group(0))
        if e:
            declared = e.group(1).decode("ascii", "replace")
    tried = []
    for enc in (declared, "utf-8", "cp1252", "latin-1"):
        if not enc or enc.lower() in tried:
            continue
        tried.append(enc.lower())
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        note = None
        if declared and enc.lower() != declared.lower():
            note = f"bytes are not valid {declared} as the XML declaration claims; decoded as {enc} instead"
        elif not declared and enc.lower() not in ("utf-8", "utf-8-sig"):
            # XML's default for an undeclared document is UTF-8, so a non-UTF-8 body is a source defect either way.
            note = f"no encoding declared and the bytes are not valid UTF-8 (XML's default); decoded as {enc} instead"
        return text, enc, note
    return data.decode("utf-8", errors="replace"), "utf-8", "undecodable bytes replaced with U+FFFD"


def _repair_xml_text(s):
    """Fix the well-formedness faults that are safe to fix without guessing.  Returns (repaired, notes).

    NOT fixed, because any fix would be a guess at clinical content: a raw '<' in text, a mismatched or unclosed
    tag, a truncated file.  Those still raise, and the file is still reported as failed."""
    notes = []
    lead = len(s) - len(s.lstrip("﻿ \t\r\n"))
    if lead:
        s = s[lead:]
        notes.append(f"stripped {lead} character(s) of whitespace/BOM ahead of the XML declaration")
    s = _XML_DECL_RE.sub("", s, count=1)  # re-added below, restamped to the encoding actually written

    clean = _XML_ILLEGAL.sub("", s)
    if clean != s:
        notes.append(f"stripped {len(s) - len(clean)} XML-illegal control character(s) from the source")
        s = clean

    counts = Counter()

    def fix(m):
        ref = m.group(0)
        if ref == "&":
            counts["bare"] += 1
            return "&amp;"
        body = ref[1:-1]
        if body.startswith("#"):
            try:
                cp = int(body[2:], 16) if body[1] in "xX" else int(body[1:])
            except ValueError:
                counts["bare"] += 1
                return "&amp;" + ref[1:]
            if _legal_cp(cp):
                return ref
            counts["illegal_ref"] += 1
            return ""
        if body in _XML_BUILTIN_ENTS:
            return ref
        cp = name2codepoint.get(body)
        if cp is not None and _legal_cp(cp):
            counts["named"] += 1
            return f"&#{cp};"
        counts["bare"] += 1
        return "&amp;" + ref[1:]

    s = _REF_RE.sub(fix, s)
    if counts["bare"]:
        notes.append(f"escaped {counts['bare']} bare/undefined '&' reference(s) as &amp;")
    if counts["named"]:
        notes.append(f"rewrote {counts['named']} HTML named entity reference(s) (&nbsp; etc.) as numeric character references")
    if counts["illegal_ref"]:
        notes.append(f"dropped {counts['illegal_ref']} character reference(s) to codepoints XML 1.0 cannot represent")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + s, notes


def load_source(path):
    """Parse the input document.  Returns (root, notes).

    Fast path first: a well-formed file is parsed once and touched by none of the above, so the repair costs the
    3457 good charts nothing.  Only a ParseError re-reads the bytes and retries on a repaired copy."""
    try:
        return ET.parse(path, parser=ET.XMLParser()).getroot(), []
    except ET.ParseError as e:
        first = str(e)  # rebound: Python unbinds the 'as' name when the except block ends
    with open(path, "rb") as f:
        data = f.read()
    text, enc, dec_note = _decode_source(data)
    repaired, notes = _repair_xml_text(text)
    if dec_note:
        notes.insert(0, dec_note)
    try:
        root = ET.fromstring(repaired.encode("utf-8"))
    except ET.ParseError as e:
        raise ValueError(f"input XML is not well-formed and could not be repaired: {e} "
                         f"(before repair: {first})") from None
    notes.insert(0, f"source XML was not well-formed ({first}) and was repaired before parsing — "
                    "the recovered document is below, but have the source system's export checked")
    return root, notes


# ----------------------------------------------------------------------------------------------- transform
def transform(path, dos_from=None, dos_to=None):
    root, load_notes = load_source(path)
    if local(root.tag) != "ClinicalDocument":
        raise ValueError(f"Not a CDA ClinicalDocument root: {root.tag}")
    ctx = Ctx(root)
    ctx.warnings[:0] = load_notes  # ahead of Ctx's own warnings: "the source was broken" leads
    header = parse_header(root, ctx.nar)
    sections = parse_sections(root, ctx)
    dos, undated = group_by_dos(sections, header)
    filter_note = None
    if dos_from or dos_to:
        total = len(dos)
        dos, dropped = filter_groups(dos, dos_from, dos_to)
        filter_note = f"Date filter {dos_from or '…'} → {dos_to or '…'}: showing {len(dos)} of {total} dates ({dropped} outside the range omitted; the section table above is unfiltered)."
    parent_map = {c: p for p in root.iter() for c in p}
    items_total = sum(len(s["items"]) for s in sections)
    dropped_total = sum(s["dropped"] for s in sections)
    summary = {
        "version": __version__, "file": os.path.basename(path), "sections": len(sections), "entries": sum(s["entryCount"] for s in sections),
        "items": items_total, "entries_dropped": dropped_total, "dos_groups": len(dos),
        "visit_dates": sum(1 for g in dos.values() if g["kind"] == "visit"), "no_visit_dates": sum(1 for g in dos.values() if g["kind"] == "no_visit"),
        "planned_only_dates": sum(1 for g in dos.values() if g["kind"] == "planned"), "undated_items": sum(len(v) for v in undated.values()),
        "sections_generic": [s["title"] for s in sections if s["handler"] == "generic" and s["entryCount"]],
        "sections_with_drops": {s["title"]: s["dropped"] for s in sections if s["dropped"]}, "warnings": list(ctx.warnings),
        "import_stamps": sorted(ctx.bulk_stamps),
        "narratives_carried": sum(1 for s in sections if s.get("_text") is not None and text(s["_text"])),
        "narrative_anchors": sum(len(s.get("nar_anchors") or []) for s in sections),
        "narrative_anchors_resolved": len(ctx.nar.resolved_ids),
        "narrative_broken_refs": {s["title"]: s["nar_broken_refs"] for s in sections if s.get("nar_broken_refs")},
    }
    return {"header": header, "sections": sections, "dos": dos, "undated": undated, "warnings": ctx.warnings, "filter_note": filter_note,
            "summary": summary, "_root": root, "_parent_map": parent_map}


# ----------------------------------------------------------------------------------------------- CLI
def expand_inputs(args, recursive=True, exclude_dir=None):
    """Resolve files, directories and globs to a de-duplicated list of (path, sub_dir) pairs.

    A directory is scanned for *.xml at every level (top level only with --no-recursive).  ``sub_dir`` is the
    file's directory relative to the input folder it was found under ("" for a file or glob given directly), so
    ``--out-dir`` can mirror the input tree: input/2323/a.xml -> output/2323/a_dosfirst.xml.

    Outputs this tool has already written are skipped, as is anything under ``exclude_dir`` (the output folder),
    so re-running over the same folder does not transform its own results.
    """
    out = []
    for a in args:
        if os.path.isdir(a):
            pat = os.path.join(a, "**", "*.xml") if recursive else os.path.join(a, "*.xml")
            for p in sorted(glob.glob(pat, recursive=recursive)):
                out.append((p, os.path.relpath(os.path.dirname(os.path.abspath(p)), os.path.abspath(a))))
        elif any(ch in a for ch in "*?["):
            out.extend((p, "") for p in sorted(glob.glob(a, recursive=recursive)))
        else:
            out.append((a, ""))
    excl = os.path.abspath(exclude_dir) + os.sep if exclude_dir else None
    seen, files = set(), []
    for p, sub in out:
        rp = os.path.abspath(p)
        if rp in seen or os.path.basename(p).startswith("."):
            continue
        if re.search(r"_dosfirst(_v\d+)?(_updated)?(_\d+)?\.xml$", os.path.basename(p)):
            continue  # our own output
        if excl and rp.startswith(excl):
            continue  # inside the output folder
        seen.add(rp)
        files.append((p, "" if sub in (".", os.curdir) else sub))
    return files


def stamp_subdir(sub, stamp):
    """Append the run date to the top-level folder of a mirrored path: 11/deep -> 11_20260902/deep.

    Only the first segment is stamped; deeper folders keep their names so the date is not repeated at every level.
    """
    if not sub or not stamp:
        return sub
    parts = sub.split(os.sep)
    parts[0] = f"{parts[0]}_{stamp}"
    return os.sep.join(parts)


def _run_tz(name):
    """The timezone the run clock reports in: --timezone if given, otherwise whatever this machine calls local.

    The option exists because "local" is not always the clock the user is looking at.  Under WSL the Linux side
    commonly sits on Etc/UTC while the Windows host is on the user's real zone, so datetime.now() -- which is
    already local -- reports a time hours away from the clock on screen (UTC vs Asia/Kathmandu is 5h45m, enough
    to put the run on the wrong calendar day).  Nothing here can auto-detect the host zone without guessing, so
    the zone is named explicitly and then printed alongside every timestamp, and the run-date folder stamp is
    taken from the same clock so the folder name and the log can never disagree."""
    if not name:
        return _datetime.now().astimezone().tzinfo
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception as e:
        print(f"--timezone {name!r} not usable ({e.__class__.__name__}: {e}); "
              f"falling back to this machine's local zone", file=sys.stderr)
        return _datetime.now().astimezone().tzinfo


DEFAULT_OUT_DIR = "output"  # where outputs go when -o is not given: a folder of its own, never among the inputs


ERROR_REPORT_HEADER = "# ccda_dosfirst consolidated error report"


def write_error_report(path, failures, total, started_at):
    """Write every failure of a run into ONE file, instead of a .error.txt beside each chart that failed.

    Scattered per-file reports mirror the input tree, so finding out which charts failed means opening folder
    after folder -- the more nested the input, the worse it gets.  This puts them all in one place: an index
    first, so the whole damage is readable at a glance, then the full traceback for each.

    Written by the parent, never by a worker: the failures arrive as picklable dicts from _process_one, so N
    processes never contend over one file and the report's order follows the input order.

    Returns the path written, or None for a clean run -- in which case a report this tool left from an earlier
    run of the same output folder is deleted, so a stale file can never be mistaken for this run's result.
    Only a file whose first line is our own header is touched; anything else of that name is left alone."""
    if not failures:
        try:
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    ours = f.readline().startswith(ERROR_REPORT_HEADER)
                if ours:
                    os.remove(path)
        except OSError:
            pass
        return None
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{ERROR_REPORT_HEADER}\n")
        f.write(f"run started: {started_at:%Y-%m-%d %H:%M:%S %Z}\n")
        f.write(f"failed: {len(failures)} of {total} file(s)\n\n")
        f.write("FAILED FILES\n")
        for i, fl in enumerate(failures, 1):
            f.write(f"{i:>4}. {fl['label']}\n      {fl['error']}\n")
        f.write("\n")
        for i, fl in enumerate(failures, 1):
            f.write("=" * 100 + "\n")
            f.write(f"[{i}/{len(failures)}] {fl['label']}\n")
            f.write(f"  input:  {fl['input']}\n")
            f.write(f"  output: {fl['base']}.*\n")
            f.write(f"  error:  {fl['error']}\n")
            f.write("-" * 100 + "\n")
            f.write(fl["trace"].rstrip() + "\n\n")
    return path


# --------------------------------------------------------------------------------------- batch worker
def _hms(sec):
    """'2h 14m 03s' / '3m 07s' / '4.85s' — a thousands-of-files run should not report its time in raw seconds."""
    if sec < 60:
        return f"{sec:.2f}s"
    m, s = divmod(int(round(sec)), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def _have_lxml():
    try:
        import lxml.etree  # noqa: F401
        return True
    except ImportError:
        return False


_RENDER_CACHE = {}


def _renderer(xsl_path):
    """Compile cda.xsl once per process and reuse it.

    render_html.render() re-parses and re-compiles the stylesheet on every single call, for a compiled form that
    never changes — redundant work per file, and the more so the larger the batch.  Not benchmarked here: lxml is
    not installed in this checkout, so the --html path could not be timed.
    Byte-for-byte the same output as render_html.render(), which stays the standalone entry point."""
    fn = _RENDER_CACHE.get(xsl_path)
    if fn is not None:
        return fn
    from lxml import etree
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
    xslt = etree.XSLT(etree.parse(xsl_path, parser), access_control=etree.XSLTAccessControl(read_file=True))

    def fn(xml_path, html_path):
        result = xslt(etree.parse(xml_path, parser))
        for e in xslt.error_log:  # xsl:message output (e.g. "attr is not legal in td") — informational
            if "is not legal in" not in e.message:
                print(f"  [xslt] {e.message}", file=sys.stderr)
        with open(html_path, "wb") as f:
            f.write(etree.tostring(result, pretty_print=True, method="html", encoding="utf-8",
                                   doctype=result.docinfo.doctype))

    _RENDER_CACHE[xsl_path] = fn
    return fn


_OPTS = None  # set once per worker process by the pool initializer, so the option dict is not pickled per file


def _worker_init(opts):
    global _OPTS
    _OPTS = opts


def _process_one(job):
    """Transform one chart and write its outputs.

    Returns a picklable result — the stderr lines to print, plus the two numbers main() aggregates.  No Element
    ever crosses the process boundary, which is what lets this run in a worker pool; the printing stays in the
    parent so a --jobs run reads exactly like a serial one.
    """
    path, base, label = job
    o = _OPTS
    lines = []
    try:
        doc = transform(path, o["lo"], o["hi"])
        s = doc["summary"]
        if o["json"]:
            with open(base + ".json", "w", encoding="utf-8") as f:
                json.dump(strip_private({k: v for k, v in doc.items() if not k.startswith("_")}), f, indent=1, default=str)
        if o["text"]:
            with open(base + ".txt", "w", encoding="utf-8") as f:
                f.write(render_text(doc) + "\n")
        if o["xml"] or o["html"]:
            root = build_dosfirst_xml(doc["_root"], doc, doc["_parent_map"], copy_entries=o["copy_entries"],
                                      header_dos=o["header_dos"], audit_tables=o["audit_tables"],
                                      encounters_section=o["encounters_section"], date_basis=o["date_basis"])
            _indent(root)
            stripped = scrub_tree(root)
            if stripped:
                s["warnings"].append(f"stripped {stripped} XML-illegal control character(s) from the output")
            xml_path = base + ".xml"
            write_dosfirst_xml(root, xml_path)
            if o["html"]:
                _renderer(o["xsl"])(xml_path, base + ".html")
                if not o["xml"]:
                    os.remove(xml_path)
        lines.append(f"{label}: sections={s['sections']} entries={s['entries']} items={s['items']} dropped={s['entries_dropped']} dates={s['dos_groups']} (visits={s['visit_dates']}, no-visit={s['no_visit_dates']}, planned-only={s['planned_only_dates']}) undated={s['undated_items']}")
        if doc.get("_header_dos_note"):
            lines.append(f"   · {doc['_header_dos_note']}")
        for wmsg in s["warnings"]:
            lines.append(f"   ! {wmsg}")
        return {"ok": True, "drop": s["entries_dropped"], "lines": lines, "error": None}
    except Exception as e:  # per-file isolation: one bad chart never takes the batch (or a worker) down
        # The traceback is handed back to the parent rather than written here: one consolidated report beats a
        # .error.txt buried beside each bad chart, and it keeps every worker out of a shared file.
        lines.append(f"{label}: FAILED — {e.__class__.__name__}: {e}")
        return {"ok": False, "drop": 0, "lines": lines,
                "error": {"label": label, "input": os.path.abspath(path), "base": base,
                          "error": f"{e.__class__.__name__}: {e}", "trace": traceback.format_exc()}}


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # Captured as an aware instant now and re-rendered in the run timezone once --timezone is parsed,
    # so choosing a zone never costs accuracy about when the run actually began.
    started_at = _datetime.now(_timezone.utc)
    ap = argparse.ArgumentParser(
        description="Regroup sectional C-CDA(s) into a DOS-first (date-of-service) view (v5, parallel).",
        epilog="Runs one worker process per CPU core by default; -j 1 forces serial.  Outputs per chart, "
               "mirrored under -o (default ./output): <stem>_dosfirst_v5.xml (always, unless --no-xml; carries an "
               "xml-stylesheet PI for CCDA.xsl), plus .json/.txt/.html on request.  Failures never stop the "
               "batch: all of them are collected in one error.txt at the top of the output folder.  "
               "Exit 1 if any file failed, 2 with --strict if any entry was dropped.")
    ap.add_argument("inputs", nargs="+", help="CCDA .xml file(s), folder(s) or glob(s); a folder is scanned for *.xml at every level")
    ap.add_argument("-o", "--out-dir", "--output", dest="out_dir", default=DEFAULT_OUT_DIR,
                    help=f"write outputs here, mirroring the input folder tree; created if missing "
                         f"(default: ./{DEFAULT_OUT_DIR}).  Outputs never land among the inputs unless you "
                         f"point this at the input folder yourself")
    ap.add_argument("--no-recursive", action="store_true", help="scan only the top level of each input folder")
    ap.add_argument("-r", "--recursive", action="store_true", help=argparse.SUPPRESS)  # now the default; kept for back-compat
    ap.add_argument("--html", action="store_true", help="also write <stem>_dosfirst.html (needs lxml)")
    ap.add_argument("--xml", action="store_true", help="write <stem>_dosfirst.xml (always on unless --no-xml)")
    ap.add_argument("--no-xml", action="store_true", help="suppress the default DOS-first XML output")
    ap.add_argument("--json", action="store_true", help="write <stem>_dosfirst.json")
    ap.add_argument("--text", action="store_true", help="write <stem>_dosfirst.txt")
    ap.add_argument("--year", type=int, help="keep only dates in this year (e.g. 2025)")
    ap.add_argument("--dos-from", help="keep dates >= YYYY-MM-DD")
    ap.add_argument("--dos-to", help="keep dates <= YYYY-MM-DD")
    ap.add_argument("--no-copy-entries", action="store_true", help="do not embed the original <entry> elements in the output XML (smaller files)")
    ap.add_argument("--date-basis", action="store_true",
                    help="widen the per-row Notes column back to 'Date basis / notes': how each row's date was derived "
                         "plus processing flags. Off by default -- it is transform audit, not clinical content, and a "
                         "clinical NLP reader ingests it as text. Always present per item in --json")
    ap.add_argument("--encounters-section", choices=("inline", "separate"), default="inline",
                    help="inline (default): each encounter entry sits in its own DOS section, the DOS-first arrangement, and "
                         "a visit section is coded LOINC 46240-8. separate: the source Encounters section is carried through "
                         "whole as one 46240-8 section and the date sections hold narrative only — use this when a reader "
                         "expects a single encounters list, as the source document has")
    ap.add_argument("--audit-tables", action="store_true",
                    help="include the 'Sections in source document' and 'Dates found' tables and the processing notes in "
                         "the output narrative. Off by default: they describe the transform, not the patient, and a clinical "
                         "NLP reader turns their count cells into spurious CPT codes. Same data is in --json/--text/stderr")
    ap.add_argument("--header-dos", choices=("latest", "earliest", "span", "none"), default="latest",
                    help="fill componentOf/encompassingEncounter/effectiveTime (the header 'Encounter' row) from a performed visit, "
                         "and replace an export-stamp serviceEvent date with the visit span: latest visit (default), earliest, "
                         "span over all visits, or none to leave the source header untouched")
    ap.add_argument("-j", "--jobs", type=int, default=0, metavar="N",
                    help=f"process N files in parallel worker processes.  Default 0 = one per CPU core "
                         f"(this machine reports {os.cpu_count() or 1}); pass 1 to force the old serial behaviour. "
                         "Charts are independent of each other, so this is where a thousands-of-files run gets its "
                         "time back. Output order and content are identical either way")
    ap.add_argument("--timezone", metavar="ZONE",
                    help="IANA zone the run clock reports in, e.g. Asia/Kathmandu (default: this machine's local "
                         "zone). Affects the started/completed lines, the error report and the run-date folder "
                         "stamp -- never a clinical date, which is always read as written in the source. Useful "
                         "under WSL, where Linux is often on UTC while the host is on your real zone")
    ap.add_argument("--strict", action="store_true", help="exit 2 if any entry was dropped in any file")
    ap.add_argument("--xsl", help="path to cda.xsl (default: ./xsl/cda.xsl next to this script)")
    a = ap.parse_args(argv)
    # The run clock: --timezone, else this machine's local zone.  Fixed once here so every timestamp in the run
    # -- stderr, the error report, the run-date folder stamp -- comes off the same clock.
    tz = _run_tz(a.timezone)
    started_at = started_at.astimezone(tz)
    # DOS-first XML is always produced unless explicitly suppressed; --html/--json/--text are additive on top of it.
    a.xml = not a.no_xml
    lo, hi = a.dos_from, a.dos_to
    if a.year:
        lo, hi = f"{a.year}-01-01", f"{a.year}-12-31"
    inputs = expand_inputs(a.inputs, not a.no_recursive, a.out_dir)
    if not inputs:
        print("no .xml input files found in: " + ", ".join(a.inputs), file=sys.stderr)
        return 1
    print(f"{len(inputs)} file(s) to process"
          + f" -> {os.path.abspath(a.out_dir)}", file=sys.stderr)
    if a.html and not _have_lxml():
        print("--html needs lxml; falling back to --xml", file=sys.stderr)
        a.xml, a.html = True, False
    xsl = a.xsl or os.path.join(os.path.dirname(os.path.abspath(__file__)), "xsl", "cda.xsl")
    run_stamp = started_at.strftime("%Y%m%d")  # one date for the whole run, from the same clock it reports
    opts = {"lo": lo, "hi": hi, "json": a.json, "text": a.text, "xml": a.xml, "html": a.html, "xsl": xsl,
            "copy_entries": not a.no_copy_entries, "header_dos": a.header_dos, "audit_tables": a.audit_tables,
            "encounters_section": a.encounters_section, "date_basis": a.date_basis}

    # Output folders are created here, in the parent, not in the workers: makedirs on the same path from several
    # processes at once is a race the workers have no reason to run.
    jobs = []
    for path, sub in inputs:
        stem = os.path.splitext(os.path.basename(path))[0]
        # With --out-dir the input folder tree is reproduced under it, its top folder stamped with the run date;
        # otherwise each output sits beside its input.
        out_dir = os.path.join(a.out_dir, stamp_subdir(sub, run_stamp))
        os.makedirs(out_dir, exist_ok=True)
        jobs.append((path, os.path.join(out_dir, stem + "_dosfirst"), os.path.join(sub, stem) if sub else stem))

    cores = os.cpu_count() or 1
    workers = min(cores, len(jobs)) if a.jobs == 0 else max(1, a.jobs)
    t0 = time.perf_counter()  # monotonic: a clock change mid-run cannot make the elapsed time negative
    print(f"started: {started_at:%Y-%m-%d %H:%M:%S %Z}  ({len(jobs)} file(s), "
          + (f"{workers} worker process(es) of {cores} core(s))" if workers > 1 else "serial)"), file=sys.stderr)

    if workers > 1 and len(jobs) > 1:
        import concurrent.futures as _cf
        # chunksize batches jobs per worker: one IPC round trip per chunk instead of per file.
        chunk = max(1, min(16, len(jobs) // (workers * 4) or 1))
        with _cf.ProcessPoolExecutor(max_workers=workers, initializer=_worker_init, initargs=(opts,)) as ex:
            results = list(ex.map(_process_one, jobs, chunksize=chunk))  # map keeps input order
    else:
        _worker_init(opts)
        results = [_process_one(j) for j in jobs]

    # All printing happens here, in input order, so a parallel run's stderr is identical to a serial one's.
    ok, failed, any_drop, failures = 0, 0, False, []
    for r in results:
        if r["ok"]:
            ok += 1
            any_drop = any_drop or bool(r["drop"])
        else:
            failed += 1
            if r.get("error"):
                failures.append(r["error"])
        for line in r["lines"]:
            print(line, file=sys.stderr)
    # One report for the whole run, at the top of the output folder -- not one per failed chart, buried in the
    # mirrored tree next to the input that produced it.  Input order, because `results` is in input order.
    err_path = write_error_report(os.path.join(a.out_dir, "error.txt"),
                                  failures, len(jobs), started_at)
    elapsed = time.perf_counter() - t0
    finished_at = _datetime.now(tz)
    print(f"done: {ok} ok, {failed} failed", file=sys.stderr)
    if err_path:
        print(f"errors:    {failed} failure(s) detailed in {err_path}", file=sys.stderr)
    print(f"started:   {started_at:%Y-%m-%d %H:%M:%S %Z}", file=sys.stderr)
    print(f"completed: {finished_at:%Y-%m-%d %H:%M:%S %Z}", file=sys.stderr)
    print(f"elapsed:   {_hms(elapsed)}"
          + (f"  ({elapsed / len(jobs):.2f}s per file avg)" if jobs else ""), file=sys.stderr)
    if failed:
        return 1
    if a.strict and any_drop:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
