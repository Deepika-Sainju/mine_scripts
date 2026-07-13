"""Per-entity replacement configuration.

Lets the caller control how each detected entity type is replaced instead of
always using Faker. Loaded from YAML or JSON (by file extension).

Strategies:
  constant   one literal value for every detection of this entity type
  pool       cycle through a provided list across distinct values
  template   token like [PERSON_00001] (also the no-config fallback)
  faker      opt-in — fake-but-realistic generated value

`strategy:` is optional: it is inferred from which key you supply
(`value:` -> constant, `pool:` -> pool, `template:` -> template). Faker is
only ever used when `strategy: faker` is explicit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

VALID_STRATEGIES = {"constant", "pool", "template", "faker"}
VALID_ON_EXHAUST = {"cycle", "hash", "error"}


def _normalize(value: str) -> str:
    """Match PseudonymStore._key normalization (case + whitespace insensitive)."""
    return " ".join(str(value).strip().lower().split())


@dataclass
class EntityRule:
    strategy: str                              # constant | pool | template | faker
    pool: Optional[list[str]] = None
    template: Optional[str] = None
    value: Optional[str] = None
    overrides: dict[str, str] = field(default_factory=dict)
    on_exhaust: str = "cycle"                  # cycle | hash | error (pool only)


@dataclass
class ReplacementConfig:
    rules: dict[str, EntityRule]               # keyed by UPPERCASE entity_type

    def rule_for(self, entity_type: str) -> Optional[EntityRule]:
        return self.rules.get(entity_type.upper())

    @classmethod
    def load(cls, path: Path) -> "ReplacementConfig":
        path = Path(path)
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() in (".yaml", ".yml"):
            import yaml  # PyYAML is in requirements

            data = yaml.safe_load(raw) or {}
        else:
            data = json.loads(raw) if raw.strip() else {}

        entities = (data or {}).get("entities", {})
        if not isinstance(entities, dict):
            raise ValueError("replacement config: 'entities' must be a mapping")

        rules: dict[str, EntityRule] = {}
        for et, spec in entities.items():
            spec = spec or {}
            strategy = spec.get("strategy")
            if not strategy:
                if "value" in spec:
                    strategy = "constant"
                elif "pool" in spec:
                    strategy = "pool"
                elif "template" in spec:
                    strategy = "template"
                else:
                    raise ValueError(
                        f"{et}: cannot infer strategy — set 'strategy:' or one of "
                        f"'value' / 'pool' / 'template'"
                    )
            if strategy not in VALID_STRATEGIES:
                raise ValueError(
                    f"{et}: unknown strategy {strategy!r} "
                    f"(expected one of {sorted(VALID_STRATEGIES)})"
                )

            on_exhaust = spec.get("on_exhaust", "cycle")
            if on_exhaust not in VALID_ON_EXHAUST:
                raise ValueError(
                    f"{et}: invalid on_exhaust {on_exhaust!r} "
                    f"(expected one of {sorted(VALID_ON_EXHAUST)})"
                )

            overrides = {
                _normalize(k): v for k, v in (spec.get("overrides") or {}).items()
            }

            rule = EntityRule(
                strategy=strategy,
                pool=spec.get("pool"),
                template=spec.get("template"),
                value=spec.get("value"),
                overrides=overrides,
                on_exhaust=on_exhaust,
            )

            # Fail at load-time if a strategy's required field is missing.
            if strategy == "constant" and rule.value is None:
                raise ValueError(f"{et}: strategy 'constant' requires 'value'")
            if strategy == "pool" and not rule.pool:
                raise ValueError(f"{et}: strategy 'pool' requires a non-empty 'pool' list")
            if strategy == "template" and not rule.template:
                raise ValueError(f"{et}: strategy 'template' requires 'template'")

            rules[str(et).upper()] = rule

        return cls(rules=rules)
