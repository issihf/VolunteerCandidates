"""Engine, sessions and the built-in defaults."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import DEFAULT_FOLLOW_UP_DAYS, db_path
from app.storage.models import Base, Setting, Status

# key, name, kind, gets an automatic follow-up (default days), requires a date
DEFAULT_STATUSES = [
    ("new", "חדש", "active", True, False),
    ("whatsapp_sent", "נשלחה פנייה בווטסאפ", "active", True, False),
    ("replied", "התקבלה תשובה", "active", True, False),
    ("interested", "יצר קשר ומעוניין", "active", True, False),
    ("invited", "הוזמן לראיון", "active", True, False),
    ("interviewed", "עבר ראיון", "active", False, False),
    ("failed_interview", "לא עבר ראיון", "final", False, False),
    ("not_interested", "לא מעוניין להתנדב כמלווה", "final", False, False),
    ("not_now", "לא מעוניין כרגע, יהיה מעוניין בעתיד", "final", False, True),
]


def make_engine(path: Path | str | None = None):
    target = str(path) if path is not None else str(db_path())
    url = "sqlite://" if target == ":memory:" else f"sqlite:///{target}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def init_db(engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ensure_defaults(session)
        session.commit()


def ensure_defaults(session: Session) -> None:
    if session.get(Setting, "follow_up_days") is None:
        session.add(Setting(key="follow_up_days", value=str(DEFAULT_FOLLOW_UP_DAYS)))
    existing = set(session.scalars(select(Status.key)))
    for order, (key, name, kind, follows, needs_date) in enumerate(DEFAULT_STATUSES, start=1):
        if key in existing:
            continue
        session.add(Status(key=key, name=name, sort_order=order, kind=kind,
                           follow_up_days=DEFAULT_FOLLOW_UP_DAYS if follows else None,
                           requires_date=needs_date))
    session.flush()


def session_factory(engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)
