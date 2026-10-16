"""Settings actions: the statuses (names, order, follow-up days, visibility)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.actions.candidates import UserError
from app.storage import models as m
from app.storage.db import get_default_follow_up_days

PROTECTED_ENABLED = {"new"}  # new candidates always start here


def ordered_statuses(session: Session) -> list[m.Status]:
    return list(session.scalars(select(m.Status).order_by(m.Status.sort_order, m.Status.id)))


def _normalize_order(session: Session) -> list[m.Status]:
    rows = ordered_statuses(session)
    for i, s in enumerate(rows, start=1):
        s.sort_order = i
    session.flush()
    return rows


def _days(value: str) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    if not value.isdigit() or not (1 <= int(value) <= 365):
        raise UserError("מספר ימי המעקב צריך להיות בין 1 ל-365 (או ריק, ללא מעקב אוטומטי)")
    return int(value)


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise UserError("חובה למלא שם לסטטוס")
    if len(name) > 120:
        raise UserError("שם הסטטוס ארוך מדי")
    return name


def update_status(session: Session, status_id: int, *, name: str, kind: str, follow_up_days: str,
                  requires_date: bool, enabled: bool) -> None:
    status = session.get(m.Status, status_id)
    if status is None:
        raise UserError("הסטטוס לא נמצא")
    if kind not in ("active", "final"):
        raise UserError("סוג סטטוס לא חוקי")
    name = _clean_name(name)
    taken = session.scalars(select(m.Status).where(m.Status.name == name, m.Status.id != status_id)).first()
    if taken:
        raise UserError("כבר יש סטטוס בשם הזה")
    if status.key in PROTECTED_ENABLED and not enabled:
        raise UserError("הסטטוס הראשוני לא ניתן להסתרה: מועמדים חדשים מתחילים בו")
    status.name, status.kind = name, kind
    status.follow_up_days = _days(follow_up_days)
    status.requires_date = requires_date
    status.enabled = enabled


def add_status(session: Session, *, name: str, kind: str = "active") -> m.Status:
    if kind not in ("active", "final"):
        raise UserError("סוג סטטוס לא חוקי")
    name = _clean_name(name)
    if session.scalars(select(m.Status).where(m.Status.name == name)).first():
        raise UserError("כבר יש סטטוס בשם הזה")
    top = session.scalar(select(func.max(m.Status.sort_order))) or 0
    days = get_default_follow_up_days(session) if kind == "active" else None
    status = m.Status(key=None, name=name, sort_order=top + 1, kind=kind, follow_up_days=days, requires_date=False)
    session.add(status)
    session.flush()
    return status


def move_status(session: Session, status_id: int, direction: str) -> None:
    rows = _normalize_order(session)
    idx = next((i for i, s in enumerate(rows) if s.id == status_id), None)
    if idx is None:
        raise UserError("הסטטוס לא נמצא")
    other = idx - 1 if direction == "up" else idx + 1
    if 0 <= other < len(rows):
        rows[idx].sort_order, rows[other].sort_order = rows[other].sort_order, rows[idx].sort_order
        session.flush()


def set_default_days(session: Session, value: str) -> None:
    days = _days(value)
    if days is None:
        raise UserError("חובה למלא מספר ימים")
    setting = session.get(m.Setting, "follow_up_days")
    if setting is None:
        session.add(m.Setting(key="follow_up_days", value=str(days)))
    else:
        setting.value = str(days)


# ------------------------------------------------------------------ message templates
from app.domain.dates import now_il  # noqa: E402
from app.messaging.fill import LINK_STYLES, unknown_variables  # noqa: E402


def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.get(m.Setting, key)
    return row.value if row else default


def put_setting(session: Session, key: str, value: str) -> None:
    row = session.get(m.Setting, key)
    if row is None:
        session.add(m.Setting(key=key, value=value))
    else:
        row.value = value


def set_messaging(session: Session, sender_name: str, link_style: str) -> None:
    sender = " ".join((sender_name or "").split())
    if len(sender) > 80:
        raise UserError("השם ארוך מדי")
    if link_style not in LINK_STYLES:
        raise UserError("סוג קישור לא חוקי")
    put_setting(session, "sender_name", sender)
    put_setting(session, "whatsapp_link_style", link_style)


def ordered_templates(session: Session) -> list[m.MessageTemplate]:
    return list(session.scalars(select(m.MessageTemplate).order_by(m.MessageTemplate.sort_order, m.MessageTemplate.id)))


def _fill_template(t: m.MessageTemplate, name: str, channel: str, subject: str, neutral: str, male: str,
                   female: str, enabled: bool) -> None:
    name = " ".join((name or "").split())
    if not name:
        raise UserError("חובה למלא שם לתבנית")
    if len(name) > 120:
        raise UserError("שם התבנית ארוך מדי")
    if channel not in m.TEMPLATE_CHANNELS:
        raise UserError("ערוץ לא חוקי")
    bodies = [(neutral or "").strip(), (male or "").strip(), (female or "").strip()]
    if not any(bodies):
        raise UserError("חובה למלא לפחות נוסח אחד")
    if channel == "email" and not (subject or "").strip():
        raise UserError("בתבנית מייל חובה למלא נושא")
    bad = unknown_variables("\n".join([subject or ""] + bodies))
    if bad:
        raise UserError("משתנה לא מוכר בתבנית: " + ", ".join("{{" + b + "}}" for b in bad)
                        + ". המשתנים הקיימים: {{שם}}, {{שם_משפחה}}, {{שם_פונה}}")
    t.name, t.channel = name, channel
    t.subject = (subject or "").strip() if channel == "email" else ""
    t.body_neutral, t.body_male, t.body_female = bodies
    t.enabled = enabled
    t.updated_at = now_il().replace(tzinfo=None)


def add_template(session: Session, **kw) -> m.MessageTemplate:  # noqa: ANN003
    t = m.MessageTemplate(sort_order=(session.scalar(select(func.max(m.MessageTemplate.sort_order))) or 0) + 1)
    _fill_template(t, **kw)
    session.add(t)
    session.flush()
    return t


def update_template(session: Session, tid: int, **kw) -> None:  # noqa: ANN003
    t = session.get(m.MessageTemplate, tid)
    if t is None:
        raise UserError("התבנית לא נמצאה")
    _fill_template(t, **kw)


def delete_template(session: Session, tid: int) -> None:
    t = session.get(m.MessageTemplate, tid)
    if t is None:
        raise UserError("התבנית לא נמצאה")
    session.delete(t)
