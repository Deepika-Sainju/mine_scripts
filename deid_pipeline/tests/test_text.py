import re

from deid.core.text import apply_case, bounded_levenshtein, bounded_literal


def test_apply_case():
    assert apply_case("SMITH", "jones") == "JONES"
    assert apply_case("Smith", "jones") == "Jones"
    assert apply_case("smith", "Jones") == "jones"


def test_bounded_literal_digit_edges():
    rx = re.compile(bounded_literal("1/15/1980"))
    assert rx.search("11/15/1980") is None  # not inside a longer date
    assert rx.search("DOB 1/15/1980.") is not None


def test_bounded_literal_word_edges():
    rx = re.compile(bounded_literal("Ng"))
    assert rx.search("Ngo") is None  # not inside a longer name
    assert rx.search("Dr. Ng, MD") is not None


def test_bounded_levenshtein():
    assert bounded_levenshtein("abc", "abc", 1) == 0
    assert bounded_levenshtein("hemandez", "hernandez", 2) == 2  # OCR confusion
    assert bounded_levenshtein("aaaa", "zzzz", 1) == 2  # early exit: maxd + 1
    assert bounded_levenshtein("ab", "abcdef", 1) == 2  # length delta short-circuit
