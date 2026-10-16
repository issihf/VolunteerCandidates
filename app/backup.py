"""Encrypted backups of the database.

A backup is a consistent copy of the SQLite file (SQLite's own online backup), encrypted with
AES-256-GCM using a key derived from the program password (scrypt, random salt per file).
File name: backup-YYYYMMDD-HHMMSS-<why>.vcbak in <data>/backups. Only the owner of the password
can open them. Nothing here writes names or contents to the log."""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.config import data_dir
from app.domain.dates import now_il

MAGIC = b"VCBK1"
NAME_RE = re.compile(r"^backup-(\d{8})-(\d{6})-([a-z]+)\.vcbak$")
KINDS = {"daily": "יומי", "manual": "ידני", "import": "לפני ייבוא", "delete": "לפני מחיקה",
         "restore": "לפני שחזור", "upgrade": "לפני שדרוג"}
KEEP_DAILY, KEEP_WEEKLY, KEEP_ALL_DAYS = 14, 8, 3


class BackupError(Exception):
    pass


def backups_dir() -> Path:
    path = data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(password.encode("utf-8"))


def encrypt(data: bytes, password: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(password, salt)).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, password: str) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 28:
        raise BackupError("זה לא קובץ גיבוי של המערכת")
    salt, nonce = blob[5:21], blob[21:33]
    try:
        return AESGCM(_key(password, salt)).decrypt(nonce, blob[33:], MAGIC)
    except InvalidTag:
        raise BackupError("הסיסמה שגויה, או שהקובץ פגום") from None


def _db_file(engine) -> str:
    path = engine.url.database
    if not path or path == ":memory:":
        raise BackupError("אין קובץ מסד נתונים לגיבוי")
    return path


def create_backup(engine, password: str, kind: str = "manual", *, now: datetime | None = None,
                  directory: Path | None = None) -> Path:
    if kind not in KINDS:
        raise BackupError("סוג גיבוי לא חוקי")
    src = sqlite3.connect(_db_file(engine))
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
        data = Path(tmp).read_bytes()
    finally:
        src.close()
        Path(tmp).unlink(missing_ok=True)
    stamp = (now or now_il()).strftime("%Y%m%d-%H%M%S")
    folder = directory or backups_dir()
    target = folder / f"backup-{stamp}-{kind}.vcbak"
    n = 1
    while target.exists():  # two backups in the same second
        n += 1
        target = folder / f"backup-{stamp}{n}-{kind}.vcbak"
    target.write_bytes(encrypt(data, password))
    prune(folder, now=now)
    return target


def parse_name(name: str) -> tuple[datetime, str] | None:
    mt = re.match(r"^backup-(\d{8})-(\d{6})\d?-([a-z]+)\.vcbak$", name)
    if not mt:
        return None
    try:
        return datetime.strptime(mt.group(1) + mt.group(2), "%Y%m%d%H%M%S"), mt.group(3)
    except ValueError:
        return None


def list_backups(directory: Path | None = None) -> list[dict]:
    folder = directory or backups_dir()
    rows = []
    for p in folder.glob("backup-*.vcbak"):
        parsed = parse_name(p.name)
        if parsed:
            rows.append({"name": p.name, "when": parsed[0], "kind": parsed[1],
                         "kind_name": KINDS.get(parsed[1], parsed[1]), "size": p.stat().st_size})
    return sorted(rows, key=lambda r: r["when"], reverse=True)


def prune(directory: Path | None = None, *, now: datetime | None = None) -> list[str]:
    """Keep: every backup of the last 3 days (so the one made before a deletion is not lost to a
    later one the same day), the newest of each day for 14 days, and the newest of each week for
    8 more weeks. Everything else is removed."""
    folder = directory or backups_dir()
    rows = list_backups(folder)
    today = (now or now_il()).date()
    keep: set[str] = set()
    seen_days, seen_weeks = set(), set()
    for r in rows:  # newest first
        d = r["when"].date()
        age = (today - d).days
        if age < KEEP_ALL_DAYS:
            keep.add(r["name"])
        if age < KEEP_DAILY and d not in seen_days:
            seen_days.add(d)
            keep.add(r["name"])
        week = d.isocalendar()[:2]
        if age < KEEP_WEEKLY * 7 + KEEP_DAILY and week not in seen_weeks:
            seen_weeks.add(week)
            keep.add(r["name"])
    removed = []
    for r in rows:
        if r["name"] not in keep:
            (folder / r["name"]).unlink(missing_ok=True)
            removed.append(r["name"])
    return removed


def has_backup_today(directory: Path | None = None, *, now: datetime | None = None) -> bool:
    today = (now or now_il()).date()
    return any(r["when"].date() == today for r in list_backups(directory))


def restore_backup(engine, name: str, password: str, directory: Path | None = None) -> None:
    """Replace the live database's content with a backup (SQLite online backup into the live file)."""
    folder = directory or backups_dir()
    if not parse_name(name) or (folder / name).parent != folder or not (folder / name).is_file():
        raise BackupError("הגיבוי לא נמצא")
    data = decrypt((folder / name).read_bytes(), password)
    if not data.startswith(b"SQLite format 3\x00"):
        raise BackupError("תוכן הגיבוי אינו תקין")
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        Path(tmp).write_bytes(data)
        engine.dispose()
        src = sqlite3.connect(tmp)
        dst = sqlite3.connect(_db_file(engine))
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
    finally:
        Path(tmp).unlink(missing_ok=True)
    engine.dispose()
