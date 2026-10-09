# USACE Inland Waterway Transit Constraint Resolver

You supply an inland-waterway route. The Actor checks it against official U.S. Army Corps of Engineers (USACE) navigation information and returns one structured record. The record covers:

- **Navigation notices (NTNI)** whose published geometry intersects the route corridor, with official text (verbatim, cut at about 1,000 characters when longer, with `officialTextTruncated` and `officialTextLength`), temporal status and route position.
- **USACE locks** within the corridor, in route order, with the official river and lock codes, river mile and chambers.
- **Lock operating conditions** reported by LPMS: vessels pending, vessels locking, the reported 4-hour average delay, lockages in the last 24 h, stall stoppages and gauges. Each comes with a freshness label. Where LPMS answers "Data Unavailable" for a river (e.g. Columbia, Snake), locks are listed with `operatingConditionsStatus: "not_published_for_river"`, LPMS coverage is `not_published_for_river` and the result is `partial`: lock conditions are unknown, not a source failure.
- **`unverifiedLocationNotices`**: active notices from USACE districts within 25 nm of the corridor that publish no geometry. They are retained with their original river-mile wording and the parsed miles, but are **not** route matches. Each entry is compact: ID, title, dates and temporal status, location text, district, waterway and its official NTNI record URL, without the notice text. At most 25 are itemised; `unverifiedNoticeListing` gives the total, whether the list is truncated, and every notice ID by district.
- **Coverage** for every source, so incomplete checks are explicit.

The record opens with `status`, `summary` (confirmed match count, unverified notice count, how many are listed), `billing` and `coverage` (with a deterministic coverage note), so the decision-critical facts come first. A normal record stays well under 60,000 characters even on notice-heavy routes. The uncut record, with every unverified notice and its full official text, is in the run's default key-value store under `FULL_RESULT` (`fullRecordKey`).

## What it does not do

- It does not plan routes. The route is yours, and the corridor (default 1 nm each side) must cover the channel you mean.
- It makes no safety, passage or clearance determination and is not navigational advice. A zero-match result means only: *"No matching constraints were found in the official datasets successfully checked."*
- It does not predict delays. Queue and delay values are LPMS's reported values at retrieval time.
- It does not match by river mile in v1. That would require mapping each mile marker to your route deterministically.

## Official sources

| Source | Endpoint | Used for |
|---|---|---|
| NTNI active notices | `ndc.ops.usace.army.mil/ords/ntni/json_data/notices/{DDMMYYYY}` | Active notice set, official text, dates |
| NTNI notice detail | `.../ntni/leaflet_json/notice/{id}` (404 = no geometry published) | Notice geometry, begin/end timestamps |
| NTNI GeoJSON feed | `.../ntni/json_data/notices_geoJson/{DDMMYYYY}` | Upcoming notices with geometry |
| USACE Civil Works districts | ArcGIS `usace_cw_districts` | Which geometry-less notices to list as nearby |
| USACE Locks | ArcGIS `Locks/FeatureServer/0` | Lock locations and identifiers |
| LPMS | `ndc.ops.usace.army.mil/ords/lpms/json/lock_status_report` | Current lock operating conditions |

## Semantics

- **Temporal status:**
  - `active`, `upcoming` and `expired` come from official dates or timestamps.
  - `active_by_source` is a notice with no end date that the official active-notices service still returns. USACE asserts it is active; it is not derived from an end date and has not been verified independently.
  - Such notices are kept regardless of age. `endDate` is `null`, `endDateStatus` is `not_provided`, and `ageDays` is included.
  - `unknown` covers an inconsistent date pair or a query time on a date-only boundary.
  - Expired notices are excluded and counted.
- **Spatial check:**
  - Published geometry is checked for every active notice, from every USACE district.
  - Geometry-less notices from districts within 25 nm of the corridor are listed in `unverifiedLocationNotices`; others are counted only.
  - A notice whose geometry can't be retrieved makes coverage `partial`.
- **Status:**
  - `success`: every source was checked.
  - `partial`: a source, or some notice geometry, was not checked; or nothing was confirmed on the route while nearby notices have no published geometry.
  - `source_unavailable`: no core source answered.
  - `invalid_input`: the route was rejected.
- **Lock freshness:** LPMS states no timezone. `current` means the report is at most 6 h old assuming UTC-4..UTC-8; otherwise it is `possibly_stale` or `stale`.

## Pricing (pay per event)

- `apify-actor-start`: $0.00005.
- `waterway-route-check`: $0.10, charged when:
  - the run succeeded (including zero matches with no nearby geometry-less notices), or
  - the result is partial, both core sources (the NTNI list and the Locks layer) were checked, and something matched.
- Never charged for invalid input, unavailable sources, or partial zero-match results (including zero confirmed matches with nearby geometry-less notices).

## Input

```json
{"route": [{"lat": 40.4425, "lon": -80.015}, {"lat": 40.5263, "lon": -80.6275}], "corridorNm": 1, "atTime": "2026-10-13T15:00:00Z"}
```
