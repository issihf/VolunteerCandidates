import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from urllib.parse import unquote

from app.actions import settings as cfg
from app.actions.candidates import UserError
from app.messaging import fill as f
from app.messaging.outlook import FakeDrafter, MailError, text_to_html
from app.storage import models as m
from app.web.main import create_app
from tests.conftest import FakeFetcher
from tests.test_import_and_fetch import pages
from tests.test_web import upload


# ------------------------------------------------------------------ fill
def test_fill_text_and_leftovers():
    vals = f.candidate_values("דנה", "לוי", "")
    assert f.fill_text("שלום {{שם}} {{ שם_משפחה }}", vals) == "שלום דנה לוי"
    out = f.fill_text("מאת {{שם_מנהל_מפעמונים}}", vals)
    assert f.leftovers(out) == ["שם_מנהל_מפעמונים"]
    assert "שם_מנהל_מפעמונים" in f.missing_message(["שם_מנהל_מפעמונים"])
    assert "לא מוכר" in f.missing_message(["כלום"])


def test_fill_html_escapes_value_and_handles_split_tags():
    vals = {"שם": "<b>x</b>"}
    assert f.fill_html("<p>{{שם}}</p>", vals) == "<p>&lt;b&gt;x&lt;/b&gt;</p>"
    assert "דנה" in f.fill_html("{{<b>שם</b>}}", {"שם": "דנה"})


def test_whatsapp_link_styles():
    assert f.international("050-1234567") == "972501234567"
    api = f.whatsapp_link("0501234567", "שלום\nדנה")
    assert api.startswith("https://api.whatsapp.com/send?phone=972501234567&text=")
    assert "%0A" in api and "שלום" not in api
    assert f.whatsapp_link("0501234567", "x", "wa.me") == "https://wa.me/972501234567?text=x"
    assert f.whatsapp_link("0501234567", "x", "app").startswith("whatsapp://send?")


def test_text_to_html_escapes():
    assert "&lt;script&gt;" in text_to_html("<script>")


def test_fake_drafter_refuses_leftover():
    d = FakeDrafter()
    d.open_text_draft("נושא {{שם}}", "גוף {{שם}}", ["a@b.c"], {"שם": "דנה"})
    assert d.drafts[0]["subject"] == "נושא דנה"
    with pytest.raises(MailError) as e:
        d.open_text_draft("נושא", "גוף {{שם_מנהל_מפעמונים}}", ["a@b.c"], {})
    assert e.value.kind == "leftover"


# ------------------------------------------------------------------ template actions
def test_template_validation(session):
    ok = dict(name="ת", channel="whatsapp", subject="", neutral="שלום {{שם}}", male="", female="", enabled=True)
    t = cfg.add_template(session, **ok)
    assert t.body_neutral == "שלום {{שם}}"
    with pytest.raises(UserError):
        cfg.add_template(session, **{**ok, "neutral": ""})
    with pytest.raises(UserError, match="לא מוכר"):
        cfg.add_template(session, **{**ok, "neutral": "{{טעות}}"})
    with pytest.raises(UserError, match="נושא"):
        cfg.add_template(session, **{**ok, "channel": "email"})
    with pytest.raises(UserError):
        cfg.set_messaging(session, "x", "bogus")


# ------------------------------------------------------------------ web
@pytest.fixture()
def env(engine):
    drafter = FakeDrafter()
    app = create_app(engine=engine, fetcher_factory=lambda: FakeFetcher(pages()), drafter=drafter)
    with TestClient(app, follow_redirects=False) as c:
        upload(c)
        c.post("/fetch")
        app.state.job.wait(10)
        c.drafter = drafter
        with app.state.sessions() as s:
            c.cand = s.scalars(select(m.Candidate).where(m.Candidate.show_id == 1004)).one().id
        yield c


def add_tpl(c, **kw):
    data = dict(name="פנייה", channel="whatsapp", subject="", body_neutral="שלום {{שם}}, כאן {{שם_מנהל_מפעמונים}}",
                body_male="", body_female="")
    data.update(kw)
    r = c.post("/settings/templates/add", data=data)
    assert r.status_code == 303
    return r


def tid(c):
    with c.app.state.sessions() as s:
        return s.scalars(select(m.MessageTemplate).order_by(m.MessageTemplate.id.desc())).first().id


def test_settings_page_and_template_crud(env):
    add_tpl(env)
    page = env.get("/settings").text
    assert "פנייה" in page and "תבניות הודעות" in page
    t = tid(env)
    r = env.post(f"/settings/templates/{t}", data=dict(name="שונה-א", channel="whatsapp", body_neutral="א", enabled="1"))
    assert r.status_code == 303 and "שונה-א" in env.get("/settings").text
    bad = env.post(f"/settings/templates/{t}", data=dict(name="", channel="whatsapp", body_neutral="א"))
    assert "err=1" in bad.headers["location"]
    env.post(f"/settings/templates/{t}/delete")
    assert "שונה-א" not in env.get("/settings").text


def test_whatsapp_preview_needs_sender_then_works(env):
    add_tpl(env)
    t = tid(env)
    url = f"/candidates/{env.cand}/send/{t}"
    page = env.get(url).text
    assert "שם_מנהל_מפעמונים" in page and "פתיחה בווטסאפ" not in page  # refused: no sender name
    env.post("/settings/messaging", data=dict(sender_name="איסי", link_style="api"))
    page = env.get(url).text
    assert "פתיחה בווטסאפ" in page and "api.whatsapp.com/send?phone=972" in page
    assert "{{" not in page.split('id="msg"')[1].split("</textarea>")[0]


def test_variant_follows_gender(env):
    env.post("/settings/messaging", data=dict(sender_name="איסי", link_style="api"))
    add_tpl(env, body_neutral="ניטרלי", body_male="זכר", body_female="נקבה")
    t = tid(env)
    env.post(f"/candidates/{env.cand}/edit", data=dict(first_name="דנה", last_name="לוי", gender="female"))
    body = env.get(f"/candidates/{env.cand}/send/{t}").text.split('id="msg"')[1].split("</textarea>")[0]
    assert "נקבה" in body
    body = env.get(f"/candidates/{env.cand}/send/{t}?v=male").text.split('id="msg"')[1].split("</textarea>")[0]
    assert "זכר" in body


def test_email_draft_and_sent_logging(env):
    env.post("/settings/messaging", data=dict(sender_name="איסי", link_style="api"))
    add_tpl(env, name="מייל", channel="email", subject="שלום {{שם}}", body_neutral="גוף {{שם_מנהל_מפעמונים}}")
    t = tid(env)
    r = env.post(f"/candidates/{env.cand}/send/{t}/draft", data={"v": "neutral"})
    assert r.status_code == 303 and "err=1" not in r.headers["location"]
    assert len(env.drafter.drafts) == 1 and env.drafter.drafts[0]["to"]
    assert "איסי" in env.drafter.drafts[0]["html"]
    r = env.post(f"/candidates/{env.cand}/send/{t}/sent", data={"advance": "1"})
    assert r.headers["location"].startswith(f"/candidates/{env.cand}?flash=")
    with env.app.state.sessions() as s:
        cand = s.get(m.Candidate, env.cand)
        e = next(e for e in cand.journal if e.kind == m.J_OUT)
        assert e.channel == "email" and e.text == "תבנית: מייל"


def test_whatsapp_sent_advances_status(env):
    add_tpl(env)
    t = tid(env)
    env.post(f"/candidates/{env.cand}/send/{t}/sent", data={"advance": "1"})
    with env.app.state.sessions() as s:
        cand = s.get(m.Candidate, env.cand)
        assert any(e.kind == m.J_OUT and e.channel == "whatsapp" for e in cand.journal)
        assert cand.status.key == "whatsapp_sent"


def test_draft_failure_is_shown(engine):
    d = FakeDrafter(fail=MailError("unavailable", "אאוטלוק נפל"))
    app = create_app(engine=engine, fetcher_factory=lambda: FakeFetcher(pages()), drafter=d)
    with TestClient(app, follow_redirects=False) as c:
        upload(c)
        c.post("/fetch")
        app.state.job.wait(10)
        c.post("/settings/messaging", data=dict(sender_name="איסי", link_style="api"))
        c.post("/settings/templates/add", data=dict(name="מ", channel="email", subject="נ", body_neutral="ג"))
        with app.state.sessions() as s:
            cid = s.scalars(select(m.Candidate).where(m.Candidate.show_id == 1004)).one().id
            t = s.scalars(select(m.MessageTemplate)).one().id
        r = c.post(f"/candidates/{cid}/send/{t}/draft", data={"v": "neutral"})
        assert "err=1" in r.headers["location"] and "אאוטלוק נפל" in unquote(r.headers["location"])


def test_detail_lists_templates(env):
    add_tpl(env)
    assert "פנייה" in env.get(f"/candidates/{env.cand}").text


def test_old_variable_is_migrated(engine):
    from sqlalchemy.orm import Session
    from app.storage.db import ensure_defaults
    with Session(engine) as s:
        s.add(m.MessageTemplate(name="ישן", channel="whatsapp", body_neutral="כאן {{שם_פונה}}"))
        s.commit()
        ensure_defaults(s)
        s.commit()
        assert s.scalars(select(m.MessageTemplate)).one().body_neutral == "כאן {{שם_מנהל_מפעמונים}}"


def test_settings_page_has_unsaved_guard(env):
    page = env.get("/settings").text
    assert 'id="unsaved"' in page and "שם_מנהל_מפעמונים" in page
    assert page.count("<section") == 5 and "<section" not in page.split("</title>")[0]


# ------------------------------------------------------------------ stage 4a
def test_summary_text_and_page(env):
    add_tpl(env)
    t = tid(env)
    env.post(f"/candidates/{env.cand}/send/{t}/sent", data={"advance": "1"})
    env.post(f"/candidates/{env.cand}/note", data={"text": "ביקשה לחזור ביום ג"})
    page = env.get(f"/candidates/{env.cand}/summary").text
    assert "סיכום קשר" in page and "נשלחה פנייה בווטסאפ" in page and "ביקשה לחזור ביום ג" in page
    assert "תבנית: פנייה" in page and "/contacts/show/1004" in page


def test_delete_candidate_and_all(env):
    r = env.post(f"/candidates/{env.cand}/delete")
    assert r.status_code == 303
    with env.app.state.sessions() as s:
        assert s.get(m.Candidate, env.cand) is None
        assert s.scalars(select(m.JournalEntry)).first() is None
    bad = env.post("/settings/delete-all", data={"confirm": "x"})
    assert "err=1" in bad.headers["location"]
    with env.app.state.sessions() as s:
        assert s.scalars(select(m.Candidate)).first() is not None
    env.post("/settings/delete-all", data={"confirm": "מחיקה"})
    with env.app.state.sessions() as s:
        assert s.scalars(select(m.Candidate)).first() is None and s.scalars(select(m.Status)).first() is not None


def test_template_test_draft(env):
    env.post("/settings/messaging", data=dict(sender_name="איסי", link_style="api"))
    add_tpl(env, name="מייל", channel="email", subject="שלום {{שם}}", body_neutral="גוף {{שם_מנהל_מפעמונים}}", body_male="")
    t = tid(env)
    r = env.post(f"/settings/templates/{t}/test", data={"variant": "neutral", "to": "me@example.org"})
    assert "err=1" not in r.headers["location"]
    d = env.drafter.drafts[-1]
    assert d["to"] == ["me@example.org"] and d["subject"] == "שלום דנה" and "איסי" in d["html"]
    assert "err=1" in env.post(f"/settings/templates/{t}/test", data={"variant": "male"}).headers["location"]
    assert "err=1" in env.post(f"/settings/templates/{t}/test", data={"variant": "neutral", "to": "bad address"}).headers["location"]
    add_tpl(env, name="וו")
    assert "err=1" in env.post(f"/settings/templates/{tid(env)}/test", data={"variant": "neutral"}).headers["location"]
    assert "פתיחת טיוטת בדיקה" in env.get("/settings").text
