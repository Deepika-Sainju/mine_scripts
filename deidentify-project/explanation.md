# `deidentify` — Detailed Explanation

A **multi-format PII de-identification CLI**. It scans files (text / CSV / JSON / XML / PDF / DOCX), detects personal identifiers (HIPAA + GDPR + financial), and replaces each one with a **consistent pseudonym** — the same input value always maps to the same fake replacement, both within a run and (optionally) across runs.

---

## Table of contents

1. [Architecture overview](#architecture-overview)
2. [Data flow, end to end](#data-flow-end-to-end)
3. [Component reference](#component-reference)
   - [`cli.py` — Command-line entry point](#1-clipy--command-line-entry-point)
   - [`engine.py` — Detection + anonymization engine](#2-enginepy--detection--anonymization-engine)
   - [`pseudonym.py` — Consistent pseudonym store](#3-pseudonympy--consistent-pseudonym-store)
   - [`recognizers.py` — Custom HIPAA recognizers](#4-recognizerspy--custom-hipaa-recognizers)
   - [`audit.py` — Compliance audit log](#5-auditpy--compliance-audit-log)
   - [`handlers/` — Format handlers](#6-handlers--format-handlers)
   - [`tests/test_with_stub.py` — Smoke test](#7-teststest_with_stubpy--smoke-test)
   - [`samples/` — Test inputs](#8-samples--test-inputs)
4. [Key design decisions & limitations](#key-design-decisions--limitations)

---

## Architecture overview

The system has three layers:

1. **Detection + replacement engine** (`engine.py`, `recognizers.py`) — built on Microsoft Presidio + spaCy NER.
2. **Consistency layer** (`pseudonym.py`) — a keyed mapping store so `"John Doe"` → always the same fake name.
3. **Format handlers** (`handlers/`) — one per file type, each knowing how to read/write its format while pushing the actual text through the engine.

The CLI (`cli.py`) wires it all together, and `audit.py` records what happened for compliance review.

### Project layout

```
deidentify/
  __init__.py
  cli.py              # argparse entry point
  engine.py           # Presidio wiring + custom 'pseudonym' operator
  pseudonym.py        # consistent (entity, value) -> fake-value store
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
tests/test_with_stub.py
pyproject.toml
requirements.txt
```

### Supported formats

| Format     | Extensions          | Backend                       |
|------------|---------------------|-------------------------------|
| Plain text | `.txt` `.log` `.md` | built-in                      |
| CSV / TSV  | `.csv` `.tsv`       | stdlib `csv` + header hints   |
| JSON       | `.json`             | stdlib `json`                 |
| XML        | `.xml`              | `lxml`                        |
| PDF        | `.pdf`              | `PyMuPDF` (text-layer only)   |
| Word       | `.docx`             | `python-docx`                 |

---

## Data flow, end to end

```
CLI parses args
   │
   ├─ PseudonymStore  ──(loads mappings.json if present)
   ├─ DeidentificationEngine ──(loads spaCy + Presidio + custom recognizers)
   └─ AuditLog
   │
   for each file:
      get_handler(ext) ──► handler.process(in, out, engine)
                                │
                                ├─ read format-specific structure
                                ├─ for each text chunk: engine.anonymize(text)
                                │       ├─ detect()  → Presidio (regex + spaCy NER)
                                │       └─ anonymize → operator lambda →
                                │                       store.pseudonym_for(type, value)
                                │                          └─ Faker or [TOKEN_00001]
                                ├─ write de-identified output (preserving structure)
                                └─ return {format, findings}
      audit.record(...)
   │
   store.save()   → mappings.json (consistency across runs)
   audit.save()   → audit.json   (compliance record)
   print summary
```

The single interface every handler depends on is the engine's
`anonymize(text) -> (anonymized_text, findings)` contract.

---

## Component reference

### 1. `cli.py` — Command-line entry point

The orchestrator. Registered as the `deidentify` console script in `pyproject.toml`.

**`build_arg_parser()`** defines the flags:

| Flag | Default | Purpose |
|------|---------|---------|
| `input` | — | File or folder to process |
| `-o, --output` | — | Output folder |
| `-r, --recursive` | off | Recurse into subfolders |
| `--mapping-store` | none | JSON file to persist pseudonym mappings across runs |
| `--audit-log` | none | Where to write the JSON audit report |
| `--confidence` | `0.4` | Presidio's minimum detection score; higher = fewer false positives |
| `--spacy-model` | `en_core_web_lg` | The NER model (`en_core_web_sm` for a lighter install) |
| `--dry-run` | off | Detect + log everything, but discard the de-identified output |
| `--seed` | `42` | Faker seed for reproducible pseudonyms |
| `--list-formats` | — | Print supported extensions and exit |

**`iter_files()`** yields files from a single path or walks a directory (`**/*` if recursive, `*` otherwise).

**`main()` flow:**

1. Handle `--list-formats` early-exit; validate that `input`/`output` exist.
2. Build a `PseudonymStore` (loading existing mappings if `--mapping-store` points to a file).
3. Build the `DeidentificationEngine` (this loads spaCy — slow, so it prints a notice to stderr).
4. For each file: look up its handler via `get_handler()`. Unsupported extensions are skipped and recorded as an error in the audit. For `--dry-run`, it still runs the full pipeline but writes into a `TemporaryDirectory` that's discarded — so you get detection counts without producing output.
5. **Per-file errors are caught** (`except Exception`) and logged, so one bad file doesn't abort the batch.
6. Output preserves folder structure: when input is a directory, the relative path under `input` is recreated under `output`.
7. At the end, `store.save()` persists mappings and `audit.save()` writes the log; a summary table prints to stderr.

> **Note:** `main()` always returns `0`, even when individual files errored — the exit code does not reflect failures. Inspect the audit log to find them.

---

### 2. `engine.py` — Detection + anonymization engine

The core. Wraps Presidio's two engines (analyzer + anonymizer).

**`DEFAULT_ENTITIES`** — the ~22 entity types it looks for:

- **Generic / GDPR:** `PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `LOCATION`, `DATE_TIME`, `IP_ADDRESS`, `URL`, `NRP`
- **US / HIPAA IDs:** `US_SSN`, `US_DRIVER_LICENSE`, `US_PASSPORT`, `US_ITIN`, `US_BANK_NUMBER`
- **Financial:** `CREDIT_CARD`, `IBAN_CODE`, `CRYPTO`
- **UK:** `UK_NHS`, `UK_NINO`
- **Medical:** `MEDICAL_LICENSE`
- **Custom (from `recognizers.py`):** `MEDICAL_RECORD_NUMBER`, `DEVICE_IDENTIFIER`, `HEALTH_PLAN_NUMBER`, `ACCOUNT_NUMBER`

**`__init__` wiring:**

- Builds a `RecognizerRegistry`, loads Presidio's predefined recognizers, then adds the custom ones.
- Configures the spaCy NLP engine via `NlpEngineProvider`.
- Creates the `AnalyzerEngine` (regex + NER detection) and `AnonymizerEngine` (replacement).
- Builds an **operator config dict** mapping each entity type to a `"custom"` operator whose lambda calls `self.store.pseudonym_for(entity_type, matched_value)`.

```python
"lambda": (lambda v, et=entity: self.store.pseudonym_for(et, v))
```

> The `et=entity` default argument captures the loop variable **by value**. This avoids the classic Python late-binding closure bug, where every lambda in the loop would otherwise share the *final* value of `entity`.

**Methods:**

- **`detect(text)`** — returns Presidio's `RecognizerResult` list (skips empty/whitespace text), filtered by `score_threshold=confidence`.
- **`anonymize(text, results=None)`** — returns `(anonymized_text, findings)`. If no results are passed, it detects first; if nothing is found it returns the text unchanged. Otherwise it runs the anonymizer with the operator config, so each detected span is swapped for its pseudonym.

---

### 3. `pseudonym.py` — Consistent pseudonym store

The "consistency" guarantee lives here.

**Keying (`_key`):** the key is `f"{entity_type}::{normalized}"`, where
`normalized = " ".join(value.strip().lower().split())`.
This collapses case and whitespace, so `"John Doe"`, `" john doe "`, and `"John  Doe"` all map to the **same** replacement.

**`pseudonym_for(entity_type, value)`:** look up the key; return the cached replacement if present, otherwise generate a new one, cache it, and return it. Guarded by a `threading.Lock` for thread safety.

**`_generate(entity_type)`:**

- Maintains per-entity-type counters.
- For recognized types it uses **Faker** to produce realistic fakes:

| Entity | Faker generator |
|--------|-----------------|
| `PERSON` | `name()` |
| `EMAIL_ADDRESS` | `email()` |
| `PHONE_NUMBER` | `phone_number()` |
| `US_SSN` | `ssn()` |
| `CREDIT_CARD` | `credit_card_number()` |
| `IBAN_CODE` | `iban()` |
| `IP_ADDRESS` | `ipv4()` |
| `LOCATION` | `city()` |
| `DATE_TIME` | `date()` |
| `URL` | `url()` |
| `US_BANK_NUMBER` | `bban()` |
| `NRP` | `country()` |

- For everything else (the ID-style entities like MRN, device serials), it produces a **deterministic token** like `[MEDICAL_RECORD_NUMBER_00001]` — auditable and obviously synthetic.

**Persistence (`_load` / `save`):** if `--mapping-store` is given, the `mappings` and `counters` dicts are read at startup and written back as JSON (sorted keys, indented) at the end. This is what carries consistency across runs — useful if you need to join two anonymized datasets later.

> **Subtlety:** Faker is seeded once at construction (`Faker.seed(seed)`). Across separate runs *without* a mapping store, output is reproducible only because of the seed and consistent processing order. The **mapping store** is the reliable way to guarantee cross-run consistency.

---

### 4. `recognizers.py` — Custom HIPAA recognizers

Adds four `PatternRecognizer`s that Presidio doesn't ship out of the box. Each has a regex pattern + a confidence score + context words that boost the score when nearby.

| Entity | What it catches | Score |
|--------|----------------|-------|
| `MEDICAL_RECORD_NUMBER` | `MRN: 12345678`, `Medical Record #: ABC-123`, `MR# ...` | 0.85 |
| `DEVICE_IDENTIFIER` | `Device ID:`, `Serial No.:`, `SN ...` (HIPAA identifier #14) | 0.8 |
| `HEALTH_PLAN_NUMBER` | `Health Plan #`, `Member ID`, `Beneficiary No.` (HIPAA #9) | 0.85 |
| `ACCOUNT_NUMBER` | `Account Number:`, `Acct. ...` (HIPAA #8) | 0.8 |

These are all **label-anchored** — they require a keyword like "MRN" or "Account" preceding the value, which keeps them from firing on every random number. The `context` lists (e.g. `["mrn", "medical", "record", "patient", "chart"]`) raise confidence when those words appear nearby.

---

### 5. `audit.py` — Compliance audit log

A lightweight recorder.

- **`record(input, output, result, error)`** appends one entry per file: timestamp, input/output paths, `status` (`ok`/`error`), error text, format, per-entity-type `entity_counts`, and a total `finding_count`.
- **`save()`** writes `{"started": ..., "files": [...]}` as JSON.
- **`summary()`** aggregates across all entries: total files, error count, and combined entity totals — this is what the CLI prints at the end.

The audit log is the spot-check artifact: no detector is perfect, so the log is what a human reviews for compliance.

---

### 6. `handlers/` — Format handlers

All inherit from **`base.py`'s `FormatHandler`** (ABC), which declares an `extensions` tuple and a
`process(input_path, output_path, engine) -> dict` method returning `{"format": ..., "findings": [...]}`.

**`__init__.py`** is the dispatcher: it instantiates all six handlers once, and `get_handler(path)` matches by lowercased file extension. `supported_extensions()` lists them all.

#### `text_handler.py` (`.txt`, `.log`, `.md`)
Simplest case. Reads the whole file, calls `engine.anonymize(text)`, writes the result. Records full findings including character `start`/`end` offsets.

#### `csv_handler.py` (`.csv`, `.tsv`)
Two-layer detection:

1. Runs the engine on **each non-empty cell**.
2. **Header hinting:** `SENSITIVE_HEADERS` maps column names (`patient_name`, `ssn`, `mrn`, `email`, etc.) to entity types. `_classify_header()` matches a column name exactly or by substring (so `patient_first_name` matches `name`). If the engine finds **nothing** in a cell but its column is flagged sensitive, it force-pseudonymizes the whole cell via `store.pseudonym_for(et, cell)` and records the finding with `"source": "header_hint"`.

This fallback matters because a lone first name in a "Notes" column has no surrounding context for the NER model to latch onto. Delimiter is `\t` for `.tsv`, `,` otherwise. The header row is passed through unchanged.

#### `json_handler.py` (`.json`)
Recursively walks the parsed structure (`_walk`): descends into dicts and lists, anonymizes any non-empty **string** value, leaves numbers/booleans/null alone. **Keys are not anonymized** — only values. Writes back with `indent=2, ensure_ascii=False`.

#### `xml_handler.py` (`.xml`)
Uses `lxml`. Iterates every element and anonymizes three places: `element.text` (content), `element.tail` (text between this element's close and the next sibling's start), and every **attribute value**. Note `resolve_entities=False` — a sensible XXE-safety choice. Writes with declaration + pretty-print. Tag names and attribute *names* are left intact (e.g. the `patientId="MRN8842917"` attribute *value* gets replaced, but the attribute name stays).

#### `pdf_handler.py` (`.pdf`)
Uses **PyMuPDF (`fitz`)** — imported lazily so the package only errors if you actually process a PDF. Per page: extract text, detect entities, dedupe by `(entity_type, original_value)`, then for each unique span use `page.search_for(original)` to locate its rectangle(s) and schedule a **redaction annotation** that paints white over the original glyphs and inserts the replacement text (font size ≈ 75% of rect height, min 8 pt). `page.apply_redactions()` commits them; saved with `garbage=4, deflate=True` to compact the file.

**Caveats:** replacement isn't pixel-perfect — font/sizing is approximate and long replacements can clip. **Scanned/image-only PDFs need OCR, which isn't included** — only the embedded text layer is processed.

#### `docx_handler.py` (`.docx`)
Uses **`python-docx`** (lazy import). Works at the **paragraph** level: grabs the full paragraph text, anonymizes it, and if it changed, writes the result into `runs[0].text` and **clears all other runs**. This is deliberate — it catches entities that Word splits across "runs" (a single name fragmented because of cursor positions), but the tradeoff is that **sub-paragraph formatting collapses** to the first run's style in any modified paragraph. Unmodified paragraphs are untouched. It processes body paragraphs, table cells, and header/footer paragraphs.

---

### 7. `tests/test_with_stub.py` — Smoke test

A standalone script (not pytest). Because spaCy models can't always be downloaded in a sandbox, it swaps in a **`StubEngine`** — a minimal regex-only detector (email, SSN, credit card, phone, date, MRN, crude two-word name) that mimics the real engine's `detect`/`anonymize` interface.

- Its `anonymize` applies replacements **right-to-left** so character indices stay valid as the string length changes.
- Its `detect` resolves overlaps by preferring the earlier + longer match.

It runs every sample through its handler, prints finding counts, then does two verifications:

- **Reproducibility:** re-running produces byte-identical output.
- **Cross-file consistency:** the same person appearing in `sample.txt`, `sample.json`, etc. should get the same pseudonym via the shared mapping store.

This proves the handlers + `PseudonymStore` work end-to-end without needing the heavy ML dependencies.

---

### 8. `samples/` — Test inputs

Four small synthetic medical-record files (`.txt`, `.csv`, `.json`, `.xml`) containing fake PII — names, DOBs, SSNs, MRNs, phones, emails, addresses, a credit card. They deliberately share entities (e.g. "John Anderson", "Dr. Sarah Chen", `MRN8842917`) across formats so the cross-file consistency behavior can be observed.

---

## Key design decisions & limitations

- **Decision support, not certification** — no detector catches 100% of PII; HIPAA Safe Harbor / Expert Determination requires human review, which the audit log supports.
- **Consistency is the headline feature** — keyed, normalized pseudonyms enable joining anonymized datasets later.
- **Graceful degradation** — per-file errors don't stop the batch; optional dependencies (PyMuPDF, python-docx) only fail when their format is actually used.
- **Known weak spots:**
  - Approximate PDF text replacement (font/sizing, possible clipping).
  - No OCR for scanned/image-only PDFs.
  - DOCX formatting collapse in modified paragraphs.
  - Short, context-free CSV cells (mitigated by header hints).
- **Tuning knobs:**
  - `--confidence` for the precision/recall tradeoff.
  - `DEFAULT_ENTITIES` in `engine.py` to add/remove entity types.
  - `recognizers.py` for new custom patterns.

---

## Quick start

```bash
pip install -e .
python -m spacy download en_core_web_lg     # ~560 MB, best accuracy
# OR smaller:
python -m spacy download en_core_web_sm     # ~13 MB, then use --spacy-model en_core_web_sm

# Single file
deidentify samples/sample.txt -o ./out --audit-log out/audit.json

# Folder, recursive, persistent mapping across runs
deidentify ./inbox -o ./out -r \
  --mapping-store ./mappings.json \
  --audit-log ./out/audit.json

# Dry run: detect everything, write only the audit log
deidentify ./inbox -o /tmp/discarded -r --audit-log audit.json --dry-run
```
