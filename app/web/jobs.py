"""The background run that fetches waiting people's pages."""
from __future__ import annotations

import logging
import threading
from typing import Callable

from app.importer.fetch import FetchError, FetchSummary, PageFetcher, process_pending

log = logging.getLogger("app.jobs")


class FetchJob:
    def __init__(self, session_factory, fetcher_factory: Callable[[], PageFetcher]) -> None:
        self._session_factory = session_factory
        self._fetcher_factory = fetcher_factory
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.summary = FetchSummary()
        self.message = ""
        self.error = ""
        self.finished = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self.summary, self.message, self.error, self.finished = FetchSummary(), "", "", False
            self._thread = threading.Thread(target=self._run, name="fetch-job", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop.set()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def status(self) -> dict:
        s = self.summary
        return {
            "running": self.running, "finished": self.finished, "message": self.message, "error": self.error,
            "total": s.total, "done": s.done, "candidate": s.candidate, "not_relevant": s.not_relevant,
            "review": s.review, "failed": s.failed, "stopped": s.stopped,
        }

    def _run(self) -> None:
        fetcher = None
        session = self._session_factory()
        try:
            fetcher = self._fetcher_factory()
            process_pending(
                session, fetcher,
                on_progress=lambda s: setattr(self, "summary", s),
                on_message=lambda t: setattr(self, "message", t),
                should_stop=self._stop.is_set,
            )
        except FetchError as exc:
            self.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - never show details (may include page text)
            log.exception("fetch job failed")
            self.error = f"התקלה: {type(exc).__name__}"
        finally:
            session.close()
            if fetcher is not None:
                try:
                    fetcher.close()
                except Exception:  # noqa: BLE001
                    pass
            self.finished = True
