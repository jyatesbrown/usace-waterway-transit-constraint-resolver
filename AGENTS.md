# AGENTS.md

- Python 3.12, deterministic only: no LLMs, no browsers, no third-party or crowd-sourced production sources.
- Official sources only: USACE NTNI and LPMS (ndc.ops.usace.army.mil/ords), USACE Locks and Civil Works district layers (ArcGIS, services7.arcgis.com/n1YM8pTrFmm7L4hs).
- Tests never hit the network: use fixtures in `tests/fixtures/usace/` and `respx`.
- Confirmed notice matches are geometry-only. Notices without published geometry go to `unverifiedLocationNotices`; never treat them as non-matches.
- Undated notices returned by the active-notices service are `active_by_source`; never infer an expiry or apply an age cutoff.
- A failed or malformed source must become `partial`/`source_unavailable`, never a zero-match success. Never emit safety or passage verdicts.
- LPMS reports rate limits as HTTP 200 with an `error` body; its latitude/longitude are swapped (use ArcGIS lock coordinates).

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
pytest
python -m scripts.live_smoke_test [atTime]
python -m scripts.export_dataset_schema
```
