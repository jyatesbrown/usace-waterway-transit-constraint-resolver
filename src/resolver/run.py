"""Orchestration: route -> official USACE notices + locks + LPMS conditions -> one ordered record."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import numpy as np
import shapely
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

from ..billing import billing_decision
from ..geo.geometry import METRES_PER_NM, QueryArea, route_area
from ..models.input import ActorInput
from ..models.output import (
    Billing,
    Coverage,
    EffectiveWindow,
    Lock,
    LockConditions,
    Notice,
    NoticeGeometryCoverage,
    Query,
    Queue,
    Result,
    RouteRelationship,
    Source,
    SourceCoverage,
    SourceFailure,
    Summary,
    UnverifiedLocationNotice,
)
from ..normalization.dates import Temporal, parse_timestamp, temporal_status
from ..normalization.rivermile import river_mile_references
from ..sources import locks as locks_src
from ..sources import ntni
from ..sources.cache import KeyValueCache, NullCache
from ..utils.errors import SourceError
from ..utils.http import create_client

LIST_TTL = 15 * 60
LOCKS_TTL = 24 * 3600
LPMS_TTL = 5 * 60
DETAIL_TTL = 24 * 3600
NO_GEOMETRY_TTL = 6 * 3600
UNVERIFIED_LISTING_MARGIN_NM = 25.0
LOCK_NOTICE_RADIUS_NM = 0.25
DETAIL_CONCURRENCY = 8
CACHE_READ_CONCURRENCY = 32
NO_GEOMETRY_INDEX_KEY = "ntni-no-geometry-index"
DETAIL_FIELDS = ("id", "title", "begin_date", "end_date", "nn_category", "geojson")
LPMS_CURRENT_MINUTES = 6 * 60
LPMS_OFFSETS_HOURS = (4, 8)
ZERO_MATCH_TEXT = "No matching constraints were found in the official datasets successfully checked."


def _route_key(lonlat: list[tuple[float, float]], margin: float) -> str:
    raw = json.dumps([[round(x, 5), round(y, 5)] for x, y in lonlat] + [margin])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Outcome:
    result: Result
    elapsed_ms: float


@dataclass
class Fetched:
    value: Any = None
    fetched_at: datetime | None = None
    from_cache: bool = False
    error: SourceError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def _cached(
    cache: KeyValueCache, key: str, ttl: float, fetch: Callable[[], Awaitable[Any]], now: datetime
) -> Fetched:
    hit = await cache.get(key, ttl)
    if hit is not None:
        return Fetched(hit[0], datetime.fromtimestamp(hit[1], UTC), True)
    try:
        value = await fetch()
    except SourceError as exc:
        return Fetched(error=exc)
    await cache.put(key, value)
    return Fetched(value, now, False)


def invalid_input(message: str, checked_at: str, corridor_nm: float = 1.0) -> Result:
    return Result(
        status="invalid_input",
        checked_at=checked_at,
        at_time=checked_at,
        query=Query(corridor_nm=corridor_nm),
        errors=[message],
        billing=Billing(billable=False, reason="Not charged: invalid input."),
    )


def _geometry(features: list[dict[str, Any]]) -> BaseGeometry | None:
    geoms = []
    for f in features:
        try:
            g = shape(f["geometry"])
        except Exception:
            continue
        if not g.is_empty:
            geoms.append(shapely.make_valid(g))
    return shapely.union_all(geoms) if geoms else None


def _coords_bbox(features: list[dict[str, Any]]) -> list[float] | None:
    """Lon/lat bounds of raw GeoJSON coordinates, or None when they cannot be read."""
    xs: list[float] = []
    ys: list[float] = []

    def walk(c: Any) -> None:
        if isinstance(c, (list, tuple)) and len(c) >= 2 and all(isinstance(v, (int, float)) for v in c[:2]):
            xs.append(float(c[0]))
            ys.append(float(c[1]))
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)

    def geom(g: Any) -> None:
        if not isinstance(g, dict):
            return
        if g.get("type") == "GeometryCollection":
            for sub in g.get("geometries") or []:
                geom(sub)
        else:
            walk(g.get("coordinates"))

    for f in features:
        geom(f.get("geometry"))
    return [min(xs), min(ys), max(xs), max(ys)] if xs else None


def _route_bbox(area: QueryArea, corridor_nm: float) -> tuple[float, float, float, float] | None:
    """Conservative lon/lat window around the corridor: twice the corridor plus 1 nm, in degrees."""
    line = area.route_line
    if line is None:
        return None
    xy = shapely.get_coordinates(line)
    lons, lats = area.to_wgs84.transform(xy[:, 0], xy[:, 1])
    pad_lat = 2 * (corridor_nm + 1.0) / 60.0
    max_abs_lat = min(float(np.max(np.abs(lats))) + pad_lat, 80.0)
    pad_lon = pad_lat / math.cos(math.radians(max_abs_lat))
    return (
        float(np.min(lons)) - pad_lon,
        float(np.min(lats)) - pad_lat,
        float(np.max(lons)) + pad_lon,
        float(np.max(lats)) + pad_lat,
    )


def _bbox_disjoint(a: Any, b: tuple[float, float, float, float] | None) -> bool:
    if b is None or not isinstance(a, list) or len(a) != 4 or a[2] - a[0] > 180:
        return False
    return a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3]


def _relationship(area: QueryArea, geom_wgs: BaseGeometry) -> RouteRelationship | None:
    local = area.project(geom_wgs)
    if not local.intersects(area.search_area):
        return None
    line = area.route_line
    assert line is not None
    contact = local.intersection(area.search_area)
    coords = shapely.get_coordinates(contact if not contact.is_empty else local)
    along = float(shapely.line_locate_point(line, shapely.points(coords)).min())
    return RouteRelationship(
        intersects_route=bool(local.intersects(line)),
        intersects_corridor=True,
        route_position=round(along / line.length, 4) if line.length else 0.0,
        route_distance_nm=round(along / METRES_PER_NM, 2),
        minimum_distance_nm=0.0 if local.intersects(line) else round(area.geodesic_distance_nm(line, local), 3),
    )


def _text(rec: dict[str, Any]) -> str:
    return "\n".join(str(rec.get(k) or "") for k in ("title", "remarks"))


def _waterway(rec: dict[str, Any]) -> str | None:
    w = rec.get("waterwayNames")
    if isinstance(w, list):
        return ", ".join(str(x) for x in w) or None
    return str(w) if w else None


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _temporal_fields(t: Temporal) -> dict[str, Any]:
    return {
        "temporal_status": t.status,
        "status_basis": t.status_basis,
        "effective_window": EffectiveWindow(start=t.begin, end=t.end),
        "end_date_status": t.end_date_status,
        "issue_date": t.issued,
        "age_days": t.age_days,
        "official_text_says_until_further_notice": t.until_further_notice_text,
    }


def _attachments(rec: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"name": f.get("name"), "url": f.get("url")} for f in rec.get("fileNames") or [] if isinstance(f, dict)]


def _lpms_freshness(entry: str | None, now: datetime) -> tuple[str, list[int], str]:
    try:
        local = datetime.fromisoformat(str(entry))
    except (TypeError, ValueError):
        return "stale", [], "LPMS entry time missing or unreadable; treat conditions as unverified."
    ages = sorted(
        int((now - (local.replace(tzinfo=UTC) + timedelta(hours=h))).total_seconds() // 60) for h in LPMS_OFFSETS_HOURS
    )
    lo, hi = max(ages[0], 0), max(ages[1], 0)
    note = "LPMS states no timezone; age assumes the lock's local time is between UTC-4 and UTC-8."
    if hi <= LPMS_CURRENT_MINUTES:
        return "current", [lo, hi], note
    if lo > LPMS_CURRENT_MINUTES:
        return "stale", [lo, hi], note + " Report is older than 6 hours."
    return "possibly_stale", [lo, hi], note + " Report may be older than 6 hours."


async def run_query(
    inp: ActorInput,
    *,
    client: httpx.AsyncClient | None = None,
    cache: KeyValueCache | None = None,
    now: datetime | None = None,
) -> Outcome:
    started = time.perf_counter()
    now = now or datetime.now(UTC)
    owns = client is None
    client = client or create_client()
    try:
        result = await _run(inp, client, cache or NullCache(), now)
    finally:
        if owns:
            await client.aclose()
    return Outcome(result, (time.perf_counter() - started) * 1000)


async def _run(inp: ActorInput, client: httpx.AsyncClient, cache: KeyValueCache, now: datetime) -> Result:
    at = inp.at_time or now
    day: date = now.date()
    area = route_area(inp.lonlat, inp.corridor_nm)
    margin = inp.corridor_nm + UNVERIFIED_LISTING_MARGIN_NM

    async def districts() -> list[str]:
        return await ntni.fetch_route_districts(client, inp.lonlat, margin)

    active, upcoming, dist, lock_layer = await asyncio.gather(
        _cached(cache, f"ntni-active-{day}", LIST_TTL, lambda: ntni.fetch_active_notices(client, day), now),
        _cached(cache, f"ntni-upcoming-{day}", LIST_TTL, lambda: ntni.fetch_upcoming_geo(client, day), now),
        _cached(cache, "districts-" + _route_key(inp.lonlat, margin), LOCKS_TTL, districts, now),
        _cached(cache, "usace-locks", LOCKS_TTL, lambda: locks_src.fetch_locks(client), now),
    )
    failures: list[SourceFailure] = []
    for name, f in (
        ("ntni_active", active),
        ("ntni_upcoming", upcoming),
        ("usace_districts", dist),
        ("corps_locks", lock_layer),
    ):
        if f.error:
            failures.append(SourceFailure(source=name, state=str(f.error.state), detail=f.error.detail))

    # ---- locks ---------------------------------------------------------------------------------
    locks: list[Lock] = []
    if lock_layer.ok:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for feat in lock_layer.value:
            p = feat.get("properties") or {}
            key = locks_src.lock_key(p.get("RIVERCD"), p.get("LOCKCD"))
            if key and (feat.get("geometry") or {}).get("type") == "Point":
                groups.setdefault(key, []).append(feat)
        line = area.route_line
        assert line is not None
        for (river, _number), feats in groups.items():
            lon, lat = feats[0]["geometry"]["coordinates"][:2]
            local = area.project(Point(lon, lat))
            if not local.within(area.search_area):
                continue
            along = line.project(local)
            p0 = feats[0]["properties"]
            locks.append(
                Lock(
                    lock_id=f"{river}-{p0.get('LOCKCD')}",
                    name=None,
                    river_code=river,
                    lock_code=str(p0.get("LOCKCD")),
                    river=p0.get("RIVER"),
                    river_mile=_num(p0.get("RIVERMI")),
                    waterway_project=p0.get("WWPRJCT"),
                    district=p0.get("DISTRICT"),
                    ndc_codes=sorted(
                        {str(f["properties"].get("NDCCODE")) for f in feats if f["properties"].get("NDCCODE")}
                    ),
                    chambers=[
                        {
                            "chamber": f["properties"].get("CHMBCD"),
                            "name": f["properties"].get("CHAMBN"),
                            "lengthFt": f["properties"].get("LENGTH"),
                            "widthFt": f["properties"].get("WIDTH"),
                        }
                        for f in sorted(feats, key=lambda f: str(f["properties"].get("CHMBCD")))
                    ],
                    coordinates={"lat": lat, "lon": lon},
                    route_position=round(along / line.length, 4) if line.length else 0.0,
                    route_distance_nm=round(along / METRES_PER_NM, 2),
                    distance_from_route_nm=round(area.geodesic_distance_nm(line, local), 3),
                    operating_conditions_status="lpms_unavailable",
                    source=Source(
                        system="USACE Locks (IWR Navigation and Civil Works Decision Support)",
                        url=locks_src.LOCKS_LAYER_URL,
                    ),
                )
            )
        locks.sort(key=lambda lk: lk.route_position)

    lpms = Fetched()
    if locks:
        rivers = sorted({lk.river_code for lk in locks})
        lpms = await _cached(
            cache, "lpms-status-" + "-".join(rivers), LPMS_TTL, lambda: locks_src.fetch_lock_status(client, rivers), now
        )
        if lpms.error:
            failures.append(SourceFailure(source="lpms", state=str(lpms.error.state), detail=lpms.error.detail))
        else:
            rows = {locks_src.lock_key(r.get("riverCode"), r.get("lockNo")): r for r in lpms.value}
            for lk in locks:
                row = rows.get(locks_src.lock_key(lk.river_code, lk.lock_code))
                if row is None:
                    lk.operating_conditions_status = "not_reported_by_lpms"
                    continue
                fresh, ages, fnote = _lpms_freshness(row.get("entryDatetime"), now)
                if abs((at - now).total_seconds()) > 3600:
                    fnote += " Conditions are as retrieved, not as of atTime."
                lk.name = row.get("lockName")
                lk.operating_conditions_status = "reported"
                lk.operating_conditions = LockConditions(
                    hours_of_operation=row.get("hoursOfOperation"),
                    queue=Queue(
                        vessels_pending=_int(row.get("totalPendingArrivals")),
                        vessels_locking=_int(row.get("totalLocking")),
                        reported_average_delay_minutes=_num(row.get("average4HourDelay")),
                        reported_at_local=row.get("entryDatetime"),
                    ),
                    lockages_last_24h={
                        "up": _int(row.get("totalLockedUp24Hours")),
                        "down": _int(row.get("totalLockedDown24Hours")),
                    },
                    active_stall_stoppages=row.get("activeStallStoppages"),
                    lpms_ntni_links=row.get("ntniNoticesLinks"),
                    upper_gauge_ft=(str(row["upperGauge"]).strip() if row.get("upperGauge") is not None else None),
                    lower_gauge_ft=(str(row["lowerGauge"]).strip() if row.get("lowerGauge") is not None else None),
                    freshness=fresh,  # type: ignore[arg-type]
                    age_minutes_range=ages,
                    freshness_note=fnote,
                    source=Source(system="Lock Performance Monitoring System (LPMS)", url=locks_src.LPMS_STATUS_URL),
                )

    # ---- notices -------------------------------------------------------------------------------
    notices: dict[int, Notice] = {}
    geoms: dict[int, BaseGeometry] = {}
    geometry_checked = 0
    unverified: dict[int, UnverifiedLocationNotice] = {}
    expired = 0
    not_retrieved: list[int] = []
    candidates: list[dict[str, Any]] = []
    no_geometry_outside = 0
    list_url = ntni.LIST_URL.format(day=ntni.ddmmyyyy(day))
    if active.ok:
        latest: dict[int, dict[str, Any]] = {}
        for rec in active.value:
            cn = _int(rec.get("controlNumber"))
            if cn is None:
                continue
            if cn not in latest or (_int(rec.get("amendmentNumber")) or 0) > (
                _int(latest[cn].get("amendmentNumber")) or 0
            ):
                latest[cn] = rec
        candidates = list(latest.values())
        route_districts = set(dist.value) if dist.ok else None
        index_hit = await cache.get(NO_GEOMETRY_INDEX_KEY, NO_GEOMETRY_TTL)
        no_geo_index: dict[str, float] = dict(index_hit[0]) if index_hit and isinstance(index_hit[0], dict) else {}
        fresh_after = time.time() - NO_GEOMETRY_TTL
        new_no_geo: dict[str, float] = {}
        window = _route_bbox(area, inp.corridor_nm)
        net = asyncio.Semaphore(DETAIL_CONCURRENCY)
        reads = asyncio.Semaphore(CACHE_READ_CONCURRENCY)

        async def detail(rec: dict[str, Any]) -> tuple[dict[str, Any], str, Any]:
            """Classify one notice as `none` (no geometry), `error`, `nomatch` or `match`."""
            cn = int(rec["controlNumber"])
            idx = f"{cn}-{rec.get('amendmentNumber') or 0}"
            if no_geo_index.get(idx, 0) >= fresh_after:
                return rec, "none", None
            key = f"ntni-notice-{idx}"
            async with reads:
                hit = await cache.get(key, DETAIL_TTL)
            if hit is not None and hit[0] is not None:
                d = hit[0]
            else:
                # Fetch, trim and cache inside the semaphore so at most DETAIL_CONCURRENCY payloads are in memory.
                async with net:
                    try:
                        raw = await ntni.fetch_notice_detail(client, cn)
                    except SourceError:
                        return rec, "error", None
                    if raw is None or not raw.get("geojson"):
                        new_no_geo[idx] = time.time()
                        return rec, "none", None
                    d = {k: raw.get(k) for k in DETAIL_FIELDS}
                    del raw
                    try:
                        d["bbox"] = _coords_bbox(ntni.detail_features(d))
                    except SourceError:
                        return rec, "error", None
                    await cache.put(key, d)
            if _bbox_disjoint(d.get("bbox"), window):
                return rec, "nomatch", None
            try:
                geom = _geometry(ntni.detail_features(d))
            except SourceError:
                return rec, "error", None
            if geom is None:
                return rec, "error", None
            rel = _relationship(area, geom)
            return (rec, "match", (d, geom, rel)) if rel is not None else (rec, "nomatch", None)

        for rec, kind, payload in await asyncio.gather(*(detail(r) for r in candidates)):
            cn = int(rec["controlNumber"])
            text = _text(rec)
            if kind == "error":
                not_retrieved.append(cn)
                continue
            if kind == "nomatch":
                geometry_checked += 1
                continue
            if kind == "none":
                t = temporal_status(rec, at, text=text)
                if t.status == "expired":
                    expired += 1
                    continue
                if route_districts is not None and rec.get("districtCode") not in route_districts:
                    no_geometry_outside += 1
                    continue
                refs = river_mile_references(text)
                unverified[cn] = UnverifiedLocationNotice(
                    notice_id=cn,
                    amendment_number=_int(rec.get("amendmentNumber")),
                    title=rec.get("title"),
                    **_temporal_fields(t),
                    location_status="river_mile_text_only" if refs else "unlocated",
                    river_mile_text=[r.text for r in refs],
                    parsed_river_miles=[{"from": r.mile_from, "to": r.mile_to} for r in refs],
                    waterway=_waterway(rec),
                    district=rec.get("districtCode"),
                    official_text=rec.get("remarks"),
                    source=Source(system="Notices to Navigation Interests", url=list_url),
                )
                continue
            geometry_checked += 1
            d, geom, rel = payload
            t = temporal_status(
                rec,
                at,
                text=text,
                begin_at=parse_timestamp(d.get("begin_date")),
                end_at=parse_timestamp(d.get("end_date")),
            )
            if t.status == "expired":
                expired += 1
                continue
            notices[cn] = Notice(
                notice_id=cn,
                amendment_number=_int(rec.get("amendmentNumber")),
                title=rec.get("title") or d.get("title"),
                category=d.get("nn_category"),
                **_temporal_fields(t),
                temporal_note=t.note,
                relationship_to_route=rel,
                waterway=_waterway(rec),
                district=rec.get("districtCode"),
                official_text=rec.get("remarks"),
                attachments=_attachments(rec),
                source=Source(system="Notices to Navigation Interests", url=ntni.NOTICE_URL.format(id=cn)),
            )
            geoms[cn] = geom
        if new_no_geo:
            kept = {k: v for k, v in no_geo_index.items() if v >= fresh_after}
            await cache.put(NO_GEOMETRY_INDEX_KEY, kept | new_no_geo)
    candidate_ids = {_int(r.get("controlNumber")) for r in candidates}
    if upcoming.ok:
        for rec in upcoming.value:
            cn = _int(rec.get("controlNumber"))
            if cn is None or cn in notices or cn in candidate_ids:
                continue
            geom = _geometry(ntni.feed_features(rec))
            if geom is None:
                continue
            rel = _relationship(area, geom)
            if rel is None:
                continue
            t = temporal_status(rec, at, text=_text(rec), in_active_list=False)
            if t.status == "expired":
                expired += 1
                continue
            notices[cn] = Notice(
                notice_id=cn,
                amendment_number=_int(rec.get("amendmentNumber")),
                title=rec.get("title"),
                **_temporal_fields(t),
                temporal_note=t.note,
                relationship_to_route=rel,
                waterway=_waterway(rec),
                district=rec.get("districtCode"),
                official_text=rec.get("remarks"),
                attachments=_attachments(rec),
                source=Source(
                    system="Notices to Navigation Interests", url=ntni.UPCOMING_GEO_URL.format(day=ntni.ddmmyyyy(day))
                ),
            )
            geoms[cn] = geom

    # ---- lock <-> notice association -----------------------------------------------------------
    radius = LOCK_NOTICE_RADIUS_NM * METRES_PER_NM
    for lk in locks:
        lp = area.project(Point(lk.coordinates["lon"], lk.coordinates["lat"]))
        for n in notices.values():
            if area.project(geoms[n.notice_id]).distance(lp) <= radius:
                lk.related_notice_ids.append(n.notice_id)
                n.related_lock_ids.append(lk.lock_id)

    notice_list = sorted(notices.values(), key=lambda n: (n.relationship_to_route.route_position, n.notice_id))
    unverified_list = sorted(unverified.values(), key=lambda n: (n.district or "", n.notice_id))

    # ---- coverage and status -------------------------------------------------------------------
    geo_status = "unavailable" if not active.ok else "partial" if not_retrieved else "complete"
    lpms_cov = SourceCoverage(
        status="not_queried" if not locks or not lock_layer.ok else "success" if lpms.ok else "unavailable",
        checked_at=iso(lpms.fetched_at) if lpms.fetched_at else None,
        from_cache=lpms.from_cache,
        detail=None if locks else "No USACE lock within the route corridor; LPMS not queried.",
    )

    def cov(f: Fetched) -> SourceCoverage:
        return SourceCoverage(
            status="success" if f.ok else "unavailable",
            checked_at=iso(f.fetched_at) if f.fetched_at else None,
            from_cache=f.from_cache,
        )

    matched = len(notice_list) + len(locks)
    complete = (
        active.ok
        and upcoming.ok
        and dist.ok
        and lock_layer.ok
        and geo_status == "complete"
        and lpms_cov.status != "unavailable"
        and not (matched == 0 and unverified_list)
    )
    core_ok = active.ok and lock_layer.ok
    if complete:
        status = "success"
    elif not active.ok and not upcoming.ok and not lock_layer.ok:
        status = "source_unavailable"
    else:
        status = "partial"
    note = _coverage_note(
        status,
        matched,
        len(candidates) if active.ok else None,
        sorted(dist.value) if dist.ok else None,
        no_geometry_outside,
        len(unverified_list),
        not_retrieved,
        failures,
        lpms_cov,
    )
    coverage = Coverage(
        ntni=cov(active),
        ntni_upcoming=cov(upcoming),
        ntni_geometry=NoticeGeometryCoverage(
            status=geo_status,  # type: ignore[arg-type]
            active_notices_considered=len(candidates),
            geometry_checked=geometry_checked,
            no_geometry_published=len(unverified_list) + no_geometry_outside,
            geometry_not_retrieved=sorted(not_retrieved),
            unverified_listing_margin_nm=margin,
            unverified_listing_districts=sorted(dist.value) if dist.ok else None,
            no_geometry_outside_listing_districts=no_geometry_outside,
        ),
        corps_locks=cov(lock_layer),
        lpms=lpms_cov,
        complete_for_requested_sources=complete,
        source_failures=failures,
        coverage_note=note,
    )
    constraints: list[dict[str, Any]] = [
        {
            "routePosition": n.relationship_to_route.route_position,
            "routeDistanceNm": n.relationship_to_route.route_distance_nm,
            "type": "navigation_notice",
            "noticeId": n.notice_id,
            "title": n.title,
            "temporalStatus": n.temporal_status,
            "relatedLockIds": n.related_lock_ids,
        }
        for n in notice_list
    ] + [
        {
            "routePosition": lk.route_position,
            "routeDistanceNm": lk.route_distance_nm,
            "type": "lock",
            "lockId": lk.lock_id,
            "facility": lk.name,
            "operatingConditionsStatus": lk.operating_conditions_status,
            "vesselsPending": lk.operating_conditions.queue.vessels_pending if lk.operating_conditions else None,
            "reportedAverageDelayMinutes": (
                lk.operating_conditions.queue.reported_average_delay_minutes if lk.operating_conditions else None
            ),
            "relatedNoticeIds": lk.related_notice_ids,
        }
        for lk in locks
    ]
    constraints.sort(key=lambda c: (c["routePosition"], 0 if c["type"] == "lock" else 1))
    with_constraints = sum(
        1
        for lk in locks
        if lk.related_notice_ids or (lk.operating_conditions and lk.operating_conditions.active_stall_stoppages)
    )
    summary = Summary(
        notice_count=len(notice_list),
        lock_count=len(locks),
        locks_with_reported_constraints=with_constraints,
        locks_with_operating_conditions=sum(1 for lk in locks if lk.operating_conditions),
        unverified_location_notice_count=len(unverified_list),
        expired_notices_excluded=expired,
        notices_by_temporal_status=dict(Counter(n.temporal_status for n in notice_list)),
    )
    return Result(
        status=status,  # type: ignore[arg-type]
        checked_at=iso(now),
        at_time=iso(at),
        query=Query(
            corridor_nm=inp.corridor_nm, route_length_nm=round(area.route_length_nm or 0, 2), point_count=len(inp.route)
        ),
        coverage=coverage,
        summary=summary,
        constraints=constraints,
        locks=locks,
        notices=notice_list,
        unverified_location_notices=unverified_list,
        billing=billing_decision(status, core_ok, matched),  # type: ignore[arg-type]
    )


def _coverage_note(
    status: str,
    matched: int,
    considered: int | None,
    districts: list[str] | None,
    outside: int,
    unverified: int,
    not_retrieved: list[int],
    failures: list[SourceFailure],
    lpms: SourceCoverage,
) -> str:
    parts: list[str] = []
    if considered is not None:
        parts.append(
            f"Published geometry was checked for all {considered} active NTNI notices, from every USACE district."
        )
    if unverified:
        where = (
            f"from USACE districts {', '.join(districts)} (within {UNVERIFIED_LISTING_MARGIN_NM:g} nm of the corridor)"
            if districts is not None
            else "(district boundaries could not be checked, so all are listed)"
        )
        parts.append(
            f"{unverified} active notices {where} have no published geometry; they are listed in "
            "unverifiedLocationNotices and could not be matched or excluded spatially."
        )
    if outside:
        parts.append(f"{outside} active notices from other districts have no published geometry and are not listed.")
    if not_retrieved:
        parts.append(
            f"Geometry could not be retrieved for {len(not_retrieved)} notices, so notice coverage is incomplete."
        )
    for f in failures:
        parts.append(f"Source {f.source} was not checked ({f.state}).")
    if lpms.status == "unavailable":
        parts.append("LPMS lock operating conditions were not available.")
    if matched == 0:
        if status == "success":
            parts.append(ZERO_MATCH_TEXT)
        else:
            parts.append(
                "No geometry-confirmed constraints were found in the datasets successfully checked, but coverage is "
                "incomplete: this does not mean no constraints exist along the route."
            )
    return " ".join(parts)
