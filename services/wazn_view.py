"""
Display helper for the PATTERN (wazn) card.

Basic shapes are just the root letters with vowels (no added letters and no
special meaning). They are correct but tell a reader almost nothing, so the
card shows them in a smaller, quieter form with no "other words on this
pattern" list. tools/enrich_wazn.py stores this flag in meanings.db
(wazn_entries.is_basic) using the same rule.
"""

BASIC_LABEL_PREFIXES = (
    "Basic three-letter noun",
    "Three-letter noun with a long vowel",
    "Four-letter-root noun",
)


def is_basic(label):
    return bool(label) and label.startswith(BASIC_LABEL_PREFIXES)