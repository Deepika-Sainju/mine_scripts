"""PHI de-identification pipeline for medical charts and member/claims CSVs.

Member-file-driven: the member manifest says who the patient is, so their PHI is
matched directly — no NER, no Philter, no model (a deliberate design decision;
see the README's Notes & caveats).

Package layout (dependencies flow top to bottom; no cycles):

    core/       shared data types + span/date/text/report helpers
    detection/  direct-identifier regexes (SSN, phone, email, labeled ids, …)
    surrogate/  deterministic, content-derived fake-value generation
    member/     known-member matching + free-text scrubbing + manifest parsing
    documents/  per-format de-identifiers: CSV, C-CDA XML, PDF
    sources/    read-only S3 file-bucket access
    ocr/        LightOnOCR (vLLM, GPU) + markdown->PDF rendering
    cli/        command-line entry points (argparse, Django-style BaseCommand)

Run entry points as modules, e.g. `python -m deid.cli.project --help`.
"""
