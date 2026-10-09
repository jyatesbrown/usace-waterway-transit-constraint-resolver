from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from .errors import SourceError, SourceState

USER_AGENT = (
    "USACEWaterwayTransitConstraintResolver/1.0 "
    "(Apify Actor; +https://github.com/jyatesbrown/usace-waterway-transit-constraint-resolver)"
)
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.5
TRANSIENT_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def create_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=8),
    )


async def get_json(client: httpx.AsyncClient, url: str, no_data_bodies: tuple[str, ...] = ()) -> Any:
    """GET JSON with bounded retries on transient failures; raise `SourceError` otherwise.

    Returns None when the body is exactly one of `no_data_bodies` (a source's plain-text "no data" reply).
    """
    last: SourceError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.get(url, headers={"Accept": "application/json"})
        except httpx.TimeoutException as exc:
            last = SourceError(SourceState.TIMEOUT, f"{url}: {type(exc).__name__}")
        except httpx.HTTPError as exc:
            last = SourceError(SourceState.UNAVAILABLE, f"{url}: {type(exc).__name__}: {exc}")
        else:
            status = response.status_code
            if status in TRANSIENT_STATUSES:
                last = SourceError(SourceState.UNAVAILABLE, f"{url}: HTTP {status}")
            elif status == 404:
                raise SourceError(SourceState.NOT_FOUND, f"{url}: HTTP 404")
            elif status >= 400:
                raise SourceError(SourceState.UNAVAILABLE, f"{url}: HTTP {status}")
            else:
                if no_data_bodies and response.text.strip() in no_data_bodies:
                    return None
                try:
                    return json.loads(response.content)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise SourceError(SourceState.INVALID_FORMAT, f"{url}: invalid JSON: {exc}") from exc
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
    assert last is not None
    raise last
