"""Outlook drafts from templates written in the system.

The system never sends: it creates a draft with the person's address in "To",
shows it, and the manager presses "Send". Only this file talks to Windows (COM,
through pywin32). Tests use FakeDrafter. No names, addresses or message text are
written to the system log.
"""
from __future__ import annotations

import html as _html
import logging
import sys
import threading

from app.messaging.fill import fill_html, fill_text, leftovers, missing_message

log = logging.getLogger(__name__)

OL_NORMAL_WINDOW = 2
TIMEOUT_SECONDS = 25


class MailError(Exception):
    """kind: unavailable / not_responding / template_missing / leftover."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def allow_foreground() -> None:
    """Windows lets only the program in front bring a window forward; pass that right to Outlook."""
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception:  # noqa: BLE001 - not on Windows: the draft still opens
        pass


def text_to_html(text: str) -> str:
    """Plain text as the HTML body of a draft: escaped, line breaks kept, right to left."""
    paragraphs = _html.escape((text or "").replace("\r\n", "\n").strip()).split("\n")
    return '<div dir="rtl" style="text-align:right">' + "<br>\n".join(paragraphs) + "</div>"


def fill_text_item(subject: str, body_text: str, values: dict[str, str]) -> tuple[str, str]:
    """Subject and HTML body, filled; refuses if a variable is left."""
    if not (subject or "").strip() or not (body_text or "").strip():
        raise MailError("template_missing", "בתבנית חסרים נושא או גוף ההודעה. אפשר להשלים בהגדרות.")
    new_subject = fill_text(subject, values)
    new_body = fill_html(text_to_html(body_text), values)
    left = list(dict.fromkeys(leftovers(new_subject) + leftovers(new_body, is_html=True)))
    if left:
        raise MailError("leftover", missing_message(left))
    return new_subject, new_body


class Drafter:
    available = False

    def open_text_draft(self, subject: str, body_text: str, to: list[str], values: dict[str, str]) -> None:
        raise NotImplementedError


class UnavailableDrafter(Drafter):
    """Not on Windows, or pywin32 is missing: say so (the text can be copied instead)."""

    def open_text_draft(self, subject: str, body_text: str, to: list[str], values: dict[str, str]) -> None:
        fill_text_item(subject, body_text, values)
        raise MailError("unavailable", "Outlook לא זמין במחשב הזה, ולכן הטיוטה לא נפתחה. "
                                       "אפשר להעתיק את הנושא והטקסט ולפתוח הודעה ידנית.")


class OutlookDrafter(Drafter):
    """Classic Outlook through COM. Every call runs in its own thread with a time limit,
    so a hanging Outlook does not freeze the system."""

    available = True

    def _run(self, work):  # noqa: ANN001, ANN202
        result: dict = {}

        def target() -> None:
            import pythoncom  # pywin32

            pythoncom.CoInitialize()
            try:
                result["value"] = work()
            except MailError as e:
                result["error"] = e
            except Exception as e:  # noqa: BLE001 - COM errors come in many types
                code = getattr(e, "hresult", None)
                log.warning("outlook call failed: %s%s", type(e).__name__, f" hresult={code}" if code is not None else "")
                result["error"] = MailError(
                    "unavailable", "לא הצלחנו לפתוח את Outlook. כדאי לוודא ש-Outlook הקלאסי פתוח ולנסות שוב, "
                                   "או להעתיק את הנושא והטקסט ולפתוח הודעה ידנית.")
            finally:
                pythoncom.CoUninitialize()

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(TIMEOUT_SECONDS)
        if t.is_alive():
            log.warning("outlook call still running after %ss", TIMEOUT_SECONDS)
            raise MailError("not_responding", "Outlook לא מגיב. אפשר לסגור חלונות פתוחים ב-Outlook ולנסות שוב.")
        if "error" in result:
            raise result["error"]
        return result.get("value")

    @staticmethod
    def _outlook():  # noqa: ANN205
        import win32com.client  # pywin32

        return win32com.client.Dispatch("Outlook.Application")

    def open_text_draft(self, subject: str, body_text: str, to: list[str], values: dict[str, str]) -> None:
        new_subject, new_body = fill_text_item(subject, body_text, values)

        def work() -> None:
            item = self._outlook().CreateItem(0)  # olMailItem
            item.Subject = new_subject
            if to:
                item.To = "; ".join(to)
            allow_foreground()
            item.Display(False)  # showing it first lets Outlook add the signature
            try:
                item.HTMLBody = new_body + (item.HTMLBody or "")
            except Exception as e:  # noqa: BLE001 - without the signature the draft is still right
                log.info("outlook text draft: signature kept out: %s", type(e).__name__)
                item.HTMLBody = new_body
            try:
                inspector = item.GetInspector
                inspector.WindowState = OL_NORMAL_WINDOW
                inspector.Activate()
            except Exception as e:  # noqa: BLE001 - only cosmetic
                log.info("outlook draft shown, bringing it forward failed: %s", type(e).__name__)
            log.info("outlook text draft shown")

        self._run(work)


class FakeDrafter(Drafter):
    """For tests and demos without Outlook: every draft is remembered."""

    available = True

    def __init__(self, fail: MailError | None = None):
        self.fail = fail
        self.drafts: list[dict] = []

    def open_text_draft(self, subject: str, body_text: str, to: list[str], values: dict[str, str]) -> None:
        if self.fail:
            raise self.fail
        new_subject, new_body = fill_text_item(subject, body_text, values)
        self.drafts.append({"to": list(to), "subject": new_subject, "html": new_body})


def default_drafter() -> Drafter:
    if sys.platform != "win32":
        return UnavailableDrafter()
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError:
        return UnavailableDrafter()
    return OutlookDrafter()
