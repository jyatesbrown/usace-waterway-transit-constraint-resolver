"""Live acceptance against official USACE services (manual; never in CI). Writes docs/live-acceptance.json."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.models.input import ActorInput
from src.resolver.run import run_query
from src.sources.cache import KeyValueCache
from src.utils.http import create_client


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get_value(self, key: str) -> Any:
        return self.data.get(key)

    async def set_value(self, key: str, value: Any) -> None:
        self.data[key] = value


CASES = {
    "ohio_pittsburgh_to_willow_island": [
        [40.4425, -80.0150],
        [40.5106, -80.0820],
        [40.6463, -80.4120],
        [40.5263, -80.6275],
        [40.1650, -80.6990],
        [39.6650, -80.8650],
        [39.3900, -81.2700],
    ],
    "upper_mississippi_lock27_to_cape_girardeau": [
        [38.70, -90.17],
        [38.63, -90.18],
        [38.25, -90.37],
        [37.95, -89.95],
        [37.60, -89.52],
        [37.30, -89.52],
    ],
    "illinois_waterway_peoria_lasalle": [[40.69, -89.59], [40.86, -89.48], [41.15, -89.30], [41.32, -89.10]],
    "lake_superior_open_water": [[47.6, -88.0], [47.4, -87.0]],
}


async def collect() -> dict[str, Any]:
    out: dict[str, Any] = {}
    cache = KeyValueCache(MemoryStore())
    async with create_client() as client:
        for name, pts in CASES.items():
            route = [{"lat": a, "lon": b} for a, b in pts]
            for attempt in ("cold", "cached"):
                t = time.perf_counter()
                o = await run_query(
                    ActorInput.model_validate({"route": route, "corridorNm": 1}), client=client, cache=cache
                )
                r = o.result.to_record()
                out[f"{name}/{attempt}"] = {
                    "seconds": round(time.perf_counter() - t, 2),
                    "status": r["status"],
                    "billable": r["billing"]["billable"],
                    "summary": r["summary"],
                    "ntniGeometry": r["coverage"]["ntniGeometry"],
                    "lpms": r["coverage"]["lpms"]["status"],
                    "failures": r["coverage"]["sourceFailures"],
                    "constraints": r["constraints"],
                    "coverageNote": r["coverage"]["coverageNote"],
                }
                print(name, attempt, out[f"{name}/{attempt}"]["seconds"], r["status"], r["summary"], flush=True)
                if attempt == "cold":
                    await asyncio.sleep(13)  # stay under LPMS 5 req/min
    try:
        ActorInput.model_validate({"route": [{"lat": 40, "lon": -80}]})
    except ValidationError as exc:
        out["invalid_single_point"] = {"status": "invalid_input", "error": str(exc.errors()[0]["msg"])}
    return out


def main() -> None:
    out = asyncio.run(collect())
    Path("docs").mkdir(exist_ok=True)
    Path("docs/live-acceptance.json").write_text(json.dumps(out, indent=1) + "\n")


main()
