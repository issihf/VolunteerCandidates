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
