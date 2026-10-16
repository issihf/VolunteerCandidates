import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.storage import models as m
from app.web.main import create_app
from tests.conftest import FakeFetcher, fixture_bytes
from tests.test_import_and_fetch import pages


@pytest.fixture()
def client(engine):
    fetcher = FakeFetcher(pages())
    app = create_app(engine=engine, fetcher_factory=lambda: fetcher)
    with TestClient(app, follow_redirects=False) as c:
        c.fetcher = fetcher
        yield c


def upload(client):
    return client.post("/import", files={"file": ("contacts.html", fixture_bytes("contacts_list.html"), "text/html")})


def wait_job(client):
    client.app.state.job.wait(10)
    assert not client.app.state.job.running


def test_empty_start(client):
    r = client.get("/candidates")
    assert r.status_code == 200 and "אין רשומות" in r.text


def test_import_then_fetch_then_lists(client):
    r = upload(client)
    assert r.status_code == 303 and "tab=pending" in r.headers["location"]
    page = client.get("/candidates?tab=pending")
    assert "דוד כהן" in page.text and "050-1110001" in page.text and "שליפת פרטים (6 ממתינים)" in page.text
    assert client.post("/fetch").status_code == 303
    wait_job(client)
    assert client.fetcher.closed
    cands = client.get("/candidates").text
    assert "דנה לוי" in cands and "חדש" in cands and "יוסי כהן" in cands
    assert "שרה גולן" not in cands
    assert "שרה גולן" in client.get("/candidates?tab=not_relevant").text
    assert "בת שבע מזרחי" in client.get("/candidates?tab=review").text
    assert "הדף החזיר שגיאה 500" in client.get("/candidates?tab=failed").text
    status = client.get("/fetch/status").json()
    assert status["finished"] and status["done"] == 6 and status["candidate"] == 2


def test_second_import_says_nothing_new(client):
    upload(client)
    r = upload(client)
    assert "0 חדשים נוספו, 6 כבר היו במערכת" in client.get(r.headers["location"]).text


def test_wrong_file_is_explained(client):
    r = client.post("/import", files={"file": ("x.html", b"<html><body>hi</body></html>", "text/html")})
    assert r.status_code == 303 and "flash=" in r.headers["location"]
    assert "אנשי הקשר" in client.get(r.headers["location"]).text


def test_detail_and_resolution(client):
    upload(client)
    client.post("/fetch")
    wait_job(client)
    with client.app.state.sessions() as s:
        review = s.scalars(select(m.Candidate).where(m.Candidate.show_id == 1003)).one()
        cid = review.id
    page = client.get(f"/candidates/{cid}")
    assert page.status_code == 200 and "סימון כמועמד" in page.text
    assert "https://app.paamonim.org.il/contacts/show/1003" in page.text
    r = client.post(f"/candidates/{cid}/resolve", data={"to": "candidate"})
    assert r.status_code == 303
    assert "חדש" in client.get(f"/candidates/{cid}").text
    assert client.post(f"/candidates/{cid}/resolve", data={"to": "pending"}).status_code == 400
    assert client.get("/candidates/99999").status_code == 404


def test_whatsapp_link_for_mobile(client):
    upload(client)
    client.post("/fetch")
    wait_job(client)
    with client.app.state.sessions() as s:
        cid = s.scalars(select(m.Candidate).where(m.Candidate.show_id == 1004)).one().id
    assert "https://wa.me/972541110004" in client.get(f"/candidates/{cid}").text


def test_refetch_puts_person_back_in_queue(client):
    upload(client)
    client.post("/fetch")
    wait_job(client)
    with client.app.state.sessions() as s:
        cid = s.scalars(select(m.Candidate).where(m.Candidate.show_id == 1004)).one().id
    client.post(f"/candidates/{cid}/refetch")
    with client.app.state.sessions() as s:
        assert s.get(m.Candidate, cid).state == m.PENDING


def test_search(client):
    upload(client)
    client.post("/fetch")
    wait_job(client)
    found = client.get("/candidates?q=דנה").text
    assert "דנה לוי" in found and "יוסי כהן" not in found
    assert "יוסי כהן" in client.get("/candidates?q=כהן").text


def test_fetch_failure_to_open_browser_is_reported(engine):
    from app.importer.fetch import FetchError

    def broken():
        raise FetchError("לא ניתן לפתוח את הדפדפן")

    app = create_app(engine=engine, fetcher_factory=broken)
    with TestClient(app, follow_redirects=False) as c:
        upload(c)
        c.post("/fetch")
        app.state.job.wait(10)
        assert "לא ניתן לפתוח את הדפדפן" in c.get("/candidates?tab=pending").text


def test_key_and_host_guard(engine):
    app = create_app(engine=engine, token="secret-key")
    with TestClient(app, follow_redirects=False) as c:
        assert c.get("/candidates").status_code == 403
        r = c.get("/candidates?k=wrong")
        assert r.status_code == 403
        r = c.get("/candidates?k=secret-key")
        assert r.status_code == 303 and "vc_key" in r.headers["set-cookie"]
        assert c.get("/candidates").status_code == 200  # cookie kept by the client
        assert c.get("/candidates", headers={"host": "evil.example.com"}).status_code == 403
