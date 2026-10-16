"""What a person can do with a candidate: change status, set follow-up, log contact, note, edit."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.dates import now_il, today_il
from app.importer.apply import set_status
from app.storage import models as m


class UserError(Exception):
    """A problem the person can fix; the message is shown to them."""


def _entry(session: Session, cand: m.Candidate, kind: str, *, text: str = "", channel: str | None = None,
           on: date | None = None, status: m.Status | None = None) -> m.JournalEntry:
    entry = m.JournalEntry(candidate=cand, created_at=now_il(), occurred_on=on or today_il(), kind=kind,
                           channel=channel, text=text.strip(), status_id=status.id if status else None)
    session.add(entry)
    return entry


def change_status(session: Session, cand: m.Candidate, status_id: int, *, follow_up: date | None = None,
                  note: str = "") -> m.Status:
    status = session.get(m.Status, status_id)
    if status is None:
        raise UserError("הסטטוס לא נמצא")
    if not status.enabled and cand.status_id != status.id:
        raise UserError("הסטטוס הזה מוסתר")
    if status.requires_date and follow_up is None:
        raise UserError(f"בסטטוס \"{status.name}\" חובה לקבוע תאריך לפנייה חוזרת")
    if follow_up is not None and follow_up < today_il():
        raise UserError("תאריך המעקב לא יכול להיות בעבר")
    previous = cand.status_id
    set_status(session, cand, status)
    if follow_up is not None:
        cand.follow_up_date = follow_up
    if previous != status.id or note.strip():
        _entry(session, cand, m.J_STATUS, text=note, status=status)
    return status


def set_follow_up(cand: m.Candidate, follow_up: date | None) -> None:
    if follow_up is not None and follow_up < today_il():
        raise UserError("תאריך המעקב לא יכול להיות בעבר")
    if follow_up is None and cand.status is not None and cand.status.requires_date:
        raise UserError(f"בסטטוס \"{cand.status.name}\" חובה תאריך לפנייה חוזרת")
    cand.follow_up_date = follow_up


def add_note(session: Session, cand: m.Candidate, text: str, on: date | None = None) -> None:
    if not text.strip():
        raise UserError("ההערה ריקה")
    _entry(session, cand, m.J_NOTE, text=text, on=on)


def _status_by_key(session: Session, key: str) -> m.Status | None:
    return session.scalars(select(m.Status).where(m.Status.key == key)).first()


def log_contact(session: Session, cand: m.Candidate, direction: str, channel: str, *, on: date | None = None,
                text: str = "", advance: bool = True) -> str:
    """Record a contact we made (out) or an answer we got (in). Optionally moves
    an early status forward: a first WhatsApp -> "נשלחה פנייה בווטסאפ", an answer ->
    "התקבלה תשובה". Never moves a status backwards or out of a final one.
    Returns a short message for the person."""
    if direction not in (m.J_OUT, m.J_IN):
        raise UserError("סוג תיעוד לא חוקי")
    if channel not in m.CHANNELS:
        raise UserError("ערוץ לא חוקי")
    if on is not None and on > today_il():
        raise UserError("תאריך התיעוד לא יכול להיות עתידי")
    _entry(session, cand, direction, text=text, channel=channel, on=on)
    if not advance:
        return "נרשם ביומן"
    target_key = "whatsapp_sent" if direction == m.J_OUT and channel == "whatsapp" else \
        "replied" if direction == m.J_IN else None
    if target_key is None:
        return "נרשם ביומן"
    target = _status_by_key(session, target_key)
    current = cand.status
    if (target is None or not target.enabled or current is None or current.kind != "active"
            or target.sort_order <= current.sort_order):
        return "נרשם ביומן"
    change_status(session, cand, target.id)
    return f"נרשם ביומן, והסטטוס עודכן ל\"{target.name}\""


def edit_person(cand: m.Candidate, first_name: str, last_name: str, gender: str) -> None:
    first, last = first_name.strip(), last_name.strip()
    if not (first or last):
        raise UserError("חובה למלא שם")
    if gender not in ("", "male", "female"):
        raise UserError("מגדר לא חוקי")
    if (first, last) != (cand.first_name, cand.last_name):
        cand.first_name, cand.last_name, cand.names_edited = first, last, True
    cand.gender = gender or None



def delete_candidate(session: Session, cand: m.Candidate) -> None:
    """Removes the person and the whole journal. A later import will add them again as new."""
    session.delete(cand)


def delete_everything(session: Session, confirm: str) -> int:
    """All candidates, journals and import records. Statuses, templates and settings stay."""
    if confirm.strip() != "מחיקה":
        raise UserError("כדי למחוק הכול יש להקליד את המילה: מחיקה")
    count = 0
    for cand in list(session.scalars(select(m.Candidate))):
        session.delete(cand)
        count += 1
    for run in list(session.scalars(select(m.ImportRun))):
        session.delete(run)
    return count
