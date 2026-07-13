"""De-identify a single member or claims CSV (CPU-only)."""
from __future__ import annotations

import argparse

from deid.cli.base import BaseCommand, existing_file
from deid.documents.csv_file import deidentify_csv


class Command(BaseCommand):
    help = "De-identify a single member or claims CSV (type auto-detected from headers)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("input_csv", type=existing_file)
        parser.add_argument("-o", "--out", dest="out_csv", required=True)
        parser.add_argument("--type", dest="csv_type", choices=["member", "claims"],
                            default=None, help="Override auto-detection.")
        parser.add_argument("--seed", type=int, default=1234, help="(default: 1234)")
        parser.add_argument("-r", "--report", dest="report_path", default=None)
        parser.add_argument("--show-values", action="store_true",
                            help="Include real values in the report (else masked).")

    def handle(self, *, input_csv, out_csv, csv_type, seed, report_path, show_values):
        res = deidentify_csv(input_csv, out_csv, csv_type=csv_type, seed=seed,
                             report_path=report_path, show_values=show_values)
        print(f"[csv] {input_csv} ({res['type']}) -> {out_csv}")
        print(f"[csv] {res['rows']} rows; surrogated columns: {res['surrogated_columns']}; "
              f"{res['changed']} cells changed")
        if report_path:
            print(f"[csv] report -> {report_path}")


def main(argv=None):
    Command().run_from_argv(argv)


if __name__ == "__main__":
    main()
