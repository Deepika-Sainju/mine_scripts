# Plan: Predefined fake values by default, Faker only when explicitly chosen

## What you want

Today every replacement comes from Faker. You want to **invert the default**:

- **Default** = use your own predefined fake values from a config file.
  Concretely: when a `PERSON` is detected anywhere in the input,
  *every* detected person — whether it's `"Jane Smith"`, `"Bob Lee"`, or
  `"Dr. Sarah Chen"` — is replaced with the **same single predefined
  value** you set in the config (e.g. `"Test Patient"`).
- **Faker** = opt-in. Only used when you explicitly say `strategy: faker`
  for a given entity type.
- Specific real→fake overrides (e.g. `"Jane Smith"` → `"Patient 001"`) are
  also supported and always win for those specific names.

So the primary use case is the `constant` strategy: one literal value
substituted for every detection of that entity type. `pool`, `template`,
and `faker` remain available for other entity types where you'd rather
have variety, traceable tokens, or generated fakes.

## Design

### Replacement strategies, ranked by intended frequency of use

| Strategy | When it runs | Notes |
|---|---|---|
| `constant` | **Primary use case** — one literal value for every detection of this entity type | e.g. every `PERSON` → `"Test Patient"`. Just specify `value:`. |
| `pool` | When you want a list of values cycled through distinct detections | Picks deterministically from a list you provide. The same real value always maps to the same item in the list. |
| `template` | Built-in fallback when an entity type isn't in the config | Tokens like `[PERSON_00001]`. Safe + auditable. |
| `faker` | **Opt-in only** | Set `strategy: faker` per entity type when you actually want fake-but-realistic generated values. |

Plus an **`overrides`** table per entity type: explicit real→fake pairs that
always win over the chosen strategy.

### Default flow when a value is detected

```
detected: ("PERSON", "Jane Smith")
  ├─ already in cached mapping?       → use cached
  ├─ in entities.PERSON.overrides?    → use that
  ├─ entities.PERSON in config?
  │     └─ apply that entity's strategy
  │         (constant returns the literal value every time;
  │          pool/template/faker use the configured method)
  └─ entity not in config?            → template fallback: [PERSON_00001]
```

Faker is never reached unless `strategy: faker` is explicitly set.

### Config file format

A new flag `--replacement-config path/to/config.yaml` (or `.json`). If
omitted, **everything falls back to template tokens** — no Faker output at
all, because Faker is opt-in.

```yaml
# replacement-config.yaml

entities:
  PERSON:
    # No 'strategy:' key → defaults to 'constant' since 'value' is provided.
    # Every detected PERSON in the input becomes "Test Patient".
    value: "Test Patient"
    overrides:
      "jane smith":      "Patient 001"     # exact (normalized) match
      "dr. sarah chen":  "Doctor 001"

  PHONE_NUMBER:
    value: "(555) 555-5555"

  LOCATION:
    # 'pool' = a list, used when you want variety across distinct detections
    pool: ["Springfield", "Shelbyville", "Capital City"]
    on_exhaust: cycle                      # cycle | hash | error

  EMAIL_ADDRESS:
    strategy: faker                        # opt-in — use Faker for emails

  MEDICAL_RECORD_NUMBER:
    strategy: template
    template: "MRN-{n:05d}"
```

Notes on the format:

- **`strategy:` is optional.** It is inferred from which key you provide:
  `value:` → `constant`, `pool:` → `pool`, `template:` → `template`.
  Explicit `strategy: faker` is the only way to get Faker.
- **No top-level `defaults`** in this design. The implicit default is
  "pool from your config, template token if not configured." This makes
  Faker harder to invoke by accident.
- **`overrides`** keys are whitespace-collapsed + lowercased before lookup
  (matching `PseudonymStore._key`'s normalization), so you can write them
  naturally.
- **`on_exhaust`** for `pool`: what to do when there are more unique real
  values than pool entries.
  - `cycle` (default) — wrap around. Different real values can map to the
    same fake. Fine for redacting for publication, **bad** if you need to
    join the redacted output later.
  - `hash` — append a short hash suffix so all replacements stay unique:
    `"Test Patient A-3a7"`.
  - `error` — raise and stop. Safest for compliance.

## Code changes

### 1. New module `deidentify/replacement_config.py`

Loads + validates the config. Dataclasses:

```python
@dataclass
class EntityRule:
    strategy: str                  # "pool" | "template" | "constant" | "faker"
    pool: list[str] | None = None
    template: str | None = None
    value: str | None = None
    overrides: dict[str, str] = field(default_factory=dict)
    on_exhaust: str = "cycle"

@dataclass
class ReplacementConfig:
    rules: dict[str, EntityRule]   # keyed by entity_type, uppercased

    @classmethod
    def load(cls, path: Path) -> "ReplacementConfig": ...

    def rule_for(self, entity_type: str) -> EntityRule | None: ...
```

`load()` should infer `strategy` from which key the user supplied
(`pool` / `template` / `value`) if `strategy:` isn't explicit, and **reject
the config if a strategy's required field is missing** (e.g. `strategy: pool`
without a `pool:` list). YAML or JSON based on file extension. PyYAML is
already in the venv.

### 2. Wire config into `PseudonymStore`

File: `deidentify/pseudonym.py`.

- Add `config: ReplacementConfig | None = None` to `__init__`.
- Add `self._pool_assignments: dict[str, int] = {}` — per-entity counter
  for next pool index.
- `pseudonym_for(entity_type, value)` already passes `value` through. The
  cached-mapping lookup stays first. Then route to `_generate`:

```python
def _generate(self, entity_type: str, original_value: str) -> str:
    et = entity_type.upper()
    key_norm = " ".join(original_value.strip().lower().split())

    rule = self._config.rule_for(et) if self._config else None

    # 1) Explicit override wins
    if rule and key_norm in rule.overrides:
        return rule.overrides[key_norm]

    # 2) Configured strategy
    if rule:
        return self._apply_strategy(et, rule)

    # 3) No config entry for this type → safe template fallback
    n = self._counters[et] = self._counters.get(et, 0) + 1
    return f"[{et}_{n:05d}]"
```

`_apply_strategy()` dispatches:

- `pool` → `pool[next_index]`, increment counter, apply `on_exhaust` policy
  when index >= len(pool)
- `template` → format with `n` = next counter
- `constant` → return `rule.value`
- `faker` → look up `Faker` method by entity type (existing behavior moved
  here from current `_generate`)

The existing in-memory `_faker` instance stays — it's just only used when a
rule explicitly opts in.

### 3. CLI flag

File: `deidentify/cli.py`, `build_arg_parser()`:

```python
p.add_argument(
    "--replacement-config",
    help="YAML/JSON file defining per-entity replacement values. "
         "Defaults: pool from your config; template token like "
         "[PERSON_00001] for types not listed. Faker is used only when "
         "'strategy: faker' is set for a type.",
)
```

And in `main()`:

```python
config = (
    ReplacementConfig.load(Path(args.replacement_config))
    if args.replacement_config else None
)
store = PseudonymStore(
    mapping_file=mapping_path, seed=args.seed, config=config
)
```

### 4. Persistence interaction

`--mapping-store mappings.json` still works. Precedence on each lookup:

1. **Cached mapping** from prior runs → reused as-is. Preserves
   consistency.
2. **Explicit override** in config → wins next.
3. **Configured strategy** for the entity type.
4. **Template fallback** if entity type isn't in the config.

If you change the config after building up `mappings.json`, **already-seen
values keep their old pseudonyms**. To force fresh assignments under the new
config, delete `mappings.json` (or just the affected entries).

Document this in the README — it surprises people.

## Examples

### Example 1 — the use case you described: every PERSON → one shared value

```yaml
entities:
  PERSON:
    value: "Test Patient"
```

Behavior:

- Input: `"Jane Smith met with Bob Lee and Dr. Sarah Chen. Jane signed."`
- Output: `"Test Patient met with Test Patient and Test Patient. Test Patient signed."`
- Every detected `PERSON` — regardless of who they are — becomes
  `"Test Patient"`.
- Any non-PERSON entity (emails, phones, etc.) → template token like
  `[EMAIL_ADDRESS_00001]` (template fallback, since they aren't configured).
- Faker is never invoked.

### Example 2 — shared `PERSON` value but variety for `LOCATION`

```yaml
entities:
  PERSON:
    value: "Test Patient"                   # constant — all become this
  LOCATION:
    pool: ["Springfield", "Shelbyville"]    # pool — distinct cities cycled
  PHONE_NUMBER:
    value: "(555) 555-5555"
  EMAIL_ADDRESS:
    strategy: faker                         # the one place Faker is allowed
```

### Example 3 — pinning specific identities while everyone else collapses

```yaml
entities:
  PERSON:
    value: "Test Patient"           # default for any unknown person
    overrides:
      "ceo name here":       "CEO"
      "head of legal name":  "Chief Legal Officer"
```

Two named people always render as their role labels; every other detected
person becomes `"Test Patient"`.

### Example 4 — no config supplied at all

```bash
deidentify samples/sample.txt -o ./out --audit-log out/audit.json
# (no --replacement-config)
```

Every detected entity is replaced with a template token:
`[PERSON_00001]`, `[EMAIL_ADDRESS_00001]`, etc. Faker is never used.
This is the new "safe by default" behavior.

## Test plan

1. Unit tests in `tests/test_replacement_config.py`:
   - Load each example config above; assert each `pseudonym_for(...)`
     returns the expected replacement.
   - Override matching: whitespace and case insensitive.
   - `on_exhaust` behaviors: `cycle`, `hash`, `error`.
   - Invalid configs (e.g. `strategy: pool` without `pool:`) raise at
     load-time, not at lookup-time.

2. Integration tests:
   - Run CLI against `samples/sample.txt` with Example 1's config. Assert
     output contains `"Test Patient A"`, no Faker-generated names.
   - Run CLI with **no** `--replacement-config`. Assert every replacement
     looks like `[ENTITY_NNNNN]` — no Faker output at all.

3. Mapping-store interaction:
   - Run twice with `--mapping-store` and unchanged config: byte-identical
     output.
   - Run twice with `--mapping-store`; change config between runs. Confirm
     already-seen real values keep their original pseudonyms (config
     change does not retroactively rewrite cached mappings).

4. **Behavior-change regression**:
   - Old behavior (Faker by default, no config) is gone. Document this as a
     **breaking change** in the README. Add a "compatibility mode" config
     to `samples/` for anyone who wants the old behavior:

     ```yaml
     # samples/legacy-faker-config.yaml
     entities:
       PERSON:        { strategy: faker }
       EMAIL_ADDRESS: { strategy: faker }
       PHONE_NUMBER:  { strategy: faker }
       LOCATION:      { strategy: faker }
       DATE_TIME:     { strategy: faker }
       URL:           { strategy: faker }
       # ... etc
     ```

     Run with `--replacement-config samples/legacy-faker-config.yaml` to
     restore old behavior in one flag.

## Trade-offs

- **Breaking change.** Anyone relying on Faker-by-default sees different
  output. The legacy-faker-config sample above is the migration path. Call
  this out prominently in the README.
- **Pool exhaustion with `cycle`.** Two different real people end up as the
  same fake person — fine for publication, catastrophic if you wanted to
  join redacted output. Default to `cycle` for ergonomics but warn loudly
  in docs; `hash` or `error` for compliance-sensitive runs.
- **Curation burden.** Users now need to maintain a config (or accept ugly
  template tokens). The trade-off you're making: predictability and control
  > convenience. That's the right call for compliance contexts.
- **YAML dependency.** Already installed (PyYAML 6.0.3). Loader still
  supports `.json` for users who'd rather skip YAML.
- **Validation.** Reject configs that reference entity types not in
  `DEFAULT_ENTITIES`, pools that are empty lists, or strategies missing
  required fields. Fail at startup, not at first lookup.

## Rollback

Drop the `--replacement-config` flag, delete `replacement_config.py`, revert
the `_generate` change in `pseudonym.py`. Faker-by-default returns.

## Minimal MVP

Smallest useful slice if you want to ship in stages:

1. **`constant` strategy only** (no `pool`/`template`/`faker` switching in
   config — Faker stays as the no-config-supplied fallback for this MVP so
   nothing breaks for existing users).
2. JSON config only (skip YAML for now):
   ```json
   {
     "entities": {
       "PERSON":       { "value": "Test Patient" },
       "PHONE_NUMBER": { "value": "(555) 555-5555" }
     }
   }
   ```
3. `_generate` checks config first; if entity type has a `value`, return
   it directly. Otherwise current Faker behavior.

That alone covers the PERSON case you described — every detected name
collapses to `"Test Patient"`. `pool`, `template`, `overrides`, the
inverted Faker default, and the legacy-faker migration path can land in
follow-up changes.
