"""Render a (de-identified) markdown file to PDF."""
from __future__ import annotations

import argparse

from deid.cli.base import BaseCommand, existing_file
from deid.ocr.render import md_to_pdf


class Command(BaseCommand):
    help = "Render a (de-identified) markdown file to a paginated PDF."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("md_path", type=existing_file)
        parser.add_argument("-o", "--out", dest="pdf_path", required=True)

    def handle(self, *, md_path, pdf_path):
        md_to_pdf(md_path, pdf_path)
        print(f"[render] {md_path} -> {pdf_path}")


def main(argv=None):
    Command().run_from_argv(argv)


if __name__ == "__main__":
    main()
