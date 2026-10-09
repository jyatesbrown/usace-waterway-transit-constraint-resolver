"""Manual live check against official USACE services (never run in CI)."""

from __future__ import annotations

import asyncio
import json
import sys
import time

from src.models.input import ActorInput
from src.resolver.run import run_query

OHIO = [
    {"lat": 40.4425, "lon": -80.0150},
    {"lat": 40.5106, "lon": -80.0820},
    {"lat": 40.6463, "lon": -80.4120},
    {"lat": 40.5263, "lon": -80.6275},
    {"lat": 40.1650, "lon": -80.6990},
    {"lat": 39.6650, "lon": -80.8650},
    {"lat": 39.3900, "lon": -81.2700},
]


async def main() -> None:
    at = sys.argv[1] if len(sys.argv) > 1 else None
    t = time.perf_counter()
    out = await run_query(ActorInput.model_validate({"route": OHIO, "corridorNm": 1, "atTime": at}))
    r = out.result.to_record()
    print(json.dumps({k: r[k] for k in ("status", "summary", "billing")}, indent=1))
    print(json.dumps(r["coverage"], indent=1)[:2500])
    for c in r["constraints"]:
        print(c)
    print("elapsed s", round(time.perf_counter() - t, 2))


asyncio.run(main())
