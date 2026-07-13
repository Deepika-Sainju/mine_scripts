"""Command-line interface."""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Iterator

from .audit import AuditLog
from .engine import DeidentificationEngine
from .handlers import get_handler, supported_extensions
from .pseudonym import PseudonymStore
from .replacement_config import ReplacementConfig


def iter_files(path: Path, recursive: bool) -> Iterator[Path]:
    if path.is_file():
        yield path
    elif path.is_dir():
        pattern = "**/*" if recursive else "*"
        for p in sorted(path.glob(pattern)):
            if p.is_file():
                yield p


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deidentify",
        description=(
            "De-identify PII in files. Detects HIPAA + GDPR + financial identifiers "
            "across text/CSV/JSON/XML/PDF/DOCX and replaces them with consistent pseudonyms."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("input", nargs="?", help="Input file or folder")
    p.add_argument("-o", "--output", help="Output folder")
    p.add_argument("-r", "--recursive", action="store_true", help="Recurse into subfolders")
    p.add_argument(
        "--mapping-store",
        help="Path to JSON file storing pseudonym mappings. If given, mappings persist across runs for consistency.",
    )
    p.add_argument("--audit-log", help="Path to write audit log (JSON)")
    p.add_argument(
        "--replacement-config",
        help="YAML/JSON file defining per-entity replacement values. Without it, "
        "every entity is replaced with a template token like [PERSON_00001]. "
        "Faker-generated fakes are used only where a type sets 'strategy: faker'.",
    )
    p.add_argument(
        "--confidence",
        type=float,
        default=0.4,
        help="Minimum detection confidence (0-1). Lower = more recall, more false positives.",
    )
    p.add_argument(
        "--spacy-model",
        default="en_core_web_md",
        help="spaCy model. Use en_core_web_sm for a lighter ~13MB install.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect only — write the audit log but not the de-identified output files.",
    )
    p.add_argument("--seed", type=int, default=42, help="Faker seed (reproducible pseudonyms)")
    p.add_argument(
        "--list-formats",
        action="store_true",
        help="Print supported file extensions and exit",
    )
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.list_formats:
        print("Supported extensions:", " ".join(supported_extensions()))
        return 0

    if not args.input or not args.output:
        print("ERROR: 'input' and '-o/--output' are required (unless --list-formats).", file=sys.stderr)
        return 2

    input_path = Path(args.input)
    output_root = Path(args.output)
    if not input_path.exists():
        print(f"ERROR: input not found: {input_path}", file=sys.stderr)
        return 2

    mapping_path = Path(args.mapping_store) if args.mapping_store else None
    replacement_config = (
        ReplacementConfig.load(Path(args.replacement_config))
        if args.replacement_config
        else None
    )
    store = PseudonymStore(
        mapping_file=mapping_path, seed=args.seed, config=replacement_config
    )

    print(
        f"Loading spaCy model '{args.spacy_model}' (this may take a moment)...",
        file=sys.stderr,
    )
    engine = DeidentificationEngine(
        pseudonym_store=store,
        confidence=args.confidence,
        spacy_model=args.spacy_model,
    )

    audit = AuditLog(args.audit_log)

    files = list(iter_files(input_path, args.recursive))
    print(f"Found {len(files)} file(s) to process", file=sys.stderr)

    for file in files:
        handler = get_handler(file)
        if handler is None:
            print(f"  SKIP  {file} (no handler for {file.suffix})", file=sys.stderr)
            audit.record(
                file, None, {"format": "unsupported", "findings": []}, error="unsupported format"
            )
            continue

        # Preserve folder structure under output_root
        if input_path.is_dir():
            rel = file.relative_to(input_path)
        else:
            rel = Path(file.name)
        out_path = output_root / rel

        try:
            if args.dry_run:
                # Run the full pipeline but write to a temp dir we discard
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_out = Path(tmp) / rel.name
                    result = handler.process(file, tmp_out, engine)
                audit.record(file, None, result)
                print(
                    f"  CHECK {file}  ({len(result['findings'])} entities)",
                    file=sys.stderr,
                )
            else:
                result = handler.process(file, out_path, engine)
                audit.record(file, out_path, result)
                print(
                    f"  OK    {file} -> {out_path}  ({len(result['findings'])} entities)",
                    file=sys.stderr,
                )
        except Exception as e:  # noqa: BLE001  — we want to keep going on per-file errors
            audit.record(file, out_path, None, error=e)
            print(f"  ERR   {file}: {e}", file=sys.stderr)

    # Persist updated pseudonym mappings and audit
    store.save()
    audit.save()

    summary = audit.summary()
    print("\nSummary:", file=sys.stderr)
    print(f"  Files processed: {summary['files_processed']}", file=sys.stderr)
    print(f"  Errors:          {summary['errors']}", file=sys.stderr)
    if summary["entity_totals"]:
        print("  Entities detected:", file=sys.stderr)
        for et, c in sorted(summary["entity_totals"].items(), key=lambda x: -x[1]):
            print(f"    {et:30s} {c}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
