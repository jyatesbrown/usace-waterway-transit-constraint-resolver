"""Temporal status of NTNI notices.

The per-notice service gives begin/end timestamps (UTC); the list gives calendar dates only
(`12-OCT-2026`). Timestamps are compared directly. A calendar date is compared on the date the query
instant falls on anywhere in UTC-4..UTC-8; if those disagree the status is `unknown`.

A notice with no end date that the official active-notices service still returns is `active_by_source`:
active status is asserted by USACE, not derived from an end date, and no expiration is inferred.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

TemporalStatus = Literal["active", "active_by_source", "upcoming", "expired", "unknown"]
LOCAL_OFFSETS_HOURS = (4, 8)
BASIS_DATES = "official NTNI begin and end dates"
BASIS_SOURCE = "USACE NTNI active-notices service"
_UFN = re.compile(r"until\s+further\s+notice", re.IGNORECASE)


def parse_ntni_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        ts = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts.astimezone(UTC) if ts.tzinfo else None


def _status(at: date | datetime, begin: date | datetime | None, end: date | datetime | None) -> TemporalStatus:
    if end is not None and at > end:
        return "expired"
    if begin is not None and at < begin:
        return "upcoming"
    if begin is not None and end is not None:
        return "active"
    return "active_by_source"


@dataclass(frozen=True)
class Temporal:
    status: TemporalStatus
    status_basis: str
    begin: str | None
    end: str | None
    issued: str | None
    age_days: int | None
    until_further_notice_text: bool
    in_active_list: bool
    note: str | None = None

    @property
    def end_date_status(self) -> str:
        return "provided" if self.end else "not_provided"


def temporal_status(
    record: dict,
    at: datetime,
    *,
    text: str = "",
    begin_at: datetime | None = None,
    end_at: datetime | None = None,
    in_active_list: bool = True,
) -> Temporal:
    """`record` is an NTNI list/feed record; `begin_at`/`end_at` are per-notice timestamps when known."""
    begin_d, end_d = parse_ntni_date(record.get("beginDate")), parse_ntni_date(record.get("endDate"))
    issued = parse_ntni_date(record.get("issueDate"))
    precise = begin_at is not None or end_at is not None
    begin: date | datetime | None = begin_at if precise else begin_d
    end: date | datetime | None = end_at if precise else end_d
    note = None
    if precise:
        statuses = {_status(at, begin_at, end_at)}
    else:
        statuses = {_status((at - timedelta(hours=h)).date(), begin_d, end_d) for h in LOCAL_OFFSETS_HOURS}
    if begin and end and type(begin) is type(end) and end < begin:
        status: TemporalStatus = "unknown"
        note = "Official end precedes the official begin."
    elif len(statuses) > 1:
        status = "unknown"
        note = "Query time falls on a begin/end calendar date; NTNI dates carry no time of day."
    else:
        status = statuses.pop()
    if status == "active_by_source" and not in_active_list:
        status, note = "unknown", "No end date and not in the active-notices list."
    reference = issued or begin_d
    age = (at.date() - reference).days if reference else None
    basis = BASIS_SOURCE if status == "active_by_source" else BASIS_DATES
    return Temporal(
        status=status,
        status_basis=basis,
        begin=_iso(begin),
        end=_iso(end),
        issued=issued.isoformat() if issued else None,
        age_days=age,
        until_further_notice_text=bool(_UFN.search(text or "")),
        in_active_list=in_active_list,
        note=note,
    )


def _iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return value.isoformat()
