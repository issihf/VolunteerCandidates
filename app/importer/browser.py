"""The dedicated browser: a Playwright window with its own profile, logged in to
Paamonim once and then reused. It is separate from the person's everyday browser.

Runs headless while the login holds; if the login is lost it opens a visible
window, waits for the person to log in, and goes back to headless.
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

from app.config import PAAMONIM_BASE_URL, browser_profile_dir
from app.importer.fetch import FetchError, LoginRequired


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
                 page_timeout_ms: int = 30000) -> None:
        self.base_url = base_url.rstrip("/")
        self.profile_dir = str(profile_dir or browser_profile_dir())
        self.channel = channel if channel is not None else os.environ.get("VC_BROWSER_CHANNEL", "msedge")
        self.executable_path = executable_path or os.environ.get("VC_BROWSER_PATH") or None
        self.delay = delay
        self.login_timeout = login_timeout
        self.page_timeout_ms = page_timeout_ms
        self._pw = None
        self._ctx = None
        self._headless: bool | None = None

    # -- lifecycle
    def _start(self, headless: bool) -> None:
        if self._ctx is not None and self._headless == headless:
            return
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

    def _stop_context(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:  # noqa: BLE001
                pass
            self._ctx = None

    def close(self) -> None:
        self._stop_context()
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = None

    # -- work
    def fetch(self, show_id: int) -> bytes:
        self._start(headless=True)
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

    def ensure_login(self) -> None:
        """Show a visible window until the person has logged in (or time runs out)."""
        self._start(headless=False)
        page = self._ctx.new_page()
        try:
            page.goto(self.base_url + "/dashboard", wait_until="domcontentloaded")
            deadline = time.time() + self.login_timeout
            while time.time() < deadline:
                try:
                    if not looks_like_login(page.url, page.content()) and page.query_selector("#userName"):
                        break
                except Exception:  # noqa: BLE001 - page is navigating
                    pass
                time.sleep(1.0)
            else:
                raise LoginRequired()
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
        self._start(headless=True)
