# Actor #4 launch-readiness report: USACE Inland Waterway Transit Constraint Resolver

**Decision: PRIVATE PLATFORM TEST PASSED; PUBLISHED.** Build 1.0.2 passed the private platform test and was published on 2026-10-09 with owner authorization: https://apify.com/rhincodontypus/usace-waterway-transit-constraint-resolver (categories `TRAVEL`, `AUTOMATION`; settings and pricing unchanged after publishing). The agent benchmark is in `benchmarks/actor4/`.

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

## Notice scope (all districts)
- Published geometry is checked for every active NTNI notice, from every USACE district, on every run (870 in the live runs).
- The district layer only decides which geometry-less notices are listed in `unverifiedLocationNotices`: those from districts within 25 nm of the corridor. The rest are counted in `noGeometryOutsideListingDistricts`.
- If the district layer fails, every geometry-less notice is listed and the run is `partial`. Geometry checking is unaffected.
- Zero confirmed matches with nearby geometry-less notices is `partial` and not charged.

## Tests
- 29 offline tests on official response fixtures, using `respx`; no network calls in CI.
- `ruff check` and `ruff format --check` are clean.

## Live acceptance (`docs/live-acceptance.json`, run 2026-10-09, local, in-memory cache)
The first route warms the shared cache; the later routes reuse it, as they would on the platform.

| Route | First s | Repeat s | Status | Notices | Locks | Unverified | Charged |
|---|---:|---:|---|---:|---:|---:|---|
| Ohio, Pittsburgh–Willow Island (cold cache) | 25.6 | 2.5 | success | 9 | 6 | 36 | yes |
| Upper Mississippi, Lock 27–Cape Girardeau | 3.1 | 2.5 | success | 2 | 1 | 157 | yes |
| Illinois Waterway, Peoria–LaSalle | 2.9 | 2.1 | success | 2 | 0 | 129 | yes |
| Lake Superior, open water | 2.6 | 2.0 | partial (0 confirmed) | 0 | 0 | 39 | no |

## Performance
- **Cold cache:** 23–35 s locally and about 91 s on the platform, because every active notice's detail is fetched (about 75 MB) and cached. Peak memory was about 190 MB locally and 342 MB on the platform.
- **Warm cache:** 2–2.5 s for any route.
- **Caching:** notice geometry 24 h; a "no geometry" index 6 h; LPMS 5 min; notice list 15 min.
- **Warm-path prefilter:** each cached geometry stores its lon/lat bounds. Geometry whose bounds miss a padded window around the corridor (twice the corridor plus 1 nm) cannot intersect it and is counted as checked without being rebuilt. This is exact, not a coverage shortcut.

## Private platform acceptance (build 1.0.2, 512 MB, 180 s, `LIMITED_PERMISSIONS`, Standby off)
| Case | Run | Status | Notices | Locks | Unverified | Run s | Peak MB | Charged check |
|---|---|---|---:|---:|---:|---:|---:|---|
| Ohio, cold shared cache | FbRJa8LAblzEYgztv | success | 10 | 6 | 36 | 90.8 | 342 | yes |
| Ohio, warm | zWalnJnNCbI6rD43K | success | 10 | 6 | 36 | 6.8 | 237 | yes |
| Upper Mississippi | 6q0oxaCqRT8wzgAjw | success | 2 | 1 | 157 | 6.8 | 256 | yes |
| Illinois Waterway | MbAlzhuAtgN9XSbhH | success | 2 | 0 | 129 | 29.1* | 286 | yes |
| Lake Superior (0 confirmed) | AhNKwBXHrkdycNe3a | partial | 0 | 0 | 39 | 7.8 | 228 | no |
| One-point route | ACtvu8hZiecEDOEdY | invalid_input | – | – | – | 3.3 | 65 | no |

\* 14 s of this was the container image pull; route resolution took 5.3 s.

- Every run charged exactly one start event. `waterway-route-check` was charged exactly when the record said `billable`.
- Compute per run: $0.0002–0.0057 (cold Ohio is the maximum).
- **Memory:** at 256 MB every cold run was killed for running out of memory (exit 137, peak 244 MB), including after limiting in-flight notice payloads to 8. The default is therefore 512 MB, with a 180 s timeout because a cold run takes about 91 s. 512 MB is still one start event.

## Compute and PPE
- **Pricing:** start event $0.00005, plus `waterway-route-check` $0.10. Usage passthrough is off.
- **Compute cost:** $0.0002–0.0057 per run at 512 MB (see platform acceptance).
- **Billing rule:** charged on `success`, including zero matches when no nearby notice lacks geometry. Charged on `partial` only when the NTNI list and the Locks layer were both checked and a notice or lock was confirmed on the route.

## Source freshness
Every source records `checkedAt` and `fromCache`. LPMS conditions carry a `freshness` label (`current`, `possibly_stale` or `stale`) and an age range. LPMS states no timezone, so the age assumes UTC-4..UTC-8. When `atTime` is more than an hour from the retrieval time, conditions are labelled as of retrieval.

## Benchmark readiness
The prompt set from the brief is ready to freeze: 10 relevant directed prompts, 5 controls and 5 default-arm bypass prompts, with cross-selection checked against Actor #3. It runs with the Haiku agent and Sonnet judge after the private platform test and publishing.

## Limitations
- Matching is geometry-only. River-mile text is never matched (v2 candidate: the Waterway Network `AMILE`/`BMILE` links plus the river-mile markers).
- Geometry-less notices from districts more than 25 nm beyond the corridor are counted, not listed.
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

## Build 1.0.4: compact record (schema 1.2), public `latest`

- The 1.0.3 record (81k–419k chars) was too large for agents: the benchmark's 60k tool-result cut fell inside `unverifiedLocationNotices` in 20 of 22 runs (`docs/benchmarks/actor4-1.0.3-truncation-evidence.json`). That run was not graded for interpretation.
- 1.0.4 opens with `status`, `summary`, `billing`, `coverage`. It lists at most 25 compact unverified notices, without text or provenance, and gives every unverified notice ID in `unverifiedNoticeListing.noticeIdsByDistrict`. Confirmed-notice text is cut at about 1,000 characters (`officialTextTruncated`). The full record is in key-value store record `FULL_RESULT`. Matching, coverage, status and billing are unchanged.
- Platform acceptance on 1.0.4 covered Columbia, Snake, Ohio, Cumberland, zero-match (r15), two heavy routes (r04, r02) and invalid input. Records were 0.8k–38.8k chars. The charge matched `billing.billable` in every run.
- `latest` → 1.0.4; 1.0.3 kept as rollback under tag `rollback-v103`.
- Reduced benchmark: PASS. See `docs/benchmarks/ACTOR4_1.0.4_REDUCED_BENCHMARK.md`. Actor #4 is in maintenance / observation mode.
