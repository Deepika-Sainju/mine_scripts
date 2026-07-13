import re

import pytest
from lxml import etree

from deid.core.models import MemberPHI
from deid.documents.ccda import deidentify_ccda_file_member
from deid.member.scrubber import MemberScrubber
from deid.surrogate.generator import SurrogateGenerator

PHI = MemberPHI(first="Marcus", middle="J", last="Delgado",
                dob="03/14/1958", member_id="H900000001", gender="M")


@pytest.fixture
def deid_xml(fixtures, tmp_path):
    out = tmp_path / "ccda_deid.xml"
    res = deidentify_ccda_file_member(
        str(fixtures / "Chart/synthetic_ccda_delgado.xml"), str(out),
        MemberScrubber(PHI, seed=1234))
    return out.read_text(encoding="utf-8"), res


def test_member_phi_gone_from_output(deid_xml):
    text, _ = deid_xml
    assert not re.search(r"\bMarcus\b", text, re.I)
    assert not re.search(r"\bDelgado\b", text, re.I)
    assert "H900000001" not in text
    assert "19580314" not in text  # birthTime shifted


def test_providers_custodian_and_clinical_content_kept(deid_xml):
    text, _ = deid_xml
    assert "Whitmore" in text
    assert "Castillo" in text
    assert "Cascade Medical Group" in text
    assert "diabetes" in text


def test_output_is_valid_xml_and_reports_tally(deid_xml, tmp_path):
    text, res = deid_xml
    etree.fromstring(text.encode("utf-8"))  # parses
    assert res["by_source"].get("ccda-patient", 0) > 0
    assert len(res["report"]) == sum(res["by_source"].values())


def test_chart_fakes_match_the_csv_fakes(deid_xml):
    # the chart's fake given name must equal the member CSV's fake (same seed)
    text, _ = deid_xml
    expected_first = SurrogateGenerator(seed=1234).name_part("Marcus", "first", "M")
    assert expected_first in text


def test_deterministic_across_runs(fixtures, tmp_path):
    outs = []
    for i in (1, 2):
        out = tmp_path / f"run{i}.xml"
        deidentify_ccda_file_member(
            str(fixtures / "Chart/synthetic_ccda_delgado.xml"), str(out),
            MemberScrubber(PHI, seed=1234))
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]
