"""Password for the program (Argon2 hash in the database) and an idle lock.

The password is also what encrypts the backups, so it is kept in memory only while the
program is unlocked and is never written anywhere. There is no recovery: a forgotten
password means the backups cannot be opened (the README says so)."""
from __future__ import annotations

import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy.orm import Session

from app.storage import models as m

MIN_LENGTH = 8
IDLE_SECONDS = 15 * 60
MAX_FAILURES = 5
LOCKOUT_SECONDS = 30
_hasher = PasswordHasher()


class AuthError(Exception):
    pass


def has_password(session: Session) -> bool:
    row = session.get(m.Setting, "password_hash")
    return bool(row and row.value)


def check_new_password(password: str, again: str) -> None:
    if password != again:
        raise AuthError("שתי הסיסמאות אינן זהות")
    if len(password) < MIN_LENGTH:
        raise AuthError(f"הסיסמה צריכה להיות באורך {MIN_LENGTH} תווים לפחות")


def set_password(session: Session, password: str) -> None:
    value = _hasher.hash(password)
    row = session.get(m.Setting, "password_hash")
    if row is None:
        session.add(m.Setting(key="password_hash", value=value))
    else:
        row.value = value


def verify(session: Session, password: str) -> bool:
    row = session.get(m.Setting, "password_hash")
    if not row or not row.value:
        return False
    try:
        return _hasher.verify(row.value, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


class Lock:
    """Unlocked / locked state of the running program, with an idle timeout and a brake on guessing."""

    def __init__(self, idle_seconds: int = IDLE_SECONDS, clock=time.monotonic):  # noqa: ANN001
        self.idle_seconds = idle_seconds
        self.clock = clock
        self.secret: str | None = None
        self.last_activity = 0.0
        self.failures = 0
        self.blocked_until = 0.0

    def unlock(self, password: str) -> None:
        self.secret = password
        self.failures = 0
        self.touch()

    def touch(self) -> None:
        self.last_activity = self.clock()

    def lock(self) -> None:
        self.secret = None

    @property
    def is_unlocked(self) -> bool:
        if self.secret is None:
            return False
        if self.clock() - self.last_activity > self.idle_seconds:
            self.lock()
            return False
        return True

    def wait_seconds(self) -> int:
        return max(0, int(self.blocked_until - self.clock()) + 1) if self.clock() < self.blocked_until else 0

    def failed(self) -> None:
        self.failures += 1
        if self.failures >= MAX_FAILURES:
            self.blocked_until = self.clock() + LOCKOUT_SECONDS
            self.failures = 0
