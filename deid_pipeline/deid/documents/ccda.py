"""De-identify C-CDA (HL7 v3 Clinical Document XML) — member-file-driven.

Two passes over the parsed tree:
  * structured fields  — the patient's own identifiers (names, birthTime, ids)
    wherever they recur, plus patientRole addr/telecom, surrogated in place so
    the document stays well-formed and schema-conformant.
  * narrative <text>   — free-text blocks value-scrubbed for the known member's
    PHI + generic direct identifiers (deid.member.scrubber).

Provider/custodian/author names + contact info, every non-DOB date, and coded
clinical entries are KEPT. No OCR/GPU: the text is already digital. Output is
valid de-identified C-CDA XML.
"""
from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from deid.core.dates import parse_date
from deid.core.models import MemberPHI
from deid.core.reporting import count_by_source, write_detection_report
from deid.member.scrubber import MemberScrubber


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _parse_date_only(value):
    """The date part of a parseable date value; None if it isn't a date."""
    dt = parse_date(value)
    return dt.date() if dt else None


def _surrogate_telecom(scrubber: MemberScrubber, value: str) -> str:
    if value.lower().startswith("mailto:"):
        return "mailto:" + scrubber.surrogate("EMAIL_ADDRESS", value[7:])
    m = re.match(r"^([A-Za-z]+:)?(.*)$", value, re.S)
    prefix, rest = (m.group(1) or ""), m.group(2)
    return prefix + scrubber.surrogate("PHI", rest)  # digits scrambled, +-() kept


def _write_xml(tree, out_xml) -> None:
    """Serialize valid de-identified C-CDA XML, keeping the source encoding."""
    Path(out_xml).parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(out_xml), xml_declaration=True,
               encoding=tree.docinfo.encoding or "UTF-8")


def _surrogate_addr(el, scrubber: MemberScrubber, record) -> None:
    """Surrogate an <addr> in place (street/city/county/zip); keep <state>."""
    for sub in el.iter():
        t = _local(sub.tag)
        if not (sub.text and sub.text.strip()):
            continue
        if t in ("streetAddressLine", "additionalLocator", "deliveryAddressLine"):
            o = sub.text; sub.text = scrubber.surrogate("STREET_ADDRESS", o)
            record("STREET_ADDRESS", o, sub.text, "ccda-patient")
        elif t in ("city", "county"):
            # county is a geographic subdivision smaller than state -> Safe Harbor PHI;
            # <state> is kept (state-level geography is permitted under Safe Harbor)
            o = sub.text; sub.text = scrubber.surrogate("LOCATION", o)
            record("LOCATION", o, sub.text, "ccda-patient")
        elif t == "postalCode":
            o = sub.text; sub.text = scrubber.surrogate("ZIP", o)
            record("ZIP", o, sub.text, "ccda-patient")


def _patient_identity(root) -> MemberPHI:
    """The patient's OWN identity as embedded in the chart (recordTarget/patientRole).

    The chart is authoritative for who its patient is — the member CSV can carry a
    different/placeholder identity, so we scrub the values the chart itself uses.
    """
    firsts, middles, lasts, dob, pid = [], [], [], "", ""
    for pr in [e for e in root.iter() if _local(e.tag) == "patientRole"]:
        for child in pr:
            t = _local(child.tag)
            if t == "id" and child.get("extension") and not pid:
                pid = child.get("extension")
            elif t == "patient":
                for name_el in [e for e in child.iter() if _local(e.tag) == "name"]:
                    gv = [s.text.strip() for s in name_el
                          if _local(s.tag) == "given" and s.text and s.text.strip()]
                    fm = [s.text.strip() for s in name_el
                          if _local(s.tag) == "family" and s.text and s.text.strip()]
                    if gv:
                        firsts.append(gv[0]); middles += gv[1:]
                    lasts += fm
                for bt in [e for e in child.iter() if _local(e.tag) == "birthTime"]:
                    if bt.get("value") and not dob:
                        dob = bt.get("value")
    return MemberPHI(first=firsts[0] if firsts else "",
                     middle=middles[0] if middles else "",
                     last=lasts[0] if lasts else "", dob=dob, member_id=pid)


def _chart_patient_gender(root) -> str:
    """administrativeGenderCode under patientRole/patient, or ""."""
    gender = ""
    for pr in [e for e in root.iter() if _local(e.tag) == "patientRole"]:
        for gc in [e for e in pr.iter() if _local(e.tag) == "administrativeGenderCode"]:
            if gc.get("code"):
                gender = gc.get("code"); break
    return gender


def _resolve_patient(root, csv_phi: MemberPHI) -> MemberPHI:
    """Union the chart's own patient identity with the member CSV row.

    Name/DOB: chart is authoritative for its own patient. member_id: prefer the CSV
    InputMemberID (the cross-file join key) so its surrogate matches the CSVs.
    Gender: CSV wins over the chart's administrativeGenderCode.
    """
    cphi = _patient_identity(root)
    pick = lambda a, b: a if (a or "").strip() else b
    return MemberPHI(first=pick(cphi.first, csv_phi.first),
                     middle=pick(cphi.middle, csv_phi.middle),
                     last=pick(cphi.last, csv_phi.last),
                     dob=pick(cphi.dob, csv_phi.dob),
                     member_id=pick(csv_phi.member_id, cphi.member_id),
                     gender=pick(csv_phi.gender, _chart_patient_gender(root)))


def deidentify_ccda_file_member(input_xml, out_xml, scrubber: MemberScrubber, *,
                                report_path=None, use_regex=True,
                                show_values=False) -> dict:
    """Member-file-driven C-CDA de-id.

    The patient's identity is taken from the chart itself (recordTarget/patientRole)
    unioned with the member manifest, then scrubbed **document-wide**: any <name>
    whose given/family matches the patient's name, any <birthTime> equal to the
    patient DOB, and any <id> equal to the patient id — wherever they recur
    (playingEntity, participant, narrative tables, …). Patient addr/telecom
    (patientRole) are surrogated too. Provider/custodian/author names + contact
    info, every other date, and coded clinical entries are KEPT. Narrative <text>
    is value-scrubbed.
    """
    tree = etree.parse(str(input_xml))
    root = tree.getroot()
    report = []

    # chart-authoritative identity: refine the session so the collision guard
    # and canonical fakes reflect the values the chart itself uses
    phi = _resolve_patient(root, csv_phi=scrubber.phi)
    scrubber = scrubber.for_identity(phi)
    name_tokens = {m.group(0).lower()
                   for v in (phi.first, phi.middle, phi.last)
                   for m in re.finditer(r"[A-Za-z]{2,}", v or "")}
    dob_date = _parse_date_only(phi.dob)
    # every id the patient carries (all patientRole <id> extensions + the manifest
    # id) — so a chart with both an MRN and the InputMemberID has both scrubbed.
    pid_set = {e.get("extension") for pr in root.iter() if _local(pr.tag) == "patientRole"
               for e in pr if _local(e.tag) == "id" and e.get("extension")}
    if (phi.member_id or "").strip():
        pid_set.add(phi.member_id.strip())

    def _record(etype, original, surrogate, source):
        report.append({"type": etype, "original": original,
                       "surrogate": surrogate, "score": 0.99, "source": source})

    # ---- structured, document-wide: only the patient's own identifiers ----
    for el in root.iter():
        t = _local(el.tag)
        if t == "name" and name_tokens:
            gvfm = [s for s in el if _local(s.tag) in ("given", "family")
                    and s.text and s.text.strip()]
            if gvfm:
                toks = {s.text.strip().lower() for s in gvfm}
                if toks & name_tokens:  # this <name> is the patient (not a provider)
                    seen_given = False
                    for s in gvfm:
                        if _local(s.tag) == "given":
                            kind = "first" if not seen_given else "middle"
                            seen_given = True
                        else:
                            kind = "last"
                        o = s.text; s.text = scrubber.name_part(o, kind)
                        _record("MEMBER_" + kind.upper(), o, s.text, "ccda-patient")
            elif el.text and el.text.strip():
                # flat-text <name> (e.g. playingEntity/assignedPerson): value-scrub
                # only the patient's name tokens — provider names stay unchanged.
                new, rep = scrubber.scrub_text(el.text, use_regex=False)
                if new != el.text:
                    el.text = new
                    report.extend(rep)
        elif t == "birthTime" and dob_date and el.get("value"):
            if _parse_date_only(el.get("value")) == dob_date:
                o = el.get("value"); nv = scrubber.fake_dob(o)
                if nv != o:
                    el.set("value", nv); _record("MEMBER_DOB", o, nv, "ccda-patient")
        elif t == "id" and el.get("extension") in pid_set:
            o = el.get("extension"); el.set("extension", scrubber.identifier(o))
            _record("MEMBER_ID", o, el.get("extension"), "ccda-patient")

    # ---- patient contact info (patientRole only; provider contact is kept) ----
    for pr in [e for e in root.iter() if _local(e.tag) == "patientRole"]:
        for child in pr:
            t = _local(child.tag)
            if t == "addr":
                _surrogate_addr(child, scrubber, _record)
            elif t == "telecom" and child.get("value"):
                o = child.get("value"); child.set("value", _surrogate_telecom(scrubber, o))
                _record("PHI", o, child.get("value"), "ccda-patient")

    # ---- narrative <text>: scrub known member PHI + generic identifiers ----
    for tb in [el for el in root.iter() if _local(el.tag) == "text"]:
        for node in tb.iter():
            if node.text and node.text.strip():
                new, rep = scrubber.scrub_text(node.text, use_regex=use_regex)
                node.text = new
                report.extend(rep)
            if node is not tb and node.tail and node.tail.strip():
                new, rep = scrubber.scrub_text(node.tail, use_regex=use_regex)
                node.tail = new
                report.extend(rep)

    _write_xml(tree, out_xml)

    by_source = count_by_source(report)
    if report_path:
        write_detection_report(report_path, report, by_source, show_values)
    return {"report": report, "by_source": by_source,
            "num_pages": len([e for e in root.iter() if _local(e.tag) == "text"])}
