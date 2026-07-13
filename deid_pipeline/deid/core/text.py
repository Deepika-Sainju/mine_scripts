"""Small text utilities: token regexes, casing, boundary guards, edit distance."""
from __future__ import annotations

import re

# an alphabetic token of length >= 2 (name parts, propagatable words)
ALPHA_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")
# a word allowing apostrophes/hyphens (O'Brien, Smith-Jones)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]+")


def apply_case(original: str, base: str) -> str:
    """Re-case `base` to match the casing style of `original`."""
    if original.isupper():
        return base.upper()
    if original[:1].isupper():
        return base.capitalize()
    return base.lower()


def bounded_literal(literal: str) -> str:
    """Regex for a literal with edge guards: numeric ends get digit lookarounds,
    alphanumeric ends get word boundaries — so '1/15/1980' isn't matched inside
    '11/15/1980' and 'Ng' isn't matched inside 'Ngo'."""
    esc = re.escape(literal)
    left = r"(?<!\d)" if literal[:1].isdigit() else (r"\b" if literal[:1].isalnum() else "")
    right = r"(?!\d)" if literal[-1:].isdigit() else (r"\b" if literal[-1:].isalnum() else "")
    return left + esc + right


def bounded_levenshtein(a: str, b: str, maxd: int) -> int:
    """Levenshtein distance, early-exiting once it provably exceeds maxd."""
    la, lb = len(a), len(b)
    if abs(la - lb) > maxd:
        return maxd + 1
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        best = cur[0]
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if cur[j] < best:
                best = cur[j]
        if best > maxd:
            return maxd + 1
        prev = cur
    return prev[lb]
