"""CLI contract tests: flag names, defaults, and error behavior are the
interface scripts and runbooks depend on — pinned here so a parser change
can't silently alter them. (Parity with the previous click implementation
was verified vector-by-vector at migration time.)"""
import pytest

from deid.cli import csv_file, project, render
from deid.documents.csv_file import deidentify_csv


def parse(command_module, argv):
    return vars(command_module.Command().create_parser(prog="x").parse_args(argv))


def test_project_defaults():
    got = parse(project, ["--members-csv", "s3://b/Member/m.csv"])
    assert got == {
        "members_csv": "s3://b/Member/m.csv", "members_prefix": None,
        "output_dir": "output", "member_ids": None, "seed": 1234,
        "chart_map": None, "claims_csv": None, "skip_existing": False,
        "no_charts": False, "no_regex": False, "want_report": True,
        "show_values": False,
    }


def test_project_all_flags():
    got = parse(project, ["--members-prefix", "s3://b/Member/", "--output-dir", "./o",
                          "--member-ids", "A,B", "--seed", "42", "--claims-csv", "c.csv",
                          "--skip-existing", "--no-charts", "--no-regex",
                          "--no-report", "--show-values"])
    assert got["members_prefix"] == "s3://b/Member/" and got["seed"] == 42
    assert got["skip_existing"] and got["no_charts"] and got["no_regex"]
    assert got["want_report"] is False and got["show_values"] is True


def test_project_report_flag_pair():
    assert parse(project, ["--members-csv", "x", "--report"])["want_report"] is True
    assert parse(project, ["--members-csv", "x", "--no-report"])["want_report"] is False


def test_project_requires_exactly_one_source():
    with pytest.raises(SystemExit) as e:
        project.Command().run_from_argv([])  # neither flag
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        project.Command().run_from_argv(
            ["--members-csv", "a", "--members-prefix", "b"])  # both flags
    assert e.value.code == 2


def test_csv_defaults_and_short_flags(fixtures):
    src = str(fixtures / "Member/synthetic_member.csv")
    got = parse(csv_file, [src, "-o", "/tmp/o.csv"])
    assert got == {"input_csv": src, "out_csv": "/tmp/o.csv", "csv_type": None,
                   "seed": 1234, "report_path": None, "show_values": False}
    got = parse(csv_file, [src, "--out", "/tmp/o.csv", "--type", "claims",
                           "-r", "/tmp/r.json"])
    assert got["csv_type"] == "claims" and got["report_path"] == "/tmp/r.json"


def test_csv_rejects_missing_input(tmp_path):
    with pytest.raises(SystemExit) as e:
        parse(csv_file, [str(tmp_path / "nope.csv"), "-o", "x.csv"])
    assert e.value.code == 2


def test_csv_out_is_required(fixtures):
    with pytest.raises(SystemExit) as e:
        parse(csv_file, [str(fixtures / "Member/synthetic_member.csv")])
    assert e.value.code == 2


def test_help_exits_zero():
    for mod in (project, csv_file, render):
        with pytest.raises(SystemExit) as e:
            parse(mod, ["--help"])
        assert e.value.code == 0


def test_csv_command_end_to_end_matches_library_call(fixtures, tmp_path):
    src = str(fixtures / "Member/synthetic_member.csv")
    via_cli, via_lib = tmp_path / "cli.csv", tmp_path / "lib.csv"
    csv_file.Command().run_from_argv([src, "-o", str(via_cli), "--seed", "1234"])
    deidentify_csv(src, str(via_lib), seed=1234)
    assert via_cli.read_bytes() == via_lib.read_bytes()
