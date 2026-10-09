import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from src.models.input import ActorInput
from src.resolver.run import ZERO_MATCH_TEXT, run_query
from tests.conftest import DISTRICTS, LPMS, NTNI

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)
OHIO = [
    {"lat": 40.4425, "lon": -80.0150},
    {"lat": 40.5106, "lon": -80.0820},
    {"lat": 40.6463, "lon": -80.4120},
    {"lat": 40.5263, "lon": -80.6275},
    {"lat": 40.1650, "lon": -80.6990},
    {"lat": 39.6650, "lon": -80.8650},
]
FORBIDDEN = (
    "route_is_safe",
    "safe_to_transit",
    "fully_navigable",
    "clear_to_proceed",
    "no_navigation_hazards",
    "guaranteed_passage",
    "is safe",
    "safe to",
    "all clear",
    "unrestricted",
    "hazard-free",
)


async def run(route=OHIO, at="2026-10-13T15:00:00Z", corridor=1.0):
    async with httpx.AsyncClient() as client:
        inp = ActorInput.model_validate({"route": route, "corridorNm": corridor, "atTime": at})
        return (await run_query(inp, client=client, now=NOW)).result


async def test_route_orders_locks_and_notices(usace):
    r = await run()
    assert r.status == "success"
    assert r.billing.billable
    ids = [lk.lock_id for lk in r.locks]
    assert ids[:4] == ["OH-01", "OH-02", "OH-03", "OH-04"]
    positions = [c["routePosition"] for c in r.constraints]
    assert positions == sorted(positions)
    hannibal = next(n for n in r.notices if n.notice_id == 215082)
    assert hannibal.temporal_status == "active"
    assert hannibal.matched_by == "geometry"
    assert hannibal.effective_window.start == "2026-10-12T11:00:00Z"
    assert hannibal.relationship_to_route.intersects_corridor


async def test_lpms_conditions_joined_by_river_and_lock_code(usace):
    r = await run()
    nc = next(lk for lk in r.locks if lk.lock_id == "OH-04")
    assert nc.name == "NEW CUMBERLAND LOCK AND DAM"
    assert nc.operating_conditions.queue.vessels_pending == 1
    assert nc.operating_conditions.queue.reported_average_delay_minutes == 89
    assert nc.coordinates["lat"] > 40
    assert nc.coordinates["lon"] < -80


async def test_upcoming_notice_before_begin(usace):
    r = await run(at="2026-10-09T15:00:00Z")
    assert next(n for n in r.notices if n.notice_id == 215082).temporal_status == "upcoming"


async def test_expired_notice_excluded_and_counted(usace):
    r = await run(at="2026-12-01T15:00:00Z")
    assert all(n.notice_id != 215082 for n in r.notices)
    assert r.summary.expired_notices_excluded >= 1


async def test_text_only_notices_are_unverified_not_matched(usace):
    r = await run()
    assert r.unverified_location_notices
    u = r.unverified_location_notices[0]
    assert u.spatially_verified is False
    assert u.matched_by is None
    assert all(n.notice_id != u.notice_id for n in r.notices)
    assert r.coverage.ntni_geometry.no_geometry_published == len(r.unverified_location_notices)


async def test_notice_near_but_outside_corridor_not_matched(usace):
    far = [{"lat": 39.9, "lon": -81.6}, {"lat": 39.5, "lon": -81.9}]
    r = await run(route=far)
    assert r.notices == []
    assert r.locks == []
    assert r.status == "success"
    assert r.billing.billable
    assert ZERO_MATCH_TEXT in r.coverage.coverage_note


async def test_duplicate_notice_records_collapse(usace):
    r = await run()
    ids = [n.notice_id for n in r.notices]
    assert len(ids) == len(set(ids))


async def test_ntni_list_failure_is_partial_and_never_zero_match_success(usace):
    usace.get(url__startswith=NTNI + "/json_data/notices/").mock(return_value=httpx.Response(503))
    far = [{"lat": 39.9, "lon": -81.6}, {"lat": 39.5, "lon": -81.9}]
    r = await run(route=far)
    assert r.status == "partial"
    assert not r.billing.billable
    assert ZERO_MATCH_TEXT not in r.coverage.coverage_note
    assert any(f.source == "ntni_active" for f in r.coverage.source_failures)


async def test_lpms_rate_limit_body_is_source_failure(usace):
    usace.get(url__startswith=LPMS).mock(
        return_value=httpx.Response(200, json={"error": "Rate limit exceeded. Maximum 5 requests per minute allowed."})
    )
    r = await run()
    assert r.status == "partial"
    assert r.coverage.lpms.status == "unavailable"
    assert all(lk.operating_conditions is None for lk in r.locks)
    assert r.billing.billable


async def test_geometry_fetch_failure_makes_coverage_partial(usace):
    usace.get(f"{NTNI}/leaflet_json/notice/215139").mock(return_value=httpx.Response(500))
    r = await run()
    assert r.coverage.ntni_geometry.status == "partial"
    assert 215139 in r.coverage.ntni_geometry.geometry_not_retrieved
    assert r.status == "partial"


async def test_district_failure_makes_geometry_unavailable(usace):
    usace.get(url__startswith=DISTRICTS).mock(return_value=httpx.Response(200, json={"error": {"code": 400}}))
    r = await run()
    assert r.coverage.ntni_geometry.status == "unavailable"
    assert r.status == "partial"


async def test_stale_lpms_data_is_labelled(usace):
    async with httpx.AsyncClient() as client:
        inp = ActorInput.model_validate({"route": OHIO, "corridorNm": 1})
        r = (await run_query(inp, client=client, now=datetime(2026, 10, 11, 15, tzinfo=UTC))).result
    assert {lk.operating_conditions.freshness for lk in r.locks if lk.operating_conditions} == {"stale"}


@pytest.mark.parametrize(
    "bad",
    [
        [{"lat": 40, "lon": -80}],
        [{"lat": 40, "lon": -80}, {"lat": 40, "lon": -80}],
        [{"lat": 95, "lon": -80}, {"lat": 40, "lon": -80}],
    ],
)
def test_invalid_route(bad):
    with pytest.raises(ValidationError):
        ActorInput.model_validate({"route": bad})


async def test_no_forbidden_safety_claims(usace):
    r = (await run()).to_record()
    for n in r["notices"] + r["unverifiedLocationNotices"]:
        n.pop("officialText", None)
        n.pop("title", None)
    r["constraints"] = [{k: v for k, v in c.items() if k != "title"} for c in r["constraints"]]
    text = json.dumps(r).lower()
    assert not [w for w in FORBIDDEN if w in text]
