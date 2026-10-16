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
