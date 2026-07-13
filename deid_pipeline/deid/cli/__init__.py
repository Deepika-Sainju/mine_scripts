"""Command-line entry points (argparse, Django-style BaseCommand).

Each command is a `Command(BaseCommand)` in its own module — flags declared in
`add_arguments`, work done in `handle`. Run as modules, e.g.
`python -m deid.cli.project --help`.
"""
