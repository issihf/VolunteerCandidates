"""Putting a read page, or a person's decision, onto a candidate."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.dates import now_il, parse_paamonim_date
from app.domain.names import split_name
from app.domain.parsing import CANDIDATE as P_CANDIDATE, NOT_RELEVANT as P_NOT_RELEVANT, REVIEW as P_REVIEW, Profile, classify
from app.storage import models as m

_STATE_OF = {P_CANDIDATE: m.CANDIDATE, P_NOT_RELEVANT: m.NOT_RELEVANT, P_REVIEW: m.REVIEW}


def status_by_key(session: Session, key: str) -> m.Status | None:
    return session.scalars(select(m.Status).where(m.Status.key == key)).first()


def set_status(session: Session, cand: m.Candidate, status: m.Status) -> None:
    """Change the status; the follow-up date follows the status's own number of days."""
    now = now_il()
    cand.status_id = status.id
    cand.status = status
    cand.status_changed_at = now
    cand.follow_up_date = (now + timedelta(days=status.follow_up_days)).date() if status.follow_up_days else None


def ensure_initial_status(session: Session, cand: m.Candidate) -> None:
    if cand.state == m.CANDIDATE and cand.status_id is None:
        new = status_by_key(session, "new")
        if new:
            set_status(session, cand, new)


def apply_profile(session: Session, cand: m.Candidate, profile: Profile) -> str:
    """Fill the candidate from a page and decide its state. Returns the new state."""
    f = profile.get
    cand.fetched_at = now_il()
    if profile.user_id:
        cand.user_id = profile.user_id
    if profile.full_name and profile.full_name != cand.full_name:
        cand.full_name = profile.full_name  # the page is the source of truth for the full name
        if not cand.names_edited:
            cand.first_name, cand.last_name = split_name(cand.full_name)
    elif not (cand.first_name or cand.last_name):
        cand.first_name, cand.last_name = split_name(cand.full_name)
    cand.unit = f("unit")
    cand.paamonim_status = f("paamonim_status")
    cand.intake_date = parse_paamonim_date(f("intake_date_raw"))
    cand.activity_start = parse_paamonim_date(f("activity_start_raw"))
    cand.phone_mobile = f("phone_mobile")
    cand.phone_work = f("phone_work")
    cand.phone_work_direct = f("phone_work_direct")
    cand.phone_home = f("phone_home")
    cand.street = f("street")
    cand.city = f("city")
    cand.zip_code = f("zip_code")
    cand.email = f("email")
    cand.workplace = f("workplace")
    cand.job_title = f("job_title")
    cand.birth_date = parse_paamonim_date(f("birth_date_raw"))
    cand.languages = f("languages")
    cand.extra_options = ", ".join(profile.extra_options)
    verdict, reason = classify(profile)
    cand.state = _STATE_OF[verdict]
    cand.state_reason = reason
    ensure_initial_status(session, cand)
    return cand.state


def mark_failed(cand: m.Candidate, reason: str) -> None:
    cand.state = m.FAILED
    cand.state_reason = reason


def resolve(session: Session, cand: m.Candidate, to_state: str) -> None:
    """A person's decision for a candidate that needs review (or a correction)."""
    if to_state not in (m.CANDIDATE, m.NOT_RELEVANT):
        raise ValueError("מצב לא חוקי")
    cand.state = to_state
    cand.state_reason = "הוחלט ידנית"
    ensure_initial_status(session, cand)


def request_refetch(cand: m.Candidate) -> None:
    """Manual refresh of one person's details: back to the fetch queue."""
    cand.state = m.PENDING
    cand.state_reason = ""
