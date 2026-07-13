#!/usr/bin/env python
"""Generate a self-consistent test dataset for the de-identification pipeline.

Produces:
  test-data/member.txt   tab-separated member file
  test-data/claim.txt    tab-separated claim file
  test-data/Charts/      XML charts, each folder has a sibling .none marker;
                         some XMLs have an attached PDF, some are independent
  test-data/Chart/       standalone PDF charts

5 XMLs and 5 PDFs total (3 attached + 2 standalone).
"""
import os
import csv
import fitz  # PyMuPDF

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test-data")
# Everything (XML charts + their .none markers + attached PDFs, and the
# standalone PDF charts) lives under a single Chart/ folder.
CHART = os.path.join(ROOT, "Chart")
CHARTS = CHART


def mkdirs():
    os.makedirs(CHART, exist_ok=True)


# ---------------------------------------------------------------------------
# Members: each drives one chart. PII is echoed into the chart files so the
# de-identifier has real names / DOBs / SSNs / phones / addresses to catch.
# ---------------------------------------------------------------------------
# NOTE on columns (matches the real feed):
#   MemberGender     -> always "NA"
#   MemberMetalLevel -> actually carries the CHART FILE NAME (not Gold/Silver)
#   FilePath         -> the source system, "Eclat_Health"
# The member's name (first/middle/last) is echoed verbatim into that member's
# XML/PDF charts so the roster and the documents always agree.
SOURCE_SYSTEM = "Eclat_Health"

MEMBERS = [
    # chartfile is what appears in the MemberMetalLevel column and links the
    # roster row to its chart on disk. sex is the clinical value inside charts.
    dict(chartfile="A000-000401_244.none", inputid="A00000001",
         first="Robert",  middle="Lee",   last="Johnson",  dob="03/15/1978", sex="M",
         eff="01/01/2024", end="12/31/9999", esrd="0",
         ssn="512-33-8890", phone="(415) 555-0187", email="robert.johnson@example.com",
         addr="88 Pine Street, San Francisco, CA 94102", mrn="MRN-A000-000401",
         dx=[("C7800", "Secondary malignant neoplasm of unspecified lung"),
             ("C3490", "Malignant neoplasm of unspecified part of unspecified bronchus or lung")]),
    dict(chartfile="A000-000400_450.none", inputid="A00000002",
         first="Maria",   middle="Elena", last="Gonzalez", dob="07/22/1985", sex="F",
         eff="02/01/2024", end="12/31/9999", esrd="0",
         ssn="623-44-1201", phone="(312) 555-0143", email="maria.gonzalez@example.com",
         addr="210 Oak Avenue, Chicago, IL 60601", mrn="MRN-A000-000400",
         dx=[("E1165", "Type 2 diabetes mellitus with hyperglycemia"),
             ("I10", "Essential (primary) hypertension")]),
    dict(chartfile="A000-000402_311.none", inputid="A00000003",
         first="James",   middle="R",     last="Williams", dob="11/02/1990", sex="M",
         eff="03/01/2024", end="12/31/9999", esrd="1",
         ssn="401-55-7788", phone="(206) 555-0166", email="james.williams@example.com",
         addr="45 Cedar Road, Seattle, WA 98101", mrn="MRN-A000-000402",
         dx=[("N186", "End stage renal disease"),
             ("N179", "Acute kidney failure, unspecified")]),
    dict(chartfile="A000-000409_123.pdf", inputid="A00000004",
         first="Linda",   middle="M",     last="Martinez", dob="01/01/2000", sex="F",
         eff="01/01/2024", end="12/31/9999", esrd="0",
         ssn="330-21-4455", phone="(305) 555-0122", email="linda.martinez@example.com",
         addr="12 Bayview Drive, Miami, FL 33101", mrn="MRN-A000-000409",
         dx=[("J45909", "Unspecified asthma with (acute) exacerbation"),
             ("R05", "Cough")]),
    dict(chartfile="A000-000408_456.pdf", inputid="A00000005",
         first="David",   middle="K",     last="Chen",     dob="09/12/1965", sex="M",
         eff="04/01/2024", end="12/31/9999", esrd="0",
         ssn="774-88-3322", phone="(617) 555-0155", email="david.chen@example.com",
         addr="500 Beacon Street, Boston, MA 02115", mrn="MRN-A000-000408",
         dx=[("I509", "Heart failure, unspecified"),
             ("E785", "Hyperlipidemia, unspecified")]),
]

MEMBER_HEADER = ["MemberGender", "MemberMetalLevel", "FilePath", "InputMemberID",
                 "MemberFirstName", "MemberMiddleName", "MemberLastName", "MemberDOB",
                 "EnrollmentEffDate", "EnrollmentEndDate", "MemberESRD"]


def write_member_file():
    with open(os.path.join(ROOT, "member.csv"), "w", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(MEMBER_HEADER)
        for m in MEMBERS:
            w.writerow([
                "NA", m["chartfile"], SOURCE_SYSTEM, m["inputid"],
                m["first"], m["middle"], m["last"], m["dob"],
                m["eff"], m["end"], m["esrd"],
            ])


# ---------------------------------------------------------------------------
# Claims: reference the members above plus rendering providers (also PII).
# ---------------------------------------------------------------------------
CLAIM_HEADER = ["claimno", "memberid", "memberlastname", "memberfirstname", "gender",
                "dateofbirth", "membermetallevel", "rendering_providerid",
                "rendering_providerlastname", "rendering_providerfirstname",
                "servicestartdate", "servicethrudate", "code", "codetype",
                "related_diag", "modifier"]

# membermetallevel here also carries a chart-file-style token (not a metal tier),
# mirroring the member roster.
CLAIMS = [
    ["925535", "A00000001", "Johnson",  "Robert", "M", "03/15/1978", "A000-000401_244.none",
     "1000000003", "Miller",   "Roberto", "08/03/2025", "08/03/2025", "C782", "ICD", "C349", ""],
    ["925536", "A00000002", "Gonzalez", "Maria",  "F", "07/22/1985", "A000-000400_450.none",
     "1000000007", "Patel",    "Anita",   "07/14/2025", "07/14/2025", "E1165", "ICD", "I10", "25"],
    ["925537", "A00000003", "Williams", "James",  "M", "11/02/1990", "A000-000402_311.none",
     "1000000011", "Nguyen",   "Kevin",   "06/30/2025", "07/02/2025", "N186", "ICD", "N179", ""],
    ["925538", "A00000004", "Martinez", "Linda",  "F", "01/01/2000", "A000-000409_123.pdf",
     "1000000015", "O'Brien",  "Sean",    "05/20/2025", "05/20/2025", "J45909", "ICD", "", "LT"],
    ["925539", "A00000005", "Chen",     "David",  "M", "09/12/1965", "A000-000408_456.pdf",
     "1000000019", "Anderson", "Sarah",   "04/11/2025", "04/11/2025", "I509", "ICD", "E785", ""],
    ["925540", "A00000001", "Johnson",  "Robert", "M", "03/15/1978", "A000-000401_244.none",
     "1000000019", "Anderson", "Sarah",   "08/05/2025", "08/05/2025", "99214", "CPT", "C782", "25"],
]


def write_claim_file():
    with open(os.path.join(ROOT, "claim.csv"), "w", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(CLAIM_HEADER)
        for c in CLAIMS:
            w.writerow(c)


# ---------------------------------------------------------------------------
# Chart content builders
# ---------------------------------------------------------------------------
def xml_body(m, encounter_date, provider, note):
    dx_lines = "\n".join(
        f'    <Diagnosis code="{code}" codeType="ICD-10-CM" rank="{i}">{desc}</Diagnosis>'
        for i, (code, desc) in enumerate(m["dx"], start=1)
    )
    primary = m["dx"][0][0]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<MedicalRecord patientId="{m['mrn']}">
  <Patient>
    <Name>{m['first']} {m['middle']} {m['last']}</Name>
    <DateOfBirth>{m['dob']}</DateOfBirth>
    <Gender>{m['sex']}</Gender>
    <Contact>
      <Email>{m['email']}</Email>
      <Phone>{m['phone']}</Phone>
      <Address>{m['addr']}</Address>
    </Contact>
    <SSN>{m['ssn']}</SSN>
  </Patient>
  <Encounter date="{encounter_date}">
    <Provider>{provider}</Provider>
    <Note>{note}</Note>
  </Encounter>
  <Diagnoses primaryCode="{primary}">
{dx_lines}
  </Diagnoses>
</MedicalRecord>
"""


def write_pdf(path, m, encounter_date, provider, note):
    doc = fitz.open()
    page = doc.new_page()
    dx_block = "\n".join(f"  {code}  {desc}" for code, desc in m["dx"])
    text = (
        "PROGRESS NOTE\n"
        f"Patient: {m['first']} {m['middle']} {m['last']}\n"
        f"MRN: {m['mrn']}\n"
        f"DOB: {m['dob']}    Gender: {m['sex']}\n"
        f"SSN: {m['ssn']}\n"
        f"Phone: {m['phone']}    Email: {m['email']}\n"
        f"Address: {m['addr']}\n"
        "\n"
        f"Date of Service: {encounter_date}\n"
        f"Rendering Provider: {provider}\n"
        "\n"
        f"Clinical Note:\n{note}\n"
        "\n"
        "ICD-10-CM Diagnosis Codes:\n"
        f"{dx_block}\n"
    )
    page.insert_text((72, 72), text, fontsize=11, fontname="helv")
    doc.save(path)
    doc.close()


def touch_none(base):
    """Write the sibling .none marker for an XML chart folder."""
    with open(os.path.join(CHARTS, base + ".none"), "w") as f:
        f.write(f"chart={base}\nstatus=none\n")


def build_charts():
    # --- Chart A000-000401_244 : 2 XMLs, each WITH an attached PDF -----------
    base = "A000-000401_244"
    folder = os.path.join(CHARTS, base)
    os.makedirs(folder, exist_ok=True)
    touch_none(base)
    m = MEMBERS[0]
    for suffix, date, prov, note in [
        ("1111", "2025-08-03", "Dr. Emily Carter",
         f"Patient {m['first']} {m['last']} seen for chest pain. Spouse notified at {m['phone']}."),
        ("2222", "2025-08-05", "Dr. Sarah Anderson",
         f"Follow-up visit. Records mailed to {m['addr']}."),
    ]:
        with open(os.path.join(folder, f"{base}_{suffix}.xml"), "w") as f:
            f.write(xml_body(m, date, prov, note))
        write_pdf(os.path.join(folder, f"{base}_{suffix}.pdf"), m, date, prov, note)

    # --- Chart A000-000400_450 : 2 independent XMLs (no attached PDF) --------
    base = "A000-000400_450"
    folder = os.path.join(CHARTS, base)
    os.makedirs(folder, exist_ok=True)
    touch_none(base)
    m = MEMBERS[1]
    for suffix, date, prov, note in [
        ("1111", "2025-07-14", "Dr. Anita Patel",
         f"Diabetes management for {m['first']} {m['last']}. Contact {m['phone']}."),
        ("2222", "2025-07-16", "Dr. Anita Patel",
         f"Lab review. Patient SSN {m['ssn']} on file."),
    ]:
        with open(os.path.join(folder, f"{base}_{suffix}.xml"), "w") as f:
            f.write(xml_body(m, date, prov, note))

    # --- Chart A000-000402_311 : 1 XML WITH an attached PDF ------------------
    base = "A000-000402_311"
    folder = os.path.join(CHARTS, base)
    os.makedirs(folder, exist_ok=True)
    touch_none(base)
    m = MEMBERS[2]
    suffix, date, prov, note = ("1111", "2025-06-30", "Dr. Kevin Nguyen",
        f"ESRD workup for {m['first']} {m['last']} at {m['addr']}.")
    with open(os.path.join(folder, f"{base}_{suffix}.xml"), "w") as f:
        f.write(xml_body(m, date, prov, note))
    write_pdf(os.path.join(folder, f"{base}_{suffix}.pdf"), m, date, prov, note)


def build_standalone_pdfs():
    # A000-000409_123.pdf
    m = MEMBERS[3]
    write_pdf(os.path.join(CHART, "A000-000409_123.pdf"), m, "2025-05-20",
              "Dr. Sean O'Brien",
              f"Asthma exacerbation. {m['first']} {m['last']} reachable at {m['phone']}.")
    # A000-000408_456.pdf
    m = MEMBERS[4]
    write_pdf(os.path.join(CHART, "A000-000408_456.pdf"), m, "2025-04-11",
              "Dr. Sarah Anderson",
              f"Cardiology consult for {m['first']} {m['last']}, DOB {m['dob']}.")


def main():
    mkdirs()
    write_member_file()
    write_claim_file()
    build_charts()
    build_standalone_pdfs()
    print("Test data written to", ROOT)


if __name__ == "__main__":
    main()
