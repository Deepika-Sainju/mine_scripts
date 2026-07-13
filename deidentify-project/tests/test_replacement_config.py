"""Tests for the per-entity replacement config + PseudonymStore routing.

Runs standalone (no pytest required):  python tests/test_replacement_config.py
Each test_* function uses plain asserts, so it also works under pytest.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from deidentify.pseudonym import PseudonymStore
from deidentify.replacement_config import ReplacementConfig


def _cfg(data: dict, suffix: str = ".json") -> ReplacementConfig:
    with tempfile.NamedTemporaryFile(
        "w", suffix=suffix, delete=False, encoding="utf-8"
    ) as f:
        if suffix == ".json":
            json.dump(data, f)
        else:
            import yaml

            yaml.safe_dump(data, f)
        path = f.name
    return ReplacementConfig.load(Path(path))


def _store(config=None) -> PseudonymStore:
    # No mapping file -> isolated in-memory store per test.
    return PseudonymStore(mapping_file=None, seed=42, config=config)


# --------------------------------------------------------------------------
def test_constant_collapses_all_persons():
    cfg = _cfg({"entities": {"PERSON": {"value": "Test Patient"}}})
    s = _store(cfg)
    assert s.pseudonym_for("PERSON", "Jane Smith") == "Test Patient"
    assert s.pseudonym_for("PERSON", "Bob Lee") == "Test Patient"
    assert s.pseudonym_for("PERSON", "Dr. Sarah Chen") == "Test Patient"


def test_unconfigured_type_falls_back_to_template_token():
    cfg = _cfg({"entities": {"PERSON": {"value": "Test Patient"}}})
    s = _store(cfg)
    out = s.pseudonym_for("EMAIL_ADDRESS", "jane@example.com")
    assert out == "[EMAIL_ADDRESS_00001]", out


def test_no_config_is_all_template_tokens_no_faker():
    s = _store(None)
    assert s.pseudonym_for("PERSON", "Jane Smith") == "[PERSON_00001]"
    assert s.pseudonym_for("PERSON", "Bob Lee") == "[PERSON_00002]"
    assert s.pseudonym_for("EMAIL_ADDRESS", "a@b.com") == "[EMAIL_ADDRESS_00001]"


def test_overrides_win_and_are_normalized():
    cfg = _cfg({
        "entities": {
            "PERSON": {
                "value": "Test Patient",
                "overrides": {"jane smith": "Patient 001", "dr. sarah chen": "Doctor 001"},
            }
        }
    })
    s = _store(cfg)
    # Different case + extra whitespace must still match the override.
    assert s.pseudonym_for("PERSON", "  JANE   Smith ") == "Patient 001"
    assert s.pseudonym_for("PERSON", "Dr. Sarah Chen") == "Doctor 001"
    # Anyone else collapses to the constant.
    assert s.pseudonym_for("PERSON", "Bob Lee") == "Test Patient"


def test_pool_cycle():
    cfg = _cfg({"entities": {"LOCATION": {"pool": ["Springfield", "Shelbyville"]}}})
    s = _store(cfg)
    assert s.pseudonym_for("LOCATION", "Boston") == "Springfield"
    assert s.pseudonym_for("LOCATION", "Austin") == "Shelbyville"
    # 3rd distinct value wraps around (cycle is the default).
    assert s.pseudonym_for("LOCATION", "Denver") == "Springfield"
    # Same real value stays stable (cached).
    assert s.pseudonym_for("LOCATION", "Boston") == "Springfield"


def test_pool_hash_keeps_unique():
    cfg = _cfg({"entities": {"LOCATION": {"pool": ["City"], "on_exhaust": "hash"}}})
    s = _store(cfg)
    first = s.pseudonym_for("LOCATION", "Boston")
    second = s.pseudonym_for("LOCATION", "Austin")
    assert first == "City"
    assert second.startswith("City-") and second != "City", second


def test_pool_error_raises_on_exhaust():
    cfg = _cfg({"entities": {"LOCATION": {"pool": ["City"], "on_exhaust": "error"}}})
    s = _store(cfg)
    assert s.pseudonym_for("LOCATION", "Boston") == "City"
    try:
        s.pseudonym_for("LOCATION", "Austin")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on pool exhaustion")


def test_template_strategy_formats_counter():
    cfg = _cfg({"entities": {"MEDICAL_RECORD_NUMBER": {"template": "MRN-{n:05d}"}}})
    s = _store(cfg)
    assert s.pseudonym_for("MEDICAL_RECORD_NUMBER", "abc") == "MRN-00001"
    assert s.pseudonym_for("MEDICAL_RECORD_NUMBER", "def") == "MRN-00002"


def test_faker_opt_in_produces_non_token():
    cfg = _cfg({"entities": {"PERSON": {"strategy": "faker"}}})
    s = _store(cfg)
    out = s.pseudonym_for("PERSON", "Jane Smith")
    assert out and not out.startswith("["), out  # a real fake name, not a token


def test_strategy_inferred_from_key():
    assert _cfg({"entities": {"PERSON": {"value": "X"}}}).rule_for("PERSON").strategy == "constant"
    assert _cfg({"entities": {"LOCATION": {"pool": ["a"]}}}).rule_for("LOCATION").strategy == "pool"
    assert _cfg({"entities": {"X": {"template": "t{n}"}}}).rule_for("X").strategy == "template"


def test_invalid_configs_raise_at_load():
    # pool strategy without a pool list
    for bad in (
        {"entities": {"LOCATION": {"strategy": "pool"}}},
        {"entities": {"PERSON": {"strategy": "constant"}}},      # constant w/o value
        {"entities": {"X": {"strategy": "template"}}},           # template w/o template
        {"entities": {"X": {"strategy": "bogus", "value": "v"}}},
        {"entities": {"X": {}}},                                 # cannot infer
        {"entities": {"X": {"pool": ["a"], "on_exhaust": "boom"}}},
    ):
        try:
            _cfg(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for config: {bad}")


def test_yaml_loads_too():
    cfg = _cfg({"entities": {"PERSON": {"value": "Test Patient"}}}, suffix=".yaml")
    assert _store(cfg).pseudonym_for("PERSON", "Jane Smith") == "Test Patient"


def test_mapping_store_caches_across_config_change(tmp_path_factory=None):
    # Cached real values keep their original pseudonym even if the config changes.
    with tempfile.TemporaryDirectory() as d:
        mapping = Path(d) / "mappings.json"
        cfg1 = _cfg({"entities": {"PERSON": {"value": "First"}}})
        s1 = PseudonymStore(mapping_file=mapping, seed=42, config=cfg1)
        assert s1.pseudonym_for("PERSON", "Jane Smith") == "First"
        s1.save()

        cfg2 = _cfg({"entities": {"PERSON": {"value": "Second"}}})
        s2 = PseudonymStore(mapping_file=mapping, seed=42, config=cfg2)
        # Already-seen value keeps the old pseudonym; only new values use new config.
        assert s2.pseudonym_for("PERSON", "Jane Smith") == "First"
        assert s2.pseudonym_for("PERSON", "Bob Lee") == "Second"


# --------------------------------------------------------------------------
def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")


if __name__ == "__main__":
    main()
