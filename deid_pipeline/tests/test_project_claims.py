import shutil
from pathlib import Path

from deid.cli.project import _deidentify_claims


class FakeS3:
    """download_file that copies from a local path standing in for the S3 key."""

    def download_file(self, bucket, key, dest):
        shutil.copy(key, dest)


def _setup(fixtures, tmp_path, names):
    src_dir = tmp_path / "bucket"
    src_dir.mkdir()
    index = {}
    for name in names:
        p = src_dir / name
        shutil.copy(fixtures / "Chart/synthetic_claims.csv", p)
        index[name] = str(p)  # the "S3 key" is the local path for FakeS3
    index["some_chart.xml"] = str(src_dir / "some_chart.xml")  # non-CSV ignored
    for d in ("out", "rep", "tmpd"):
        (tmp_path / d).mkdir()
    # project_root prefixes the "S3 keys" so outputs mirror by basename here.
    proot = str(src_dir) + "/"
    return index, proot, tmp_path / "out", tmp_path / "rep", tmp_path / "tmpd"


def _run(index, proot, out, rep, tmpd, claims_csv=None, skip_existing=False):
    _deidentify_claims("bucket", index, claims_csv, proot, out, rep, tmpd, FakeS3(),
                       seed=1234, want_report=True, show_values=False,
                       skip_existing=skip_existing,
                       gender_lookup={"H900000001": "M"})


def test_every_claims_csv_is_deidentified(fixtures, tmp_path):
    index, proot, out, rep, tmpd = _setup(fixtures, tmp_path, ["a_claims.csv", "b_claims.csv"])
    _run(index, proot, out, rep, tmpd)
    # outputs carry the _deid suffix (unmistakably de-identified)
    assert (out / "a_claims_deid.csv").exists() and (out / "b_claims_deid.csv").exists()
    # per-file reports (no shared name to overwrite)
    assert (rep / "a_claims_claims_report.json").exists()
    assert (rep / "b_claims_claims_report.json").exists()
    # both files carry the SAME fakes (content-derived, same seed)
    assert (out / "a_claims_deid.csv").read_bytes() == (out / "b_claims_deid.csv").read_bytes()
    # downloaded source PHI was unlinked promptly
    assert list(tmpd.iterdir()) == []


def test_skip_existing_resumes_per_file(fixtures, tmp_path):
    index, proot, out, rep, tmpd = _setup(fixtures, tmp_path, ["a_claims.csv", "b_claims.csv"])
    (out / "a_claims_deid.csv").write_text("already done")
    _run(index, proot, out, rep, tmpd, skip_existing=True)
    assert (out / "a_claims_deid.csv").read_text() == "already done"  # untouched
    assert (out / "b_claims_deid.csv").exists()                       # still processed


def test_claims_csv_flag_targets_one_file(fixtures, tmp_path):
    index, proot, out, rep, tmpd = _setup(fixtures, tmp_path, ["a_claims.csv", "b_claims.csv"])
    _run(index, proot, out, rep, tmpd, claims_csv="b_claims.csv")
    assert not (out / "a_claims_deid.csv").exists()
    assert (out / "b_claims_deid.csv").exists()
