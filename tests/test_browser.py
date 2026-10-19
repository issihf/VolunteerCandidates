"""The real browser code against a small local server (skipped when no Chromium)."""
import glob
import http.server
import threading

import pytest

from app.domain.parsing import classify, parse_profile
from app.importer.browser import PlaywrightFetcher, looks_like_login
from app.importer.fetch import FetchError, LoginRequired
from tests.conftest import fixture_bytes

CHROMIUM = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome") or [None])[0]


def test_looks_like_login():
    assert looks_like_login("https://x/login", "<html></html>")
    assert looks_like_login("https://x/login/", "<html></html>")
    assert looks_like_login("https://x/anything", '<form><input type="password"></form>')
    assert not looks_like_login("https://x/contacts/show/5", '<dl class="user_record"></dl><input type="password">')


@pytest.fixture()
def site():
    state = {"logged_in": True}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: ANN002
            pass

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/login"):
                body = '<html><body><form><input type="password"></form></body></html>'.encode()
                self.send_response(200)
            elif not state["logged_in"]:
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            elif self.path == "/contacts/show/1004":
                body = fixture_bytes("profile_candidate.html")
                self.send_response(200)
            else:
                body = "<html><body>not found</body></html>".encode()
                self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()


@pytest.mark.skipif(not CHROMIUM, reason="no Chromium available")
def test_fetch_login_and_errors(site, tmp_path):
    base, state = site
    fetcher = PlaywrightFetcher(base, profile_dir=tmp_path / "profile", executable_path=CHROMIUM, delay=0)
    try:
        page = fetcher.fetch(1004)
        assert classify(parse_profile(page))[0] == "candidate"
        with pytest.raises(FetchError):
            fetcher.fetch(5555)
        state["logged_in"] = False
        with pytest.raises(LoginRequired):
            fetcher.fetch(1004)
    finally:
        fetcher.close()


def test_missing_browser_is_a_clear_error(tmp_path):
    fetcher = PlaywrightFetcher("http://127.0.0.1:9", profile_dir=tmp_path / "p", executable_path="/nonexistent/browser")
    try:
        with pytest.raises(FetchError):
            fetcher.fetch(1)
    finally:
        fetcher.close()


@pytest.fixture()
def cookie_site():
    """A site whose login is a SESSION cookie (no expiry), like a plain web login. The 'person' logs in
    when the test sets state['login_now']: the sign-in page notices it and the cookie is issued."""
    state = {"login_now": False}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: ANN002
            pass

        def _send(self, code, body, cookie=None, location=None):
            self.send_response(code)
            if location:
                self.send_header("Location", location)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            has = "sid=ok" in (self.headers.get("Cookie") or "")
            if self.path == "/poll":
                if state["login_now"]:
                    self._send(200, b"go", cookie="sid=ok; Path=/")
                else:
                    self._send(200, b"wait")
            elif self.path.startswith("/login"):
                page = ('<html><body><form><input type="password"></form><script>setInterval(function(){'
                        'fetch("/poll").then(function(r){return r.text()}).then(function(t){'
                        'if(t==="go")location="/dashboard"})},200)</script></body></html>')
                self._send(200, page.encode())
            elif not has:
                self._send(302, b"", location="/login")
            elif self.path == "/dashboard":
                self._send(200, "<html><body>dashboard, no user name element here</body></html>".encode())  # no #userName
            elif self.path == "/contacts/show/1004":
                self._send(200, fixture_bytes("profile_candidate.html"))
            else:
                self._send(404, b"not found")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()


@pytest.mark.skipif(not CHROMIUM, reason="no Chromium available")
def test_login_survives_the_run_and_the_next_run(cookie_site, tmp_path):
    base, state = cookie_site
    profile = tmp_path / "profile"
    first = PlaywrightFetcher(base, profile_dir=profile, executable_path=CHROMIUM, delay=0, login_headless=True,
                              login_timeout=30)
    try:
        with pytest.raises(LoginRequired):
            first.fetch(1004)
        threading.Timer(1.5, lambda: state.__setitem__("login_now", True)).start()
        first.ensure_login()  # detected even though the dashboard has no #userName
        # the very next fetch, in the same run, is still logged in (the old code restarted the browser here)
        assert classify(parse_profile(first.fetch(1004)))[0] == "candidate"
    finally:
        first.close()
    assert (profile / "session-cookies.json").exists()
    # a brand-new run (new browser process) reuses the saved login: no sign-in window needed
    second = PlaywrightFetcher(base, profile_dir=profile, executable_path=CHROMIUM, delay=0)
    try:
        assert classify(parse_profile(second.fetch(1004)))[0] == "candidate"
        second.forget_login()
    finally:
        second.close()
