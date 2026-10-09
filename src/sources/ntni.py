"""USACE Notices to Navigation Interests (NTNI), official ORDS services on ndc.ops.usace.army.mil.

- `json_data/notices/{DDMMYYYY}`: the active-notice list (no geometry).
- `json_data/notices_geoJson/{DDMMYYYY}`: notices *beginning on or after* that date, with geometry
  (used for upcoming notices).
- `leaflet_json/notice/{id}`: one notice with geometry and begin/end timestamps; HTTP 404 when no
  geometry is published for it.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx
from shapely.geometry import LineString

from ..utils.errors import SourceError, SourceState
from ..utils.http import get_json

NTNI_BASE = "https://ndc.ops.usace.army.mil/ords/ntni"
LIST_URL = NTNI_BASE + "/json_data/notices/{day}"
UPCOMING_GEO_URL = NTNI_BASE + "/json_data/notices_geoJson/{day}"
NOTICE_URL = NTNI_BASE + "/leaflet_json/notice/{id}"
DISTRICTS_URL = "https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/usace_cw_districts/FeatureServer/0"
DISTRICT_ROUTE_SIMPLIFY_DEGREES = 0.01
DISTRICT_SIMPLIFY_MARGIN_NM = 1.0


def ddmmyyyy(day: date) -> str:
    return day.strftime("%d%m%Y")


def _records(data: Any, what: str) -> list[dict[str, Any]]:
    rows = data.get("items", data) if isinstance(data, dict) else data
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise SourceError(SourceState.INVALID_FORMAT, f"{what} is not a list of notices")
    return rows


async def fetch_active_notices(client: httpx.AsyncClient, day: date) -> list[dict[str, Any]]:
    return _records(await get_json(client, LIST_URL.format(day=ddmmyyyy(day))), "NTNI notice list")


async def fetch_upcoming_geo(client: httpx.AsyncClient, day: date) -> list[dict[str, Any]]:
    return _records(await get_json(client, UPCOMING_GEO_URL.format(day=ddmmyyyy(day))), "NTNI GeoJSON feed")


async def fetch_notice_detail(client: httpx.AsyncClient, control_number: int) -> dict[str, Any] | None:
    """`None` means USACE publishes no geometry for this notice (the endpoint answers 404)."""
    try:
        data = await get_json(client, NOTICE_URL.format(id=control_number))
    except SourceError as exc:
        if exc.state == SourceState.NOT_FOUND:
            return None
        raise
    if not isinstance(data, dict) or "id" not in data:
        raise SourceError(SourceState.INVALID_FORMAT, f"NTNI notice {control_number}: unexpected shape")
    return data


async def fetch_route_districts(
    client: httpx.AsyncClient, lonlat: list[tuple[float, float]], margin_nm: float
) -> list[str]:
    """USACE Civil Works district symbols (NTNI `districtCode`) within `margin_nm` of the route."""
    line = LineString(lonlat).simplify(DISTRICT_ROUTE_SIMPLIFY_DEGREES)
    geometry = {"paths": [[[round(x, 5), round(y, 5)] for x, y in line.coords]], "spatialReference": {"wkid": 4326}}
    params = {
        "geometry": json.dumps(geometry, separators=(",", ":")),
        "geometryType": "esriGeometryPolyline",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": f"{margin_nm + DISTRICT_SIMPLIFY_MARGIN_NM:g}",
        "units": "esriSRUnit_NauticalMile",
        "outFields": "SYMBOL",
        "returnGeometry": "false",
        "f": "json",
    }
    url = str(httpx.URL(DISTRICTS_URL + "/query", params=params))
    data = await get_json(client, url)
    if not isinstance(data, dict) or "error" in data or not isinstance(data.get("features"), list):
        raise SourceError(SourceState.INVALID_FORMAT, f"district query failed: {str(data)[:200]}")
    return sorted({str(f["attributes"]["SYMBOL"]) for f in data["features"] if f.get("attributes", {}).get("SYMBOL")})


def detail_features(detail: dict[str, Any]) -> list[dict[str, Any]]:
    raw = detail.get("geojson")
    try:
        fc = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise SourceError(SourceState.INVALID_FORMAT, f"notice {detail.get('id')}: invalid geojson") from exc
    feats = fc.get("features") if isinstance(fc, dict) else None
    return [f for f in feats or [] if isinstance(f, dict) and f.get("geometry")]


def feed_features(record: dict[str, Any]) -> list[dict[str, Any]]:
    shape = (record.get("geoJson") or {}).get("shape_clob") or {}
    return [f for f in shape.get("features") or [] if isinstance(f, dict) and f.get("geometry")]
