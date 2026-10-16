from sqlalchemy import func, select

from app.importer.apply import resolve, request_refetch, set_status, status_by_key
from app.importer.fetch import FetchError, process_pending
from app.importer.list_import import import_list
from app.domain.dates import today_il
from app.storage import models as m
from tests.conftest import FakeFetcher, fixture_bytes


def pages():
    return {
        1001: fixture_bytes("profile_multi.html"),
        1002: fixture_bytes("profile_other.html"),
        1003: fixture_bytes("profile_review.html"),
        1004: fixture_bytes("profile_candidate.html"),
        1005: fixture_bytes("profile_nooptions.html"),
        1006: FetchError("הדף החזיר שגיאה 500"),
    }


def test_import_adds_everyone_once(session):
    r = import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    assert (r.total, r.new, r.existing, r.repeated, r.without_link) == (7, 6, 0, 1, 1)
    assert session.scalar(select(func.count()).select_from(m.Candidate)) == 6
    c = session.scalars(select(m.Candidate).where(m.Candidate.show_id == 1003)).one()
    assert c.state == m.PENDING and c.first_name == "בת שבע" and c.last_name == "מזרחי"
    assert c.list_mobile == "058-1110003"


def test_second_import_adds_only_new_people(session):
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    again = import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    assert (again.new, again.existing) == (0, 6)
    assert session.scalar(select(func.count()).select_from(m.Candidate)) == 6


def test_existing_people_are_left_untouched(session):
    import_list(session, fixture_bytes("contacts_list.html"))
    c = session.scalars(select(m.Candidate).where(m.Candidate.show_id == 1001)).one()
    c.first_name, c.gender = "ידני", "male"
    session.commit()
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    session.refresh(c)
    assert c.first_name == "ידני" and c.gender == "male"


def test_fetch_sorts_everyone(session):
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    fetcher = FakeFetcher(pages())
    summary = process_pending(session, fetcher)
    assert (summary.total, summary.done) == (6, 6)
    assert (summary.candidate, summary.not_relevant, summary.review, summary.failed) == (2, 2, 1, 1)
    by_id = {c.show_id: c for c in session.scalars(select(m.Candidate))}
    assert by_id[1004].state == m.CANDIDATE and by_id[1004].phone_mobile == "054-1110004"
    assert by_id[1004].birth_date.year == 1980 and by_id[1004].extra_options == "ליווי משפחות"
    assert by_id[1001].state == m.CANDIDATE and by_id[1001].extra_options == "הרצאות, ליווי משפחות"
    assert by_id[1002].state == m.NOT_RELEVANT and "הרצאות" in by_id[1002].state_reason
    assert by_id[1003].state == m.REVIEW
    assert by_id[1005].state == m.NOT_RELEVANT
    assert by_id[1006].state == m.FAILED and "500" in by_id[1006].state_reason


def test_new_candidates_get_status_new_and_follow_up(session):
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    process_pending(session, FakeFetcher(pages()))
    c = session.scalars(select(m.Candidate).where(m.Candidate.show_id == 1004)).one()
    assert c.status.name == "חדש"
    assert (c.follow_up_date - today_il()).days == 5
    other = session.scalars(select(m.Candidate).where(m.Candidate.show_id == 1002)).one()
    assert other.status_id is None and other.follow_up_date is None


def test_no_page_html_is_stored(session, engine):
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    process_pending(session, FakeFetcher(pages()))
    import sqlite3
    raw = sqlite3.connect(engine.url.database).execute("select * from candidate").fetchall()
    assert "<html" not in repr(raw) and "<dl" not in repr(raw)


def test_done_people_are_not_fetched_again(session):
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    process_pending(session, FakeFetcher(pages()))
    second = FakeFetcher(pages())
    summary = process_pending(session, second)
    assert summary.total == 0 and second.calls == []


def test_login_lost_midway_pauses_and_resumes(session):
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    fetcher = FakeFetcher(pages(), login_after=3)
    messages = []
    summary = process_pending(session, fetcher, on_message=messages.append)
    assert fetcher.logins == 1 and summary.done == 6 and summary.stopped is None
    assert any("התחברות" in t for t in messages)
    assert session.scalar(select(func.count()).select_from(m.Candidate).where(m.Candidate.state == m.PENDING)) == 0


def test_login_without_a_way_to_ask_stops_cleanly(session):
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()

    class NoLogin(FakeFetcher):
        ensure_login = None

    summary = process_pending(session, NoLogin(pages(), login_after=3))
    assert summary.stopped == "login" and summary.done == 2
    assert session.scalar(select(func.count()).select_from(m.Candidate).where(m.Candidate.state == m.PENDING)) == 4


def test_user_can_stop(session):
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    n = {"i": 0}

    def stop():
        n["i"] += 1
        return n["i"] > 2

    summary = process_pending(session, FakeFetcher(pages()), should_stop=stop)
    assert summary.stopped == "user" and summary.done == 2


def test_unreadable_page_is_marked_failed_not_lost(session):
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    p = pages()
    p[1001] = "<html><body>no details</body></html>".encode()
    summary = process_pending(session, FakeFetcher(p))
    assert summary.failed == 2
    c = session.scalars(select(m.Candidate).where(m.Candidate.show_id == 1001)).one()
    assert c.state == m.FAILED


def test_manual_resolution_and_refetch(session):
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    process_pending(session, FakeFetcher(pages()))
    review = session.scalars(select(m.Candidate).where(m.Candidate.show_id == 1003)).one()
    resolve(session, review, m.CANDIDATE)
    assert review.state == m.CANDIDATE and review.status.name == "חדש"
    resolve(session, review, m.NOT_RELEVANT)
    assert review.state == m.NOT_RELEVANT
    request_refetch(review)
    assert review.state == m.PENDING


def test_set_status_uses_the_statuses_own_days(session):
    import_list(session, fixture_bytes("contacts_list.html"))
    session.commit()
    process_pending(session, FakeFetcher(pages()))
    c = session.scalars(select(m.Candidate).where(m.Candidate.show_id == 1004)).one()
    set_status(session, c, status_by_key(session, "interviewed"))
    assert c.follow_up_date is None
    invited = status_by_key(session, "invited")
    invited.follow_up_days = 9
    set_status(session, c, invited)
    assert (c.follow_up_date - today_il()).days == 9


def test_default_statuses(session):
    names = [s.name for s in session.scalars(select(m.Status).order_by(m.Status.sort_order))]
    assert names[:2] == ["חדש", "נשלחה פנייה בווטסאפ"] and "לא עבר ראיון" in names
    finals = {s.key for s in session.scalars(select(m.Status).where(m.Status.kind == "final"))}
    assert finals == {"failed_interview", "not_interested", "not_now"}
    not_now = status_by_key(session, "not_now")
    assert not_now.requires_date and not_now.follow_up_days is None
