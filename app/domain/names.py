"""Splitting the single full-name field of a Paamonim contact."""
from __future__ import annotations


def split_name(full_name: str | None) -> tuple[str, str]:
    """(first, last). The last word is the family name, the rest the first name
    ("בת שבע גדי" -> "בת שבע", "גדי"). One word is a first name only. A guess:
    the user can correct it on the candidate page."""
    parts = (full_name or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]
