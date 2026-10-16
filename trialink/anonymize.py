"""Deterministic suggestion of personal identifiers to mask before a report is sent to the cloud LLM.

Only a helper – the user must still review the text; free-form names in prose are not detected.
"""
from __future__ import annotations

import re

MASK = "[ANONYMIZOVÁNO]"

# Label lines like "Jméno: Jan Novák" – the label stays, the value is masked.
_LABELLED = re.compile(
    r"(?im)^(?P<label>[ \t]*(?:jméno|příjmení|jméno a příjmení|pacient(?:ka)?|adresa|bydliště|trvalé bydliště|"
    r"zákonný zástupce|č(?:íslo|\.)\s*pojištěnce|pojišťovna|telefon|tel\.|e-?mail)[ \t]*:[ \t]*)(?P<value>\S.*)$"
)
_PATTERNS = {
    "rodné číslo": re.compile(r"\b\d{6}\s?/\s?\d{3,4}\b"),
    "datum narození": re.compile(r"(?i)\b(?:nar\.|narozen[aáý]?|datum narození)\s*:?\s*\d{1,2}\.\s?\d{1,2}\.\s?\d{4}"),
    "e-mail": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "telefon": re.compile(r"(?<!\d)(?:\+420[ \t]?)?\d{3}[ \t]?\d{3}[ \t]?\d{3}(?!\d)"),
}


def find(text: str) -> dict[str, int]:
    """Counts of suspected identifiers by kind (no values are returned)."""
    counts: dict[str, int] = {}
    n = len(_LABELLED.findall(text or ""))
    if n:
        counts["osobní údaj v řádku (jméno, adresa…)"] = n
    for kind, pattern in _PATTERNS.items():
        n = len(pattern.findall(text or ""))
        if n:
            counts[kind] = n
    return counts


def mask(text: str) -> str:
    text = _LABELLED.sub(lambda m: m.group("label") + MASK, text or "")
    for pattern in _PATTERNS.values():
        text = pattern.sub(MASK, text)
    return text
