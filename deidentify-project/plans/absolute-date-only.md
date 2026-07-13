# Plan: Only redact absolute dates (drop relative-time phrases)

## Problem

Currently the pseudonym store ends up with entries like:

```json
"DATE_TIME::about 2 days ago": "1974-11-27",
"DATE_TIME::yesterday": "1992-05-04",
"DATE_TIME::last week": "2003-09-17"
```

Relative-time phrases ("about 2 days ago", "yesterday", "next month",
"recently") are getting redacted and replaced with real-looking dates.
This is undesirable because:

1. They aren't PII by themselves — they only become identifying when
   combined with other context, which we already redact.
2. Replacing them with a concrete date *adds* false specificity that
   wasn't in the source.
3. It pollutes the mapping store and bloats the audit log.

We only want absolute dates redacted — e.g. DOB-style values like
`1974-11-27`, `Jan 5, 2020`, `5 January 2020`, `01/05/2020`.

## Root cause

Presidio's built-in `DATE_TIME` recognizer is intentionally permissive:
it uses spaCy's `DATE` NER label, which catches anything spaCy
considers a temporal expression — including relative phrases.

We have two levers:

- **A.** Drop the built-in `DATE_TIME` and add a custom recognizer that
  matches only absolute date *patterns* (regex-based).
- **B.** Keep `DATE_TIME` but post-filter Presidio results to discard
  matches whose surface text looks relative.

**Recommendation: A.** Simpler, no double work, and the regex is
explicit about what counts as a date.

## Approach (Option A)

### Step 1 — remove built-in `DATE_TIME` from the default entity list

File: `deidentify/engine.py`, line 22.

Change:

```python
DEFAULT_ENTITIES = [
    # Generic / GDPR
    "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "LOCATION",
    "DATE_TIME", "IP_ADDRESS", "URL", "NRP",
    ...
]
```

to:

```python
DEFAULT_ENTITIES = [
    # Generic / GDPR
    "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "LOCATION",
    "ABSOLUTE_DATE", "IP_ADDRESS", "URL", "NRP",
    ...
]
```

We use a *new* entity type name (`ABSOLUTE_DATE`) instead of reusing
`DATE_TIME` so any existing mappings in `mappings.json` from prior runs
stay clearly distinguishable from the new behavior.

### Step 2 — add an `AbsoluteDateRecognizer` in `recognizers.py`

File: `deidentify/recognizers.py`. Add inside
`register_custom_recognizers()`:

```python
# Absolute dates only — DOB-style, not relative time phrases
absolute_date = PatternRecognizer(
    supported_entity="ABSOLUTE_DATE",
    name="absolute_date_recognizer",
    patterns=[
        # ISO: 1974-11-27, 2020/01/05
        Pattern("iso_date",
                r"\b(?:19|20)\d{2}[-/](?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])\b",
                0.9),
        # US: 01/05/2020, 1-5-2020
        Pattern("us_date",
                r"\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}\b",
                0.85),
        # Long form: Jan 5, 2020   January 5, 2020
        Pattern("long_month_first",
                r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
                r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
                r"\s+\d{1,2},?\s+(?:19|20)\d{2}\b",
                0.9),
        # Long form: 5 January 2020, 5-Jan-2020
        Pattern("long_day_first",
                r"\b\d{1,2}[\s-](?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
                r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
                r"Nov(?:ember)?|Dec(?:ember)?)[\s-](?:19|20)\d{2}\b",
                0.9),
    ],
    context=["dob", "birth", "born", "date", "issued", "expires", "expiry"],
)
registry.add_recognizer(absolute_date)
```

### Step 3 — add a Faker generator for `ABSOLUTE_DATE`

File: `deidentify/pseudonym.py`, in the `generators` dict in
`_generate()` (around line 74). Add:

```python
"ABSOLUTE_DATE": self._fake.date,
```

(`self._fake.date` already produces `YYYY-MM-DD`; that's what
`DATE_TIME` used.)

Optional: also keep a `"DATE_TIME": self._fake.date` entry so old
mappings still work if you re-run with a `--mapping-store` from before
this change.

### Step 4 — clear or migrate the existing `mappings.json`

The old store will contain `DATE_TIME::about 2 days ago` style entries.
Two choices:

- **Easy**: delete `mappings.json` and let it rebuild from scratch.
- **Surgical**: filter the JSON to drop any `DATE_TIME::*` key whose
  value side has known relative tokens
  (`ago`, `yesterday`, `today`, `tomorrow`, `last`, `next`, `recently`,
  `now`, `then`, `earlier`, `later`). Keep `DATE_TIME::YYYY-MM-DD`
  style keys as legacy mappings.

For a one-off cleanup, the easy path is fine.

## Test plan

1. Add a sample file under `samples/` with mixed dates:

   ```text
   Patient DOB: 1974-11-27. The procedure happened about 2 days ago,
   and follow-up is scheduled for next Tuesday. Prior visit: Jan 5, 2020.
   ```

2. Run:

   ```bash
   deidentify samples/mixed_dates.txt -o ./out \
     --audit-log out/audit.json --mapping-store ./mappings.json
   ```

3. Expected findings in `out/audit.json`:
   - `ABSOLUTE_DATE`: 2  (the `1974-11-27` and `Jan 5, 2020`)
   - `DATE_TIME`: 0
   - "about 2 days ago" and "next Tuesday" should appear unchanged in
     `out/mixed_dates.txt`.

4. Update `tests/test_with_stub.py` PATTERNS to add an
   `ABSOLUTE_DATE` regex (or rename the existing `DATE_TIME` entry) so
   the stub-engine smoke test still exercises this path.

## Trade-offs and limitations

- **Misses unusual formats** like `5th of January, 2020` or
  `Q1 2020` — extend the patterns above if those matter for your
  inputs.
- **No semantic date check.** `2020-13-45` matches the ISO regex but
  isn't a real date. Acceptable: redacting nonsense as "a date" is
  harmless.
- **Relative phrases mentioning a real date** (e.g. "around March 2019")
  will partly redact — `March 2019` won't match (no day), but
  `March 5, 2019` will. That's the intended behavior.
- **NER-only dates lost.** Things like "Christmas Day" that spaCy used
  to flag are no longer caught. Add them as patterns if needed.

## Rollback

Revert the three edits and delete `ABSOLUTE_DATE` mappings from
`mappings.json`. Built-in `DATE_TIME` behavior returns.
