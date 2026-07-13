# Plan: Add NER content-scanning to `deidentify_csv.py`

Combine the two de-identification methods by keeping the **standalone script as the
backbone** (deterministic, reviewable, HIPAA-correct, one shared crosswalk) and adding
**Presidio NER as one optional column action** for free-text columns the header rules
can't see (e.g. `FilePath`).

## Goal

- Structured columns → existing deterministic rules (pseudonym / mask / date:shift / zip3 / name).
- Free-text columns → new `ner` action scans cell *content* with the project's
  `DeidentificationEngine` and pseudonymizes any PII found inside the string.
- **One** `crosswalk.json` remains the single source of truth, so a member name found
  inside a free-text cell maps to the same token as elsewhere → joins survive.
- NER stays **optional**: if no column uses `ner`, the tool is still stdlib-only.

## Design decision (chosen)

Reuse the project's engine via import; **lazy-import** so Presidio/spaCy load only when a
column actually uses `ner`. The combined tool runs stdlib-only unless NER is invoked.

## Dependency wiring (the cross-project part)

The engine lives in `deidentify-project/deidentify/`; the script runs from
`standalone_project/`. To import it:

- Install the package into whatever env runs the script:
  `pip install -e /mnt/c/Users/.../deidentify-project` (pulls presidio-analyzer,
  presidio-anonymizer, spacy, faker + `python -m spacy download en_core_web_md`).
- Lazy import inside the `ner` code path:
  `from deidentify.engine import DeidentificationEngine`.
- If the import fails and a column is marked `ner`, **fail loud** with an actionable
  message (don't silently fall back to `keep` — that would leak PHI).

## Code changes (all in `deidentify_csv.py`)

1. **New action `ner`** — extend the ACTIONS docstring and `apply_action()`:
   - `elif action == "ner":` run the cell through the engine and return the anonymized
     text. Hits are pseudonymized through the **same crosswalk** (see #3).

2. **Lazy engine builder** — module-level `_get_engine()` that constructs the
   `DeidentificationEngine` once (memoized) on first `ner` use, honoring
   `--spacy-model` / `--confidence` flags.

3. **Bridge engine → Crosswalk** — small adapter class exposing the one method the
   engine calls on its store: `pseudonym_for(entity_type, value)`. Back it with a new
   `ner` namespace section in `Crosswalk` (and include it in `Crosswalk.save()`), so NER
   replacements are consistent and re-identifiable via the same crosswalk file.
   Pass the adapter as `DeidentificationEngine(pseudonym_store=adapter)`.

4. **`init` auto-suggest** — add a DETECT rule so free-text / path-like columns
   (`filepath`, `path`, `notes`, `comment`, `description`, `remarks`, `text`) default to
   `ner`. For your files this flags `FilePath` (currently `keep`). User still reviews
   before running.

5. **CLI flags** — add `--spacy-model` (default `en_core_web_md`) and `--confidence`
   (default `0.4`) to the `run` subparser; thread into `_get_engine()`.

## Validation

- Re-run on `member.csv` / `claims.csv` with `FilePath` set to `ner`; confirm:
  - structured columns transform exactly as before (diff against current `deid_out/`),
  - names/IDs embedded in `FilePath` get pseudonymized,
  - a name appearing both in `MemberFirstName` and inside `FilePath` resolves consistently
    via the crosswalk.
- Confirm tool still runs with **no** `ner` columns and **no** presidio installed.
- Add a small fixture test mirroring `tests/test_with_stub.py` (stub engine so tests
  don't require spaCy) covering the `ner` branch and the crosswalk adapter.

## Out of scope

- No changes to the `deidentify-project` package itself (reused as-is).
- No merge of the two CLIs; the project CLI keeps working independently.

## Open question for you

Confirm the env: should I target the **existing venv that runs the script**
(`airflowvenv`/`testvenv`) for the `pip install -e`, or a fresh venv? This determines the
install/test commands.
