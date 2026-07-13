"""Member-file-driven project de-identification (entry point = the member CSV).

    member CSV (S3) ─► per member row:
        resolve its chart in Chart/ (one recursive listing) ─► OCR (PDF) or parse (C-CDA)
        ─► surrogate that member's PHI + generic identifiers (keep providers / DOS / clinical)
        ─► write de-identified chart (local)
    ─► also de-identify the member CSV and every claims CSV (consistent, same seed)

Reads directly from the S3 file bucket (READ-ONLY); writes to a LOCAL output dir.
No database, no NER. Source files (real PHI) live only in a temp dir and are
deleted on exit — only de-identified outputs persist. PDF charts need the OCR
stack (vLLM + GPU); C-CDA/XML and the CSVs are CPU-only.
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from deid.cli.base import BaseCommand, CommandError
from deid.documents import deidentify_chart, deidentify_chart_to
from deid.documents.csv_file import deidentify_csv
from deid.member.chart_map import parse_chart_map
from deid.member.manifest import parse_member_rows
from deid.sources import s3 as s3_source

log = logging.getLogger("deid.cli.project")


def _rel(project_root, key) -> Path:
    """Source key's path relative to the project root (basename if outside it).

    Outputs and reports mirror this, so `Member/…` and `Chart/…` files keep their
    layout and never collide on basename (e.g. Member/member.csv vs Chart/member.csv).
    """
    return Path(key[len(project_root):] if key.startswith(project_root) else Path(key).name)


def _deid_chart(bucket, key, local_dest, out_stem, report_path, s3, scrubber, *,
                seed, use_regex, show_values, skip_existing, who, label) -> str:
    """Download + de-identify one chart file (a main chart or an attachment).

    Writes ``<out_stem>_deid.<ext>`` (parent dirs created for nested attachments),
    reusing the member's ``scrubber`` so fakes stay consistent across the set.
    Frees the downloaded source PHI immediately. Returns 'ok'|'existing'|'failed'.
    """
    ext = ".xml" if str(key).lower().endswith(".xml") else ".pdf"
    out_file = Path(f"{out_stem}_deid{ext}")
    if skip_existing and out_file.exists():
        return "existing"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    src = s3_source.download(bucket, key, str(local_dest), s3)
    try:
        deidentify_chart(src, out_stem, scrubber.phi, seed=seed, use_regex=use_regex,
                         report_path=report_path, show_values=show_values, scrubber=scrubber)
        return "ok"
    except Exception as e:
        log.warning("member %s: chart %r FAILED: %r", who, label, e)
        return "failed"
    finally:
        Path(src).unlink(missing_ok=True)  # free source PHI promptly at scale


def _process_member_csv(bucket, member_key, chart_prefix, project_root, out_dir,
                        rep_dir, tmp, s3, *, seed, no_charts, no_regex, want_report,
                        show_values, subset, skip_existing
                        ) -> Tuple[int, int, List[str], int, Dict[str, str]]:
    """De-identify one member CSV + its charts.

    Charts are resolved ONE MEMBER AT A TIME via a scoped `Chart/<stem>*` listing
    (`s3.resolve_chart`) — never a full enumeration of `Chart/`, so this scales to
    buckets with millions of charts. Each member row's main chart AND its
    attachments (files nested in `Chart/<stem>/`) are de-identified with ONE
    scrubber, so a member maps to the same fakes across the whole document set.
    Every output mirrors the source's path under the project root with a `_deid`
    suffix — no basename collisions, clear provenance.

    Returns (charts_ok, charts_already_done, skipped_member_ids, num_rows,
    gender_by_member_id) — the gender map feeds the claims CSV de-id so fake
    first names stay consistent even when claims lacks a gender column.
    """
    rel = _rel(project_root, member_key)
    local_member = s3_source.download(bucket, member_key, str(tmp / rel), s3)
    rows = parse_member_rows(local_member)
    genders = {phi.member_id: phi.gender for phi, _ in rows
               if phi.member_id and phi.gender}

    out_csv = out_dir / rel.parent / f"{rel.stem}_deid{rel.suffix}"
    if not (skip_existing and out_csv.exists()):
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rp = str(rep_dir / rel.parent / f"{rel.stem}_member_report.json") if want_report else None
        if rp:
            Path(rp).parent.mkdir(parents=True, exist_ok=True)
        deidentify_csv(local_member, str(out_csv), csv_type="member", seed=seed,
                       report_path=rp, show_values=show_values)

    ok, existing, skipped = 0, 0, []
    if no_charts:
        return ok, existing, skipped, len(rows), genders

    from deid.member.scrubber import MemberScrubber  # CPU-only; lazy for CSV-only runs

    def _tally(status):
        nonlocal ok, existing
        if status == "ok":
            ok += 1
        elif status == "existing":
            existing += 1
        return status

    def _run_chart(key, who, scrubber):
        crel = _rel(project_root, key)
        rp = str(rep_dir / crel.parent / f"{crel.stem}_report.json") if want_report else None
        return _tally(_deid_chart(
            bucket, key, tmp / crel, out_dir / crel.parent / crel.stem, rp, s3,
            scrubber, seed=seed, use_regex=not no_regex, show_values=show_values,
            skip_existing=skip_existing, who=who, label=str(crel)))

    for phi, chart_name in rows:
        if subset and phi.member_id not in subset:
            continue
        chart_name = chart_name or ""
        scrubber = MemberScrubber(phi, seed=seed)  # ONE session: main chart + attachments

        # Scoped lookup: lists only Chart/<stem>* (main + its attachments), not all of Chart/.
        main_key, attachments = ("", [])
        if chart_name:
            main_key, attachments = s3_source.resolve_chart(bucket, chart_prefix, chart_name, s3)

        # --- the main chart the member row names ---
        if chart_name and not chart_name.lower().endswith(".none"):
            if not main_key:
                log.warning("member %s: chart %r not in Chart/ — skipping",
                            phi.member_id, chart_name)
                skipped.append(phi.member_id)
            elif _run_chart(main_key, phi.member_id, scrubber) == "failed":
                skipped.append(phi.member_id)
        else:
            skipped.append(phi.member_id)  # .none / missing main chart

        # --- attachments nested in Chart/<stem>/ (same member identity) ---
        for ckey in attachments:
            if not ckey.lower().endswith((".pdf", ".xml")):
                continue  # only charts; a nested CSV is a claims file (handled elsewhere)
            if _run_chart(ckey, phi.member_id, scrubber) == "failed":
                skipped.append(phi.member_id)
    return ok, existing, skipped, len(rows), genders


def _deidentify_claims(bucket, index, claims_csv, project_root, out_dir, rep_dir, tmp, s3, *,
                       seed, want_report, show_values, skip_existing,
                       gender_lookup=None):
    """De-identify EVERY claims CSV found in Chart/ (or only --claims-csv), once each."""
    targets: List[Tuple[str, str]] = []  # (bucket, key)
    if claims_csv:
        if claims_csv.startswith("s3://"):
            targets = [s3_source.parse_s3_uri(claims_csv)]
        elif claims_csv in index:
            targets = [(bucket, index[claims_csv])]
        else:
            print(f"[project] --claims-csv {claims_csv!r} not found in Chart/",
                  file=sys.stderr)
    else:
        targets = [(bucket, k) for k in s3_source.find_claims_keys(index)]
    if not targets:
        print("[project] no claims CSV found in Chart/ (skipping)")
        return

    done, existing = 0, 0
    for claims_bucket, key in targets:
        rel = _rel(project_root, key)
        out_csv = out_dir / rel.parent / f"{rel.stem}_deid{rel.suffix}"
        if skip_existing and out_csv.exists():
            existing += 1
            continue
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        local = s3_source.download(claims_bucket, key, str(tmp / rel), s3)
        try:
            rp = str(rep_dir / rel.parent / f"{rel.stem}_claims_report.json") if want_report else None
            if rp:
                Path(rp).parent.mkdir(parents=True, exist_ok=True)
            deidentify_csv(local, str(out_csv), csv_type="claims", seed=seed,
                           report_path=rp, show_values=show_values,
                           gender_lookup=gender_lookup)
        finally:
            Path(local).unlink(missing_ok=True)  # free source PHI promptly
        done += 1
        print(f"[project] claims CSV de-identified -> {out_csv}")
    extra = f", {existing} already done" if existing else ""
    print(f"[project] claims: {done} de-identified{extra} ({len(targets)} found)")


# ── mapping-driven flow (charts fetched by explicit s3:// path; no listing) ──────

def _deid_member_csvs(member_keys, bucket, out_dir, rep_dir, tmp, s3, *,
                      seed, want_report, show_values, skip_existing) -> Dict[str, object]:
    """De-identify each member CSV → out/Member/<name>_deid.csv; return {member_id: phi}.

    The returned map is the PHI source for the chart-map flow (joined on str_member_id).
    """
    phi_by_id: Dict[str, object] = {}
    for mk in member_keys:
        name = Path(mk).name
        stem, suffix = Path(name).stem, Path(name).suffix
        local = s3_source.download(bucket, mk, str(tmp / name), s3)
        for phi, _ in parse_member_rows(local):
            if phi.member_id:
                phi_by_id[phi.member_id] = phi
        out_csv = out_dir / "Member" / f"{stem}_deid{suffix}"
        if not (skip_existing and out_csv.exists()):
            out_csv.parent.mkdir(parents=True, exist_ok=True)
            rp = str(rep_dir / "Member" / f"{stem}_member_report.json") if want_report else None
            if rp:
                Path(rp).parent.mkdir(parents=True, exist_ok=True)
            deidentify_csv(local, str(out_csv), csv_type="member", seed=seed,
                           report_path=rp, show_values=show_values)
        print(f"[project] member CSV de-identified -> {out_csv}")
    return phi_by_id


def _run_chart_map(chart_map_uri, phi_by_id, out_dir, rep_dir, tmp, s3, *,
                   seed, no_regex, want_report, show_values, skip_existing):
    """De-identify every chart named in the mapping CSV, fetched by explicit s3:// path.

    Per row: `.none` source → write an empty marker at file_dest; otherwise download
    file_source, de-identify by the SOURCE's type, and write to out_dir/file_dest
    verbatim. One MemberScrubber per str_member_id (consistent fakes). No S3 listing.
    """
    from deid.member.scrubber import MemberScrubber  # CPU-only; lazy

    if chart_map_uri.startswith("s3://"):
        b, k = s3_source.parse_s3_uri(chart_map_uri)
        local_map = s3_source.download(b, k, str(tmp / Path(k).name), s3)
    else:
        local_map = chart_map_uri
    rows = parse_chart_map(local_map)

    scrubbers: Dict[str, object] = {}
    ok = markers = existing = skipped = 0
    seen_dest = set()
    src_dir = tmp / "chart_src"
    for i, row in enumerate(rows):
        out_file = out_dir / row.file_dest
        if row.file_dest in seen_dest:
            log.warning("duplicate file_dest %r — later row overwrites earlier", row.file_dest)
        seen_dest.add(row.file_dest)

        if skip_existing and out_file.exists():
            existing += 1
            continue
        out_file.parent.mkdir(parents=True, exist_ok=True)

        # .none marker: nothing to de-identify — write an empty file to preserve the tree.
        if row.is_none_marker:
            out_file.write_bytes(b"")
            markers += 1
            continue

        phi = phi_by_id.get(row.member_id)
        if phi is None:
            log.warning("member %r not in member CSV — skipping %s", row.member_id, row.file_dest)
            skipped += 1
            continue
        scrubber = scrubbers.get(row.member_id)
        if scrubber is None:
            scrubber = scrubbers[row.member_id] = MemberScrubber(phi, seed=seed)

        sb, sk = s3_source.parse_s3_uri(row.file_source)
        # temp name keeps the SOURCE extension (routing is by source type); index avoids collisions.
        src = s3_source.download(sb, sk, str(src_dir / f"{i}_{Path(sk).name}"), s3)
        rp = str(rep_dir / (row.file_dest + ".json")) if want_report else None
        if rp:
            Path(rp).parent.mkdir(parents=True, exist_ok=True)
        try:
            deidentify_chart_to(src, out_file, phi, seed=seed, use_regex=not no_regex,
                                report_path=rp, show_values=show_values, scrubber=scrubber)
            ok += 1
        except Exception as e:
            log.warning("chart-map row %s (%s) FAILED: %r", row.file_dest, row.file_source, e)
            skipped += 1
        finally:
            Path(src).unlink(missing_ok=True)

    extra = f", {existing} already done" if existing else ""
    print(f"[project] chart-map: {ok} charts de-identified, {markers} .none marker(s), "
          f"{skipped} skipped{extra} ({len(rows)} rows)")


class Command(BaseCommand):
    help = ("De-identify a project's charts + member/claims CSVs, driven by the "
            "member CSV(s) in the S3 file bucket.")

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--members-csv", default=None,
                            help="One member CSV: s3://<bucket>/processed/<client>/<project>/Member/<file>.csv")
        parser.add_argument("--members-prefix", default=None,
                            help="A Member/ folder: s3://…/Member/ — de-identify EVERY *.csv under it "
                                 "in one pass (Chart/ indexed once, OCR engine reused).")
        parser.add_argument("--output-dir", default="output",
                            help="LOCAL folder for de-identified outputs. (default: output)")
        parser.add_argument("--member-ids", default=None,
                            help="Comma-separated InputMemberID subset (default: all rows).")
        parser.add_argument("--seed", type=int, default=1234,
                            help="Surrogate seed; keep constant across runs/files. (default: 1234)")
        parser.add_argument("--chart-map", default=None,
                            help="Mapping CSV (s3:// URI or local path) with columns "
                                 "str_member_id,source_file,file_source,file_dest. When given, "
                                 "charts are fetched by explicit file_source path and written to "
                                 "file_dest (no Chart/ listing); the member CSV supplies PHI only.")
        parser.add_argument("--claims-csv", default=None,
                            help="De-identify ONLY this claims CSV (s3:// URI or a filename in "
                                 "Chart/); default: every *.csv found under Chart/.")
        parser.add_argument("--skip-existing", action="store_true",
                            help="Resume: skip files whose de-identified output already exists.")
        parser.add_argument("--no-charts", action="store_true",
                            help="De-identify the CSVs only; skip chart resolution/de-id.")
        parser.add_argument("--no-regex", action="store_true",
                            help="Chart text: match only the known member's PHI (skip generic-id regex).")
        parser.add_argument("--report", dest="want_report",
                            action=argparse.BooleanOptionalAction, default=True,
                            help="Write per-file JSON reports. (default: on)")
        parser.add_argument("--show-values", action="store_true",
                            help="Include real PHI in reports (else masked).")

    def handle(self, *, members_csv, members_prefix, output_dir, member_ids, seed,
               chart_map, claims_csv, skip_existing, no_charts, no_regex, want_report,
               show_values):
        if bool(members_csv) == bool(members_prefix):
            raise CommandError("provide exactly one of --members-csv or --members-prefix")

        s3 = s3_source.s3_client()
        if members_prefix:
            bucket, prefix = s3_source.parse_s3_uri(members_prefix)
            member_keys = s3_source.list_csv_keys(bucket, prefix, s3)
            if not member_keys:
                raise CommandError(f"no .csv found under {members_prefix}")
        else:
            bucket, mk = s3_source.parse_s3_uri(members_csv)
            member_keys = [mk]

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        rep_dir = out_dir / "reports"
        if want_report:
            rep_dir.mkdir(parents=True, exist_ok=True)
        subset = {s.strip() for s in member_ids.split(",")} if member_ids else None

        # ── mapping-driven flow: charts come from --chart-map, not a Chart/ listing ──
        if chart_map:
            with tempfile.TemporaryDirectory(prefix="deid_src_") as tmp:
                tmp = Path(tmp)
                print(f"[project] chart-map mode: {len(member_keys)} member CSV(s) for PHI")
                phi_by_id = _deid_member_csvs(member_keys, bucket, out_dir, rep_dir, tmp, s3,
                                              seed=seed, want_report=want_report,
                                              show_values=show_values, skip_existing=skip_existing)
                print(f"[project] {len(phi_by_id)} member identities loaded")
                if not no_charts:
                    _run_chart_map(chart_map, phi_by_id, out_dir, rep_dir, tmp, s3,
                                   seed=seed, no_regex=no_regex, want_report=want_report,
                                   show_values=show_values, skip_existing=skip_existing)
            print(f"[project] done (chart-map). outputs -> {out_dir}/")
            return

        project_root = s3_source.project_root_for(member_keys[0])

        with tempfile.TemporaryDirectory(prefix="deid_src_") as tmp:
            tmp = Path(tmp)
            # Charts are resolved per member row (scoped Chart/<stem>* listings) — we
            # NEVER enumerate all of Chart/, so this scales to millions of charts.
            chart_prefix = s3_source.chart_prefix_for(member_keys[0])

            print(f"[project] {len(member_keys)} member CSV(s) to process")
            total_ok, total_existing, total_skipped = 0, 0, 0
            gender_lookup: dict = {}
            for i, mk in enumerate(member_keys, 1):
                ok, existing, sk, nrows, genders = _process_member_csv(
                    bucket, mk, chart_prefix, project_root, out_dir, rep_dir, tmp, s3,
                    seed=seed, no_charts=no_charts, no_regex=no_regex, want_report=want_report,
                    show_values=show_values, subset=subset, skip_existing=skip_existing)
                total_ok += ok; total_existing += existing; total_skipped += len(sk)
                gender_lookup.update(genders)
                extra = f", {existing} already done" if existing else ""
                print(f"[project] ({i}/{len(member_keys)}) {Path(mk).name}: "
                      f"{nrows} rows, {ok} de-identified{extra}, {len(sk)} skipped")

            # Claims after the member CSVs, so the gender map is complete
            # (keeps fake first names consistent if a claims file lacks a gender column).
            # Top-level Chart/*.csv only — a cheap delimited listing, not a full scan.
            if not no_charts:
                claims_index = {Path(k).name: k
                                for k in s3_source.find_claims_keys_toplevel(bucket, chart_prefix, s3)}
                _deidentify_claims(bucket, claims_index, claims_csv, project_root, out_dir, rep_dir,
                                   tmp, s3, seed=seed, want_report=want_report,
                                   show_values=show_values, skip_existing=skip_existing,
                                   gender_lookup=gender_lookup)

        done_msg = (f"[project] done: {len(member_keys)} member CSV(s), {total_ok} charts "
                    f"de-identified, {total_skipped} skipped")
        if total_existing:
            done_msg += f", {total_existing} already done"
        print(done_msg + f". outputs -> {out_dir}/")


def main(argv=None):
    Command().run_from_argv(argv)


if __name__ == "__main__":
    main()
