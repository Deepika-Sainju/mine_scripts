#!/usr/bin/env python3
r"""
flatfile_to_ccda.py (v3) — Build one C-CDA XML per member from the PCW PostgreSQL tables.

Inputs (one table each; names come from .env, see below):
    memberdemo       demographics                     -> recordTarget
    idcrosswalk      RGN_CD | MRN_NB | MBI            -> extra patient id (MBI); memberdemo.MBI is the fallback
    encounternotes   one row per encounter note       -> Encounters section + Notes section
    visitdiagnosis   ICD-10 list per encounter        -> diagnoses on the matching encounter, else Problems
    internallab      one row per lab result           -> Results section

Rows of all five tables are matched on (RGN_CD, mrn_padded).  in_exclude_list / in_delete_list are not used.
Patient id = mrn_padded; output is <out_dir>/<RGN_CD>/<mrn_padded>.xml (one folder per region).

Settings come from a .env file next to this script (or --env-file): PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD,
CCDA_DEMO_TABLE, CCDA_CROSSWALK_TABLE, CCDA_ENCOUNTER_TABLE, CCDA_DIAGNOSIS_TABLE, CCDA_LAB_TABLE, CCDA_OUT_DIR.  First found wins:
command-line flag, then a variable already set in the environment, then .env, then the default.

Layout (DOS-first; CDA R2 XSD-valid, rendered with cda.xsl): one top-level section per date of service, newest first,
titled "DOS MM/DD/YYYY — <encounter> — <provider>", holding the standard C-CDA sections for that day (Encounters,
Problems, Notes, Results, each only when it has data).  A last "Undated" section holds whatever has no usable date.
Dates are written as HL7 TS ("20250403" / "20250403103400"), local time as written, no timezone.

Visits: the notes file has one row per note.  Rows sharing an ENCOUNTER_ID are one visit; rows without one are grouped
by date + ENCOUNTER_NAME.  Every distinct note author is a performer on the visit; each note keeps its own author.

Diagnoses: visit diagnosis rows carry no date.  When their ENCOUNTER_ID matches a dated visit they go to that DOS's
Problems section; otherwise to Undated, with a warning.  Each ICD-10 code is its own coded problem, with no display
name: DX_NAME cannot be split on commas reliably ("MAJOR DEPRESSIVE DISORDER, RECURRENT EPISODE, SEVERE" is one name),
so the full DX_NAME list is kept verbatim in the narrative instead.

Usage (Windows Command Prompt):
    python flatfile_to_ccda.py
        -> everything from v3\.env; writes <CCDA_OUT_DIR>\<RGN_CD>\<mrn_padded>.xml
    python flatfile_to_ccda.py -o "C:\data\PCW_ccda" --lab-table pcw.internallab_test
    python flatfile_to_ccda.py --env-file "C:\secrets\pcw_prod.env" --render-html
        -> XML in <out>\<RGN_CD>, HTML in <out>_html\<RGN_CD> (render_html.py; needs: pip install lxml)
Quote paths that contain spaces.  Python 3.9+; needs psycopg2 (pip install psycopg2-binary), and lxml for --render-html.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys
import uuid
from collections import OrderedDict, defaultdict
from datetime import date, datetime
from xml.etree import ElementTree as ET

V3 = "urn:hl7-org:v3"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
SDTC = "urn:hl7-org:sdtc"
ET.register_namespace("", V3)
ET.register_namespace("xsi", XSI)
ET.register_namespace("sdtc", SDTC)
XSI_TYPE = f"{{{XSI}}}type"

LOINC = "2.16.840.1.113883.6.1"
SNOMED = "2.16.840.1.113883.6.96"
ICD10 = "2.16.840.1.113883.6.90"
CPT = "2.16.840.1.113883.6.12"
NPI_ROOT = "2.16.840.1.113883.4.6"
MBI_ROOT = "2.16.840.1.113883.4.927"
# Placeholder OIDs for this organisation's own identifiers — replace with the real assigning-authority OIDs if known.
MEMBER_ID_ROOT = "2.16.840.1.113883.19.5.1"
MRN_ROOT = "2.16.840.1.113883.19.5.2"
PROVIDER_ID_ROOT = "2.16.840.1.113883.19.5.3"
ENCOUNTER_ID_ROOT = "2.16.840.1.113883.19.5.4"
ID_NAMESPACE = uuid.UUID("6f1c2d4e-8a3b-4c5d-9e7f-0a1b2c3d4e5f")  # uuid5 namespace -> ids are stable across reruns


# family -> (.env variable holding its schema.table, default table name)
TABLES = OrderedDict([
    ("demo", ("CCDA_DEMO_TABLE", "memberdemo")),
    ("crosswalk", ("CCDA_CROSSWALK_TABLE", "idcrosswalk")),
    ("encounter", ("CCDA_ENCOUNTER_TABLE", "encounternotes")),
    ("diagnosis", ("CCDA_DIAGNOSIS_TABLE", "visitdiagnosis")),
    ("lab", ("CCDA_LAB_TABLE", "internallab")),
])
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV = os.path.join(HERE, ".env")

_XML_ILLEGAL = re.compile("[^\\x09\\x0a\\x0d\\x20-\\ud7ff\\ue000-\\ufffd\\U00010000-\\U0010ffff]")
# Note text: typographic characters with a plain keyboard equivalent are swapped for it (so "patient’s" doesn't split);
# NOTE_KEEP survives as is (clinical symbols whose meaning would be lost); anything else that isn't a keyboard
# character -- the source system's separators, which arrive as U+FFFD or control bytes -- becomes a line break.
NOTE_ASCII = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
                            "\u00a0": " ", "\u2026": "..."})
NOTE_KEEP = "°±µ×÷"
_NOTE_BREAK = re.compile(f"[^\\t\\n\\x20-\\x7e{NOTE_KEEP}]")
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
INTERPRETATION = {"H": "High", "HH": "Critical high", "L": "Low", "LL": "Critical low", "A": "Abnormal",
                  "AA": "Critical abnormal", "N": "Normal"}
GENDER = {"M": ("M", "Male"), "MALE": ("M", "Male"), "F": ("F", "Female"), "FEMALE": ("F", "Female")}

WARNINGS: list[str] = []


def warn(msg):
    WARNINGS.append(msg)
    print(f"WARNING: {msg}", file=sys.stderr)


# ----------------------------------------------------------------------------------------------- dates
_DATE_FORMATS = (
    ("%Y-%m-%d %H:%M:%S.%f", True), ("%Y-%m-%d %H:%M:%S", True), ("%Y-%m-%d %H:%M", True),
    ("%Y-%m-%dT%H:%M:%S.%f", True), ("%Y-%m-%dT%H:%M:%S", True), ("%Y-%m-%dT%H:%M", True), ("%Y-%m-%d", False),
    ("%m/%d/%Y %H:%M:%S", True), ("%m/%d/%Y %H:%M", True), ("%m/%d/%Y", False),
    ("%Y%m%d%H%M%S", True), ("%Y%m%d%H%M", True), ("%Y%m%d", False),
)


def hl7_ts(value, with_time=True, strict=False):
    """Flat-file date/datetime -> HL7 TS 'YYYYMMDD' or 'YYYYMMDDhhmmss' (local time as written, no timezone).
    with_time=False always returns the date only.  A value in none of _DATE_FORMATS is returned exactly as written
    (with a warning) so nothing from the source is lost; strict=True returns None for it instead, silently."""
    if not value:
        return None
    v = value.strip()
    for fmt, has_time in _DATE_FORMATS:
        try:
            dt = datetime.strptime(v, fmt)
        except ValueError:
            continue
        if has_time and with_time:
            return dt.strftime("%Y%m%d%H%M%S")
        return dt.strftime("%Y%m%d")
    if strict:
        return None
    warn(f"unrecognised date {value!r} — kept as written in the narrative; the item goes under Undated")
    return v


def is_ts(s):
    """True for a converted HL7 TS (all digits, at least YYYYMMDD); False for a date kept as written."""
    return bool(s) and s.isdigit() and len(s) >= 8


def us_date(ts):
    """HL7 TS -> 'MM/DD/YYYY[ hh:mm]' for the human-readable narrative (as in the athena samples)."""
    if not ts:
        return ""
    if not is_ts(ts):
        return ts
    out = f"{ts[4:6]}/{ts[6:8]}/{ts[:4]}"
    if len(ts) >= 12:
        out += f" {ts[8:10]}:{ts[10:12]}"
    return out


# ----------------------------------------------------------------------------------------------- settings (.env)
def load_env(path):
    """KEY=value lines from a .env file into os.environ.  Blank lines and # comments are skipped, surrounding quotes
    removed when they wrap the whole value ("abc" -> abc, but "schema"."table" is kept as is).  A variable already
    set in the real environment wins over the file.  Returns False if there is no file."""
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = (s.strip() for s in line.split("=", 1))
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'" and v[0] not in v[1:-1]:
                v = v[1:-1]
            os.environ.setdefault(k, v)
    return True


# ----------------------------------------------------------------------------------------------- reading the tables
def clean(v):
    if v is None:
        return ""
    return _XML_ILLEGAL.sub("", v).strip()


def to_text(v):
    """DB value -> the same text the flat files had, so hl7_ts() and the builders work unchanged.
    timestamp / timestamptz -> 'YYYY-MM-DD hh:mm:ss' (timezone dropped, time as stored), date -> 'YYYY-MM-DD'."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    return clean(str(v))


def table_parts(name):
    """'schema.table' / '"PWC_test".member_demo' -> ['PWC_test', 'member_demo'].  Dots inside double quotes are part
    of the name and "" is a literal quote, as in SQL.  Each part is then used exactly as written (case-sensitive)."""
    parts, cur, quoted, i = [], "", False, 0
    while i < len(name):
        c = name[i]
        if c == '"':
            if quoted and name[i + 1:i + 2] == '"':
                cur += '"'
                i += 1
            else:
                quoted = not quoted
        elif c == "." and not quoted:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += c
        i += 1
    parts.append(cur.strip())
    return parts


def load_table(conn, family, table):
    """All rows of [schema.]table -> list of dicts keyed by UPPER-CASE column name (mrn_padded -> MRN_PADDED,
    Current_ICD10_LIST -> CURRENT_ICD10_LIST).  A server-side cursor streams big note tables in chunks."""
    from psycopg2 import sql
    query = sql.SQL("SELECT * FROM {}").format(sql.Identifier(*table_parts(table)))
    rows = []
    with conn.cursor(name=f"ccda_{family}") as cur:
        cur.itersize = 5000
        cur.execute(query)
        cols = None
        for rec in cur:
            if cols is None:
                cols = [d[0].upper() for d in cur.description]
                if not {"RGN_CD", "MRN_PADDED"} <= set(cols):
                    warn(f"{table}: no RGN_CD/mrn_padded column — every row will be skipped")
            # CONTENT stays raw: clean() would delete the separator bytes note_text() turns into line breaks
            r = {c: ("" if v is None else str(v)) if c == "CONTENT" else to_text(v) for c, v in zip(cols, rec)}
            r["_SOURCE_FILE"] = table
            rows.append(r)
    print(f"  {table}: {len(rows)} row(s)")
    return rows


def member_key(row):
    """(RGN_CD, mrn_padded): the region is part of the key so the same padded MRN in two regions stays two members."""
    rgn, mrn = row.get("RGN_CD", ""), row.get("MRN_PADDED", "")
    if not rgn or not mrn:
        warn(f"{row.get('_SOURCE_FILE')}: row without RGN_CD/mrn_padded skipped ({rgn!r}, {mrn!r})")
        return None
    return rgn, mrn


# ----------------------------------------------------------------------------------------------- xml helpers
def sub(parent, tag, text=None, **attrs):
    xsi_type = attrs.pop("xsi_type", None)
    el = ET.SubElement(parent, f"{{{V3}}}{tag}", {k: v for k, v in attrs.items() if v not in (None, "")})
    if xsi_type:
        el.set(XSI_TYPE, xsi_type)
    if text not in (None, ""):
        el.text = text
    return el


def new_id(*parts):
    return str(uuid.uuid5(ID_NAMESPACE, "|".join(str(p) for p in parts)))


def ts_el(parent, tag, ts):
    return sub(parent, tag, value=ts) if is_ts(ts) else sub(parent, tag, nullFlavor="UNK")


def html_to_text(s):
    """CONTENT with MIME_TYPE text/html -> plain text with line breaks (tags dropped, entities decoded)."""
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\f\v]+", " ", s)
    return re.sub(r"\s*\n\s*", "\n", s).strip()


def multiline(parent, text):
    """Put text into a narrative element, turning newlines into <br/> so the note keeps its shape."""
    lines = (text or "").splitlines() or [""]
    parent.text = lines[0]
    for line in lines[1:]:
        br = sub(parent, "br")
        br.tail = line


def narrative_table(section, headers):
    text = sub(section, "text")
    table = sub(text, "table", border="1", width="100%")
    tr = sub(sub(table, "thead"), "tr")
    for h in headers:
        sub(tr, "th", h)
    return sub(table, "tbody")


def person_name(parent, raw):
    """'LAST, FIRST M' -> <name><given/><family/></name>; anything else is kept as a single text name."""
    name = sub(parent, "name")
    if "," in raw:
        last, rest = [p.strip() for p in raw.split(",", 1)]
        for g in rest.split():
            sub(name, "given", g)
        sub(name, "family", last)
    else:
        name.text = raw
    return name


def new_section(body, template, code, code_name, title):
    sec = sub(sub(body, "component"), "section")
    sub(sec, "templateId", root=template)
    sub(sec, "code", code=code, codeSystem=LOINC, codeSystemName="LOINC", displayName=code_name)
    sub(sec, "title", title)
    return sec


# ----------------------------------------------------------------------------------------------- header
def build_header(doc, member_id, demo, mbi, all_dates):
    sub(doc, "realmCode", code="US")
    sub(doc, "typeId", root="2.16.840.1.113883.1.3", extension="POCD_HD000040")
    sub(doc, "templateId", root="2.16.840.1.113883.10.20.22.1.1", extension="2015-08-01")
    sub(doc, "templateId", root="2.16.840.1.113883.10.20.22.1.2", extension="2015-08-01")
    sub(doc, "id", root=new_id(member_id, "document"))
    sub(doc, "code", code="34133-9", codeSystem=LOINC, codeSystemName="LOINC", displayName="Summarization of Episode Note")
    sub(doc, "title", "PCW Clinical Summary")
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    sub(doc, "effectiveTime", value=now)
    sub(doc, "confidentialityCode", code="N", codeSystem="2.16.840.1.113883.5.25")
    sub(doc, "languageCode", code="en-US")

    pr = sub(sub(doc, "recordTarget"), "patientRole")
    sub(pr, "id", root=MEMBER_ID_ROOT, extension=demo.get("MRN_PADDED"),
        assigningAuthorityName=f"PCW patient id (mrn_padded, {demo.get('RGN_CD')})")
    if demo.get("MRN_NB"):
        sub(pr, "id", root=MRN_ROOT, extension=demo["MRN_NB"], assigningAuthorityName=f"MRN ({demo.get('RGN_CD')})")
    if mbi:
        sub(pr, "id", root=MBI_ROOT, extension=mbi, assigningAuthorityName="CMS MBI")
    if any(demo.get(k) for k in ("ADDRESS_LINE1", "CITY", "STATE", "ZIP_CD")):
        addr = sub(pr, "addr", use="HP")
        for k in ("ADDRESS_LINE1", "ADDRESS_LINE2"):
            if demo.get(k):
                sub(addr, "streetAddressLine", demo[k])
        sub(addr, "city", demo.get("CITY"))
        sub(addr, "state", demo.get("STATE"))
        sub(addr, "postalCode", demo.get("ZIP_CD"))
        sub(addr, "country", "US")
    else:
        sub(pr, "addr", nullFlavor="UNK")
    phones = OrderedDict()
    for col, use in (("HOME_PHONE", "HP"), ("MBE_HOME_PHONE", "HP"), ("MBE_CELL_PHONE", "MC")):
        num = re.sub(r"\D", "", demo.get(col, ""))
        if num and num not in phones:
            phones[num] = use
    for num, use in phones.items():
        pretty = f"+1-{num[:3]}-{num[3:6]}-{num[6:]}" if len(num) == 10 else num
        sub(pr, "telecom", use=use, value=f"tel:{pretty}")
    if not phones:
        sub(pr, "telecom", nullFlavor="UNK")

    pt = sub(pr, "patient")
    if demo.get("FIRST_NAME") or demo.get("LAST_NAME"):
        name = sub(pt, "name", use="L")
        for k in ("FIRST_NAME", "MIDDLE_NAME"):
            if demo.get(k):
                sub(name, "given", demo[k])
        sub(name, "family", demo.get("LAST_NAME"))
    else:
        sub(pt, "name", nullFlavor="UNK")
    g = GENDER.get(demo.get("SEX", "").upper())
    if g:
        sub(pt, "administrativeGenderCode", code=g[0], codeSystem="2.16.840.1.113883.5.1",
            codeSystemName="AdministrativeGender", displayName=g[1])
    else:
        sub(pt, "administrativeGenderCode", nullFlavor="UNK")
    ts_el(pt, "birthTime", hl7_ts(demo.get("DATE_OF_BIRTH"), with_time=False))

    author = sub(doc, "author")
    sub(author, "time", value=now)
    aa = sub(author, "assignedAuthor")
    sub(aa, "id", root=new_id("PCW", "author-device"))
    sub(aa, "addr", nullFlavor="NI")
    sub(aa, "telecom", nullFlavor="NI")
    dev = sub(aa, "assignedAuthoringDevice")
    sub(dev, "manufacturerModelName", "PCW flat file extract")
    sub(dev, "softwareName", "flatfile_to_ccda.py")

    org = sub(sub(sub(doc, "custodian"), "assignedCustodian"), "representedCustodianOrganization")
    sub(org, "id", root=new_id("PCW", "custodian"))
    sub(org, "name", "PCW")
    sub(org, "telecom", nullFlavor="NI")
    sub(org, "addr", nullFlavor="NI")

    days = sorted(d[:8] for d in all_dates if is_ts(d))
    if days:
        se = sub(sub(doc, "documentationOf"), "serviceEvent", classCode="PCPR")
        et = sub(se, "effectiveTime")
        sub(et, "low", value=days[0])
        sub(et, "high", value=days[-1])


# ----------------------------------------------------------------------------------------------- sections
def split_list(s):
    return [p.strip() for p in (s or "").split(",") if p.strip()]


def dx_codes(dx_row):
    """ICD-10 codes of one visit-diagnosis row.  Names are never paired with codes by position: DX_NAME cannot be split
    reliably (names contain commas), so the full DX_NAME is kept verbatim in the narrative instead."""
    return split_list(dx_row.get("CURRENT_ICD10_LIST"))


def note_text(r):
    """Note CONTENT -> plain text where every non-keyboard character (see _NOTE_BREAK) is a line break."""
    content = r.get("CONTENT", "").replace("\r\n", "\n").replace("\r", "\n")
    if "html" in (r.get("MIME_TYPE") or "").lower():
        content = html_to_text(content)
    content = _NOTE_BREAK.sub("\n", content.translate(NOTE_ASCII))
    content = re.sub(r"[ \t]*\n[ \t]*", "\n", content)
    return re.sub(r"\n{3,}", "\n\n", content).strip()


def problem_observation(parent, member_id, key, icd, onset, ref_id):
    obs = sub(parent, "observation", classCode="OBS", moodCode="EVN")
    sub(obs, "templateId", root="2.16.840.1.113883.10.20.22.4.4", extension="2015-08-01")
    sub(obs, "id", root=new_id(member_id, key, icd))
    sub(obs, "code", code="282291009", codeSystem=SNOMED, codeSystemName="SNOMED CT", displayName="Diagnosis")
    t = sub(obs, "text")
    sub(t, "reference", value=f"#{ref_id}")
    sub(obs, "statusCode", code="completed")
    et = sub(obs, "effectiveTime")
    ts_el(et, "low", onset)
    sub(obs, "value", xsi_type="CD", code=icd, codeSystem=ICD10, codeSystemName="ICD-10-CM")
    return obs


def author_names(visit):
    return list(OrderedDict.fromkeys(r["DOCUMENT_AUTHOR"] for r in visit["rows"] if r.get("DOCUMENT_AUTHOR")))


def dos_section(body, day, visits):
    """Top-level date-of-service section; the standard C-CDA sections for that day are nested inside it."""
    sec = sub(sub(body, "component"), "section")
    if len(visits) == 1:
        parts = [visits[0]["rows"][0].get("ENCOUNTER_NAME") or "Encounter", "; ".join(author_names(visits[0]))]
        suffix = " — ".join(x for x in parts if x)
    elif visits:
        suffix = f"{len(visits)} visits"
    else:
        suffix = "no visit recorded (results only)"
    sub(sec, "title", f"DOS {us_date(day)} — {suffix}")
    return sec


def undated_section(body):
    sec = sub(sub(body, "component"), "section")
    sub(sec, "title", "Undated (no usable date in the source)")
    return sec


def build_encounters(parent, member_id, visits, p):
    """Encounters section: one encounter per visit (note rows grouped by ENCOUNTER_ID, else by date + ENCOUNTER_NAME);
    every distinct note author is a performer."""
    sec = new_section(parent, "2.16.840.1.113883.10.20.22.2.22.1", "46240-8",
                      "History of hospitalizations+History of outpatient visits", "Encounters")
    tbody = narrative_table(sec, ["Encounter ID", "Encounter", "Date", "Provider", "Provider Type", "NPI", "Notes"])
    for n, v in enumerate(visits, start=1):
        r = v["rows"][0]
        rid = f"{p}encounter{n}"
        tr = sub(tbody, "tr")
        sub(tr, "td", r.get("ENCOUNTER_ID") or "")
        sub(sub(tr, "td"), "content", r.get("ENCOUNTER_NAME") or "Encounter", ID=rid)
        for v_ in (us_date(v["date"]), "; ".join(author_names(v)),
                   "; ".join(OrderedDict.fromkeys(a.get("PROVIDER_TYPE") for a in v["authors"] if a.get("PROVIDER_TYPE"))),
                   "; ".join(OrderedDict.fromkeys(a.get("PROVIDER_NPI") for a in v["authors"] if a.get("PROVIDER_NPI"))),
                   str(len(v["rows"]))):
            sub(tr, "td", v_ or "")

        enc = sub(sub(sec, "entry", typeCode="DRIV"), "encounter", classCode="ENC", moodCode="EVN")
        sub(enc, "templateId", root="2.16.840.1.113883.10.20.22.4.49", extension="2015-08-01")
        sub(enc, "id", root=v["id"])
        if r.get("ENCOUNTER_ID"):
            sub(enc, "id", root=ENCOUNTER_ID_ROOT, extension=r["ENCOUNTER_ID"])
        code = sub(enc, "code", nullFlavor="OTH")
        sub(sub(code, "originalText"), "reference", value=f"#{rid}")
        t = sub(enc, "text")
        sub(t, "reference", value=f"#{rid}")
        sub(enc, "statusCode", code="completed")
        ts_el(enc, "effectiveTime", v["date"])
        for a in v["authors"]:
            ae = sub(sub(enc, "performer"), "assignedEntity")
            if a.get("PROVIDER_NPI"):
                sub(ae, "id", root=NPI_ROOT, extension=a["PROVIDER_NPI"])
            if a.get("PROVIDER_ID"):
                sub(ae, "id", root=PROVIDER_ID_ROOT, extension=a["PROVIDER_ID"])
            if not (a.get("PROVIDER_NPI") or a.get("PROVIDER_ID")):
                sub(ae, "id", nullFlavor="UNK")
            if a.get("PROVIDER_TYPE"):
                sub(ae, "code", nullFlavor="OTH", displayName=a["PROVIDER_TYPE"])
            if a.get("DOCUMENT_AUTHOR"):
                person_name(sub(ae, "assignedPerson"), a["DOCUMENT_AUTHOR"])
    return sec


def build_notes(parent, member_id, visits, p):
    """Notes section: one Note Activity per note row, dated by its visit (unknown date for an undated visit)."""
    sec = new_section(parent, "2.16.840.1.113883.10.20.22.2.65", "34109-9", "Note", "Notes")
    tbody = narrative_table(sec, ["Date", "Encounter", "Note Type", "Note", "Author", "Status", "Filed", "Last Updated"])
    n = 0
    for v in visits:
        for r in v["rows"]:
            content = note_text(r)
            if not content:
                continue
            n += 1
            nid = f"{p}note{n}"
            filed = hl7_ts(r.get("NOT_FILETM_LOC_DTTM") or r.get("NOTE_FILE_TIME_DTTM"))
            updated = hl7_ts(r.get("UPD_AUT_LOCAL_DTTM") or r.get("DOCUMENT_UPDATE_DATE"))
            tr = sub(tbody, "tr")
            for v_ in (us_date(v["date"]), r.get("ENCOUNTER_NAME"), r.get("NOTE_TYPE")):
                sub(tr, "td", v_ or "")
            multiline(sub(tr, "td", ID=nid), content)
            for v_ in (r.get("DOCUMENT_AUTHOR"), r.get("DOCUMENT_SIGNED_STATUS"), us_date(filed), us_date(updated)):
                sub(tr, "td", v_ or "")

            act = sub(sub(sec, "entry"), "act", classCode="ACT", moodCode="EVN")
            sub(act, "templateId", root="2.16.840.1.113883.10.20.22.4.202", extension="2016-11-01")
            sub(act, "id", root=new_id(member_id, "note", v["id"], n))
            code = sub(act, "code", code="34109-9", codeSystem=LOINC, codeSystemName="LOINC", displayName="Note")
            if r.get("NOTE_TYPE"):
                sub(code, "translation", nullFlavor="OTH", displayName=r["NOTE_TYPE"])
            t = sub(act, "text", mediaType="text/plain")
            sub(t, "reference", value=f"#{nid}")
            sub(act, "statusCode", code="completed")
            ts_el(act, "effectiveTime", v["date"])
            if r.get("DOCUMENT_AUTHOR") or filed:
                au = sub(act, "author")
                ts_el(au, "time", filed)
                aa = sub(au, "assignedAuthor")
                if r.get("PROVIDER_NPI"):
                    sub(aa, "id", root=NPI_ROOT, extension=r["PROVIDER_NPI"])
                else:
                    sub(aa, "id", nullFlavor="UNK")
                if r.get("DOCUMENT_AUTHOR"):
                    person_name(sub(aa, "assignedPerson"), r["DOCUMENT_AUTHOR"])
            if is_ts(v["date"]):  # the encounter entry exists only for a dated visit
                er = sub(act, "entryRelationship", typeCode="COMP", inversionInd="true")
                sub(sub(er, "encounter", classCode="ENC", moodCode="EVN"), "id", root=v["id"])
    return sec


def build_problems(parent, member_id, dx_rows, onset, p):
    """Visit diagnoses, dated by their visit (onset), or undated when no dated encounter matches their ENCOUNTER_ID."""
    sec = new_section(parent, "2.16.840.1.113883.10.20.22.2.5.1", "11450-4", "Problem list", "Problems")
    tbody = narrative_table(sec, ["Encounter ID", "ICD-10 Code", "Diagnosis names (as listed)", "Date"])
    n = 0
    for d in dx_rows:
        for icd in dx_codes(d):
            n += 1
            pid = f"{p}problem{n}"
            tr = sub(tbody, "tr")
            sub(tr, "td", d.get("ENCOUNTER_ID") or "")
            sub(sub(tr, "td"), "content", icd, ID=pid)
            sub(tr, "td", d.get("DX_NAME") or "")
            sub(tr, "td", us_date(onset) if onset else "Not provided in source (encounter not found)")

            act = sub(sub(sec, "entry"), "act", classCode="ACT", moodCode="EVN")
            sub(act, "templateId", root="2.16.840.1.113883.10.20.22.4.3", extension="2015-08-01")
            sub(act, "id", root=new_id(member_id, "concern", d.get("ENCOUNTER_ID"), icd))
            sub(act, "code", code="CONC", codeSystem="2.16.840.1.113883.5.6")
            sub(act, "statusCode", code="active")
            ts_el(sub(act, "effectiveTime"), "low", onset)
            er = sub(act, "entryRelationship", typeCode="SUBJ")
            problem_observation(er, member_id, f"dx|{d.get('ENCOUNTER_ID')}", icd, onset, pid)
    return sec


def build_results(parent, member_id, labs, p):
    """Results section: rows grouped into one organizer per (service date, CPT code)."""
    sec = new_section(parent, "2.16.840.1.113883.10.20.22.2.3.1", "30954-2",
                      "Relevant diagnostic tests/laboratory data", "Results")
    tbody = narrative_table(sec, ["Date", "Test", "LOINC", "CPT", "Value", "Units", "Reference Range", "Flag"])
    panels = OrderedDict()
    for r in sorted(labs, key=lambda r: (r["_date"] or "", r.get("CPT_CODE", ""), r.get("RESULT_NAME", ""))):
        panels.setdefault((r["_date"], r.get("CPT_CODE", "")), []).append(r)
    n = 0
    for (date, cpt), rows in panels.items():
        org = sub(sub(sec, "entry", typeCode="DRIV"), "organizer", classCode="BATTERY", moodCode="EVN")
        sub(org, "templateId", root="2.16.840.1.113883.10.20.22.4.1", extension="2015-08-01")
        sub(org, "id", root=new_id(member_id, "panel", date, cpt))
        if cpt:
            names = list(OrderedDict.fromkeys(r.get("RESULT_NAME") for r in rows if r.get("RESULT_NAME")))
            sub(org, "code", code=cpt, codeSystem=CPT, codeSystemName="CPT", displayName=", ".join(names) or None)
        else:
            sub(org, "code", nullFlavor="UNK")
        sub(org, "statusCode", code="completed")
        ts_el(org, "effectiveTime", date)
        for r in rows:
            n += 1
            rid = f"{p}result{n}"
            value, unit, flag = r.get("TEST_RESULT_VALUE", ""), r.get("UNITS", ""), r.get("RESULT_FLAG", "").upper()
            tr = sub(tbody, "tr")
            sub(tr, "td", us_date(date))
            sub(sub(tr, "td"), "content", r.get("RESULT_NAME") or r.get("LOINC_CODE") or "Result", ID=rid)
            for v in (r.get("LOINC_CODE"), cpt, value, unit, r.get("REFERENCE_RANGE"), flag):
                sub(tr, "td", v or "")

            obs = sub(sub(org, "component"), "observation", classCode="OBS", moodCode="EVN")
            sub(obs, "templateId", root="2.16.840.1.113883.10.20.22.4.2", extension="2015-08-01")
            sub(obs, "id", root=new_id(member_id, "result", date, cpt, r.get("LOINC_CODE"), r.get("RESULT_NAME"), value))
            if r.get("LOINC_CODE"):
                sub(obs, "code", code=r["LOINC_CODE"], codeSystem=LOINC, codeSystemName="LOINC", displayName=r.get("RESULT_NAME"))
            else:
                c = sub(obs, "code", nullFlavor="UNK")
                sub(c, "originalText", r.get("RESULT_NAME"))
            t = sub(obs, "text")
            sub(t, "reference", value=f"#{rid}")
            sub(obs, "statusCode", code="completed")
            ts_el(obs, "effectiveTime", date)
            if _NUMBER.match(value):
                sub(obs, "value", xsi_type="PQ", value=value, unit=unit or "1")
            elif value:
                sub(obs, "value", value if not unit else f"{value} {unit}", xsi_type="ST")
            else:
                sub(obs, "value", xsi_type="ST", nullFlavor="NI")
            if flag in INTERPRETATION:
                sub(obs, "interpretationCode", code=flag, codeSystem="2.16.840.1.113883.5.83",
                    codeSystemName="ObservationInterpretation", displayName=INTERPRETATION[flag])
            elif flag:
                sub(obs, "interpretationCode", nullFlavor="OTH", displayName=flag)
            if r.get("REFERENCE_RANGE"):
                orng = sub(sub(obs, "referenceRange"), "observationRange")
                sub(orng, "text", r["REFERENCE_RANGE"])
    return sec


def build_day(parent, member_id, visits, dx_rows, labs, onset, p):
    """The standard sections for one DOS (or for Undated), each only when it has data."""
    dated = [v for v in visits if is_ts(v["date"])]
    if dated:
        build_encounters(parent, member_id, dated, p)
    if dx_rows:
        build_problems(parent, member_id, dx_rows, onset, p)
    if any(note_text(r) for v in visits for r in v["rows"]):
        build_notes(parent, member_id, visits, p)
    if labs:
        build_results(parent, member_id, labs, p)


# ----------------------------------------------------------------------------------------------- per member
def build_member(member_id, data):
    demo = data["demo"]
    visits = OrderedDict()
    for r in data["encounter"]:
        # DOS = the visit date. CONTACT_DATE / EFFECTIVE_DATE_DTTM are fallbacks when ENCOUNTER_DATE is blank.
        # A recognisable date in any of them beats an unrecognisable ENCOUNTER_DATE; if none is recognisable, the first
        # non-blank one is kept as written (shown in the narrative, but the note goes to Undated).
        cols = ("ENCOUNTER_DATE", "CONTACT_DATE", "EFFECTIVE_DATE_DTTM")
        date = next((d for d in (hl7_ts(r.get(c), with_time=False, strict=True) for c in cols) if d), None)
        if not date:
            date = next((hl7_ts(r[c]) for c in cols if r.get(c)), None)
        elif r.get("ENCOUNTER_DATE") and not hl7_ts(r["ENCOUNTER_DATE"], with_time=False, strict=True):
            warn(f"{member_id}: unrecognised ENCOUNTER_DATE {r['ENCOUNTER_DATE']!r} in {r['_SOURCE_FILE']} — "
                 f"visit dated from CONTACT_DATE/EFFECTIVE_DATE_DTTM instead ({date})")
        if not is_ts(date):
            warn(f"{member_id}: encounter row in {r['_SOURCE_FILE']} has no usable date — note kept under Undated")
        # one visit per ENCOUNTER_ID; rows without one are grouped by date + ENCOUNTER_NAME
        key = r.get("ENCOUNTER_ID") or f"{date}|{r.get('ENCOUNTER_NAME')}"
        v = visits.setdefault(key, {"rows": [], "date": date, "id": new_id(member_id, "encounter", key)})
        v["rows"].append(r)
        if is_ts(date) and (not is_ts(v["date"]) or date < v["date"]):
            v["date"] = date  # rows of one ENCOUNTER_ID disagreeing on the date: the earliest wins
    for v in visits.values():
        v["authors"] = list(OrderedDict(((r.get("DOCUMENT_AUTHOR"), r.get("PROVIDER_NPI"), r.get("PROVIDER_ID")), r)
                                        for r in v["rows"]
                                        if r.get("DOCUMENT_AUTHOR") or r.get("PROVIDER_NPI") or r.get("PROVIDER_ID")).values())

    days = defaultdict(lambda: {"visits": [], "dx": [], "labs": []})
    undated = {"visits": [], "dx": [], "labs": []}
    by_enc_id = {}
    for v in visits.values():
        if is_ts(v["date"]):
            days[v["date"][:8]]["visits"].append(v)
            if v["rows"][0].get("ENCOUNTER_ID"):
                by_enc_id[v["rows"][0]["ENCOUNTER_ID"]] = v
        else:
            undated["visits"].append(v)
    for d in data["diagnosis"]:
        v = by_enc_id.get(d.get("ENCOUNTER_ID"))
        if v:
            days[v["date"][:8]]["dx"].append(d)
        else:
            warn(f"{member_id}: diagnosis row with ENCOUNTER_ID {d.get('ENCOUNTER_ID')!r} matches no dated encounter — "
                 f"kept under Undated")
            undated["dx"].append(d)
    for r in data["lab"]:
        r["_date"] = hl7_ts(r.get("SERVICE_DATE"), with_time=False)
        (days[r["_date"][:8]]["labs"] if is_ts(r["_date"]) else undated["labs"]).append(r)

    doc = ET.Element(f"{{{V3}}}ClinicalDocument")
    build_header(doc, member_id, demo, data["mbi"], list(days))
    body = sub(sub(doc, "component"), "structuredBody")
    for i, day in enumerate(sorted(days, reverse=True), start=1):  # newest DOS first
        g = days[day]
        g["visits"].sort(key=lambda v: v["date"])
        build_day(dos_section(body, day, g["visits"]), member_id, g["visits"], g["dx"], g["labs"], day, f"d{i}")
    if any(undated.values()):
        sec = undated_section(body)
        build_day(sec, member_id, undated["visits"], undated["dx"], undated["labs"], None, "u")
        if len(sec) == 1:  # title only: the undated rows were notes with no text
            body.remove(body[-1])
    if not len(body):
        sec = new_section(body, "2.16.840.1.113883.10.20.22.2.65", "34109-9", "Note", "Notes")
        sub(sub(sec, "text"), "paragraph", "No clinical data in the flat files for this member.")
    return doc, {"dos": len(days), "visits": sum(len(g["visits"]) for g in days.values()),
                 "notes": sum(1 for v in visits.values() for r in v["rows"] if note_text(r)),
                 "dx_rows_dated": sum(len(g["dx"]) for g in days.values()), "dx_rows_undated": len(undated["dx"]),
                 "labs": len(data["lab"]), "undated_visits": len(undated["visits"])}


def win_path(p):
    """cmd.exe turns a quoted path ending in a backslash ("C:\\data\\PCW\\") into C:\\data\\PCW" — the closing quote
    survives as a literal character.  Strip it, plus stray whitespace, so that form still works."""
    return os.path.normpath(p.strip().strip('"').strip())


def safe_dirname(s):
    """RGN_CD -> a folder name Windows accepts."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(" .") or "_"


def connect():
    """Connect with the PG* variables (from .env or the environment); stop with a clear message if one is missing."""
    missing = [k for k in ("PGHOST", "PGDATABASE", "PGUSER") if not os.environ.get(k)]
    if missing:
        sys.exit(f"error: {', '.join(missing)} not set — add to .env (see .env.example)")
    try:
        import psycopg2
    except ImportError:
        sys.exit("error: reading the database needs psycopg2. Install it with:  pip install psycopg2-binary")
    try:
        conn = psycopg2.connect("", application_name="flatfile_to_ccda")  # libpq reads PGHOST, PGPASSWORD, ...
    except psycopg2.OperationalError as exc:
        sys.exit(f"error: could not connect to {os.environ['PGUSER']}@{os.environ['PGHOST']}/"
                 f"{os.environ['PGDATABASE']}: {str(exc).strip()}")
    conn.set_session(readonly=True)
    return conn


def main(argv=None):
    # The Command Prompt console (and output redirected to a file) may use cp1252/cp437; never crash on a
    # character it cannot show, e.g. an accented name in a warning.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="PCW PostgreSQL tables -> one C-CDA XML per member, "
                                             "in <out-dir>\\<RGN_CD>\\<mrn_padded>.xml")
    ap.add_argument("--env-file", help="settings file (default: .env next to this script)")
    ap.add_argument("-o", "--out-dir", help="output folder (default: CCDA_OUT_DIR from .env)")
    for fam, (var, default) in TABLES.items():
        ap.add_argument(f"--{fam}-table", help=f"[schema.]table (default: {var} from .env, else {default})")
    ap.add_argument("--render-html", action="store_true",
                    help="also render each XML to HTML, in <out-dir>_html\\<RGN_CD> (needs lxml: pip install lxml)")
    ap.add_argument("--xsl", help="stylesheet for --render-html (default: xsl\\cda.xsl next to this script)")
    args = ap.parse_args(argv)

    env_file = win_path(args.env_file) if args.env_file else DEFAULT_ENV
    if load_env(env_file):
        print(f"Settings from {env_file}")
    elif args.env_file:
        ap.error(f"env file not found: {env_file}")
    out_dir = args.out_dir or os.environ.get("CCDA_OUT_DIR")
    if not out_dir:
        ap.error("no output folder — pass -o or set CCDA_OUT_DIR in .env")
    out_dir = win_path(out_dir)
    tables = OrderedDict((fam, getattr(args, f"{fam}_table") or os.environ.get(var) or default)
                         for fam, (var, default) in TABLES.items())
    render = None
    if args.render_html:  # checked before any work, so a missing lxml / stylesheet fails fast
        import render_html
        try:
            render = render_html.make_renderer(win_path(args.xsl) if args.xsl else render_html.DEFAULT_XSL)
        except render_html.RenderSetupError as exc:
            ap.error(str(exc))

    conn = connect()
    print(f"Reading {os.environ['PGDATABASE']} on {os.environ['PGHOST']}")
    members = defaultdict(lambda: {"demo": {}, "mbi": None, "encounter": [], "diagnosis": [], "lab": [], "ids": None})
    try:
        for fam, table in tables.items():
            try:
                rows = load_table(conn, fam, table)
            except Exception as exc:  # psycopg2.Error: missing table, no permission, ... — no traceback
                if not type(exc).__module__.startswith("psycopg2"):
                    raise
                sys.exit(f"error: could not read {fam} table {table}: {str(exc).splitlines()[0]}")
            for r in rows:
                key = member_key(r)
                if not key:
                    continue
                m = members[key]
                m["ids"] = m["ids"] or {"RGN_CD": r["RGN_CD"], "MRN_PADDED": r["MRN_PADDED"], "MRN_NB": r.get("MRN_NB", "")}
                if fam == "demo":
                    for k, v in r.items():  # several demo rows for one member: first non-empty value wins
                        if v and not m["demo"].get(k):
                            m["demo"][k] = v
                elif fam == "crosswalk":
                    if m["mbi"] and r.get("MBI") and r["MBI"] != m["mbi"]:
                        warn(f"{key[0]}_{key[1]}: more than one MBI in crosswalk ({m['mbi']}, {r['MBI']}) — keeping the first")
                    m["mbi"] = m["mbi"] or r.get("MBI") or None
                else:
                    m[fam].append(r)
    finally:
        conn.close()

    print(f"\nWriting {len(members)} member file(s) to {out_dir}\\<RGN_CD>")
    written = defaultdict(list)  # region folder -> xml paths
    for rgn, mrn in sorted(members):
        data = members[(rgn, mrn)]
        label = f"{rgn}_{mrn}"  # warnings and stable entry ids; the document's patient id is mrn_padded
        if not data["demo"]:
            warn(f"{label}: no memberdemo row — demographics left as unknown")
            data["demo"] = dict(data["ids"])
        demo_mbi = data["demo"].get("MBI")
        if not data["mbi"]:
            data["mbi"] = demo_mbi or None  # crosswalk first; memberdemo.MBI when the crosswalk has none
        elif demo_mbi and demo_mbi != data["mbi"]:
            warn(f"{label}: crosswalk MBI {data['mbi']} differs from memberdemo MBI {demo_mbi} — using the crosswalk one")
        doc, stats = build_member(label, data)
        ET.indent(doc, space="  ")
        rgn_dir = os.path.join(out_dir, safe_dirname(rgn))
        os.makedirs(rgn_dir, exist_ok=True)
        path = os.path.join(rgn_dir, f"{safe_dirname(mrn)}.xml")
        ET.ElementTree(doc).write(path, encoding="UTF-8", xml_declaration=True)
        written[rgn_dir].append(path)
        print(f"  {os.path.basename(rgn_dir)}\\{os.path.basename(path)}  " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    failed = []
    if render:
        html_root = render_html.html_dir_for(out_dir)
        total = sum(len(p) for p in written.values())
        print(f"\nRendering HTML to {html_root}\\<RGN_CD>")
        ok = 0
        for rgn_dir, paths in written.items():
            n, f = render_html.render_files(paths, os.path.join(html_root, os.path.basename(rgn_dir)), render)
            ok += n
            failed += f
        print(f"  {ok} of {total} rendered")
    if WARNINGS:
        print(f"\n{len(WARNINGS)} warning(s) — see above", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
