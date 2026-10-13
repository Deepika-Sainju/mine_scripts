#!/usr/bin/env python3
r"""
flatfile_to_ccda.py — Build one C-CDA XML per member from the PCW pipe-delimited flat files.

Inputs (all in one folder; every family may be split over several files, e.g. ..._internallab.txt, ..._internallab2.csv):
    pwc_out_retro_ma_memberdemo*                  demographics                 -> recordTarget
    pwc_out_retro_ma_idcrosswalk*                 RGN_CD | MRN_NB | MBI        -> extra patient id (MBI)
    pwc_out_retro_ma_clarityepicencounternotes*   one row per encounter note   -> Encounters section + Notes section
    pwc_out_retro_ma_clarityvisitdiagnosis*       ICD-10 list per encounter    -> diagnoses on the matching encounter, else Problems
    pwc_out_retro_ma_internallab*                 one row per lab result       -> Results section

Each file may be .txt or .csv, and a family may mix both.  The delimiter is detected per file from its header line
(pipe for the flat files, comma for CSV, tab also accepted), whatever the extension says.  Values containing the
delimiter (e.g. a DX_NAME with commas in a CSV) must be double-quoted.

Member id = RGN_CD_MRN_NB (e.g. MA_12345678); output is <out_dir>/<member id>.xml.

Dates: every date is written as an HL7 TS ("20250403" / "20250403103400"), with no separators and no timezone, which is
the form ccda_dosfirst_v9.py reads.  It reads the local calendar date as written, and only <encounter> entries create a
"DOS" group there, so each encounter row becomes a real encounter entry dated by ENCOUNTER_DATE.

Diagnoses: DX_NAME cannot be split on commas reliably ("MAJOR DEPRESSIVE DISORDER, RECURRENT EPISODE, SEVERE" is one
name), so names are paired with ICD-10 codes only when the counts agree.  Otherwise each ICD-10 code becomes its own
coded problem, and the full DX_NAME list is kept verbatim in the narrative.  Visit diagnosis rows carry no date: when
their ENCOUNTER_ID matches an encounter row they are attached to that visit, otherwise they go to Problems undated.

Usage (Windows Command Prompt; the input folder is required):
    python flatfile_to_ccda.py -i "C:\Users\Deepika.Sainju\Documents\reports\PCW"
        -> writes C:\Users\Deepika.Sainju\Documents\reports\PCW\ccda_output\<member id>.xml
    python flatfile_to_ccda.py -i "C:\data\PCW" -o "C:\data\PCW_ccda"
    python flatfile_to_ccda.py -i "C:\data\PCW" -o "C:\data\PCW_ccda" --render-html
        -> XML in C:\data\PCW_ccda, HTML in C:\data\PCW_ccda_html (render_html.py; needs: pip install lxml)
Quote paths that contain spaces.  Python 3.9+; standard library only, except --render-html which needs lxml.
"""
from __future__ import annotations

import argparse
import csv
import glob
import html
import os
import re
import sys
import uuid
from collections import OrderedDict, defaultdict
from datetime import datetime
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
ID_NAMESPACE = uuid.UUID("6f1c2d4e-8a3b-4c5d-9e7f-0a1b2c3d4e5f")  # uuid5 namespace -> ids are stable across reruns


# file family -> glob prefix.  "<prefix>*.txt" / "<prefix>*.csv" pick up <prefix>.txt, <prefix>2.csv, <prefix>_2025.txt, ...
FAMILIES = OrderedDict([
    ("demo", "pwc_out_retro_ma_memberdemo"),
    ("crosswalk", "pwc_out_retro_ma_idcrosswalk"),
    ("encounter", "pwc_out_retro_ma_clarityepicencounternotes"),
    ("diagnosis", "pwc_out_retro_ma_clarityvisitdiagnosis"),
    ("lab", "pwc_out_retro_ma_internallab"),
])
EXTENSIONS = ("txt", "csv")
DELIMITERS = ("|", ",", "\t")

_XML_ILLEGAL = re.compile("[^\\x09\\x0a\\x0d\\x20-\\ud7ff\\ue000-\\ufffd\\U00010000-\\U0010ffff]")
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
    warn(f"unrecognised date {value!r} — kept as written (not an HL7 date, so downstream date grouping may not use it)")
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


# ----------------------------------------------------------------------------------------------- reading flat files
def clean(v):
    if v is None:
        return ""
    return _XML_ILLEGAL.sub("", v).strip()


def detect_delimiter(data, path):
    """The delimiter that appears most often in the header line: pipe for the flat files, comma for CSV.  Column names
    never contain these, so the header count is reliable where sniffing data rows (long notes full of commas) is not."""
    header = data.split("\n", 1)[0]
    counts = {d: header.count(d) for d in DELIMITERS}
    delim = max(counts, key=counts.get)
    if not counts[delim]:
        warn(f"{os.path.basename(path)}: no |, comma or tab in header — assuming pipe-delimited")
        return "|"
    return delim


def read_rows(path):
    """Pipe-, comma- or tab-delimited, double-quoted file -> list of dicts keyed by UPPER-CASE header.
    Tolerates BOM / cp1252."""
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            data = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    delim = detect_delimiter(data, path)
    reader = csv.reader(data.splitlines(keepends=True), delimiter=delim, quotechar='"')
    try:
        header = [h.strip().upper() for h in next(reader)]
    except StopIteration:
        return []
    if not {"RGN_CD", "MRN_NB"} <= set(header):
        warn(f"{os.path.basename(path)}: header has no RGN_CD/MRN_NB (delimiter {delim!r}) — every row will be skipped")
    rows = []
    for n, rec in enumerate(reader, start=2):
        if not any(c.strip() for c in rec):
            continue
        if len(rec) != len(header):
            warn(f"{os.path.basename(path)} row {n}: {len(rec)} fields, header has {len(header)} — padded/truncated")
            rec = (rec + [""] * len(header))[: len(header)]
        rows.append({h: clean(c) for h, c in zip(header, rec)})
    return rows


def load_family(input_dir, prefix):
    """All rows of every <prefix>*.txt / <prefix>*.csv file, exact duplicate rows (same data in two files) removed."""
    files = sorted({f for ext in EXTENSIONS for f in glob.glob(os.path.join(input_dir, f"{prefix}*.{ext}"))})
    rows, seen = [], set()
    for f in files:
        got = read_rows(f)
        for r in got:
            key = tuple(sorted(r.items()))
            if key in seen:
                continue
            seen.add(key)
            r["_SOURCE_FILE"] = os.path.basename(f)
            rows.append(r)
        print(f"  {os.path.basename(f)}: {len(got)} row(s)")
    return rows


def member_key(row):
    rgn, mrn = row.get("RGN_CD", ""), row.get("MRN_NB", "")
    if not rgn or not mrn:
        warn(f"{row.get('_SOURCE_FILE')}: row without RGN_CD/MRN_NB skipped ({rgn!r}, {mrn!r})")
        return None
    return f"{rgn}_{mrn}"


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
    return sub(parent, tag, value=ts) if ts else sub(parent, tag, nullFlavor="UNK")


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
    sub(pr, "id", root=MEMBER_ID_ROOT, extension=member_id, assigningAuthorityName="PCW member id (RGN_CD_MRN_NB)")
    sub(pr, "id", root=MRN_ROOT, extension=demo.get("MRN_NB"), assigningAuthorityName=f"MRN ({demo.get('RGN_CD')})")
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


def dx_pairs(dx_row):
    """[(icd10, name or None), ...] — names only when DX_NAME splits into exactly as many parts as there are codes."""
    codes = split_list(dx_row.get("CURRENT_ICD10_LIST"))
    names = split_list(dx_row.get("DX_NAME"))
    if len(names) != len(codes):
        names = [None] * len(codes)
    return list(zip(codes, names))


def problem_observation(parent, member_id, key, icd, name, onset, ref_id):
    obs = sub(parent, "observation", classCode="OBS", moodCode="EVN")
    sub(obs, "templateId", root="2.16.840.1.113883.10.20.22.4.4", extension="2015-08-01")
    sub(obs, "id", root=new_id(member_id, key, icd))
    sub(obs, "code", code="282291009", codeSystem=SNOMED, codeSystemName="SNOMED CT", displayName="Diagnosis")
    t = sub(obs, "text")
    sub(t, "reference", value=f"#{ref_id}")
    sub(obs, "statusCode", code="completed")
    et = sub(obs, "effectiveTime")
    ts_el(et, "low", onset)
    sub(obs, "value", xsi_type="CD", code=icd, codeSystem=ICD10, codeSystemName="ICD-10-CM", displayName=name)
    return obs


def build_encounters(body, member_id, encounters, dx_by_enc):
    """Encounters section.  Diagnoses whose ENCOUNTER_ID matches are nested as Encounter Diagnosis acts."""
    sec = new_section(body, "2.16.840.1.113883.10.20.22.2.22.1", "46240-8",
                      "History of hospitalizations+History of outpatient visits", "Encounters")
    tbody = narrative_table(sec, ["Encounter ID", "Encounter", "Date", "Provider", "Provider Type", "NPI",
                                  "Diagnoses (ICD-10)", "Diagnosis names (as listed)"])
    for n, e in enumerate(encounters, start=1):
        r = e["row"]
        rid = f"encounter{n}"
        dxs = dx_by_enc.get(r.get("ENCOUNTER_ID"), []) if r.get("ENCOUNTER_ID") else []
        tr = sub(tbody, "tr")
        sub(sub(tr, "td"), "content", r.get("ENCOUNTER_ID") or r.get("ORIGINAL_ID") or "", ID=f"{rid}id")
        sub(sub(tr, "td"), "content", r.get("ENCOUNTER_NAME") or "Encounter", ID=rid)
        for v in (us_date(e["date"]), r.get("DOCUMENT_AUTHOR"), r.get("PROVIDER_TYPE"), r.get("PROVIDER_NPI"),
                  "; ".join(d.get("CURRENT_ICD10_LIST", "") for d in dxs), "; ".join(d.get("DX_NAME", "") for d in dxs)):
            sub(tr, "td", v or "")

        enc = sub(sub(sec, "entry", typeCode="DRIV"), "encounter", classCode="ENC", moodCode="EVN")
        sub(enc, "templateId", root="2.16.840.1.113883.10.20.22.4.49", extension="2015-08-01")
        sub(enc, "id", root=e["id"])
        if r.get("ENCOUNTER_ID"):
            sub(enc, "id", root=MEMBER_ID_ROOT + ".enc", extension=r["ENCOUNTER_ID"])
        code = sub(enc, "code", nullFlavor="OTH")
        sub(sub(code, "originalText"), "reference", value=f"#{rid}")
        if r.get("NOTE_TYPE"):
            sub(code, "translation", nullFlavor="OTH", displayName=r["NOTE_TYPE"])
        t = sub(enc, "text")
        sub(t, "reference", value=f"#{rid}")
        sub(enc, "statusCode", code="completed")
        sub(enc, "effectiveTime", value=e["date"])
        if r.get("DOCUMENT_AUTHOR") or r.get("PROVIDER_NPI") or r.get("PROVIDER_ID"):
            ae = sub(sub(enc, "performer"), "assignedEntity")
            if r.get("PROVIDER_NPI"):
                sub(ae, "id", root=NPI_ROOT, extension=r["PROVIDER_NPI"])
            if r.get("PROVIDER_ID"):
                sub(ae, "id", root=PROVIDER_ID_ROOT, extension=r["PROVIDER_ID"])
            if not (r.get("PROVIDER_NPI") or r.get("PROVIDER_ID")):
                sub(ae, "id", nullFlavor="UNK")
            if r.get("PROVIDER_TYPE"):
                sub(ae, "code", nullFlavor="OTH", displayName=r["PROVIDER_TYPE"])
            if r.get("DOCUMENT_AUTHOR"):
                person_name(sub(ae, "assignedPerson"), r["DOCUMENT_AUTHOR"])
        for dn, d in enumerate(dxs, start=1):
            act = sub(sub(enc, "entryRelationship", typeCode="REFR"), "act", classCode="ACT", moodCode="EVN")
            sub(act, "templateId", root="2.16.840.1.113883.10.20.22.4.80", extension="2015-08-01")
            sub(act, "code", code="29308-4", codeSystem=LOINC, codeSystemName="LOINC", displayName="Diagnosis")
            for icd, name in dx_pairs(d):
                er = sub(act, "entryRelationship", typeCode="SUBJ")
                problem_observation(er, member_id, f"encdx|{e['id']}|{dn}", icd, name, e["date"][:8] if is_ts(e["date"]) else e["date"], rid)
    return sec


def build_notes(body, member_id, encounters):
    """Notes section: one Note Activity per encounter row, dated by the visit so it groups under that DOS."""
    sec = new_section(body, "2.16.840.1.113883.10.20.22.2.65", "34109-9", "Note", "Notes")
    tbody = narrative_table(sec, ["Date", "Encounter", "Note Type", "Note", "Author", "Status", "Filed", "Last Updated"])
    for n, e in enumerate(encounters, start=1):
        r = e["row"]
        content = r.get("CONTENT", "")
        if "html" in (r.get("MIME_TYPE") or "").lower():
            content = html_to_text(content)
        if not content:
            continue
        nid = f"note{n}"
        filed = hl7_ts(r.get("NOT_FILETM_LOC_DTTM") or r.get("NOTE_FILE_TIME_DTTM"))
        updated = hl7_ts(r.get("UPD_AUT_LOCAL_DTTM") or r.get("DOCUMENT_UPDATE_DATE"))
        tr = sub(tbody, "tr")
        for v in (us_date(e["date"]), r.get("ENCOUNTER_NAME"), r.get("NOTE_TYPE")):
            sub(tr, "td", v or "")
        multiline(sub(tr, "td", ID=nid), content)
        for v in (r.get("DOCUMENT_AUTHOR"), r.get("DOCUMENT_SIGNED_STATUS"), us_date(filed), us_date(updated)):
            sub(tr, "td", v or "")

        act = sub(sub(sec, "entry"), "act", classCode="ACT", moodCode="EVN")
        sub(act, "templateId", root="2.16.840.1.113883.10.20.22.4.202", extension="2016-11-01")
        sub(act, "id", root=new_id(member_id, "note", e["id"]))
        code = sub(act, "code", code="34109-9", codeSystem=LOINC, codeSystemName="LOINC", displayName="Note")
        if r.get("NOTE_TYPE"):
            sub(code, "translation", nullFlavor="OTH", displayName=r["NOTE_TYPE"])
        t = sub(act, "text", mediaType="text/plain")
        sub(t, "reference", value=f"#{nid}")
        sub(act, "statusCode", code="completed")
        sub(act, "effectiveTime", value=e["date"])
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
        er = sub(act, "entryRelationship", typeCode="COMP", inversionInd="true")
        enc = sub(er, "encounter", classCode="ENC", moodCode="EVN")
        sub(enc, "id", root=e["id"])
    return sec


def build_problems(body, member_id, dx_rows):
    """Visit diagnoses with no matching encounter row: kept, undated (the flat file carries no date)."""
    sec = new_section(body, "2.16.840.1.113883.10.20.22.2.5.1", "11450-4", "Problem list", "Problems")
    tbody = narrative_table(sec, ["Encounter ID", "ICD-10 Code", "Diagnosis", "Diagnosis names (as listed)", "Date"])
    n = 0
    for d in dx_rows:
        for icd, name in dx_pairs(d):
            n += 1
            pid = f"problem{n}"
            tr = sub(tbody, "tr")
            sub(tr, "td", d.get("ENCOUNTER_ID") or "")
            sub(tr, "td", icd)
            sub(sub(tr, "td"), "content", name or icd, ID=pid)
            sub(tr, "td", d.get("DX_NAME") or "")
            sub(tr, "td", "Not provided in source (encounter not found)")

            act = sub(sub(sec, "entry"), "act", classCode="ACT", moodCode="EVN")
            sub(act, "templateId", root="2.16.840.1.113883.10.20.22.4.3", extension="2015-08-01")
            sub(act, "id", root=new_id(member_id, "concern", d.get("ENCOUNTER_ID"), icd))
            sub(act, "code", code="CONC", codeSystem="2.16.840.1.113883.5.6")
            sub(act, "statusCode", code="active")
            sub(sub(act, "effectiveTime"), "low", nullFlavor="UNK")
            er = sub(act, "entryRelationship", typeCode="SUBJ")
            problem_observation(er, member_id, f"dx|{d.get('ENCOUNTER_ID')}", icd, name, None, pid)
    return sec


def build_results(body, member_id, labs):
    """Results section: rows grouped into one organizer per (service date, CPT code)."""
    sec = new_section(body, "2.16.840.1.113883.10.20.22.2.3.1", "30954-2",
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
            rid = f"result{n}"
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


# ----------------------------------------------------------------------------------------------- per member
def build_member(member_id, data):
    demo = data["demo"]
    encounters = []
    for r in data["encounter"]:
        # DOS = the visit date. CONTACT_DATE / EFFECTIVE_DATE_DTTM are fallbacks when ENCOUNTER_DATE is blank.
        # A recognisable date in any of them beats an unrecognisable ENCOUNTER_DATE; if none is recognisable, the first
        # non-blank one is kept as written.
        cols = ("ENCOUNTER_DATE", "CONTACT_DATE", "EFFECTIVE_DATE_DTTM")
        date = next((d for d in (hl7_ts(r.get(c), with_time=False, strict=True) for c in cols) if d), None)
        if not date:
            date = next((hl7_ts(r[c]) for c in cols if r.get(c)), None)
        elif r.get("ENCOUNTER_DATE") and not hl7_ts(r["ENCOUNTER_DATE"], with_time=False, strict=True):
            warn(f"{member_id}: unrecognised ENCOUNTER_DATE {r['ENCOUNTER_DATE']!r} in {r['_SOURCE_FILE']} — "
                 f"visit dated from CONTACT_DATE/EFFECTIVE_DATE_DTTM instead ({date})")
        if not date:
            warn(f"{member_id}: encounter row in {r['_SOURCE_FILE']} has no usable date — note kept undated")
        key = r.get("ENCOUNTER_ID") or r.get("ORIGINAL_ID") or f"{date}|{r.get('NOTE_TYPE')}|{r.get('NOTE_FILE_TIME_DTTM')}"
        encounters.append({"row": r, "date": date, "id": new_id(member_id, "encounter", key)})
    encounters.sort(key=lambda e: e["date"] or "")
    dated = [e for e in encounters if e["date"]]

    enc_ids = {e["row"].get("ENCOUNTER_ID") for e in dated if e["row"].get("ENCOUNTER_ID")}
    dx_by_enc, unmatched_dx = defaultdict(list), []
    for d in data["diagnosis"]:
        if d.get("ENCOUNTER_ID") in enc_ids:
            dx_by_enc[d["ENCOUNTER_ID"]].append(d)
        else:
            unmatched_dx.append(d)

    labs = data["lab"]
    for r in labs:
        r["_date"] = hl7_ts(r.get("SERVICE_DATE"), with_time=False)

    doc = ET.Element(f"{{{V3}}}ClinicalDocument")
    build_header(doc, member_id, demo, data["mbi"], [e["date"] for e in dated] + [r["_date"] for r in labs])
    body = sub(sub(doc, "component"), "structuredBody")
    if dated:
        build_encounters(body, member_id, dated, dx_by_enc)
    if encounters:
        build_notes(body, member_id, dated)
    if unmatched_dx:
        build_problems(body, member_id, unmatched_dx)
    if labs:
        build_results(body, member_id, labs)
    if not len(body):
        sec = new_section(body, "2.16.840.1.113883.10.20.22.2.65", "34109-9", "Note", "Notes")
        sub(sub(sec, "text"), "paragraph", "No clinical data in the flat files for this member.")
    return doc, {"encounters": len(dated), "notes": len(dated), "dx_on_encounter": sum(len(v) for v in dx_by_enc.values()),
                 "dx_unmatched": len(unmatched_dx), "labs": len(labs)}


def win_path(p):
    """cmd.exe turns a quoted path ending in a backslash ("C:\\data\\PCW\\") into C:\\data\\PCW" — the closing quote
    survives as a literal character.  Strip it, plus stray whitespace, so that form still works."""
    return os.path.normpath(p.strip().strip('"').strip())


def main(argv=None):
    # The Command Prompt console (and output redirected to a file) may use cp1252/cp437; never crash on a
    # character it cannot show, e.g. an accented name in a warning.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="PCW flat files -> one C-CDA XML per member (RGN_CD_MRN_NB)")
    ap.add_argument("-i", "--input-dir", required=True, help="folder with the .txt / .csv files (required)")
    ap.add_argument("-o", "--out-dir", help="output folder (default: <input-dir>\\ccda_output)")
    ap.add_argument("--render-html", action="store_true",
                    help="also render each XML to HTML, in <out-dir>_html (needs lxml: pip install lxml)")
    ap.add_argument("--xsl", help="stylesheet for --render-html (default: xsl\\cda.xsl next to this script)")
    args = ap.parse_args(argv)
    args.input_dir = win_path(args.input_dir)
    if not os.path.isdir(args.input_dir):
        ap.error(f"input folder not found: {args.input_dir}")
    out_dir = win_path(args.out_dir) if args.out_dir else os.path.join(args.input_dir, "ccda_output")
    render = None
    if args.render_html:  # checked before any work, so a missing lxml / stylesheet fails fast
        import render_html
        try:
            render = render_html.make_renderer(win_path(args.xsl) if args.xsl else render_html.DEFAULT_XSL)
        except render_html.RenderSetupError as exc:
            ap.error(str(exc))
    os.makedirs(out_dir, exist_ok=True)
    csv.field_size_limit(2**31 - 1)  # clinical notes can be very long; sys.maxsize overflows on Windows

    members = defaultdict(lambda: {"demo": {}, "mbi": None, "encounter": [], "diagnosis": [], "lab": [], "ids": None})
    for fam, prefix in FAMILIES.items():
        print(f"{prefix}*.txt / *.csv")
        rows = load_family(args.input_dir, prefix)
        if not rows:
            print("  (no files)")
        for r in rows:
            mid = member_key(r)
            if not mid:
                continue
            m = members[mid]
            m["ids"] = m["ids"] or {"RGN_CD": r["RGN_CD"], "MRN_NB": r["MRN_NB"]}
            if fam == "demo":
                for k, v in r.items():  # several demo rows for one member: first non-empty value wins
                    if v and not m["demo"].get(k):
                        m["demo"][k] = v
            elif fam == "crosswalk":
                if m["mbi"] and r.get("MBI") and r["MBI"] != m["mbi"]:
                    warn(f"{mid}: more than one MBI in crosswalk ({m['mbi']}, {r['MBI']}) — keeping the first")
                m["mbi"] = m["mbi"] or r.get("MBI") or None
            else:
                m[fam].append(r)

    print(f"\nWriting {len(members)} member file(s) to {out_dir}")
    written = []
    for mid in sorted(members):
        data = members[mid]
        if not data["demo"]:
            warn(f"{mid}: no member_demo_data row — demographics left as unknown")
            data["demo"] = dict(data["ids"])
        doc, stats = build_member(mid, data)
        ET.indent(doc, space="  ")
        path = os.path.join(out_dir, f"{mid}.xml")
        ET.ElementTree(doc).write(path, encoding="UTF-8", xml_declaration=True)
        written.append(path)
        print(f"  {mid}.xml  " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    failed = []
    if render:
        html_dir = render_html.html_dir_for(out_dir)
        print(f"\nRendering HTML to {html_dir}")
        ok, failed = render_html.render_files(written, html_dir, render)
        print(f"  {ok} of {len(written)} rendered")
    if WARNINGS:
        print(f"\n{len(WARNINGS)} warning(s) — see above", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
