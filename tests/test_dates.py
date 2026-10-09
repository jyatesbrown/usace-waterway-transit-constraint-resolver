from datetime import UTC, datetime

from src.normalization.dates import parse_timestamp, temporal_status

AT = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


def rec(issue=None, begin=None, end=None):
    return {"issueDate": issue, "beginDate": begin, "endDate": end}


def test_recent_notice_without_end_date_is_active_by_source():
    t = temporal_status(rec("07-APR-2026", "27-APR-2026"), AT)
    assert t.status == "active_by_source"
    assert t.end is None
    assert t.end_date_status == "not_provided"
    assert t.status_basis == "USACE NTNI active-notices service"
    assert t.age_days == 185


def test_very_old_notice_without_end_date_is_retained_and_not_expired():
    t = temporal_status(rec("15-MAY-2015", "18-APR-2015"), AT)
    assert t.status == "active_by_source"
    assert t.begin == "2015-04-18"
    assert t.age_days > 4000


def test_explicit_future_end_date_is_active():
    t = temporal_status(rec("20-MAR-2026", "20-MAR-2026", "01-JAN-2027"), AT)
    assert t.status == "active"
    assert t.end_date_status == "provided"
    assert t.status_basis == "official NTNI begin and end dates"


def test_expired_notice():
    t = temporal_status(rec("05-OCT-2026", "05-OCT-2026", "09-OCT-2026"), datetime(2026, 10, 11, 15, tzinfo=UTC))
    assert t.status == "expired"


def test_until_further_notice_text_is_flagged_without_inferring_end():
    t = temporal_status(rec("23-OCT-2012"), AT, text="Closed until further notice.")
    assert t.until_further_notice_text
    assert t.status == "active_by_source"
    assert t.end is None


def test_upcoming_and_precise_timestamps():
    begin = parse_timestamp("2026-10-12T11:00:00Z")
    end = parse_timestamp("2026-11-10T12:00:00Z")
    assert temporal_status(rec(), AT, begin_at=begin, end_at=end).status == "upcoming"
    assert temporal_status(rec(), datetime(2026, 10, 20, tzinfo=UTC), begin_at=begin, end_at=end).status == "active"


def test_calendar_boundary_is_unknown():
    t = temporal_status(rec(None, "10-OCT-2026", "20-OCT-2026"), datetime(2026, 10, 10, 6, tzinfo=UTC))
    assert t.status == "unknown"


def test_undated_notice_not_in_active_list_is_unknown():
    assert temporal_status(rec(None, "01-OCT-2026"), AT, in_active_list=False).status == "unknown"
