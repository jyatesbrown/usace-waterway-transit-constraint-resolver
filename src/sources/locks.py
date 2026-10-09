"""Official USACE lock facilities (IWR ArcGIS `Locks` layer) and LPMS lock status.

The ArcGIS layer is the facility reference (geometry + `RIVERCD`/`LOCKCD`). LPMS `lock_status_report`
rows are joined to it by river code and lock number; LPMS coordinates are not used (they are swapped
at the source).
"""

from __future__ import annotations

from typing import Any

import httpx

from ..utils.errors import SourceError, SourceState
from ..utils.http import get_json

LOCKS_LAYER_URL = "https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/Locks/FeatureServer/0"
LOCKS_QUERY_URL = LOCKS_LAYER_URL + "/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
LPMS_STATUS_URL = "https://ndc.ops.usace.army.mil/ords/lpms/json/lock_status_report"


def lock_key(river_code: object, lock_no: object) -> tuple[str, str] | None:
    river = str(river_code or "").strip().upper()
    number = str(lock_no or "").strip().lstrip("0") or ("0" if str(lock_no or "").strip() else "")
    return (river, number) if river and number else None


async def fetch_locks(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    data = await get_json(client, LOCKS_QUERY_URL)
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list) or not features:
        raise SourceError(SourceState.INVALID_FORMAT, "Locks layer returned no features")
    return features


async def fetch_lock_status(client: httpx.AsyncClient, river_codes: list[str]) -> list[dict[str, Any]]:
    """One request for all rivers on the route. LPMS reports its rate limit as HTTP 200 + error body."""
    url = f"{LPMS_STATUS_URL}?in_river_codes={','.join(sorted(set(river_codes)))}"
    data = await get_json(client, url)
    if isinstance(data, dict) and "error" in data:
        state = SourceState.RATE_LIMITED if "rate limit" in str(data["error"]).lower() else SourceState.UNAVAILABLE
        raise SourceError(state, f"LPMS: {data['error']}")
    rows = data.get("items", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise SourceError(SourceState.INVALID_FORMAT, "LPMS lock_status_report is not a list")
    return rows
