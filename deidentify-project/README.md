# deidentify

Multi-format PII de-identification CLI. Detects HIPAA + GDPR + financial identifiers and replaces them with **consistent pseudonyms** — the same input value always maps to the same fake replacement, within a run and (optionally) across runs.

## Supported formats

| Format       | Extensions               | Backend                   |
|--------------|--------------------------|---------------------------|
| Plain text   | `.txt` `.log` `.md`      | built-in                  |
| CSV / TSV    | `.csv` `.tsv`            | stdlib `csv` + header hints |
| JSON         | `.json`                  | stdlib `json`             |
| XML          | `.xml`                   | `lxml`                    |
| PDF          | `.pdf`                   | `PyMuPDF` (text-layer only) |
| Word         | `.docx`                  | `python-docx`             |

## Install

```bash
pip install -e .
python -m spacy download en_core_web_lg     # ~560 MB, best accuracy
# OR, smaller:
python -m spacy download en_core_web_sm     # ~13 MB, then use --spacy-model en_core_web_sm
```

## Use

```bash
# Single file
deidentify samples/sample.txt -o ./out --audit-log out/audit.json

# Folder, recursive, persistent mapping across runs
deidentify ./inbox -o ./out -r \
  --mapping-store ./mappings.json \
  --audit-log ./out/audit.json

# Dry run: detect everything, write only the audit log
deidentify ./inbox -o /tmp/discarded -r --audit-log audit.json --dry-run

# Lighter spaCy model
deidentify ./inbox -o ./out -r --spacy-model en_core_web_sm
```

## What it detects

Out of the box: `PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `LOCATION`, `DATE_TIME`, `IP_ADDRESS`, `URL`, `NRP`, `US_SSN`, `US_DRIVER_LICENSE`, `US_PASSPORT`, `US_ITIN`, `US_BANK_NUMBER`, `CREDIT_CARD`, `IBAN_CODE`, `CRYPTO`, `UK_NHS`, `UK_NINO`, `MEDICAL_LICENSE`, plus custom recognizers: `MEDICAL_RECORD_NUMBER`, `DEVICE_IDENTIFIER`, `HEALTH_PLAN_NUMBER`, `ACCOUNT_NUMBER`.

To add more (e.g. country-specific tax IDs), see `deidentify/recognizers.py`.

## Pseudonymization

- Keyed by `(entity_type, normalized_value)` so `"John Doe"`, `" john doe "`, and `"John  Doe"` all collapse to the same replacement.
- **Default (no config): every entity → an auditable template token** like `[PERSON_00001]`, `[EMAIL_ADDRESS_00001]`. Predictable and safe by default; **Faker is never used unless you opt in** (see below).
- ID-style entities (MRN, device serials, etc.) → the same deterministic tokens.
- `--mapping-store FILE` persists the table to disk so consistency carries across runs (useful for joining anonymized data later).

### Controlling replacements: `--replacement-config FILE`

Pass a YAML/JSON config to choose, per entity type, what the replacement looks like. `strategy:` is optional — it's inferred from which key you set.

| Strategy | Set this | Behavior |
|----------|----------|----------|
| `constant` | `value:` | One literal value for **every** detection of this type, e.g. every `PERSON` → `"Test Patient"`. |
| `pool` | `pool:` | Cycle through a list across distinct values. `on_exhaust:` = `cycle` (default) / `hash` (unique suffix) / `error`. |
| `template` | `template:` | Tokens like `MRN-{n:05d}`. |
| `faker` | `strategy: faker` | Fake-but-realistic values via [Faker](https://faker.readthedocs.io) — **the only way to get Faker**. |

`overrides:` pins specific real→fake pairs (case/whitespace-insensitive) that always win.

```yaml
entities:
  PERSON:
    value: "Test Patient"               # every detected name collapses to this
    overrides:
      "jane smith": "Patient 001"        # …except these specific people
  LOCATION:
    pool: ["Springfield", "Shelbyville"]
  EMAIL_ADDRESS:
    strategy: faker                      # opt in to Faker for emails only
```

```bash
deidentify ./inbox -o ./out -r --replacement-config my-config.yaml
```

> **Breaking change:** earlier versions used Faker for everything by default. The default is now template tokens. To restore the old behavior in one flag, use the bundled `samples/legacy-faker-config.yaml`. **Note:** cached mappings (`--mapping-store`) always win, so changing the config does **not** retroactively rewrite values already assigned in a prior run — delete the mapping file (or the affected entries) to reassign under a new config.

## Audit log

Each run writes an audit log enumerating every file processed, what was detected, counts per entity type, and any errors. Recommended for compliance review — no detection system catches everything, and the log is what you spot-check.

## Limitations — read these

- **PDF text replacement is approximate.** PyMuPDF redacts the original glyphs and overlays the pseudonym; font and exact sizing may differ. Long replacements may visually clip. Use the audit log to verify.
- **Scanned PDFs / images need OCR**, not included. The PDF handler only processes the embedded text layer.
- **DOCX runs:** in paragraphs where any change is made, sub-paragraph formatting (e.g. one bold word in a sentence) collapses to the paragraph's first-run style. Unmodified paragraphs are untouched.
- **No detector catches 100% of PII.** HIPAA Safe Harbor / Expert Determination compliance requires human review. This system is decision-support, not certification.
- **Free text inside CSV cells works**, but very short cells without context (a lone first name in a 'Notes' column) may be missed by the NER model. Sensitive-named columns get a header-hint fallback.

## Tuning false positives / negatives

- `--confidence 0.5` (default 0.4): raise to reduce false positives (Presidio scores 0–1).
- Per-entity tuning lives in `deidentify/engine.py` (`DEFAULT_ENTITIES`) — remove entries you don't need, or add custom recognizers in `recognizers.py`.

## Project layout

```
deidentify/
  __init__.py
  cli.py              # argparse entry point
  engine.py           # Presidio wiring + custom 'pseudonym' operator
  pseudonym.py        # consistent (entity, value) -> replacement store
  replacement_config.py # per-entity replacement strategies (constant/pool/template/faker)
  recognizers.py      # custom HIPAA recognizers (MRN, device ID, etc.)
  audit.py            # JSON audit log
  handlers/
    base.py           # abstract FormatHandler
    text_handler.py
    csv_handler.py
    json_handler.py
    xml_handler.py
    pdf_handler.py
    docx_handler.py
    __init__.py       # dispatch by extension
samples/              # tiny sample inputs to test against
pyproject.toml
requirements.txt
```
