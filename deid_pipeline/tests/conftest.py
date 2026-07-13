"""Test data is GENERATED here at runtime — no chart files live in the repo.

The `fixtures` fixture builds a fully synthetic project (member manifest,
claims CSV, C-CDA chart) into a session temp dir, mirroring the S3 layout
`<project>/{Member,Chart}/`. Every value is invented; nothing resembles a
real person or record.
"""
from pathlib import Path

import pytest

MEMBER_CSV = """\
MemberGender,MemberMetalLevel,FilePath,InputMemberID,MemberFirstName,MemberMiddleName,MemberLastName,MemberDOB,EnrollmentEffDate,EnrollmentEndDate,MemberESRD
M,synthetic_ccda_delgado.xml,Eclat_Health,H900000001,Marcus,J,Delgado,03/14/1958,01/01/2024,12/31/9999,0
F,synthetic_pdf_nair.pdf,Eclat_Health,H900000002,Priya,,Nair,07/22/1971,01/01/2024,12/31/9999,0
M,synthetic_none.none,Eclat_Health,H900000003,Sam,,Rivera,01/05/1990,01/01/2024,12/31/9999,0
"""

CLAIMS_CSV = """\
MemberGender,claimno,memberid,MemberLastName,MemberFirstName,gender,dateofbirth,membermetallevel,rendering_providerid,rendering_providerfirstname,rendering_providerlastname,servicestartdate,servicethrudate,code,mbi,codetype
M,C900001,H900000001,Delgado,Marcus,M,03/14/1958,synthetic_ccda_delgado.xml,1023456789,Helen,Whitmore,03/03/2025,03/03/2025,E1122,1EG4TE5MK72,ICD
M,C900002,H900000001,Delgado,Marcus,M,03/14/1958,synthetic_ccda_delgado.xml,1023456789,Helen,Whitmore,04/11/2025,04/11/2025,99214,1EG4TE5MK72,CPT
F,C900003,H900000002,Nair,Priya,F,07/22/1971,synthetic_pdf_nair.pdf,1087654321,Elena,Castillo,02/17/2025,02/17/2025,M54.16,2FH5UF6NL83,ICD
"""

CCDA_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3">
  <recordTarget>
    <patientRole>
      <id extension="H900000001" root="2.16.840.1.113883.19.5"/>
      <addr>
        <streetAddressLine>101 Sunrise Court</streetAddressLine>
        <city>Fresno</city>
        <state>CA</state>
        <postalCode>93722</postalCode>
      </addr>
      <telecom value="tel:(559)555-0142"/>
      <patient>
        <name><given>Marcus</given><given>J</given><family>Delgado</family></name>
        <administrativeGenderCode code="M"/>
        <birthTime value="19580314"/>
      </patient>
    </patientRole>
  </recordTarget>
  <author>
    <assignedAuthor>
      <assignedPerson><name><given>Elena</given><family>Castillo</family></name></assignedPerson>
    </assignedAuthor>
  </author>
  <custodian>
    <assignedCustodian>
      <representedCustodianOrganization><name>Cascade Medical Group</name></representedCustodianOrganization>
    </assignedCustodian>
  </custodian>
  <component>
    <structuredBody>
      <component>
        <section>
          <title>Assessment and Plan</title>
          <text>Marcus Delgado, born 03/14/1958 (member id H900000001), was seen by
Dr. Helen Whitmore for type 2 diabetes. Plan and follow-up reviewed with Marcus.</text>
        </section>
      </component>
    </structuredBody>
  </component>
</ClinicalDocument>
"""


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("synthetic_project")
    (root / "Member").mkdir()
    (root / "Chart").mkdir()
    (root / "Member" / "synthetic_member.csv").write_text(MEMBER_CSV, encoding="utf-8")
    (root / "Chart" / "synthetic_claims.csv").write_text(CLAIMS_CSV, encoding="utf-8")
    (root / "Chart" / "synthetic_ccda_delgado.xml").write_text(CCDA_XML, encoding="utf-8")
    return root
