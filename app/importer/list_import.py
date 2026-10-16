"""Importing the saved "אנשי קשר" list: only people not seen before are added."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.dates import now_il
from app.domain.names import split_name
from app.domain.parsing import parse_contacts_list
from app.storage import models as m


@dataclass
class ImportResult:
    total: int          # rows that link to a contact page
    new: int            # people added (waiting for their details)
    existing: int       # already in the system, left as they are
    repeated: int       # the same person twice in the file
    without_link: int   # rows that could not be used


def import_list(session: Session, data: bytes | str) -> ImportResult:
    rows, skipped = parse_contacts_list(data)
    known = set(session.scalars(select(m.Candidate.show_id)))
    now = now_il()
    run = m.ImportRun(created_at=now, rows_total=len(rows), rows_without_link=skipped)
    session.add(run)
    session.flush()
    new = existing = repeated = 0
    seen: set[int] = set()
    for row in rows:
        if row.show_id in seen:
            repeated += 1
            continue
        seen.add(row.show_id)
        if row.show_id in known:
            existing += 1
            continue
        first, last = split_name(row.name)
        session.add(m.Candidate(
            show_id=row.show_id, user_id=row.row_user_id, import_id=run.id, imported_at=now,
            state=m.PENDING, full_name=row.name, first_name=first, last_name=last,
            list_unit=row.unit, list_role=row.role, list_city=row.city,
            list_mobile=row.mobile, list_email=row.email,
        ))
        new += 1
    run.rows_new, run.rows_existing, run.rows_repeated = new, existing, repeated
    session.flush()
    return ImportResult(total=len(rows), new=new, existing=existing, repeated=repeated, without_link=skipped)
