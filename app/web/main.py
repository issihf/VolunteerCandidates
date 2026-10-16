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
from app.actions import candidates as act
from app.actions import settings as cfg
from app.domain.dates import format_date, today_il
from app.domain.phones import best_whatsapp_number
from app.importer import apply
from app.importer.list_import import import_list
from app.storage import models as m
from app.storage.db import get_default_follow_up_days, init_db, make_engine, session_factory
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
    def candidates(request: Request, tab: str = m.CANDIDATE, q: str = "", flash: str = "", s: str = "",
                   due: str = ""):  # noqa: ANN202
        if tab not in m.STATES:
            tab = m.CANDIDATE
        with db() as session:
            stmt = select(m.Candidate).where(m.Candidate.state == tab)
            if tab == m.CANDIDATE:
                if s == "all":
                    pass
                elif s == "final":
                    stmt = stmt.where(m.Candidate.status_id.in_(select(m.Status.id).where(m.Status.kind == "final")))
                elif s.isdigit():
                    stmt = stmt.where(m.Candidate.status_id == int(s))
                else:  # default: people still in the process
                    stmt = stmt.where(or_(m.Candidate.status_id.is_(None), m.Candidate.status_id.in_(
                        select(m.Status.id).where(m.Status.kind != "final"))))
                if due:
                    stmt = stmt.where(m.Candidate.follow_up_date.is_not(None), m.Candidate.follow_up_date <= today_il())
            if q.strip():
                like = f"%{q.strip()}%"
                stmt = stmt.where(or_(m.Candidate.full_name.like(like), m.Candidate.phone_mobile.like(like),
                                      m.Candidate.list_mobile.like(like), m.Candidate.email.like(like),
                                      m.Candidate.list_email.like(like), m.Candidate.city.like(like)))
            stmt = stmt.order_by(m.Candidate.follow_up_date.is_(None), m.Candidate.follow_up_date, m.Candidate.id)
            rows = list(session.scalars(stmt))
            for r in rows:
                _ = r.status
            all_counts = counts(session)
            due_count = session.scalar(select(func.count()).select_from(m.Candidate).where(
                m.Candidate.state == m.CANDIDATE, m.Candidate.follow_up_date.is_not(None),
                m.Candidate.follow_up_date <= today_il(),
                or_(m.Candidate.status_id.is_(None), m.Candidate.status_id.in_(
                    select(m.Status.id).where(m.Status.kind != "final")))))
            ctx = {"tab": tab, "tabs": TABS, "counts": all_counts, "rows": rows, "q": q, "flash": flash,
                   "job": app.state.job.status(), "pending": all_counts[m.PENDING], "s": s, "due": due,
                   "due_count": due_count, "statuses": cfg.ordered_statuses(session)}
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
    def detail(request: Request, cid: int, flash: str = "", err: str = ""):  # noqa: ANN202
        with db() as session:
            cand = session.get(m.Candidate, cid)
            if cand is None:
                raise HTTPException(404, "המועמד לא נמצא")
            _ = cand.status
            wa = best_whatsapp_number(cand.phone_mobile, cand.list_mobile, cand.phone_work, cand.phone_work_direct,
                                      cand.phone_home)
            options = [st for st in cfg.ordered_statuses(session) if st.enabled or st.id == cand.status_id]
            journal = list(cand.journal)
            for e in journal:
                _ = e.status
            ctx = {"c": cand, "whatsapp": wa, "state_names": dict(TABS), "flash": flash, "err": bool(err),
                   "status_options": options, "journal": journal, "channels": m.CHANNELS,
                   "kind_names": {m.J_STATUS: "סטטוס", m.J_NOTE: "הערה", m.J_OUT: "פנינו", m.J_IN: "התקבלה תשובה"}}
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

    # ----------------------------------------------------------- candidate actions
    def parse_date(text: str) -> date | None:
        text = (text or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            raise act.UserError("התאריך אינו תקין") from None

    def do(cid: int, fn: Callable) -> RedirectResponse:
        """Run an action on a candidate; show its message or the problem on the candidate's page."""
        with db() as session:
            cand = session.get(m.Candidate, cid)
            if cand is None:
                raise HTTPException(404, "המועמד לא נמצא")
            try:
                message = fn(session, cand) or "נשמר"
                session.commit()
                err = ""
            except act.UserError as exc:
                session.rollback()
                message, err = str(exc), "&err=1"
        return RedirectResponse(f"/candidates/{cid}?flash={quote(message)}{err}", status_code=303)

    @app.post("/candidates/{cid}/status")
    def post_status(cid: int, status_id: int = Form(...), follow_up: str = Form(""), note: str = Form("")):  # noqa: ANN202
        def run(session, cand):  # noqa: ANN001, ANN202
            status = act.change_status(session, cand, status_id, follow_up=parse_date(follow_up), note=note)
            return f"הסטטוס עודכן ל\"{status.name}\""
        return do(cid, run)

    @app.post("/candidates/{cid}/follow-up")
    def post_follow_up(cid: int, follow_up: str = Form(""), clear: str = Form("")):  # noqa: ANN202
        def run(session, cand):  # noqa: ANN001, ANN202
            act.set_follow_up(cand, None if clear else parse_date(follow_up))
            return "תאריך המעקב עודכן"
        return do(cid, run)

    @app.post("/candidates/{cid}/edit")
    def post_edit(cid: int, first_name: str = Form(""), last_name: str = Form(""), gender: str = Form("")):  # noqa: ANN202
        def run(session, cand):  # noqa: ANN001, ANN202
            act.edit_person(cand, first_name, last_name, gender)
            return "הפרטים נשמרו"
        return do(cid, run)

    @app.post("/candidates/{cid}/note")
    def post_note(cid: int, text: str = Form(""), on: str = Form("")):  # noqa: ANN202
        def run(session, cand):  # noqa: ANN001, ANN202
            act.add_note(session, cand, text, parse_date(on))
            return "ההערה נוספה ליומן"
        return do(cid, run)

    @app.post("/candidates/{cid}/contact")
    def post_contact(cid: int, direction: str = Form(...), channel: str = Form(...), on: str = Form(""),
                     text: str = Form(""), advance: str = Form("")):  # noqa: ANN202
        def run(session, cand):  # noqa: ANN001, ANN202
            return act.log_contact(session, cand, direction, channel, on=parse_date(on), text=text,
                                   advance=bool(advance))
        return do(cid, run)

    # ------------------------------------------------------------------ settings
    def settings_redirect(message: str = "", err: bool = False) -> RedirectResponse:
        extra = "&err=1" if err else ""
        return RedirectResponse(f"/settings?flash={quote(message)}{extra}" if message else "/settings", status_code=303)

    def settings_do(fn: Callable) -> RedirectResponse:
        with db() as session:
            try:
                message = fn(session) or "נשמר"
                session.commit()
                return settings_redirect(message)
            except act.UserError as exc:
                session.rollback()
                return settings_redirect(str(exc), err=True)

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, flash: str = "", err: str = ""):  # noqa: ANN202
        with db() as session:
            rows = cfg.ordered_statuses(session)
            used = dict(session.execute(select(m.Candidate.status_id, func.count()).group_by(m.Candidate.status_id)).all())
            ctx = {"statuses": rows, "used": used, "flash": flash, "err": bool(err),
                   "default_days": get_default_follow_up_days(session)}
        return templates.TemplateResponse(request, "settings.html", ctx)

    @app.post("/settings/status/{sid}")
    def post_status_update(sid: int, name: str = Form(""), kind: str = Form("active"), follow_up_days: str = Form(""),
                           requires_date: str = Form(""), enabled: str = Form("")):  # noqa: ANN202
        return settings_do(lambda session: (cfg.update_status(
            session, sid, name=name, kind=kind, follow_up_days=follow_up_days,
            requires_date=bool(requires_date), enabled=bool(enabled)), "הסטטוס נשמר")[1])

    @app.post("/settings/add-status")
    def post_status_add(name: str = Form(""), kind: str = Form("active")):  # noqa: ANN202
        return settings_do(lambda session: (cfg.add_status(session, name=name, kind=kind), "הסטטוס נוסף")[1])

    @app.post("/settings/status/{sid}/move")
    def post_status_move(sid: int, direction: str = Form("up")):  # noqa: ANN202
        return settings_do(lambda session: (cfg.move_status(session, sid, direction), "הסדר עודכן")[1])

    @app.post("/settings/default-days")
    def post_default_days(days: str = Form("")):  # noqa: ANN202
        return settings_do(lambda session: (cfg.set_default_days(session, days), "ברירת המחדל עודכנה")[1])

    return app
