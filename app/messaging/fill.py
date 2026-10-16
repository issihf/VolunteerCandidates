"""Fill message templates. Pure: no Outlook, no database.

Only {{שם}}, {{שם_משפחה}} and {{שם_פונה}} are replaced; the text around them is
never changed. A variable without a value stays in the text, and the message is
refused instead of going out with a hole in it.

    values = candidate_values("דנה", "לוי", "איסי")
    fill_text("שלום {{שם}}", values)     -> "שלום דנה"
    leftovers(text)                      -> ["שם_פונה"] when a value was missing
"""
from __future__ import annotations

import html
import re
from urllib.parse import quote

VARIABLES = ("שם", "שם_משפחה", "שם_פונה")
VARIABLE_HELP = {
    "שם": "שם פרטי של המועמד/ת",
    "שם_משפחה": "שם משפחה",
    "שם_פונה": "שם הפונה מפעמונים (נקבע בהגדרות)",
}
_INVISIBLE = "‎‏‪‫‬‭‮⁦⁧⁨⁩﻿"
_ANY_VAR = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


def candidate_values(first_name: str | None, last_name: str | None, sender: str | None) -> dict[str, str]:
    """An empty value is left out, so the variable stays in the text and the message is refused."""
    values = {"שם": (first_name or "").strip(), "שם_משפחה": (last_name or "").strip(),
              "שם_פונה": (sender or "").strip()}
    return {k: v for k, v in values.items() if v}


def _strip_invisible(text: str) -> str:
    return text.translate({ord(c): None for c in _INVISIBLE})


def fill_text(text: str | None, values: dict[str, str]) -> str:
    """Plain text (subject, WhatsApp). Unknown or empty variables stay as they are."""
    if not text:
        return ""

    def repl(m: re.Match) -> str:
        name = _strip_invisible(m.group(1)).strip()
        return values.get(name, m.group(0))

    return _ANY_VAR.sub(repl, text)


def _html_pattern(name: str) -> re.Pattern:
    gap = r"(?:<[^>]*>|[\s" + _INVISIBLE + r"]|&nbsp;)*"
    parts = [re.escape(ch) for ch in "{{" + name + "}}"]
    return re.compile(gap.join(parts))


_HTML_PATTERNS = {name: _html_pattern(name) for name in VARIABLES}


def fill_html(body: str | None, values: dict[str, str]) -> str:
    """HTML body. The value is escaped; tags that were in the middle of the variable are kept after it."""
    if not body:
        return ""
    for name, pattern in _HTML_PATTERNS.items():
        if name in values:
            value = html.escape(values[name])
            body = pattern.sub(lambda m, v=value: v + "".join(re.findall(r"<[^>]*>", m.group(0))), body)
    return body


def visible_text(body: str) -> str:
    body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", body)
    body = re.sub(r"<[^>]*>", "", body)
    return _strip_invisible(html.unescape(body)).replace("\xa0", " ")


def leftovers(text: str | None, is_html: bool = False) -> list[str]:
    """Variables still in the text (missing value or a typo)."""
    if not text:
        return []
    plain = visible_text(text) if is_html else _strip_invisible(text)
    return list(dict.fromkeys(m.group(1).strip() for m in _ANY_VAR.finditer(plain)))


def placeholders(text: str | None, is_html: bool = False) -> list[str]:
    return leftovers(text, is_html)


def unknown_variables(text: str | None, is_html: bool = False) -> list[str]:
    return [v for v in placeholders(text, is_html) if v not in VARIABLES]


def missing_message(names: list[str]) -> str:
    known = [n for n in names if n in VARIABLES]
    unknown = [n for n in names if n not in VARIABLES]
    parts = []
    if known:
        parts.append("חסר ערך ל" + ", ".join("{{" + n + "}}" for n in known))
    if unknown:
        parts.append("בתבנית יש משתנה לא מוכר: " + ", ".join("{{" + n + "}}" for n in unknown))
    return ". ".join(parts) + ". ההודעה לא הוכנה."


# ------------------------------------------------------------------ WhatsApp

def international(mobile: str) -> str:
    """0501234567 -> 972501234567 (WhatsApp links want no + and no leading 0)."""
    digits = re.sub(r"\D", "", mobile or "")
    if digits.startswith("972"):
        return digits
    return "972" + digits[1:] if digits.startswith("0") else digits


LINK_STYLES = {
    "api": "קישור רשמי (api.whatsapp.com)",
    "app": "ישר לאפליקציה במחשב (whatsapp://)",
    "wa.me": "קישור קצר (wa.me)",
}


def whatsapp_link(mobile: str, text: str, style: str = "api") -> str:
    """Click-to-chat with the message ready. Hebrew and line breaks are kept. The short
    wa.me link loses emoji on the way, so the default is the full api.whatsapp.com address;
    "app" skips the browser."""
    number = international(mobile)
    encoded = quote(text, safe="") if text else ""
    if style == "wa.me":
        return f"https://wa.me/{number}" + (f"?text={encoded}" if text else "")
    base = "whatsapp://send" if style == "app" else "https://api.whatsapp.com/send"
    return f"{base}?phone={number}" + (f"&text={encoded}" if text else "")
