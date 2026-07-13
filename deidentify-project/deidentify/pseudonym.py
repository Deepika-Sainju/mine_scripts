"""Consistent pseudonym mapping.

Same (entity_type, value) always maps to the same replacement within a run.
Optionally persist mappings to JSON so consistency carries across runs.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Optional

from faker import Faker

from .replacement_config import ReplacementConfig


class PseudonymStore:
    def __init__(
        self,
        mapping_file: Optional[Path] = None,
        seed: int = 42,
        locale: str = "en_US",
        config: Optional[ReplacementConfig] = None,
    ):
        self._fake = Faker(locale)
        Faker.seed(seed)
        self._lock = threading.Lock()
        self._mapping_file = Path(mapping_file) if mapping_file else None
        self._config = config
        self._counters: dict[str, int] = {}
        self._pool_assignments: dict[str, int] = {}  # entity_type -> next pool index
        self._mappings: dict[str, str] = {}
        if self._mapping_file and self._mapping_file.exists():
            self._load()

    # ---------- persistence ----------
    def _load(self) -> None:
        with self._mapping_file.open() as f:
            data = json.load(f)
        self._mappings = data.get("mappings", {})
        self._counters = data.get("counters", {})

    def save(self) -> None:
        if not self._mapping_file:
            return
        self._mapping_file.parent.mkdir(parents=True, exist_ok=True)
        with self._mapping_file.open("w") as f:
            json.dump(
                {"mappings": self._mappings, "counters": self._counters},
                f,
                indent=2,
                sort_keys=True,
            )

    # ---------- lookup ----------
    @staticmethod
    def _key(entity_type: str, value: str) -> str:
        # Case-insensitive, whitespace-normalized key so "John Doe" and "john  doe" collapse
        normalized = " ".join(value.strip().lower().split())
        return f"{entity_type}::{normalized}"

    def pseudonym_for(self, entity_type: str, value: str) -> str:
        key = self._key(entity_type, value)
        with self._lock:
            cached = self._mappings.get(key)
            if cached is not None:
                return cached
            replacement = self._generate(entity_type, value)
            self._mappings[key] = replacement
            return replacement

    # ---------- generation ----------
    def _generate(self, entity_type: str, original_value: str) -> str:
        """Resolve a replacement, honoring the replacement config if present.

        Precedence: explicit override -> configured strategy -> safe template
        token. Faker is reached only when a rule explicitly sets strategy=faker.
        """
        et = entity_type.upper()
        rule = self._config.rule_for(et) if self._config else None

        if rule is not None:
            key_norm = " ".join(str(original_value).strip().lower().split())
            if key_norm in rule.overrides:
                return rule.overrides[key_norm]
            return self._apply_strategy(et, rule, original_value)

        # No config (or no rule for this type) -> safe, auditable template token.
        return self._template_token(et)

    def _template_token(self, et: str) -> str:
        self._counters[et] = self._counters.get(et, 0) + 1
        return f"[{et}_{self._counters[et]:05d}]"

    def _apply_strategy(self, et: str, rule, original_value: str) -> str:
        if rule.strategy == "constant":
            return rule.value
        if rule.strategy == "faker":
            return self._faker_value(et)
        if rule.strategy == "template":
            self._counters[et] = self._counters.get(et, 0) + 1
            return rule.template.format(n=self._counters[et], et=et)
        if rule.strategy == "pool":
            idx = self._pool_assignments.get(et, 0)
            self._pool_assignments[et] = idx + 1
            size = len(rule.pool)
            if idx < size:
                return rule.pool[idx]
            # Pool exhausted: more distinct real values than pool entries.
            if rule.on_exhaust == "error":
                raise ValueError(
                    f"replacement pool for {et} exhausted "
                    f"(>{size} distinct values); set on_exhaust: hash to keep "
                    f"uniqueness or enlarge the pool"
                )
            base = rule.pool[idx % size]
            if rule.on_exhaust == "hash":
                suffix = hashlib.sha256(str(original_value).encode()).hexdigest()[:4]
                return f"{base}-{suffix}"
            return base  # cycle (default)
        raise ValueError(f"unknown strategy: {rule.strategy!r}")

    def _faker_value(self, et: str) -> str:
        self._counters[et] = self._counters.get(et, 0) + 1
        n = self._counters[et]

        generators = {
            "PERSON": self._fake.name,
            "EMAIL_ADDRESS": self._fake.email,
            "PHONE_NUMBER": self._fake.phone_number,
            "US_SSN": self._fake.ssn,
            "CREDIT_CARD": self._fake.credit_card_number,
            "IBAN_CODE": self._fake.iban,
            "IP_ADDRESS": self._fake.ipv4,
            "LOCATION": self._fake.city,
            "DATE_TIME": self._fake.date,
            "URL": self._fake.url,
            "US_BANK_NUMBER": lambda: self._fake.bban(),
            "NRP": self._fake.country,
        }
        gen = generators.get(et)
        if gen:
            return gen()

        # No Faker generator for this type -> auditable token.
        return f"[{et}_{n:05d}]"
