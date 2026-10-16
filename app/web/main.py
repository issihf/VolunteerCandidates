"""The local web app: candidates list, import, fetching, one candidate's page."""
from __future__ import annotations

import secrets
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import __version__
from app.config import APP_TITLE, PAAMONIM_BASE_URL
from app.domain.dates import format_date, today_il
from app.domain.phones import best_whatsapp_number
from app.importer import apply
from app.importer.list_import import import_list
from app.storage import models as m
from app.storage.db import init_db, make_engine, session_factory
from app.web.jobs import FetchJob

HERE = Path(__file__).parent
TABS = [
    (m.CANDIDATE, "מועמדים"),
    (m.PENDING, "ממתינים לשליפה"),
    (m.REVIEW, "לבדיקה"),
    (m.NOT_RELEVANT, "לא רלוונטי"),
    (m.FAILED, "נכשלו"),
]
MAX_UPLOAD = 20 * 1024 * 1024
COOKIE = "vc_key"


def create_app(*, engine=None, fetcher_factory: Callable | None = None, token: str | None = None) -> FastAPI:
    engine = engine or make_engine()
    init_db(engine)
    sessions = session_factory(engine)
    if fetcher_factory is None:
        def fetcher_factory():  # noqa: ANN202
            from app.importer.browser import PlaywrightFetcher
            return PlaywrightFetcher()

    app = FastAPI(title=APP_TITLE, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sessions = sessions
    app.state.job = FetchJob(sessions, fetcher_factory)
    app.state.token = token
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(app_title=APP_TITLE, version=__version__, base_url=PAAMONIM_BASE_URL,
                                 fmt_date=format_date, today=today_il)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def guard(request: Request, call_next):  # noqa: ANN202
        """Only the window we opened may talk to the server: a one-time key in a
        cookie, and no requests addressed to any other host name."""
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        expected = request.app.state.token
        if expected:
            key = request.query_params.get("k")
            if key and secrets.compare_digest(key, expected):
                resp = RedirectResponse(request.url.path or "/", status_code=303)
                resp.set_cookie(COOKIE, expected, httponly=True, samesite="strict")
                return resp
            if not secrets.compare_digest(request.cookies.get(COOKIE, ""), expected):
                return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)

    def db():  # noqa: ANN202
        return sessions()

    def counts(session: Session) -> dict[str, int]:
        rows = dict(session.execute(select(m.Candidate.state, func.count()).group_by(m.Candidate.state)).all())
        return {state: rows.get(state, 0) for state, _ in TABS}

    @app.get("/", include_in_schema=False)
    def home():  # noqa: ANN202
        return RedirectResponse("/candidates", status_code=303)

    @app.get("/candidates", response_class=HTMLResponse)
    def candidates(request: Request, tab: str = m.CANDIDATE, q: str = "", flash: str = ""):  # noqa: ANN202
        if tab not in m.STATES:
            tab = m.CANDIDATE
        with db() as session:
            stmt = select(m.Candidate).where(m.Candidate.state == tab)
            if q.strip():
                like = f"%{q.strip()}%"
                stmt = stmt.where(or_(m.Candidate.full_name.like(like), m.Candidate.phone_mobile.like(like),
                                      m.Candidate.list_mobile.like(like), m.Candidate.email.like(like),
                                      m.Candidate.list_email.like(like), m.Candidate.city.like(like)))
            stmt = stmt.order_by(m.Candidate.follow_up_date.is_(None), m.Candidate.follow_up_date, m.Candidate.id)
            rows = list(session.scalars(stmt))
            for r in rows:
                _ = r.status
            ctx = {"tab": tab, "tabs": TABS, "counts": counts(session), "rows": rows, "q": q, "flash": flash,
                   "job": app.state.job.status(), "pending": counts(session)[m.PENDING]}
        return templates.TemplateResponse(request, "candidates.html", ctx)

    @app.post("/import")
    async def do_import(file: UploadFile = File(...)):  # noqa: ANN202
        data = await file.read(MAX_UPLOAD + 1)
        if not data or len(data) > MAX_UPLOAD:
            return RedirectResponse("/candidates?flash=" + quote("הקובץ ריק או גדול מדי"), status_code=303)
        with db() as session:
            result = import_list(session, data)
            session.commit()
        if result.total == 0:
            msg = "לא נמצאו בקובץ שורות עם קישור לדף פעיל. האם זה קובץ אנשי הקשר?"
            return RedirectResponse("/candidates?flash=" + quote(msg), status_code=303)
        msg = (f"נקראו {result.total} שורות: {result.new} חדשים נוספו, {result.existing} כבר היו במערכת"
               + (f", {result.repeated} חזרו בקובץ" if result.repeated else ""))
        return RedirectResponse(f"/candidates?tab={m.PENDING}&flash=" + quote(msg), status_code=303)

    @app.post("/fetch")
    def start_fetch():  # noqa: ANN202
        app.state.job.start()
        return RedirectResponse(f"/candidates?tab={m.PENDING}", status_code=303)

    @app.post("/fetch/stop")
    def stop_fetch():  # noqa: ANN202
        app.state.job.stop()
        return RedirectResponse(f"/candidates?tab={m.PENDING}", status_code=303)

    @app.get("/fetch/status")
    def fetch_status():  # noqa: ANN202
        return JSONResponse(app.state.job.status())

    @app.get("/candidates/{cid}", response_class=HTMLResponse)
    def detail(request: Request, cid: int):  # noqa: ANN202
        with db() as session:
            cand = session.get(m.Candidate, cid)
            if cand is None:
                raise HTTPException(404, "המועמד לא נמצא")
            _ = cand.status
            wa = best_whatsapp_number(cand.phone_mobile, cand.list_mobile, cand.phone_work, cand.phone_work_direct,
                                      cand.phone_home)
            ctx = {"c": cand, "whatsapp": wa, "state_names": dict(TABS)}
        return templates.TemplateResponse(request, "detail.html", ctx)

    @app.post("/candidates/{cid}/resolve")
    def resolve(cid: int, to: str = Form(...)):  # noqa: ANN202
        with db() as session:
            cand = session.get(m.Candidate, cid)
            if cand is None:
                raise HTTPException(404)
            if to not in (m.CANDIDATE, m.NOT_RELEVANT):
                raise HTTPException(400)
            apply.resolve(session, cand, to)
            session.commit()
        return RedirectResponse(f"/candidates/{cid}", status_code=303)

    @app.post("/candidates/{cid}/refetch")
    def refetch(cid: int):  # noqa: ANN202
        with db() as session:
            cand = session.get(m.Candidate, cid)
            if cand is None:
                raise HTTPException(404)
            apply.request_refetch(cand)
            session.commit()
        return RedirectResponse(f"/candidates?tab={m.PENDING}", status_code=303)

    return app
