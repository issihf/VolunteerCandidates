"""Dates as Paamonim shows them ("11 באוגוסט , 1973") and today in Israel."""
from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import TIMEZONE

_MONTHS = {
    "ינואר": 1, "פברואר": 2, "מרץ": 3, "מארס": 3, "אפריל": 4, "מאי": 5, "יוני": 6,
    "יולי": 7, "אוגוסט": 8, "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12,
}
_HE_DATE = re.compile(r"^\s*(\d{1,2})\s+(\S+?)\s*,?\s*(\d{4})\s*$")
_DMY = re.compile(r"^\s*(\d{1,2})[-./](\d{1,2})[-./](\d{2,4})\s*$")


def now_il() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None, microsecond=0)


def today_il() -> date:
    return now_il().date()


def parse_paamonim_date(text: str | None) -> date | None:
    """"11 באוגוסט , 1973" -> date(1973, 8, 11); also "06-11-18". None when unreadable."""
    if not text:
        return None
    m = _HE_DATE.match(text)
    if m:
        day, month_word, year = int(m.group(1)), m.group(2), int(m.group(3))
        month = _MONTHS.get(month_word)
        if month is None and month_word.startswith("ב"):
            month = _MONTHS.get(month_word[1:])
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                return None
        return None
    m = _DMY.match(text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def format_date(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""
