"""Engine, sessions and the built-in defaults."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, inspect, select, text
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


def _add_missing_columns(engine) -> None:
    """Additive migration: a database made by an older version gets the columns
    added later. New tables are created by create_all; nothing is ever dropped."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'
                default = col.default.arg if col.default is not None and col.default.is_scalar else None
                if default is not None:
                    ddl += " DEFAULT " + (str(int(default)) if isinstance(default, bool) else
                                          str(default) if isinstance(default, (int, float)) else "'" + str(default).replace("'", "''") + "'")
                conn.execute(text(ddl))


def init_db(engine) -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
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
    _rename_old_variable(session)


def _rename_old_variable(session: Session) -> None:
    """Templates written in version 0.3.0 used {{שם_פונה}}; it is now {{שם_מנהל_מפעמונים}}."""
    from sqlalchemy import select as _select
    from app.storage.models import MessageTemplate
    for t in session.scalars(_select(MessageTemplate)):
        for col in ("subject", "body_neutral", "body_male", "body_female"):
            old = getattr(t, col) or ""
            if "שם_פונה" in old:
                setattr(t, col, old.replace("שם_פונה", "שם_מנהל_מפעמונים"))
    session.flush()


def session_factory(engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)


def get_default_follow_up_days(session: Session) -> int:
    """The number of days given to a status added in the settings."""
    setting = session.get(Setting, "follow_up_days")
    try:
        return max(1, int(setting.value)) if setting else DEFAULT_FOLLOW_UP_DAYS
    except ValueError:
        return DEFAULT_FOLLOW_UP_DAYS
