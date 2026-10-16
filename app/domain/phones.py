"""Israeli phone numbers: display as given, normalize for WhatsApp."""
from __future__ import annotations

import re


def digits_only(raw: str | None) -> str:
    return re.sub(r"\D", "", raw or "")


def to_international(raw: str | None) -> str | None:
    """"054-6450145" -> "972546450145". None when it does not look like an Israeli number."""
    d = digits_only(raw)
    if d.startswith("00972"):
        d = d[2:]
    if d.startswith("972"):
        national = d[3:]
    elif d.startswith("0"):
        national = d[1:]
    else:
        return None
    if not (8 <= len(national) <= 9) or national.startswith("0"):
        return None
    return "972" + national


def is_mobile(raw: str | None) -> bool:
    intl = to_international(raw)
    return bool(intl) and intl[3:].startswith("5") and len(intl) == 12


def best_whatsapp_number(*numbers: str | None) -> str | None:
    """The first mobile number among the given ones, international, digits only."""
    for n in numbers:
        if is_mobile(n):
            return to_international(n)
    return None
