"""Django-style command base for the deid CLIs.

Every command is a class with exactly two responsibilities:

    class Command(BaseCommand):
        help = "one-line description shown by --help"
        def add_arguments(self, parser): ...   # declare the flags (argparse)
        def handle(self, **options): ...       # do the work

BaseCommand owns everything else — building the parser, parsing argv,
logging setup, and turning CommandError into a clean message + exit code —
so all commands share one lifecycle and read identically.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys


class CommandError(Exception):
    """Raise from handle() for a user-facing error: printed cleanly (no
    traceback) and the process exits with status 2, matching argparse's own
    usage-error behaviour."""


def existing_file(path: str) -> str:
    """argparse type= validator: the argument must be an existing file."""
    if not os.path.isfile(path):
        raise argparse.ArgumentTypeError(f"file {path!r} does not exist")
    return path


class BaseCommand:
    help = ""

    def create_parser(self, prog: str | None = None) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog=prog, description=self.help)
        self.add_arguments(parser)
        return parser

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Declare the command's flags. Override in subclasses."""

    def handle(self, **options):
        """The command body, called with the parsed flags as kwargs."""
        raise NotImplementedError

    def run_from_argv(self, argv=None):
        """Parse argv (default: sys.argv[1:]) and run the command."""
        options = vars(self.create_parser().parse_args(argv))
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        try:
            return self.handle(**options)
        except CommandError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
