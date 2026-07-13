# deid_pipeline

De-identifies PHI in medical charts (PDF + C-CDA/XML) and member CSVs by replacing it with
**realistic synthetic surrogates** (fake names, shifted DOBs, format-preserved IDs)
instead of black-box redaction — while **keeping providers, Dates of Service, and all
clinical content** (ICD-10/CPT/lab codes, vitals, notes) intact, so the data stays
usable and linkable.

The pipeline is **mapping-driven**: a **mapping CSV** lists every chart to de-identify —
its exact S3 source and its output path — and a **member CSV** supplies each patient's PHI
(joined on `InputMemberID`). Charts are fetched by exact key with **no S3 listing**, so it
scales to projects of any size and works across buckets. A GPU is needed only for PDF (OCR).

---

## What it does

Given a **member CSV** (PHI source) and a **mapping CSV** (the chart manifest), for each
mapping row it:

1. fetches the chart from `file_source` (a full `s3://` URL — data or file bucket),
2. de-identifies it by type — **PDF** via OCR, **C-CDA/XML** via structured parsing,
3. writes the result to `file_dest` (relative to `--output-dir`), **verbatim**,

and de-identifies the **member CSV** itself. All of a member's charts use one seed and one
identity, so a member maps to the **same** fake across every chart and the member CSV (the
de-identified files still join on `memberid`).

**Removed:** member name (first/middle/last), DOB, member id, plus generic identifiers in
free text (SSN, phone, email, address, ZIP, URL, IP, MRN).
**Kept:** provider names + NPIs, Dates of Service, enrollment dates, and clinical content.

### Package layout (`deid/`)

All code lives in the `deid/` package; the CLI runs as a module (`python -m deid.cli.<name>`).

- `deid/core/` — shared building blocks: data models (`Detection`, `MemberPHI`),
  span merge/replace, date parsing/shifting, text utils, report writing.
- `deid/detection/` — regex `patterns` for generic direct identifiers (SSN, phone,
  email, address, ZIP, URL, IP, VIN, labeled ids).
- `deid/surrogate/` — deterministic, content-derived fake generator + the clinical
  keep-word vocabulary.
- `deid/member/` — member scrubbing: known-member `matching` (exact / fuzzy OCR /
  patient-label / DOB-label), the `MemberScrubber` session (one per member; owns the
  armed collision guard, canonical fakes, and the digital/OCR scrub profiles),
  member-CSV `manifest`, and the `chart_map` reader.
- `deid/documents/` — per-format de-identifiers: `csv_file`, `ccda` (HL7 XML),
  `pdf` (OCR→scrub→render); chart dispatch by extension.
- `deid/sources/` — read-only S3 access (`s3`).
- `deid/ocr/` — `lighton` (LightOnOCR via vLLM, GPU) and `render` (markdown→PDF).
- `deid/cli/` — command-line entry points (`argparse`, Django-style `BaseCommand`):
  `project`, `csv_file`, `render`.

---

## Setup on a new machine

### Prerequisites

- **Linux** with an **NVIDIA GPU + drivers** — required only to OCR **PDF** charts
  (`vLLM` + LightOnOCR). CSV and C-CDA de-id are **CPU-only**. Verify the GPU:
  ```bash
  nvidia-smi
  ```
- **Python 3.10** (pinned: `>=3.10,<3.11` — what the GPU boxes are validated on). `uv` installs it for you.
- **AWS access** to the S3 buckets referenced by the member CSV and mapping (see Configuration).

### 1. Install `uv`

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec $SHELL          # reload PATH
uv --version
```

### 2. Get the code

```bash
git clone clone git@ssh.dev.azure.com:v3/ECLATHealthSolutions/ECLAT.AI/ECLAT.AI.Scripts
git checkout refactor/deid-package-structure
cd ECLAT.AI.Scripts/deid_pipeline
```

### 3. Install dependencies

```bash
uv sync          # creates .venv, installs all deps incl. vLLM
```

`uv sync` also fetches Python 3.10 if it isn't present. (On a machine with **no GPU**,
the `vllm` install may still succeed but the PDF/OCR path won't run — CSV and C-CDA
paths work regardless.)

### 4. Configure

```bash
cp .env.example .env      # then edit
```

Only AWS creds + region are needed. Credentials resolve from the standard chain —
env vars, `AWS_PROFILE`, or an instance role — so you can leave the keys blank if
the machine has a role. Buckets are taken from the `s3://` URIs you pass (member CSV,
mapping, and the `file_source` paths inside the mapping); there is no bucket variable.

Confirm which AWS identity you're using before any run:

```bash
uv run python -c "import boto3; print(boto3.client('sts').get_caller_identity()['Arn'])"
```

The running identity needs **`s3:GetObject`** on every bucket the mapping references
(commonly the **data** bucket for charts and the **file** bucket for member/mapping CSVs),
plus `kms:Decrypt` if the objects are KMS-encrypted. Nothing is written back to S3.

### 5. First PDF run downloads the OCR model

The LightOnOCR-2-1B model (~2 GB, `lightonai/LightOnOCR-2-1B`) downloads to the
HuggingFace cache on the first PDF de-id. CSV/C-CDA runs don't touch it.

---

## Quickstart — verify on synthetic data (no S3, no GPU needed)

No chart or member files live in the repo: the test suite **generates** a fully
synthetic project at runtime (`tests/conftest.py`) and runs the whole CPU-only flow
against it, including an end-to-end leak check:

```bash
uv run pytest tests/ -q
```

To eyeball a de-identified output, run the CSV command against any member or
claims CSV of your own:

```bash
uv run python -m deid.cli.csv_file YOUR_MEMBER.csv -o out/deid.csv -r out/report.json
```

---

## Run

```bash
# 1. Go to the pipeline dir + set region
cd ~/ECLAT.AI.Scripts/deid_pipeline
export AWS_REGION=us-east-1

# 2. Confirm which AWS identity you are (must GetObject the referenced buckets)
aws sts get-caller-identity

# 3. De-identify: member CSV supplies PHI, mapping CSV drives the charts
uv run python -m deid.cli.project \
  --members-csv s3://<bucket>/.../Member/<member>.csv \
  --chart-map   s3://<bucket>/.../<mapping>.csv \
  --output-dir ./out --no-report --skip-existing
```

- `--members-csv` may instead be `--members-prefix s3://…/Member/` to load PHI from
  **every** `*.csv` in a folder in one pass.
- `--chart-map` may be an `s3://…` URI (downloaded like the member CSV) or a local file.
- On real PHI, **omit `--show-values`** so any reports stay masked; `--no-report` writes none.

For a long batch, detach it so it survives your SSH session and watch progress:

```bash
nohup uv run python -m deid.cli.project \
  --members-csv s3://<bucket>/.../Member/<member>.csv \
  --chart-map   s3://<bucket>/.../<mapping>.csv \
  --output-dir ./out --no-report --skip-existing > run.log 2>&1 &
tail -f run.log
```

If it crashes, **rerun the exact same command** — `--skip-existing` skips outputs that
already exist and continues.

### Mapping CSV schema

```
str_member_id,file_source,file_dest[,source_file]
```

| Column            | Required | Meaning                                                                        |
| ----------------- | -------- | ------------------------------------------------------------------------------ |
| `str_member_id` | yes      | join key → member CSV`InputMemberID` (the identity to scrub with)           |
| `file_source`   | yes      | full`s3://` URL of the chart to fetch (data **or** file bucket)        |
| `file_dest`     | yes      | output path,**relative** to `--output-dir`, written **verbatim** |
| `source_file`   | no       | logical label only; not used to locate or write charts                         |

Per row: `file_source` is downloaded, de-identified **by its own type** (`.pdf` → OCR,
`.xml` → C-CDA), and written to `<output-dir>/<file_dest>`. Rules:

- **One-to-many** = multiple rows per member (a chart + its attachments, or many charts).
  All of a member's rows are de-identified under **one identity** (consistent fakes).
- A **`.none`** `file_source` is a 0-byte marker → an **empty file** is written at
  `file_dest` (the real charts are the group's other rows).
- **Output format follows the source**, not `file_dest`'s extension.
- A `str_member_id` **not** in the member CSV → that chart row is **skipped + logged**
  (no PHI to scrub with). `.none` markers are still written.
- `file_dest` should be **unique** across the mapping (it's the output path; duplicates
  warn and the later row wins).

Example (`ps1999` with several charts; one member row in the member CSV):

```
str_member_id,file_source,file_dest
ps1999,s3://…-data-bucket/media/private/clients/2/projects/9/charts/72CWLL/pdf/LMRqFlp.pdf,Chart/2026/ps1999_4507.pdf
ps1999,s3://…-data-bucket/media/private/clients/2/projects/9/charts/72CWLL/xml/uZurX0r.xml,Chart/2026/ps1999_4507/ps1999_4507.xml
ps1999,s3://…-file-bucket/processed/…/Chart/ccda_0209.none,Chart/2026/ps1999_4739.none
```

### Output

Local only. Source PHI files are downloaded to a temp dir and **deleted on exit** — only
de-identified outputs persist. Charts land exactly at `<output-dir>/<file_dest>`; the
member CSV lands at `out/Member/<name>_deid.csv`:

```
out/<file_dest>                       de-identified charts (verbatim paths from the mapping)
out/Member/<member>_deid.csv          de-identified member CSV
out/reports/<file_dest>.json          per-file detection reports (only if reports enabled)
```

### `deid.cli.project` options

| Flag                       | Default      | Purpose                                                         |
| -------------------------- | ------------ | --------------------------------------------------------------- |
| `--members-csv`          | one of these | member CSV (PHI source):`s3://…/Member/<file>.csv`           |
| `--members-prefix`       | one of these | a`Member/` folder: load PHI from every `*.csv` under it     |
| `--chart-map`            | required     | mapping CSV (`s3://…` or local): the chart manifest          |
| `--output-dir`           | `output`   | local folder for de-identified outputs                          |
| `--skip-existing`        | off          | resume: skip outputs that already exist                         |
| `--no-report`            | off          | skip per-file JSON reports (use in prod)                        |
| `--member-ids`           | all rows     | comma-separated`InputMemberID` subset                         |
| `--no-regex`             | off          | chart text: match only known member PHI (skip generic-id regex) |
| `--report / --no-report` | report on    | write per-file JSON reports                                     |
| `--show-values`          | masked       | include real PHI in reports —**do not use on prod PHI**  |
| `--seed N`               | 1234         | surrogate seed (keep constant across runs/files)                |

### De-identify a single CSV

```bash
uv run python -m deid.cli.csv_file INPUT.csv -o OUT.csv [--type member|claims] [-r report.json] [--show-values]
```

Type is auto-detected from the header; `--type` overrides it.

---

## Notes & caveats

- **GPU only for PDFs.** CSV and C-CDA de-id are CPU-only; only PDF charts invoke vLLM.
- **Deterministic** given the same input + `--seed` (OCR runs greedy at `temperature=0`).
- Expect noisy vLLM logs and an `Engine core died` line at shutdown — both harmless.
- **`--show-values` writes real PHI** into the reports. Only use it on synthetic/dev data
  or a secure box; production reports should stay masked (the default), or use `--no-report`.
- **Output format follows the source file**, so a mapping row's `file_dest` extension must
  match its `file_source` type (a PDF can't be written as `.xml`).
- **PDF exposure (known gap):** the OCR path removes the patient via CSV values + label
  passes (`Name:`/`DOB:`) + fuzzy matching, but a patient name/DOB that is *both* absent
  from the CSV *and* unlabeled can still leak. C-CDA is unaffected (it reads the patient
  identity from the chart's `recordTarget`).

## Tests

```bash
uv run python -m pytest tests/       # unit + fixture end-to-end (CPU-only, no S3/GPU)
```

Covers date/span/text helpers, the surrogate generator's determinism and collision guard,
member matching (exact / fuzzy / labels), free-text scrubbing, CSV referential integrity,
the mapping-CSV reader, the CLI contract, and a C-CDA end-to-end leak check. All test data
is synthetic and generated at runtime by `tests/conftest.py` — no chart or member files are
stored in the repo. (`pytest` is an optional `dev` extra: `uv sync --extra dev` first, and
always invoke it as `uv run python -m pytest`.)
