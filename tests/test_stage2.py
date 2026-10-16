from datetime import timedelta

import pytest
from sqlalchemy import inspect, select, text

from app.actions import candidates as act
from app.actions import settings as cfg
from app.domain.dates import today_il
from app.importer.apply import apply_profile, status_by_key
from app.importer.fetch import process_pending
from app.importer.list_import import import_list
from app.domain.parsing import parse_profile
from app.storage import models as m
from app.storage.db import init_db, make_engine
from tests.conftest import FakeFetcher, fixture_bytes
from tests.test_import_and_fetch import pages


@pytest.fixture()
def cand(session):
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    process_pending(session, FakeFetcher(pages()))
    return session.scalars(select(m.Candidate).where(m.Candidate.show_id == 1004)).one()


def sid(session, key):
    return status_by_key(session, key).id


def test_change_status_logs_and_sets_follow_up(session, cand):
    status = act.change_status(session, cand, sid(session, "invited"), note="ראיון ביום ג")
    session.commit()
    assert status.name == "הוזמן לראיון" and cand.status.key == "invited"
    assert (cand.follow_up_date - today_il()).days == 5
    entry = cand.journal[0]
    assert entry.kind == m.J_STATUS and entry.status.key == "invited" and entry.text == "ראיון ביום ג"


def test_same_status_without_note_is_not_logged_twice(session, cand):
    act.change_status(session, cand, sid(session, "new"))
    assert len(cand.journal) == 0


def test_manual_follow_up_on_status_change(session, cand):
    day = today_il() + timedelta(days=12)
    act.change_status(session, cand, sid(session, "replied"), follow_up=day)
    assert cand.follow_up_date == day


def test_past_follow_up_is_refused(session, cand):
    with pytest.raises(act.UserError):
        act.change_status(session, cand, sid(session, "replied"), follow_up=today_il() - timedelta(days=1))
    with pytest.raises(act.UserError):
        act.set_follow_up(cand, today_il() - timedelta(days=1))


def test_not_now_requires_a_date(session, cand):
    not_now = sid(session, "not_now")
    with pytest.raises(act.UserError, match="חובה"):
        act.change_status(session, cand, not_now)
    day = today_il() + timedelta(days=90)
    act.change_status(session, cand, not_now, follow_up=day)
    assert cand.follow_up_date == day
    with pytest.raises(act.UserError):
        act.set_follow_up(cand, None)  # cannot remove the date while "not now"


def test_final_status_has_no_follow_up(session, cand):
    act.change_status(session, cand, sid(session, "failed_interview"))
    assert cand.follow_up_date is None and cand.status.kind == "final"


def test_follow_up_can_be_cleared_or_moved(session, cand):
    act.set_follow_up(cand, None)
    assert cand.follow_up_date is None
    day = today_il() + timedelta(days=3)
    act.set_follow_up(cand, day)
    assert cand.follow_up_date == day


def test_whatsapp_contact_advances_new_status(session, cand):
    msg = act.log_contact(session, cand, m.J_OUT, "whatsapp", text="הודעה ראשונה")
    assert "נשלחה פנייה בווטסאפ" in msg and cand.status.key == "whatsapp_sent"
    kinds = [e.kind for e in cand.journal]
    assert m.J_OUT in kinds and m.J_STATUS in kinds


def test_reply_advances_but_never_goes_backwards(session, cand):
    act.change_status(session, cand, sid(session, "invited"))
    msg = act.log_contact(session, cand, m.J_IN, "whatsapp")
    assert msg == "נרשם ביומן" and cand.status.key == "invited"
    act.log_contact(session, cand, m.J_OUT, "whatsapp")
    assert cand.status.key == "invited"


def test_final_status_is_not_changed_by_contact(session, cand):
    act.change_status(session, cand, sid(session, "not_interested"))
    act.log_contact(session, cand, m.J_IN, "phone")
    assert cand.status.key == "not_interested"


def test_email_contact_and_no_advance(session, cand):
    act.log_contact(session, cand, m.J_OUT, "email")
    assert cand.status.key == "new"
    act.log_contact(session, cand, m.J_OUT, "whatsapp", advance=False)
    assert cand.status.key == "new"


def test_contact_validation(session, cand):
    with pytest.raises(act.UserError):
        act.log_contact(session, cand, "sideways", "whatsapp")
    with pytest.raises(act.UserError):
        act.log_contact(session, cand, m.J_OUT, "pigeon")
    with pytest.raises(act.UserError):
        act.log_contact(session, cand, m.J_OUT, "email", on=today_il() + timedelta(days=1))


def test_notes(session, cand):
    act.add_note(session, cand, "  שיחה נעימה  ")
    assert cand.journal[0].text == "שיחה נעימה" and cand.journal[0].kind == m.J_NOTE
    with pytest.raises(act.UserError):
        act.add_note(session, cand, "   ")


def test_edit_person_survives_refetch(session, cand):
    act.edit_person(cand, "דנה", "לוי-כהן", "female")
    assert cand.gender == "female" and cand.names_edited
    cand.full_name = "שם ישן"  # the page now says something else
    apply_profile(session, cand, parse_profile(fixture_bytes("profile_candidate.html")))
    assert (cand.first_name, cand.last_name) == ("דנה", "לוי-כהן")
    with pytest.raises(act.UserError):
        act.edit_person(cand, "", "", "")
    with pytest.raises(act.UserError):
        act.edit_person(cand, "א", "ב", "other")


# ---- settings

def test_rename_and_days(session):
    new = status_by_key(session, "interested")
    cfg.update_status(session, new.id, name="מעוניין", kind="active", follow_up_days="9", requires_date=False, enabled=True)
    assert new.name == "מעוניין" and new.follow_up_days == 9
    cfg.update_status(session, new.id, name="מעוניין", kind="active", follow_up_days="", requires_date=False, enabled=True)
    assert new.follow_up_days is None


def test_settings_validation(session):
    new = status_by_key(session, "new")
    for bad in ("0", "400", "x", "-3"):
        with pytest.raises(act.UserError):
            cfg.update_status(session, new.id, name="חדש", kind="active", follow_up_days=bad, requires_date=False, enabled=True)
    with pytest.raises(act.UserError):
        cfg.update_status(session, new.id, name="", kind="active", follow_up_days="5", requires_date=False, enabled=True)
    with pytest.raises(act.UserError):
        cfg.update_status(session, new.id, name="נשלחה פנייה בווטסאפ", kind="active", follow_up_days="5", requires_date=False, enabled=True)
    with pytest.raises(act.UserError):
        cfg.update_status(session, new.id, name="חדש", kind="active", follow_up_days="5", requires_date=False, enabled=False)


def test_add_status_and_use_it(session, cand):
    added = cfg.add_status(session, name="ממתין למסמכים")
    assert added.key is None and added.follow_up_days == 5 and added.sort_order == 10
    cfg.set_default_days(session, "7")
    assert cfg.add_status(session, name="עוד אחד").follow_up_days == 7
    assert cfg.add_status(session, name="סיים", kind="final").follow_up_days is None
    with pytest.raises(act.UserError):
        cfg.add_status(session, name="ממתין למסמכים")
    act.change_status(session, cand, added.id)
    assert cand.status.name == "ממתין למסמכים" and (cand.follow_up_date - today_il()).days == 5


def test_hidden_status_not_selectable_but_kept(session, cand):
    interested = status_by_key(session, "interested")
    cfg.update_status(session, interested.id, name=interested.name, kind="active", follow_up_days="5", requires_date=False, enabled=False)
    with pytest.raises(act.UserError):
        act.change_status(session, cand, interested.id)


def test_move_status(session):
    rows = cfg.ordered_statuses(session)
    first, second = rows[0], rows[1]
    cfg.move_status(session, second.id, "up")
    assert [s.id for s in cfg.ordered_statuses(session)][:2] == [second.id, first.id]
    cfg.move_status(session, second.id, "up")  # already first: nothing happens
    assert cfg.ordered_statuses(session)[0].id == second.id


def test_advance_follows_user_order(session, cand):
    """The "forward" rule uses the order set in the settings."""
    ws = status_by_key(session, "whatsapp_sent")
    cfg.move_status(session, ws.id, "up")  # now before "new"
    assert act.log_contact(session, cand, m.J_OUT, "whatsapp") == "נרשם ביומן"


# ---- migration

def test_old_database_gets_new_columns(tmp_path):
    path = tmp_path / "old.db"
    eng = make_engine(path)
    with eng.begin() as c:
        c.execute(text("CREATE TABLE setting (key VARCHAR(80) PRIMARY KEY, value TEXT)"))
        c.execute(text("CREATE TABLE candidate (id INTEGER PRIMARY KEY, show_id INTEGER UNIQUE, imported_at DATETIME, state VARCHAR(20), full_name VARCHAR(200), first_name VARCHAR(100), last_name VARCHAR(100), gender VARCHAR(10))"))
        c.execute(text("INSERT INTO candidate (id, show_id, imported_at, state, full_name, first_name, last_name) VALUES (1, 77, '2026-10-16 10:00:00', 'candidate', 'א ב', 'א', 'ב')"))
    init_db(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("candidate")}
    assert {"names_edited", "status_id", "follow_up_date", "phone_mobile", "extra_options"} <= cols
    assert inspect(eng).has_table("journal_entry")
    from sqlalchemy.orm import Session
    with Session(eng) as s:
        old = s.scalars(select(m.Candidate)).one()
        assert old.show_id == 77 and old.names_edited is False and old.full_name == "א ב"
    init_db(eng)  # running again changes nothing
