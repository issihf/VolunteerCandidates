"""Database tables (SQLite through SQLAlchemy)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Status(Base):
    """A step in the candidate's path. Editable in the settings (stage 2)."""
    __tablename__ = "status"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str | None] = mapped_column(String(40), unique=True)  # system name, None for user-added
    name: Mapped[str] = mapped_column(String(120))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(10), default="active")  # active | final
    follow_up_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = no automatic follow-up
    requires_date: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ImportRun(Base):
    """One list import. The file itself is never stored."""
    __tablename__ = "import_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_new: Mapped[int] = mapped_column(Integer, default=0)
    rows_existing: Mapped[int] = mapped_column(Integer, default=0)
    rows_repeated: Mapped[int] = mapped_column(Integer, default=0)
    rows_without_link: Mapped[int] = mapped_column(Integer, default=0)


# Candidate.state values
PENDING = "pending"          # in the list, details not fetched yet
CANDIDATE = "candidate"      # lists "ליווי משפחות"
NOT_RELEVANT = "not_relevant"
REVIEW = "review"            # looks similar, needs a person to decide
FAILED = "failed"            # fetching or reading the page failed
STATES = (CANDIDATE, PENDING, REVIEW, NOT_RELEVANT, FAILED)


class Candidate(Base):
    __tablename__ = "candidate"
    id: Mapped[int] = mapped_column(primary_key=True)
    show_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)  # contacts/show/<id>
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("import_run.id"), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    state: Mapped[str] = mapped_column(String(20), default=PENDING, index=True)
    state_reason: Mapped[str] = mapped_column(Text, default="")

    full_name: Mapped[str] = mapped_column(String(200), default="")
    first_name: Mapped[str] = mapped_column(String(100), default="")
    last_name: Mapped[str] = mapped_column(String(100), default="")
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)  # male | female | None

    # what the list showed (kept until the page is read)
    list_unit: Mapped[str] = mapped_column(String(200), default="")
    list_role: Mapped[str] = mapped_column(String(100), default="")
    list_city: Mapped[str] = mapped_column(String(100), default="")
    list_mobile: Mapped[str] = mapped_column(String(50), default="")
    list_email: Mapped[str] = mapped_column(String(200), default="")

    # from the contact page
    unit: Mapped[str] = mapped_column(String(200), default="")
    paamonim_status: Mapped[str] = mapped_column(String(50), default="")
    intake_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    activity_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    phone_mobile: Mapped[str] = mapped_column(String(50), default="")
    phone_work: Mapped[str] = mapped_column(String(50), default="")
    phone_work_direct: Mapped[str] = mapped_column(String(50), default="")
    phone_home: Mapped[str] = mapped_column(String(50), default="")
    street: Mapped[str] = mapped_column(String(200), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    zip_code: Mapped[str] = mapped_column(String(20), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    workplace: Mapped[str] = mapped_column(String(200), default="")
    job_title: Mapped[str] = mapped_column(String(200), default="")
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    languages: Mapped[str] = mapped_column(String(200), default="")
    extra_options: Mapped[str] = mapped_column(Text, default="")

    # the path (stage 2 adds the journal and settings screens)
    status_id: Mapped[int | None] = mapped_column(ForeignKey("status.id"), nullable=True)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    follow_up_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    status: Mapped[Status | None] = relationship()

    @property
    def display_mobile(self) -> str:
        return self.phone_mobile or self.list_mobile

    @property
    def display_email(self) -> str:
        return self.email or self.list_email

    @property
    def display_city(self) -> str:
        return self.city or self.list_city
