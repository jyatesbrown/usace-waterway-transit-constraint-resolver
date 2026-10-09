# Actor #4 reduced benchmark — build 1.0.4 (compact record)

**Decision: PASS — Actor #4 enters maintenance / observation mode.** Remaining failures are ordinary agent reasoning errors, not product defects; no further feature cycle is started from them.

- Agent: `claude-haiku-5-5`. Routine judge: `claude-sonnet-5-5` (unchanged rubric). Opus calls: 0 (no Sonnet/manual disagreements).
- Target: public `latest` = build 1.0.4. Rollback baseline: build 1.0.3, tag `rollback-v103`.
- Manual review: all 7 failed judgments, 0 ambiguous, plus seeded 10% of passes (seed 20261008: 1 item). All judge verdicts upheld.
- Raw data: `docs/benchmarks/actor4-1.0.4/` (episodes, judgments, manual review, summary).
- The earlier 1.0.3 campaign is kept ungraded as evidence the verbose record exceeded the agent's 60k tool-result cut: `docs/benchmarks/actor4-1.0.3-truncation-evidence.json` (20 of 22 records cut, max 404,137 chars, always inside `unverifiedLocationNotices`).

## Results

| | Directed (10 relevant, 5 controls) | Default arm (5 relevant, web tools) |
|---|---:|---:|
| Actor #4 discovered / selected | 10/10 / 10/10 | 2/5 / 2/5 |
| Valid first call | 10/10 | 2/2 |
| Interpretation correct | 7/10 | 1/2 |
| Unverified notice presented as confirmed | 0 | 0 |
| Safety/clearance language | 0 | 0 |
| Bypass | – | 3/5, all materially inferior, honest hedges, 0 factual errors; 0 equivalent bypasses |
| Actor #4 picked on controls | 0/5 | – |
| Actor #3 picked for Actor #4 prompts | 0 | 0 |

- **Output size:** largest tool result in any episode 40,215 chars; no result exceeded the 60,000-char cut.
- **Partial / zero-match:** every partial result (r08, r10, r13 LPMS `not_published_for_river`; r15, r16 zero-match) was described as partial; both zero-match answers said the route could not be cleared and that the run was not billed.
- **Cross-selection:** control c04 (Coast Guard safety zones, New York Harbor) correctly selected Actor #3, not Actor #4.
- **Billing:** 12 Actor #4 runs, 12 start events, 9 route checks; the 3 uncharged runs were the zero-match partial runs. No unexpected charges.

## Failures (all `agent_reasoning_error`)

- r01 (directed): two `active_by_source` notices presented as plainly active; unverified total and truncation not stated.
- r04 (directed): `active_by_source` notices grouped as "may be stale"; a geometry-confirmed notice doubted.
- r14 (directed): district codes LRN/SAM decoded as Louisville/Savannah (they are Nashville/Mobile).
- r16 (default): unverified notices dismissed on outside geography and called possibly stale; zero-match partial itself reported correctly.

The "may be stale / superseded" reading of `active_by_source` persists from 1.0.3 (about 7 of 19 then, 2 of 12 now). The record already states that the status is source-asserted and not an expiry; further wording changes are not pursued under the stopping rule.
