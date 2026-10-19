"""The dedicated browser: a Playwright window with its own profile, logged in to
Paamonim once and then reused. It is separate from the person's everyday browser.

Runs headless while the login holds; if the login is lost it opens a visible
window, waits for the person to log in, and goes back to headless.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from app.config import PAAMONIM_BASE_URL, browser_profile_dir
from app.importer.fetch import FetchError, LoginRequired

log = logging.getLogger("app.browser")

COOKIE_FILE = "session-cookies.json"
COOKIE_MAX_AGE = 7 * 24 * 3600  # a saved login older than this is not reused


def looks_like_login(url: str, html: str) -> bool:
    """True when the browser landed on a sign-in page instead of the contact page."""
    path = urlparse(url).path.rstrip("/")
    if path == "/login" or path.startswith("/login/") and not path.startswith("/login/logout"):
        return True
    low = html.lower()
    return 'type="password"' in low and "user_record" not in low


class PlaywrightFetcher:
    def __init__(self, base_url: str = PAAMONIM_BASE_URL, *, profile_dir=None, channel: str | None = None,
                 executable_path: str | None = None, delay: float = 0.6, login_timeout: float = 300.0,
                 page_timeout_ms: int = 30000, login_headless: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.profile_dir = str(profile_dir or browser_profile_dir())
        self.channel = channel if channel is not None else os.environ.get("VC_BROWSER_CHANNEL", "msedge")
        self.executable_path = executable_path or os.environ.get("VC_BROWSER_PATH") or None
        self.delay = delay
        self.login_timeout = login_timeout
        self.page_timeout_ms = page_timeout_ms
        self._login_headless = login_headless  # tests only: a real login needs a visible window
        self._pw = None
        self._ctx = None
        self._headless: bool | None = None
        self._forgotten = False

    # -- lifecycle
    def _start(self, headless: bool) -> None:
        if self._ctx is not None and self._headless == headless:
            return
        self._save_cookies()  # a login held only in memory (a session cookie) would be lost by the restart
        self._stop_context()
        if self._pw is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
        kwargs: dict = {"headless": headless, "locale": "he-IL"}
        if self.executable_path:
            kwargs["executable_path"] = self.executable_path
        elif self.channel:
            kwargs["channel"] = self.channel
        if self.executable_path:
            kwargs["args"] = ["--no-sandbox"]
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(self.profile_dir, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise FetchError("לא ניתן לפתוח את הדפדפן (Edge/Chrome). בדקו שהוא מותקן") from exc
        self._headless = headless
        self._restore_cookies()

    # -- the login survives restarts: Paamonim's login is a session cookie, which a closed browser forgets
    def _cookie_path(self) -> Path:
        return Path(self.profile_dir) / COOKIE_FILE

    def _host(self) -> str:
        return urlparse(self.base_url).hostname or ""

    def _save_cookies(self) -> None:
        if self._ctx is None or self._forgotten:
            return
        try:
            host = self._host()
            def ours(c: dict) -> bool:
                d = c.get("domain", "").lstrip(".")
                return bool(host) and (host == d or host.endswith("." + d))
            mine = [c for c in self._ctx.cookies() if ours(c)]
            if not mine:
                return
            path = self._cookie_path()
            path.write_text(json.dumps({"saved_at": time.time(), "cookies": mine}), encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception:  # noqa: BLE001 - never break a run over this
            log.warning("could not save the login cookies", exc_info=True)

    def _restore_cookies(self) -> None:
        path = self._cookie_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - float(data.get("saved_at", 0)) > COOKIE_MAX_AGE:
                return
            now = time.time()
            cookies = []
            for c in data.get("cookies", []):
                c = dict(c)
                if c.get("expires", -1) in (None, -1) or c["expires"] < now:
                    c["expires"] = now + COOKIE_MAX_AGE
                cookies.append(c)
            if cookies:
                self._ctx.add_cookies(cookies)
        except FileNotFoundError:
            return
        except Exception:  # noqa: BLE001
            log.warning("could not restore the login cookies", exc_info=True)

    def forget_login(self) -> None:
        """Delete the saved login (used when the person asks to sign out of Paamonim)."""
        self._forgotten = True
        try:
            self._cookie_path().unlink()
        except FileNotFoundError:
            pass

    def _stop_context(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:  # noqa: BLE001
                pass
            self._ctx = None

    def close(self) -> None:
        self._save_cookies()
        self._stop_context()
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = None

    # -- work
    def fetch(self, show_id: int) -> bytes:
        if self._ctx is None:
            self._start(headless=True)  # after a login the visible window is kept: a restart would drop the session
        page = self._ctx.new_page()
        try:
            page.set_default_timeout(self.page_timeout_ms)
            try:
                resp = page.goto(f"{self.base_url}/contacts/show/{show_id}", wait_until="domcontentloaded")
                html = page.content()
                url = page.url
            except Exception as exc:  # noqa: BLE001
                raise FetchError("הדף לא נטען (תקלת רשת או זמן המתנה)") from exc
            if looks_like_login(url, html):
                raise LoginRequired()
            if resp is not None and resp.status >= 400:
                raise FetchError(f"הדף החזיר שגיאה {resp.status}")
            return html.encode("utf-8")
        finally:
            page.close()
            if self.delay:
                time.sleep(self.delay)

    def _logged_in(self, page, stable: list) -> bool:
        """Positive signs only: the header shows the user's name, or we sit on a Paamonim page that is not a
        sign-in page for several checks in a row."""
        url, html = page.url, page.content()
        if looks_like_login(url, html):
            stable[0] = 0
            return False
        if page.query_selector("#userName"):
            return True
        if urlparse(url).hostname == self._host():
            stable[0] += 1
            return stable[0] >= 3
        stable[0] = 0
        return False

    def ensure_login(self) -> None:
        """Show a visible window until the person has logged in (or time runs out). The window stays open
        for the rest of the run, so the login is never lost between the sign-in and the fetching."""
        self._start(headless=self._login_headless)
        page = self._ctx.new_page()
        last = ""
        try:
            page.goto(self.base_url + "/dashboard", wait_until="domcontentloaded")
            deadline = time.time() + self.login_timeout
            stable = [0]
            while time.time() < deadline:
                try:
                    last = urlparse(page.url).path
                    if self._logged_in(page, stable):
                        break
                except Exception:  # noqa: BLE001 - page is navigating
                    pass
                time.sleep(1.0)
            else:
                log.warning("login not detected before the time ran out (last page path: %s)", last)
                raise LoginRequired()
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
        self._save_cookies()
