"""Open a link in the person's own default program (browser or the WhatsApp app), never inside the app window.

Inside the app window (Edge WebView2) a link to WhatsApp would replace the app's screen, or open a bare
window that covers it. The server runs on the same computer, so it can ask Windows to open the link.
Only WhatsApp addresses are allowed, so this can never be used to open anything else.
"""
from __future__ import annotations

import os
import webbrowser

ALLOWED_PREFIXES = ("https://api.whatsapp.com/send", "https://wa.me/", "whatsapp://send")


def is_allowed(url: str) -> bool:
    return url.startswith(ALLOWED_PREFIXES)


def open_external(url: str) -> None:
    if not is_allowed(url):
        raise ValueError("not a WhatsApp link")
    if os.name == "nt":
        os.startfile(url)  # noqa: S606 - the default program for https:// or whatsapp://
    else:
        webbrowser.open(url)
