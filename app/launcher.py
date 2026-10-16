"""Start the system: local server on 127.0.0.1 + an application window.

    python -m app.launcher            # app window (pywebview / Edge WebView2)
    python -m app.launcher --browser  # default browser instead (for checks)
"""
from __future__ import annotations

import argparse
import logging
import secrets
import socket
import sys
import threading
import time
import webbrowser
from logging.handlers import TimedRotatingFileHandler

import uvicorn

from app.config import APP_TITLE, data_dir, logs_dir
from app.web.main import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_up(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("השרת המקומי לא עלה")


def setup_logging() -> None:
    """Technical events only: never names, phones or emails."""
    handler = TimedRotatingFileHandler(logs_dir() / "system.log", when="midnight", backupCount=30, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    logging.getLogger("app").setLevel(logging.INFO)


def _alert(text: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, APP_TITLE, 0x10)
    else:
        print(text, file=sys.stderr)


def _single_instance():  # noqa: ANN202
    """One copy at a time: two would share one database."""
    handle = open(data_dir() / "app.lock", "a+b")  # noqa: SIM115 - kept open while running
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true", help="open the default browser instead of an app window")
    args = parser.parse_args()
    lock = _single_instance()
    if lock is None:
        _alert("התוכנה כבר פתוחה.")
        return 1
    setup_logging()
    token = secrets.token_urlsafe(24)
    port = _free_port()
    app = create_app(token=token)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_until_up(port)
    except RuntimeError as exc:
        _alert(str(exc))
        return 1
    url = f"http://127.0.0.1:{port}/candidates?k={token}"
    try:
        if args.browser:
            webbrowser.open(url)
            while thread.is_alive():
                time.sleep(1)
        else:
            import webview

            webview.create_window(APP_TITLE, url, width=1200, height=820)
            webview.start()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
