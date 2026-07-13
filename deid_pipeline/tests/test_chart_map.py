import pytest

from deid.member.chart_map import ChartMapRow, parse_chart_map

HEADER = "str_member_id,source_file,file_source,file_dest\n"


def _write(tmp_path, body):
    p = tmp_path / "map.csv"
    p.write_text(HEADER + body)
    return str(p)


def test_parses_rows_and_groups(tmp_path):
    body = (
        "H51,a.pdf,s3://b/clients/2/charts/X/pdf/o.pdf,Chart/out/a.pdf\n"
        "ps001,c.pdf,s3://b/clients/2/charts/Y/pdf/1.pdf,Chart/out/c.pdf\n"
        "ps001,c.pdf,s3://b/clients/2/charts/Y/xml/2.xml,Chart/out/c/child.xml\n"
    )
    rows = parse_chart_map(_write(tmp_path, body))
    assert len(rows) == 3
    assert rows[0] == ChartMapRow("H51", "a.pdf", "s3://b/clients/2/charts/X/pdf/o.pdf", "Chart/out/a.pdf")
    # (member_id, source_file) is a grouping, not unique — ps001/c.pdf repeats
    assert [r.file_dest for r in rows if r.member_id == "ps001"] == ["Chart/out/c.pdf", "Chart/out/c/child.xml"]


def test_none_marker_flag(tmp_path):
    body = (
        "m1,x.none,s3://b/proc/Chart/ccda_0209.none,Chart/out/x.none\n"
        "m1,x.none,s3://b/clients/2/charts/Z/xml/a.xml,Chart/out/x/CarePlan.xml\n"
    )
    rows = parse_chart_map(_write(tmp_path, body))
    assert rows[0].is_none_marker is True
    assert rows[1].is_none_marker is False


def test_skips_blank_and_incomplete_lines(tmp_path):
    body = (
        "H51,a.pdf,s3://b/c/pdf/o.pdf,Chart/out/a.pdf\n"
        "\n"
        "   ,   ,   ,   \n"
        ",,,\n"
        "m2,b.pdf,,Chart/out/b.pdf\n"          # missing file_source -> skipped
        "m3,c.pdf,s3://b/c/pdf/x.pdf,\n"        # missing file_dest -> skipped
    )
    rows = parse_chart_map(_write(tmp_path, body))
    assert len(rows) == 1
    assert rows[0].member_id == "H51"


def test_missing_required_column_raises(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("str_member_id,source_file,file_dest\nH51,a.pdf,Chart/out/a.pdf\n")  # no file_source
    with pytest.raises(ValueError):
        parse_chart_map(str(p))


def test_source_file_column_is_optional(tmp_path):
    # mapping without the (unused) source_file column still parses
    p = tmp_path / "no_src.csv"
    p.write_text(
        "str_member_id,file_source,file_dest\n"
        "H51,s3://b/c/pdf/o.pdf,Chart/out/a.pdf\n"
    )
    rows = parse_chart_map(str(p))
    assert len(rows) == 1
    assert rows[0].member_id == "H51"
    assert rows[0].file_source == "s3://b/c/pdf/o.pdf"
    assert rows[0].file_dest == "Chart/out/a.pdf"
    assert rows[0].source_file == ""          # absent → empty label
    assert rows[0].is_none_marker is False
