"""Reading Paamonim pages (saved or live). Pure functions: bytes in, data out.

Nothing here keeps the HTML: pages contain tokens and a link with a password,
so only the fields the system needs ever leave this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import html as lh

from app.config import EXTRA_OPTION_FAMILIES

SHOW_RE = re.compile(r"/contacts/show/(\d+)")
_SAVED_FROM = re.compile(r"saved from url=\(\d+\)\S*?/contacts/show/(\d+)")
_WS = re.compile(r"\s+")


class PageParseError(Exception):
    """The page is not the contact page we expect (the message is safe to log)."""


def _clean(text: str | None) -> str:
    return _WS.sub(" ", (text or "").replace("\xa0", " ")).strip()


def _label(text: str | None) -> str:
    t = _clean(text).replace("״", '"').replace("”", '"').replace("“", '"')
    return t.rstrip(":").strip()


def _parse(data: bytes | str):
    if isinstance(data, str):
        data = data.encode("utf-8")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    parser = lh.HTMLParser(encoding="utf-8")
    return lh.document_fromstring(data, parser=parser)


# ---------------------------------------------------------------- list

@dataclass
class ListRow:
    show_id: int
    name: str
    unit: str = ""
    role: str = ""
    org_role: str = ""
    city: str = ""
    mobile: str = ""
    work_phone: str = ""
    email: str = ""
    row_user_id: int | None = None


_LIST_COLUMNS = {
    "שם איש קשר": "name", "יחידה": "unit", "תפקיד ביחידה": "role", "תפקיד אירגוני": "org_role",
    "ישוב": "city", "טלפון נייד": "mobile", "טלפון עבודה": "work_phone", 'דוא"ל': "email",
}


def parse_contacts_list(data: bytes | str) -> tuple[list[ListRow], int]:
    """Rows of the "אנשי קשר" table that link to a contact page.
    Returns (rows, rows_without_link). Repeats of the same contact are kept
    (the importer decides what to do with them)."""
    doc = _parse(data)
    rows: list[ListRow] = []
    skipped = 0
    for table in doc.xpath('//table[contains(concat(" ", normalize-space(@class), " "), " tbl_chart ")]'):
        headers = [_label(th.text_content()) for th in table.xpath(".//tr[1]/th")]
        index = {_LIST_COLUMNS[h]: i for i, h in enumerate(headers) if h in _LIST_COLUMNS}
        if "name" not in index:
            continue
        for tr in table.xpath(".//tr[td]"):
            tds = tr.xpath("./td")
            if len(tds) <= index["name"]:
                skipped += 1
                continue
            link = None
            for a in tds[index["name"]].xpath(".//a[@href]"):
                m = SHOW_RE.search(a.get("href", ""))
                if m:
                    link = (int(m.group(1)), _clean(a.text_content()))
                    break
            if link is None:
                skipped += 1
                continue
            values = {key: _clean(tds[i].text_content()) if i < len(tds) else "" for key, i in index.items()}
            values["name"] = link[1] or values.get("name", "")
            row_id = None
            m = re.match(r"user_(\d+)$", tr.get("id") or "")
            if m:
                row_id = int(m.group(1))
            rows.append(ListRow(show_id=link[0], row_user_id=row_id, **values))
    return rows, skipped


# ------------------------------------------------------------- profile

_PROFILE_LABELS = {
    "יחידה ראשית": "unit", "תחילת קליטה": "intake_date_raw", "סטטוס": "paamonim_status",
    "תחילת פעילות": "activity_start_raw", "טלפון עבודה ישיר": "phone_work_direct",
    "טלפון עבודה": "phone_work", "טלפון נייד": "phone_mobile", "טלפון בית": "phone_home",
    "כתובת - רחוב ובית": "street", "ישוב": "city", "מיקוד": "zip_code", 'דוא"ל': "email",
    "מקום עבודה": "workplace", "תפקיד בעבודה": "job_title", "תאריך לידה": "birth_date_raw",
    "שליטה בשפות": "languages",
}
_EXTRA_LABEL = "אפשרויות סיוע נוספות"
_OPTION_SPLIT = re.compile(r"[,;\n|]+")


@dataclass
class Profile:
    full_name: str = ""
    show_id: int | None = None
    user_id: int | None = None
    fields: dict[str, str] = field(default_factory=dict)
    has_extra_options_field: bool = False
    extra_options_raw: str = ""
    extra_options: list[str] = field(default_factory=list)

    def get(self, key: str) -> str:
        return self.fields.get(key, "")


def split_options(raw: str) -> list[str]:
    return [o for o in (_clean(p) for p in _OPTION_SPLIT.split(raw)) if o]


def parse_profile(data: bytes | str) -> Profile:
    """A contact page ("איש קשר: ..."). Raises PageParseError when the
    details block is missing."""
    raw_bytes = data.encode("utf-8") if isinstance(data, str) else data
    doc = _parse(raw_bytes)
    records = doc.xpath('//dl[contains(concat(" ", normalize-space(@class), " "), " user_record ")]')
    if not records:
        raise PageParseError("לא נמצא בלוק פרטי הקשר בדף")
    profile = Profile()
    header = doc.xpath('//h2[@id="pageHeader"]')
    if header:
        title = _clean(header[0].text_content())
        profile.full_name = re.sub(r"^איש קשר\s*:\s*", "", title)
    m = _SAVED_FROM.search(raw_bytes[:2000].decode("utf-8", "ignore"))
    if m:
        profile.show_id = int(m.group(1))
    uid = doc.xpath('//input[@id="user_id"]/@value')
    if uid and uid[0].isdigit():
        profile.user_id = int(uid[0])
    for dt in records[0].xpath("./dt"):
        dd = dt.getnext()
        while dd is not None and dd.tag != "dd":
            dd = dd.getnext()
        if dd is None:
            continue
        label = _label(dt.text_content())
        if label == _EXTRA_LABEL:
            profile.has_extra_options_field = True
            lines = [t for t in (_clean(x) for x in dd.itertext()) if t]
            profile.extra_options_raw = "\n".join(lines)
            profile.extra_options = split_options(profile.extra_options_raw)
            continue
        key = _PROFILE_LABELS.get(label)
        if key and key not in profile.fields:
            profile.fields[key] = _clean(dd.text_content())
    return profile


# --------------------------------------------------------- eligibility

CANDIDATE = "candidate"
NOT_RELEVANT = "not_relevant"
REVIEW = "review"


def classify(profile: Profile) -> tuple[str, str]:
    """(state, reason). A candidate lists "ליווי משפחות" among the extra options.
    Anything that merely resembles it (e.g. "ליווי משפחות עם עסק") goes to manual
    review instead of being silently dropped."""
    if not profile.has_extra_options_field:
        return NOT_RELEVANT, "אין שדה אפשרויות סיוע נוספות"
    if EXTRA_OPTION_FAMILIES in profile.extra_options:
        return CANDIDATE, ""
    if EXTRA_OPTION_FAMILIES in profile.extra_options_raw:
        return REVIEW, "אפשרויות סיוע: " + ", ".join(profile.extra_options)
    if not profile.extra_options:
        return NOT_RELEVANT, "שדה אפשרויות הסיוע ריק"
    return NOT_RELEVANT, "אפשרויות סיוע: " + ", ".join(profile.extra_options)
