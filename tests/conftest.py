from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.storage.db import init_db, make_engine

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("VC_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine(tmp_path / "test.db")
    init_db(eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine, expire_on_commit=False) as s:
        yield s


class FakeFetcher:
    """Serves saved pages by contact number; raises what it is told to."""

    def __init__(self, pages: dict[int, bytes | Exception], login_after: int | None = None):
        self.pages = pages
        self.calls: list[int] = []
        self.logins = 0
        self.closed = False
        self._need_login = login_after  # raise LoginRequired on the Nth call until ensure_login()

    def fetch(self, show_id: int) -> bytes:
        from app.importer.fetch import LoginRequired
        self.calls.append(show_id)
        if self._need_login is not None and len(self.calls) >= self._need_login:
            raise LoginRequired()
        item = self.pages[show_id]
        if isinstance(item, Exception):
            raise item
        return item

    def ensure_login(self):
        self.logins += 1
        self._need_login = None

    def close(self):
        self.closed = True
