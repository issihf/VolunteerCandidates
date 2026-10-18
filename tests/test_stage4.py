from datetime import datetime, timedelta
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import backup as bk
from app.security import auth
from app.storage import models as m
from app.web.main import create_app
from tests.conftest import FakeFetcher
from tests.test_import_and_fetch import pages
from tests.test_web import upload


# ------------------------------------------------------------------ backup module
def test_encrypt_roundtrip_and_wrong_password():
    blob = bk.encrypt(b"SQLite format 3\x00data", "סיסמה-123")
    assert b"data" not in blob
    assert bk.decrypt(blob, "סיסמה-123").endswith(b"data")
    with pytest.raises(bk.BackupError):
        bk.decrypt(blob, "אחר")
    with pytest.raises(bk.BackupError):
        bk.decrypt(b"nonsense", "x")


def test_backup_and_restore(engine, session):
    session.add(m.Setting(key="marker", value="before"))
    session.commit()
    path = bk.create_backup(engine, "pw-12345678", "manual")
    assert path.exists() and b"marker" not in path.read_bytes()
    session.get(m.Setting, "marker").value = "after"
    session.commit()
    with pytest.raises(bk.BackupError):
        bk.restore_backup(engine, path.name, "wrong")
    bk.restore_backup(engine, path.name, "pw-12345678")
    session.expire_all()
    assert session.get(m.Setting, "marker").value == "before"


def test_restore_rejects_path_tricks(engine):
    with pytest.raises(bk.BackupError):
        bk.restore_backup(engine, "../data.db", "x")


def test_prune_keeps_14_days_and_8_weeks(tmp_path):
    now = datetime(2026, 10, 16, 12, 0, 0)
    names = []
    for days_ago in range(0, 100):
        for hour in (9, 15) if days_ago < 3 else (9,):
            t = now - timedelta(days=days_ago)
            t = t.replace(hour=hour)
            n = f"backup-{t:%Y%m%d-%H%M%S}-daily.vcbak"
            (tmp_path / n).write_bytes(b"x")
            names.append(n)
    bk.prune(tmp_path, now=now)
    left = bk.list_backups(tmp_path)
    days = {r["when"].date() for r in left}
    assert all((now.date() - (now - timedelta(days=i)).date()).days == i for i in range(14))
    for i in range(14):
        assert (now - timedelta(days=i)).date() in days
    assert len(days) < 40 and all((now.date() - d).days < 80 for d in days)
    # one file per day for the last days
    assert len([r for r in left if r["when"].date() == (now - timedelta(days=5)).date()]) == 1
    assert len([r for r in left if r["when"].date() == (now - timedelta(days=1)).date()]) == 2  # recent days keep all


# ------------------------------------------------------------------ auth
def test_lock_idles_out_and_throttles():
    t = [0.0]
    lock = auth.Lock(idle_seconds=60, clock=lambda: t[0])
    lock.unlock("pw")
    assert lock.is_unlocked
    t[0] = 61
    assert not lock.is_unlocked and lock.secret is None
    for _ in range(auth.MAX_FAILURES):
        lock.failed()
    assert lock.wait_seconds() > 0
    t[0] = 200
    assert lock.wait_seconds() == 0


def test_password_hash_not_plain(session):
    auth.set_password(session, "סיסמה-מאוד-ארוכה")
    session.commit()
    stored = session.get(m.Setting, "password_hash").value
    assert stored.startswith("$argon2") and "סיסמה" not in stored
    assert auth.verify(session, "סיסמה-מאוד-ארוכה") and not auth.verify(session, "x")
    with pytest.raises(auth.AuthError):
        auth.check_new_password("short", "short")
    with pytest.raises(auth.AuthError):
        auth.check_new_password("longenough1", "different1")


# ------------------------------------------------------------------ web with password
@pytest.fixture()
def pw_client(engine):
    app = create_app(engine=engine, fetcher_factory=lambda: FakeFetcher(pages()), require_password=True)
    with TestClient(app, follow_redirects=False) as c:
        yield c


def test_everything_redirects_until_password(pw_client):
    assert pw_client.get("/candidates").headers["location"] == "/setup"
    assert pw_client.get("/fetch/status").status_code == 403
    bad = pw_client.post("/setup", data={"password": "short", "again": "short"})
    assert bad.headers["location"].startswith("/setup?flash=")
    ok = pw_client.post("/setup", data={"password": "good-password-1", "again": "good-password-1"})
    assert ok.headers["location"] == "/candidates"
    assert pw_client.get("/candidates").status_code == 200
    # a daily backup was made at the first entry
    assert len(bk.list_backups()) == 1


def test_login_lock_and_throttle(pw_client):
    pw_client.post("/setup", data={"password": "good-password-1", "again": "good-password-1"})
    pw_client.post("/lock")
    assert pw_client.get("/candidates").headers["location"] == "/login"
    wrong = pw_client.post("/login", data={"password": "nope"})
    assert "flash=" in wrong.headers["location"]
    assert pw_client.get("/candidates").status_code == 303
    assert pw_client.post("/login", data={"password": "good-password-1"}).headers["location"] == "/candidates"
    assert pw_client.get("/candidates").status_code == 200
    pw_client.post("/lock")
    for _ in range(auth.MAX_FAILURES):
        pw_client.post("/login", data={"password": "bad"})
    r = pw_client.post("/login", data={"password": "good-password-1"})  # right password, but blocked
    assert "יותר מדי" in unquote(r.headers["location"])


def test_import_makes_backup_and_delete_all_needs_it(pw_client):
    pw_client.post("/setup", data={"password": "good-password-1", "again": "good-password-1"})
    before = len(bk.list_backups())
    upload(pw_client)
    kinds = [r["kind"] for r in bk.list_backups()]
    assert len(kinds) == before + 1 and "import" in kinds
    pw_client.post("/settings/delete-all", data={"confirm": "מחיקה"})
    assert "delete" in [r["kind"] for r in bk.list_backups()]


def test_settings_backup_and_restore_flow(pw_client):
    pw_client.post("/setup", data={"password": "good-password-1", "again": "good-password-1"})
    page = pw_client.get("/settings").text
    assert "גיבוי עכשיו" in page and "החלפת סיסמה" in page
    pw_client.post("/settings/backup/now")
    name = [r for r in bk.list_backups() if r["kind"] == "manual"][0]["name"]
    upload(pw_client)
    wrong = pw_client.post("/settings/backup/restore", data={"name": name, "password": "nope"})
    assert "err=1" in wrong.headers["location"]
    ok = pw_client.post("/settings/backup/restore", data={"name": name, "password": "good-password-1"})
    assert "err=1" not in ok.headers["location"]
    assert "restore" in [r["kind"] for r in bk.list_backups()]


def test_change_password(pw_client):
    pw_client.post("/setup", data={"password": "good-password-1", "again": "good-password-1"})
    bad = pw_client.post("/settings/password", data={"old": "x", "password": "new-password-22", "again": "new-password-22"})
    assert "err=1" in bad.headers["location"]
    pw_client.post("/settings/password", data={"old": "good-password-1", "password": "new-password-22", "again": "new-password-22"})
    pw_client.post("/lock")
    assert pw_client.post("/login", data={"password": "new-password-22"}).headers["location"] == "/candidates"


def test_reminder_banner(pw_client):
    pw_client.post("/setup", data={"password": "good-password-1", "again": "good-password-1"})
    upload(pw_client)
    assert "כונן חיצוני" in pw_client.get("/candidates").text
    pw_client.post("/settings/external-copy")
    assert "כונן חיצוני" not in pw_client.get("/candidates").text.split("<main>")[0]


def test_powershell_scripts_survive_windows_powershell():
    """Windows PowerShell 5.1 reads a script without a BOM as ANSI: Hebrew text then breaks the parser."""
    from pathlib import Path
    for script in Path(__file__).parent.parent.glob("*.ps1"):
        raw = script.read_bytes()
        if any(b > 127 for b in raw):
            assert raw.startswith(b"\xef\xbb\xbf"), f"{script.name} has Hebrew but no UTF-8 BOM"


def test_release_package_is_one_click():
    from pathlib import Path
    root = Path(__file__).parent.parent
    cmd = (root / "packaging" / "Install.cmd").read_text()
    assert "install.ps1" in cmd and "ExecutionPolicy Bypass" in cmd
    assert "-From" not in cmd  # "%~dp0" ends with a backslash, so \" swallows the closing quote and Resolve-Path fails
    assert (root / "packaging" / "README-INSTALL.txt").exists()
    build = (root / "build.ps1").read_text()
    assert "Install.cmd" in build and "README-INSTALL.txt" in build
    workflow = (root / "packaging" / "release.yml.copy-to-.github-workflows").read_text()  # remote tools cannot write .github; copied there by hand
    assert "Install.cmd" in workflow and "gh release create" in workflow and "windows-latest" in workflow
    ps1 = (root / "install.ps1").read_bytes()
    text = ps1.decode("utf-8-sig")
    assert "$env:TEMP" in text and "Try-Shortcut" in text  # shortcut is built outside any Hebrew path (OneDrive Desktop) and is never fatal
    assert ps1.startswith(b"\xef\xbb\xbf")  # Hebrew shortcut name needs the BOM in Windows PowerShell 5.1


def test_icon_is_wired_in():
    from pathlib import Path
    root = Path(__file__).parent.parent
    ico = (root / "packaging" / "icon.ico").read_bytes()
    assert ico[:4] == b"\x00\x00\x01\x00" and int.from_bytes(ico[4:6], "little") >= 6  # several sizes
    assert 'icon=str(ROOT / "packaging" / "icon.ico")' in (root / "packaging" / "volunteercandidates.spec").read_text()
    assert (root / "app" / "web" / "static" / "icon.svg").read_text().startswith("<svg")


def test_login_pages_show_the_icon(engine):
    app = create_app(engine=engine, require_password=True)
    with TestClient(app, follow_redirects=False) as c:
        assert "/static/icon.svg" in c.get("/setup").text
        c.post("/setup", data={"password": "good-password-1", "again": "good-password-1"})
        c.post("/lock")
        assert "/static/icon.svg" in c.get("/login").text
        assert c.get("/static/icon.svg").status_code == 200
