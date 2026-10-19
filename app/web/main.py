"""The local web app: candidates list, import, fetching, one candidate's page."""
from __future__ import annotations

import logging
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
from app.actions.summary import build_summary
from app.domain.dates import format_date, today_il
from app.domain.phones import best_whatsapp_number
from app import backup as bk
from app.config import data_dir
from app.importer import apply
from app.security import auth
from app.messaging import fill as mfill
from app.messaging.outlook import Drafter, MailError, default_drafter
from app.importer.list_import import import_list
from app.storage import models as m
from app.storage.db import get_default_follow_up_days, init_db, make_engine, session_factory
from app.web.external import open_external
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


def create_app(*, engine=None, fetcher_factory: Callable | None = None, token: str | None = None,
               drafter: Drafter | None = None, require_password: bool = False,
               opener: Callable[[str], None] | None = None) -> FastAPI:
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
    app.state.drafter = drafter or default_drafter()
    app.state.opener = opener or open_external
    app.state.lock = auth.Lock()
    app.state.require_password = require_password
    log = logging.getLogger(__name__)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(app_title=APP_TITLE, version=__version__, base_url=PAAMONIM_BASE_URL,
                                 fmt_date=format_date, today=today_il, require_password=require_password)
    templates.env.globals["backup_reminder"] = lambda: reminder_text()
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    OPEN_PATHS = ("/login", "/setup", "/static")

    @app.middleware("http")
    async def password_gate(request: Request, call_next):  # noqa: ANN202
        """With a password: nothing but the login pages works until it is entered, and the
        program locks itself after 15 idle minutes."""
        if not request.app.state.require_password or request.url.path.startswith(OPEN_PATHS):
            return await call_next(request)
        lock = request.app.state.lock
        if not lock.is_unlocked:
            if request.url.path == "/fetch/status":
                return JSONResponse({"error": "locked"}, status_code=403)
            with sessions() as session:
                target = "/login" if auth.has_password(session) else "/setup"
            return RedirectResponse(target, status_code=303)
        if request.url.path != "/fetch/status":
            lock.touch()
        return await call_next(request)

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
                   due: str = "", err: str = ""):  # noqa: ANN202
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
                   "due_count": due_count, "statuses": cfg.ordered_statuses(session), "err": bool(err),
                   "msg_templates": [t for t in cfg.ordered_templates(session) if t.enabled], "channels": m.CHANNELS}
        return templates.TemplateResponse(request, "candidates.html", ctx)

    @app.post("/import")
    async def do_import(file: UploadFile = File(...)):  # noqa: ANN202
        data = await file.read(MAX_UPLOAD + 1)
        if not data or len(data) > MAX_UPLOAD:
            return RedirectResponse("/candidates?flash=" + quote("הקובץ ריק או גדול מדי"), status_code=303)
        auto_backup("import")
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
                   "msg_templates": [t for t in cfg.ordered_templates(session) if t.enabled],
                   "statuses": cfg.ordered_statuses(session), "status_options": options, "journal": journal, "channels": m.CHANNELS,
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

    def back_to(cid: int, next_url: str, message: str, err: bool) -> RedirectResponse:
        """Back to the page the action was started from (the list or the candidate), with the message."""
        target = f"/candidates/{cid}"
        nxt = (next_url or "").strip()
        if nxt.startswith("/candidates") and "//" not in nxt and "\\" not in nxt and "\n" not in nxt:
            base, _, query = nxt.partition("?")
            pairs = [p for p in query.split("&") if p and not p.startswith(("flash=", "err="))]
            target = base + ("?" + "&".join(pairs) if pairs else "")
        sep = "&" if "?" in target else "?"
        return RedirectResponse(f"{target}{sep}flash={quote(message)}{'&err=1' if err else ''}", status_code=303)

    def do(cid: int, fn: Callable, next_url: str = "") -> RedirectResponse:
        """Run an action on a candidate; show its message or the problem on the page it came from."""
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
        return back_to(cid, next_url, message, bool(err))

    @app.post("/candidates/{cid}/status")
    def post_status(cid: int, status_id: int = Form(...), follow_up: str = Form(""), note: str = Form(""),
                    next: str = Form("")):  # noqa: ANN202
        def run(session, cand):  # noqa: ANN001, ANN202
            status = act.change_status(session, cand, status_id, follow_up=parse_date(follow_up), note=note)
            return f"הסטטוס עודכן ל\"{status.name}\""
        return do(cid, run, next)

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
    def post_contact(cid: int, direction: str = Form(...), channel: str = Form("other"), on: str = Form(""),
                     text: str = Form(""), advance: str = Form(""), next: str = Form("")):  # noqa: ANN202
        def run(session, cand):  # noqa: ANN001, ANN202
            if direction == "note":
                act.add_note(session, cand, text, parse_date(on))
                return "ההערה נוספה ליומן"
            return act.log_contact(session, cand, direction, channel, on=parse_date(on), text=text,
                                   advance=bool(advance))
        return do(cid, run, next)

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
                   "default_days": get_default_follow_up_days(session),
                   "msg_templates": cfg.ordered_templates(session),
                   "sender_name": cfg.get_setting(session, "sender_name"),
                   "link_style": cfg.get_setting(session, "whatsapp_link_style", "api"),
                   "link_styles": mfill.LINK_STYLES, "template_channels": m.TEMPLATE_CHANNELS,
                   "variables": mfill.VARIABLE_HELP,
                   "backups": bk.list_backups() if app.state.require_password else [],
                   "backup_dir": str(bk.backups_dir()), "backup_kinds": bk.KINDS,
                   "last_copy": cfg.get_setting(session, "last_external_copy")}
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

    # ------------------------------------------------------- message templates (settings)
    @app.post("/settings/messaging")
    def post_messaging(sender_name: str = Form(""), link_style: str = Form("api")):  # noqa: ANN202
        return settings_do(lambda session: (cfg.set_messaging(session, sender_name, link_style), "ההגדרות נשמרו")[1])

    @app.post("/settings/templates/add")
    def post_template_add(name: str = Form(""), channel: str = Form("whatsapp"), subject: str = Form(""),
                          body_neutral: str = Form(""), body_male: str = Form(""), body_female: str = Form(""),
                          enabled: str = Form("1")):  # noqa: ANN202
        return settings_do(lambda session: (cfg.add_template(
            session, name=name, channel=channel, subject=subject, neutral=body_neutral, male=body_male,
            female=body_female, enabled=bool(enabled)), "התבנית נוספה")[1])

    @app.post("/settings/templates/{tid}")
    def post_template_update(tid: int, name: str = Form(""), channel: str = Form("whatsapp"), subject: str = Form(""),
                             body_neutral: str = Form(""), body_male: str = Form(""), body_female: str = Form(""),
                             enabled: str = Form("")):  # noqa: ANN202
        return settings_do(lambda session: (cfg.update_template(
            session, tid, name=name, channel=channel, subject=subject, neutral=body_neutral, male=body_male,
            female=body_female, enabled=bool(enabled)), "התבנית נשמרה")[1])

    @app.post("/settings/templates/{tid}/delete")
    def post_template_delete(tid: int):  # noqa: ANN202
        return settings_do(lambda session: (cfg.delete_template(session, tid), "התבנית נמחקה")[1])

    # --------------------------------------------------------------- preparing a message
    def prepare(session: Session, cand: m.Candidate, tpl: m.MessageTemplate, variant: str) -> dict:
        """Everything the preview shows. Problems are returned as `problem`, not raised."""
        sender = cfg.get_setting(session, "sender_name")
        values = mfill.candidate_values(cand.first_name, cand.last_name, sender)
        available = [(k, label) for k, label in (("neutral", "ניטרלי"), ("male", "לשון זכר"), ("female", "לשון נקבה"))
                     if tpl.body_for(k).strip()]
        keys = [k for k, _ in available]
        preferred = cand.gender if cand.gender in keys else "neutral"
        chosen = variant if variant in keys else (preferred if preferred in keys else (keys[0] if keys else "neutral"))
        subject = mfill.fill_text(tpl.subject, values) if tpl.channel == "email" else ""
        text = mfill.fill_text(tpl.body_for(chosen), values)
        left = list(dict.fromkeys(mfill.leftovers(subject) + mfill.leftovers(text)))
        problem, link, target = "", "", ""
        if left:
            problem = mfill.missing_message(left)
        elif tpl.channel == "whatsapp":
            number = best_whatsapp_number(cand.phone_mobile, cand.list_mobile, cand.phone_work,
                                          cand.phone_work_direct, cand.phone_home)
            if not number:
                problem = "אין למועמד/ת מספר נייד לוואטסאפ."
            else:
                target = number
                link = mfill.whatsapp_link(number, text, cfg.get_setting(session, "whatsapp_link_style", "api"))
        else:
            target = cand.display_email
            if not target:
                problem = "אין למועמד/ת כתובת מייל."
        return {"c": cand, "t": tpl, "variants": available, "variant": chosen, "subject": subject, "text": text,
                "problem": problem, "link": link, "target": target,
                "gender_missing": cand.gender not in ("male", "female") and len(keys) > 1}

    def load_pair(session: Session, cid: int, tid: int):  # noqa: ANN202
        cand, tpl = session.get(m.Candidate, cid), session.get(m.MessageTemplate, tid)
        if cand is None or tpl is None:
            raise HTTPException(404)
        return cand, tpl

    @app.get("/candidates/{cid}/send/{tid}", response_class=HTMLResponse)
    def send_preview(request: Request, cid: int, tid: int, v: str = "", flash: str = "", err: str = ""):  # noqa: ANN202
        with db() as session:
            cand, tpl = load_pair(session, cid, tid)
            ctx = prepare(session, cand, tpl, v)
            ctx.update(flash=flash, err=bool(err), channels=m.CHANNELS, drafter_ok=app.state.drafter.available)
        return templates.TemplateResponse(request, "send.html", ctx)

    @app.post("/candidates/{cid}/send/{tid}/open")
    def send_open(cid: int, tid: int, v: str = Form("")):  # noqa: ANN202
        """Open the filled-in WhatsApp message in the default program (not inside the app window)."""
        with db() as session:
            cand, tpl = load_pair(session, cid, tid)
            ctx = prepare(session, cand, tpl, v)
        if ctx["problem"] or tpl.channel != "whatsapp" or not ctx["link"]:
            return JSONResponse({"ok": False, "error": ctx["problem"] or "התבנית אינה תבנית ווטסאפ"}, status_code=400)
        try:
            app.state.opener(ctx["link"])
        except Exception:  # noqa: BLE001
            log.warning("could not open WhatsApp", exc_info=True)
            return JSONResponse({"ok": False, "error": "לא הצלחתי לפתוח את ווטסאפ"}, status_code=500)
        return {"ok": True}

    @app.post("/candidates/{cid}/whatsapp")
    def open_whatsapp_chat(cid: int):  # noqa: ANN202
        """Open an empty WhatsApp chat with the candidate (the link next to the mobile number)."""
        with db() as session:
            cand = session.get(m.Candidate, cid)
            if cand is None:
                raise HTTPException(404, "המועמד לא נמצא")
            number = best_whatsapp_number(cand.phone_mobile, cand.list_mobile, cand.phone_work, cand.phone_work_direct,
                                          cand.phone_home)
        if not number:
            return JSONResponse({"ok": False, "error": "אין מספר נייד"}, status_code=400)
        try:
            app.state.opener(f"https://wa.me/{number}")
        except Exception:  # noqa: BLE001
            log.warning("could not open WhatsApp", exc_info=True)
            return JSONResponse({"ok": False, "error": "לא הצלחתי לפתוח את ווטסאפ"}, status_code=500)
        return {"ok": True}

    @app.post("/candidates/{cid}/send/{tid}/draft")
    def send_draft(cid: int, tid: int, v: str = Form("")):  # noqa: ANN202
        with db() as session:
            cand, tpl = load_pair(session, cid, tid)
            ctx = prepare(session, cand, tpl, v)
            back = f"/candidates/{cid}/send/{tid}?v={ctx['variant']}"
            if ctx["problem"] or tpl.channel != "email":
                return RedirectResponse(back + "&err=1&flash=" + quote(ctx["problem"] or "התבנית אינה תבנית מייל"),
                                        status_code=303)
            try:
                app.state.drafter.open_text_draft(tpl.subject, tpl.body_for(ctx["variant"]), [ctx["target"]],
                                                  mfill.candidate_values(cand.first_name, cand.last_name,
                                                                         cfg.get_setting(session, "sender_name")))
            except MailError as exc:
                return RedirectResponse(back + "&err=1&flash=" + quote(exc.message), status_code=303)
        return RedirectResponse(back + "&flash=" + quote("הטיוטה נפתחה ב-Outlook. אחרי השליחה אפשר לתעד כאן שנשלחה."),
                                status_code=303)

    @app.post("/candidates/{cid}/send/{tid}/sent")
    def send_sent(cid: int, tid: int, on: str = Form(""), advance: str = Form("")):  # noqa: ANN202
        with db() as session:
            cand, tpl = load_pair(session, cid, tid)
            try:
                message = act.log_contact(session, cand, m.J_OUT, tpl.channel, on=parse_date(on),
                                          text=f"תבנית: {tpl.name}", advance=bool(advance))
                session.commit()
            except act.UserError as exc:
                session.rollback()
                return RedirectResponse(f"/candidates/{cid}/send/{tid}?err=1&flash=" + quote(str(exc)), status_code=303)
        return RedirectResponse(f"/candidates/{cid}?flash=" + quote(message), status_code=303)

    # ------------------------------------------------------------- summary and delete
    @app.get("/candidates/{cid}/summary", response_class=HTMLResponse)
    def summary(request: Request, cid: int):  # noqa: ANN202
        with db() as session:
            cand = session.get(m.Candidate, cid)
            if cand is None:
                raise HTTPException(404)
            _ = cand.status
            ctx = {"c": cand, "summary": build_summary(cand)}
        return templates.TemplateResponse(request, "summary.html", ctx)

    @app.get("/candidates/{cid}/summary.txt")
    def summary_txt(cid: int):  # noqa: ANN202
        from fastapi.responses import PlainTextResponse
        with db() as session:
            cand = session.get(m.Candidate, cid)
            if cand is None:
                raise HTTPException(404)
            _ = cand.status
            return PlainTextResponse(build_summary(cand), headers={"Cache-Control": "no-store"})

    @app.post("/candidates/{cid}/delete")
    def post_delete(cid: int):  # noqa: ANN202
        with db() as session:
            cand = session.get(m.Candidate, cid)
            if cand is None:
                raise HTTPException(404)
            auto_backup("delete")
            act.delete_candidate(session, cand)
            session.commit()
        return RedirectResponse("/candidates?flash=" + quote("המועמד/ת נמחק/ה"), status_code=303)

    @app.post("/settings/delete-all")
    def post_delete_all(confirm: str = Form("")):  # noqa: ANN202
        def run(session):  # noqa: ANN001, ANN202
            if confirm.strip() == "מחיקה":
                auto_backup("delete", required=True)
            n = act.delete_everything(session, confirm)
            return f"נמחקו {n} מועמדים וכל הנתונים שלהם"
        return settings_do(run)

    # ------------------------------------------------------- password, lock, backups
    def secret() -> str | None:
        return app.state.lock.secret

    def auto_backup(kind: str, *, required: bool = False) -> None:
        """Encrypted backup before a risky step. Without a password in memory there is nothing to
        encrypt with: skipped. A failure stops steps that destroy data (required=True)."""
        pw = secret()
        if pw is None:
            return
        try:
            bk.create_backup(engine, pw, kind)
        except Exception as exc:  # noqa: BLE001
            log.warning("backup failed: %s", type(exc).__name__)
            if required:
                raise act.UserError("לא הצלחנו ליצור גיבוי לפני הפעולה, ולכן היא לא בוצעה") from None

    def daily_backup() -> None:
        try:
            if secret() and not bk.has_backup_today():
                bk.create_backup(engine, secret(), "daily")
        except Exception as exc:  # noqa: BLE001
            log.warning("daily backup failed: %s", type(exc).__name__)

    def reminder_text() -> str:
        """A gentle weekly nudge to copy the encrypted backups out of this computer."""
        if not app.state.require_password:
            return ""
        with sessions() as session:
            row = session.get(m.Setting, "last_external_copy")
            if session.scalar(select(func.count()).select_from(m.Candidate)) == 0:
                return ""
            try:
                last = date.fromisoformat(row.value) if row and row.value else None
            except ValueError:
                last = None
        if last is None or (today_il() - last).days >= 7:
            return "כדאי להעתיק את קבצי הגיבוי לכונן חיצוני (הגדרות > גיבוי)."
        return ""

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request, flash: str = ""):  # noqa: ANN202
        with db() as session:
            if auth.has_password(session):
                return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "password.html", {"mode": "setup", "flash": flash, "err": bool(flash), "bare": True})

    @app.post("/setup")
    def setup_post(password: str = Form(""), again: str = Form("")):  # noqa: ANN202
        with db() as session:
            if auth.has_password(session):
                return RedirectResponse("/login", status_code=303)
            try:
                auth.check_new_password(password, again)
            except auth.AuthError as exc:
                return RedirectResponse("/setup?flash=" + quote(str(exc)), status_code=303)
            auth.set_password(session, password)
            session.commit()
        app.state.lock.unlock(password)
        daily_backup()
        return RedirectResponse("/candidates", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, flash: str = ""):  # noqa: ANN202
        with db() as session:
            if not auth.has_password(session):
                return RedirectResponse("/setup", status_code=303)
        return templates.TemplateResponse(request, "password.html", {"mode": "login", "flash": flash, "err": bool(flash), "bare": True})

    @app.post("/login")
    def login_post(password: str = Form("")):  # noqa: ANN202
        lock = app.state.lock
        wait = lock.wait_seconds()
        if wait:
            return RedirectResponse("/login?flash=" + quote(f"יותר מדי ניסיונות. אפשר לנסות שוב בעוד {wait} שניות"), status_code=303)
        with db() as session:
            ok = auth.verify(session, password)
        if not ok:
            lock.failed()
            return RedirectResponse("/login?flash=" + quote("הסיסמה שגויה"), status_code=303)
        lock.unlock(password)
        daily_backup()
        return RedirectResponse("/candidates", status_code=303)

    @app.post("/lock")
    def lock_now():  # noqa: ANN202
        app.state.lock.lock()
        return RedirectResponse("/login", status_code=303)

    @app.post("/settings/password")
    def post_password(old: str = Form(""), password: str = Form(""), again: str = Form("")):  # noqa: ANN202
        def run(session):  # noqa: ANN001, ANN202
            if not auth.verify(session, old):
                raise act.UserError("הסיסמה הנוכחית שגויה")
            try:
                auth.check_new_password(password, again)
            except auth.AuthError as exc:
                raise act.UserError(str(exc)) from None
            auth.set_password(session, password)
            app.state.lock.secret = password
            return "הסיסמה הוחלפה. גיבויים קודמים נשארים מוצפנים בסיסמה הישנה."
        return settings_do(run)

    @app.post("/settings/backup/now")
    def post_backup_now():  # noqa: ANN202
        def run(session):  # noqa: ANN001, ANN202
            if secret() is None:
                raise act.UserError("גיבוי מוצפן זמין רק כשהתוכנה מוגנת בסיסמה")
            auto_backup("manual", required=True)
            return "הגיבוי נוצר"
        return settings_do(run)

    @app.post("/settings/backup/restore")
    def post_restore(name: str = Form(""), password: str = Form("")):  # noqa: ANN202
        def run(session):  # noqa: ANN001, ANN202
            session.rollback()
            path = bk.backups_dir() / name
            if not (bk.parse_name(name) and path.is_file()):
                raise act.UserError("הגיבוי לא נמצא")
            try:
                bk.decrypt(path.read_bytes(), password)
            except bk.BackupError as exc:
                raise act.UserError(str(exc)) from None
            auto_backup("restore", required=True)
            try:
                bk.restore_backup(engine, name, password)
                init_db(engine)
            except bk.BackupError as exc:
                raise act.UserError(str(exc)) from None
            return "הגיבוי שוחזר. אם הסיסמה בגיבוי שונה, בכניסה הבאה תהיה הסיסמה של אותו זמן."
        return settings_do(run)

    @app.post("/settings/external-copy")
    def post_external_copy():  # noqa: ANN202
        def run(session):  # noqa: ANN001, ANN202
            row = session.get(m.Setting, "last_external_copy")
            if row is None:
                session.add(m.Setting(key="last_external_copy", value=today_il().isoformat()))
            else:
                row.value = today_il().isoformat()
            return "נרשם שהעתקת לכונן חיצוני"
        return settings_do(run)

    @app.post("/settings/templates/{tid}/test")
    def post_template_test(tid: int, variant: str = Form("neutral"), to: str = Form("")):  # noqa: ANN202
        """Open a sample draft of an email template in Outlook, without any candidate: made-up
        name and family name, the real manager name from the settings."""
        def run(session):  # noqa: ANN001, ANN202
            tpl = session.get(m.MessageTemplate, tid)
            if tpl is None:
                raise act.UserError("התבנית לא נמצאה")
            if tpl.channel != "email":
                raise act.UserError("בדיקה ב-Outlook זמינה לתבניות מייל בלבד")
            body = tpl.body_for(variant)
            if not body.strip():
                raise act.UserError("הנוסח שנבחר ריק בתבנית הזו")
            sender = cfg.get_setting(session, "sender_name") or "(שם המנהל/ת)"
            values = mfill.candidate_values("דנה", "כהן", sender)
            address = to.strip()
            if address and ("@" not in address or " " in address):
                raise act.UserError("כתובת המייל אינה תקינה")
            try:
                app.state.drafter.open_text_draft(tpl.subject, body, [address] if address else [], values)
            except MailError as exc:
                raise act.UserError(exc.message) from None
            return "טיוטת בדיקה נפתחה ב-Outlook (השם בטיוטה: דנה כהן). היא לא נשלחת ולא נרשמת ביומן."
        return settings_do(run)

    return app
