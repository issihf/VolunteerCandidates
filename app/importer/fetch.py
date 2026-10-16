"""Reading each waiting person's page, one at a time, through a fetcher.

The fetcher is anything with fetch(show_id) -> page bytes (the real one drives
a dedicated browser; tests use saved pages). The page is parsed and dropped:
it is never stored.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.parsing import PageParseError, parse_profile
from app.importer.apply import apply_profile, mark_failed
from app.storage import models as m

log = logging.getLogger("app.fetch")


class LoginRequired(Exception):
    """The browser is not logged in to Paamonim."""


class FetchError(Exception):
    """A page could not be loaded (the message is safe to show and log)."""


class PageFetcher(Protocol):
    def fetch(self, show_id: int) -> bytes: ...
    def close(self) -> None: ...


@dataclass
class FetchSummary:
    total: int = 0
    done: int = 0
    candidate: int = 0
    not_relevant: int = 0
    review: int = 0
    failed: int = 0
    stopped: str | None = None   # None | "login" | "user"
    errors: list[str] = field(default_factory=list)


def process_pending(
    session: Session,
    fetcher: PageFetcher,
    *,
    on_progress: Callable[[FetchSummary], None] | None = None,
    on_message: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> FetchSummary:
    """Fetch and read every pending person. Commits after each one, so an
    interruption loses nothing. A lost login pauses for a new login (if the
    fetcher can ask for one) and carries on from the same person."""
    ids = list(session.scalars(select(m.Candidate.id).where(m.Candidate.state == m.PENDING).order_by(m.Candidate.id)))
    summary = FetchSummary(total=len(ids))
    say = on_message or (lambda _t: None)
    tick = on_progress or (lambda _s: None)
    tick(summary)
    for cid in ids:
        if should_stop and should_stop():
            summary.stopped = "user"
            break
        cand = session.get(m.Candidate, cid)
        if cand is None or cand.state != m.PENDING:
            continue
        try:
            page = _fetch_with_login(fetcher, cand.show_id, say)
        except LoginRequired:
            summary.stopped = "login"
            break
        except FetchError as exc:
            mark_failed(cand, str(exc))
            summary.failed += 1
            session.commit()
        else:
            try:
                state = apply_profile(session, cand, parse_profile(page))
            except PageParseError as exc:
                mark_failed(cand, str(exc))
                summary.failed += 1
            else:
                setattr(summary, state, getattr(summary, state) + 1)
            session.commit()
        summary.done += 1
        tick(summary)
    log.info("fetch finished: %s", {k: v for k, v in summary.__dict__.items() if k != "errors"})
    return summary


def _fetch_with_login(fetcher: PageFetcher, show_id: int, say: Callable[[str], None]) -> bytes:
    try:
        return fetcher.fetch(show_id)
    except LoginRequired:
        ensure = getattr(fetcher, "ensure_login", None)
        if ensure is None:
            raise
        say("נדרשת התחברות לפעמונים בחלון הדפדפן שנפתח. אחרי ההתחברות השליפה תמשיך.")
        ensure()  # raises LoginRequired if the person did not log in in time
        say("")
        return fetcher.fetch(show_id)
