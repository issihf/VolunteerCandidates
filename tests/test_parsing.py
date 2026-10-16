from datetime import date

from app.domain.dates import parse_paamonim_date
from app.domain.names import split_name
from app.domain.parsing import (CANDIDATE, NOT_RELEVANT, REVIEW, PageParseError, classify, parse_contacts_list,
                                parse_profile, split_options)
from app.domain.phones import best_whatsapp_number, is_mobile, to_international
from tests.conftest import fixture_bytes

import pytest


def test_list_rows_and_skipped():
    rows, skipped = parse_contacts_list(fixture_bytes("contacts_list.html"))
    assert skipped == 1  # the empty regional-coordinator row has no link
    assert [r.show_id for r in rows] == [1001, 1002, 1003, 1004, 1005, 1006, 1006]
    first = rows[0]
    assert first.name == "דוד כהן" and first.role == "מלווה" and first.city == "ירושלים"
    assert first.mobile == "050-1110001" and first.email == "david.cohen@example.com"
    assert first.row_user_id == 621


def test_list_accepts_text_and_bom():
    data = fixture_bytes("contacts_list.html")
    assert len(parse_contacts_list(data.decode("utf-8"))[0]) == 7
    assert len(parse_contacts_list(b"\xef\xbb\xbf" + data)[0]) == 7


def test_list_without_table_gives_nothing():
    assert parse_contacts_list(b"<html><body><p>hello</p></body></html>") == ([], 0)


def test_profile_fields():
    p = parse_profile(fixture_bytes("profile_candidate.html"))
    assert p.full_name == "דנה לוי" and p.show_id == 1004 and p.user_id == 9001
    assert p.get("phone_mobile") == "054-1110004"
    assert p.get("email") == "dana.levi@example.com"
    assert p.get("street") == "הרצל 1" and p.get("city") == "ירושלים"
    assert p.get("birth_date_raw") == "5 באפריל , 1980"
    assert p.get("languages") == "אנגלית"
    assert p.get("workplace") == "חברה לדוגמה"  # trailing space removed
    assert p.extra_options == ["ליווי משפחות"]


@pytest.mark.parametrize("name,verdict", [
    ("candidate", CANDIDATE), ("multi", CANDIDATE), ("other", NOT_RELEVANT),
    ("review", REVIEW), ("nooptions", NOT_RELEVANT)])
def test_classify(name, verdict):
    state, reason = classify(parse_profile(fixture_bytes(f"profile_{name}.html")))
    assert state == verdict
    if verdict != CANDIDATE:
        assert reason


def test_multi_options_are_split():
    p = parse_profile(fixture_bytes("profile_multi.html"))
    assert p.extra_options == ["הרצאות", "ליווי משפחות"]


def test_no_details_block_is_an_error():
    with pytest.raises(PageParseError):
        parse_profile("<html><body><h2>משהו אחר</h2></body></html>")


def test_split_options():
    assert split_options("א, ב;ג\nד ") == ["א", "ב", "ג", "ד"]


def test_parsed_profile_keeps_no_page_text():
    p = parse_profile(fixture_bytes("profile_candidate.html"))
    assert "<" not in repr(p)


def test_dates():
    assert parse_paamonim_date("11 באוגוסט , 1973") == date(1973, 8, 11)
    assert parse_paamonim_date("19 במרץ , 2019") == date(2019, 3, 19)
    assert parse_paamonim_date("06 בנובמבר , 2018") == date(2018, 11, 6)
    assert parse_paamonim_date("06-11-18") == date(2018, 11, 6)
    assert parse_paamonim_date("") is None and parse_paamonim_date("בלה") is None
    assert parse_paamonim_date("31 בפברואר , 2020") is None


def test_split_name():
    assert split_name("רחל אברהם") == ("רחל", "אברהם")
    assert split_name("בת שבע גדי") == ("בת שבע", "גדי")
    assert split_name("דוד") == ("דוד", "")
    assert split_name("") == ("", "")


def test_phones():
    assert to_international("054-6450145") == "972546450145"
    assert to_international("+972 54 645 0145") == "972546450145"
    assert to_international("0546450145") == "972546450145"
    assert to_international("02-6482553") == "97226482553"
    assert to_international("12345") is None and to_international("") is None
    assert is_mobile("050-1112222") and not is_mobile("02-6482553")
    assert best_whatsapp_number("02-6482553", "052-1110002") == "972521110002"
    assert best_whatsapp_number("02-6482553", None) is None
