# Actor #4 launch-readiness report: USACE Inland Waterway Transit Constraint Resolver

**Decision: READY FOR PRIVATE PLATFORM TEST.** Nothing has been deployed to Apify yet; deployment needs separate authorization.

## Source feasibility
All sources are official USACE, need no key, and every one was reached in live runs:
- NTNI active list
- NTNI per-notice detail
- NTNI GeoJSON feed (upcoming notices)
- the `usace_cw_districts` and Locks ArcGIS layers
- LPMS `lock_status_report`

**Geometry coverage:** 161 of 872 active notices publish geometry. The rest are retained in `unverifiedLocationNotices` and are never counted as non-matches.

**Source quirks:**
- **Per-notice detail endpoint:** answers 404 *or* 200 with `geojson: null` when a notice has no geometry. Both are treated as "no geometry published".
- **LPMS:** latitude and longitude are swapped, and the rate limit (5 requests per minute) is reported as HTTP 200 with an `error` body.
- **ArcGIS:** the Locks layer's server-side spatial filter fails. The district query works when `inSR=4326` is given.

## Competition
None. The 12 Store and MCP searches found no Actor covering NTNI, Corps locks or LPMS (from the go/no-go investigation).

## Route and lock joining
- **Locks:** taken from the Locks layer and grouped by `(RIVERCD, LOCKCD)` into one facility with its chambers. A lock matches when its point lies inside the corridor, which is built as metre buffers in a local azimuthal-equidistant projection. Locks are ordered by projection onto the route.
- **LPMS:** rows are joined on `(riverCode, lockNo)` with leading zeros normalized.
- **Lock–notice links:** a notice is related to a lock when the notice geometry lies within 0.25 nm of the lock point.

## Tests
- 25 offline tests on official response fixtures, using `respx`; no network calls in CI.
- `ruff check` and `ruff format --check` are clean.

## Live acceptance (`docs/live-acceptance.json`, run 2026-10-09, local, in-memory cache)
| Route | Cold s | Cached s | Status | Notices | Locks | Unverified |
|---|---:|---:|---|---:|---:|---:|
| Ohio, Pittsburgh–Willow Island | 5.5–10.9 | 2.6–3.1 | success | 9 | 6 | 37 |
| Upper Mississippi, Lock 27–Cape Girardeau | 8.2–8.4 | 3.0 | success* | 2 | 1 | 157 |
| Illinois Waterway, Peoria–LaSalle | 6.2–6.4 | 2.3–2.8 | success | 2 | 0 | 130 |
| Lake Superior, open water | 3.6–4.0 | 2.2–2.4 | success (0 matches) | 0 | 0 | 40 |

\* In the first run this route was `partial`: notice 12461 returned 200 with `geojson: null`, which was counted as a failed retrieval. That is fixed and covered by a test.

## Performance
- **Cached:** 2–3 s, which meets the under-5 s target.
- **Cold:** 4–11 s, which mostly meets the under-10 s target. Cold time scales with the number of notices in the route's districts that have geometry; their detail payloads run from 0.3 to 1.5 MB each.
- **LPMS:** cached for 5 minutes. Notice geometry is cached for 24 h, and "no geometry" results for 6 h.

## Compute and PPE
- **Pricing:** start event $0.00005, plus `waterway-route-check` $0.10. Usage passthrough is off.
- **Compute cost:** to be measured on the platform at 256 MB. A run takes about 3–11 s with light CPU; Actor #3's comparable runs cost $0.0003–0.0014.
- **Billing rule:** charged on `success`, including zero matches. Charged on `partial` only when the NTNI list and the Locks layer were both checked and something matched.

## Source freshness
Every source records `checkedAt` and `fromCache`. LPMS conditions carry a `freshness` label (`current`, `possibly_stale` or `stale`) and an age range. LPMS states no timezone, so the age assumes UTC-4..UTC-8. When `atTime` is more than an hour from the retrieval time, conditions are labelled as of retrieval.

## Benchmark readiness
The prompt set from the brief is ready to freeze: 10 relevant directed prompts, 5 controls and 5 default-arm bypass prompts, with cross-selection checked against Actor #3. It runs with the Haiku agent and Sonnet judge after the private platform test and publishing.

## Limitations
- Matching is geometry-only. River-mile text is never matched (v2 candidate: the Waterway Network `AMILE`/`BMILE` links plus the river-mile markers).
- Active notices from districts more than 25 nm beyond the corridor are not spatially checked. They are counted in coverage.
- Upcoming notices are covered only where they appear in the GeoJSON feed.
- `active_by_source` is USACE's assertion, not independent verification.
- LPMS values are as reported, not predictions.

## Bypass resistance
Likely adequate. A generic agent would need to:
- find the unlisted ORDS endpoints;
- fetch geometry notice by notice;
- intersect a projected corridor;
- order the locks along the route;
- join LPMS.

**Exception:** "delays at locks on the Ohio" can be answered with a single LPMS call.
