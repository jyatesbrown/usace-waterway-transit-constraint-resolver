"""Score the Actor #4 benchmark from raw harness episodes.

Usage:
    APIFY_TOKEN=... ANTHROPIC_API_KEY=... python benchmarks/actor4/analyze.py --judge-model <id>
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
from pathlib import Path

import httpx

HERE = Path(__file__).parent
RES = HERE.parent / "results" / "actor4"
API = "https://api.apify.com/v2"
NAMES = {"actor4": "rhincodontypus/usace-waterway-transit-constraint-resolver"}
IDS = {"yCe6FapeAhjNo6XiQ": "actor4"}
OTHER_PORTFOLIO = ("actor2", "actor3")
BY_NAME = {v: k for k, v in NAMES.items()}
PRIMARY_EVENT = {"actor4": "waterway-route-check"}
PRICE = {"apify-actor-start": 0.00005, "waterway-route-check": 0.10}
CAPABILITY = {
    "actor4": "Checks a supplied U.S. inland-waterway route (ordered lat/lon points + corridor) against every active "
    "USACE Notice to Navigation Interests (NTNI) with published geometry, from every USACE district, plus the "
    "official USACE Locks layer and LPMS current lock status. Returns geometry-confirmed notices crossing the "
    "corridor, locks on the route in route order with reported delays/queues/stoppages, temporal status "
    "(active/upcoming/active_by_source = listed active with no end date, source-asserted), nearby notices with no "
    "published geometry listed separately as unverified (never counted as matches or non-matches), official "
    "provenance and coverage. Never states a route is safe or clear.",
}

RUBRIC = {
    "actor4": """You grade whether an assistant's answer correctly interprets the dataset record(s) returned by a
USACE inland-waterway route resolver. Judge ONLY against the record(s). If several records are supplied, grade
the answer against the record(s) it actually describes (match by route/input and content), not simply the last one.
Check, where relevant to the question:
- confirmed constraints: only notices in `notices` (matchedBy geometry, spatiallyVerified true) and locks in `locks`
  are confirmed on the route; counts, names, route order and lock conditions (delays, queue, stoppages) must match;
- unverifiedLocationNotices are nearby notices WITHOUT published geometry: the answer must not present them as
  confirmed on the route, and must not treat them as ruled out;
- temporal status: active / upcoming / active_by_source. active_by_source means USACE lists the notice as active
  with no end date; it is source-asserted, not independently verified as physically in effect, and no expiry is
  inferred. Presenting it as verified-current, or as expired/stale, is an error;
- status success vs partial and coverage: a partial result with zero confirmed matches does NOT establish that no
  constraints exist; lock-condition (LPMS) availability; source failures;
- provenance (official USACE sources).
An answer is wrong if it states a route is safe, clear, unrestricted, hazard-free or guaranteed passable, or turns
zero confirmed matches into absence of constraints; set safetyLanguage=true in that case.
For a record with status partial, fill partialHandling: usesMatches (correctly uses available matches; null if
none), statesMissingCoverage (says coverage is incomplete / unverified notices remain), rejectsWhole (wrongly
dismisses the whole result), assumesComplete (treats coverage as complete). Otherwise partialHandling is null.
Set unverifiedAsConfirmed=true if the answer presents any unverified-location notice as confirmed on the route.
If incorrect, set failureClass to exactly one of: actor_output_ambiguity, agent_reasoning_error,
source_data_limitation, parser_data_defect, store_positioning_problem, judge_rubric_issue.
Return only JSON: {"correct": true|false, "failureClass": null|"...", "errors": ["short labels"],
"safetyLanguage": true|false, "unverifiedAsConfirmed": true|false, "describedRunIds": ["run ids the answer
describes"], "partialHandling": null|{"usesMatches": true|false|null, "statesMissingCoverage": true|false,
"rejectsWhole": true|false, "assumesComplete": true|false}, "notes": "one or two sentences"}.""",
}

BYPASS_RUBRIC = """An AI agent answered a user request WITHOUT using a specialised tool. Classify what it did and
how good the substitute was compared with the specialised tool's capability (described below). If a reference
record from the specialised tool is supplied, use it to judge whether the agent's answer is accurate/complete.
behavior: exactly one of generic_web_browsing, direct_official_source_lookup, manual_multi_source_reconstruction,
manual_geospatial_reasoning, different_actor, answer_from_model_memory, cannot_complete, other.
(direct_official_source_lookup = fetched an official site/API page directly, e.g. ndc.ops.usace.army.mil,
corpslocks.usace.army.mil; manual_multi_source_reconstruction = combined several official documents itself;
manual_geospatial_reasoning = compared coordinates/geometry itself.)
substitute: exactly one of
- equivalent: realistically the same quality/structure/reliability with little extra effort;
- inferior_but_usable: answers the immediate question but loses meaningful structure, provenance, reconciliation
  or certainty;
- materially_inferior: cannot reasonably reproduce the capability without substantial extra tooling/multi-step work
  (including a confident but incomplete/unverified answer);
- failed: no useful answer.
Also set factualErrors=true if the answer contains a statement contradicted by the reference record or the fetched
official pages, and safetyLanguage=true if it declares a route/area safe/clear/unrestricted/hazard-free.
Return only JSON: {"behavior": "...", "substitute": "...", "factualErrors": true|false, "safetyLanguage":
true|false, "notes": "one or two sentences"}."""


def apify(path: str):
    r = httpx.get(f"{API}{path}", headers={"Authorization": f"Bearer {os.environ['APIFY_TOKEN']}"}, timeout=60)
    r.raise_for_status()
    return r.json()


def llm_json(model: str, content: str) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ["BENCHMARK_LLM_API_KEY"]
    for attempt in range(3):
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            timeout=300,
            json={"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": content}]},
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
        if r.status_code in (429, 500, 529) and attempt < 2:
            continue
        r.raise_for_status()
        text = "".join(b["text"] for b in r.json()["content"] if b["type"] == "text")
        return json.loads(text[text.index("{") : text.rindex("}") + 1])
    raise RuntimeError("judge failed")


def key_of(target: str | None) -> str | None:
    if not target:
        return None
    base = target.split(":")[0]
    return BY_NAME.get(base) or IDS.get(base)


def pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


def stats(xs: list[float]) -> dict | None:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {
        "n": len(xs),
        "mean": round(statistics.mean(xs), 2),
        "median": round(statistics.median(xs), 2),
        "min": round(min(xs), 2),
        "max": round(max(xs), 2),
    }


def clip(obj, n: int) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "...[truncated]"


def load(arm: str) -> list[dict]:
    p = RES / f"raw-{arm}.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def invocation_rows(e: dict, cache: dict) -> list[dict]:
    rows = []
    for c in e["actorCalls"]:
        k = key_of(c["actor"])
        if not k:
            continue
        sc = c.get("structured") or {}
        run_id = sc.get("runId")
        if run_id and run_id not in cache:
            run = apify(f"/actor-runs/{run_id}")["data"]
            ds = run.get("defaultDatasetId")
            items = apify(f"/datasets/{ds}/items?clean=1") if ds else []
            cache[run_id] = {"run": run, "record": items[0] if isinstance(items, list) and items else None}
        got = cache.get(run_id) or {"run": {}, "record": None}
        run, rec = got["run"], got["record"]
        status = (rec or {}).get("status")
        rows.append(
            {
                "actor": k,
                "runId": run_id,
                "input": c.get("input"),
                "isError": c.get("isError"),
                "callError": None if run_id else (c.get("resultText") or "")[:400],
                "runStatus": run.get("status"),
                "datasetStatus": status,
                "executionSuccess": run.get("status") == "SUCCEEDED" and rec is not None,
                "valid": run.get("status") == "SUCCEEDED" and rec is not None and status != "invalid_input",
                "runTimeSecs": (run.get("stats") or {}).get("runTimeSecs"),
                "toolLatencyS": c.get("latencyS"),
                "chargedEventCounts": run.get("chargedEventCounts"),
                "billable": ((rec or {}).get("billing") or {}).get("billable"),
                "record": rec,
            }
        )
    return rows


def partial_class(rec: dict) -> str | None:
    if not rec or rec.get("status") not in ("success", "partial"):
        return None
    n = len(rec.get("notices") or []) + len(rec.get("locks") or [])
    if rec["status"] == "partial":
        return "positive_partial" if n else "zero_match_partial"
    return "complete_positive" if n else "complete_zero_match"


def trace_summary(e: dict) -> str:
    out = []
    for t in e["toolTrace"]:
        out.append(f"- {t['tool']} {clip(t['args'], 400)} -> {(t.get('resultText') or '(not executed)')[:1500]}")
    return "\n".join(out) or "(no tool calls)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge-model", default="claude-sonnet-5-5")
    a = ap.parse_args()
    jpath = RES / f"judgments-{a.judge_model}.json"
    judg = json.loads(jpath.read_text()) if jpath.exists() else {}
    cpath = RES / ".run-cache.json"
    cache = json.loads(cpath.read_text()) if cpath.exists() else {}
    arms = {arm: load(arm) for arm in ("default", "directed")}
    reference = {}
    episodes = {}
    for arm, eps in arms.items():
        for e in eps:
            e["_inv"] = invocation_rows(e, cache)
            for r in e["_inv"]:
                if r["record"] and r["actor"] == e["group"] and r["valid"]:
                    reference.setdefault(e["id"], r["record"])
            episodes[(arm, e["id"])] = e
    cpath.write_text(json.dumps(cache))

    rpath = RES / "manual-review.json"
    reviewed = json.loads(rpath.read_text())["items"] if rpath.exists() else {}

    def judged(key: str, content_fn):
        if key not in judg:
            judg[key] = {**llm_json(a.judge_model, content_fn()), "judgeModel": a.judge_model}
            jpath.write_text(json.dumps(judg, indent=1, ensure_ascii=False))
        final = ((reviewed.get(key) or {}).get("review") or {}).get("final")
        return {**judg[key], **final, "overriddenBy": "manual_review"} if final else judg[key]

    summary = {"arms": {}, "crossSelection": {}, "billing": {}, "competitors": {}}
    for arm, eps in arms.items():
        arm_out = {}
        for actor in NAMES:
            rel = [e for e in eps if e["group"] == actor]
            non = [e for e in eps if e["group"] != actor]
            rows = []
            for e in rel:
                ranks = [s["oursRank"][actor] for s in e["searches"] if s["oursRank"].get(actor)]
                inv = [r for r in e["_inv"] if r["actor"] == actor]
                row = {
                    "id": e["id"],
                    "class": e["category"],
                    "rank": min(ranks) if ranks else None,
                    "searched": bool(e["searches"]),
                    "selected": bool(inv) or actor in e.get("portfolioCalls", []),
                    "firstSelection": e["selected"],
                    "invocations": [{k: v for k, v in r.items() if k != "record"} for r in inv],
                    "elapsedS": e.get("elapsedS"),
                    "finalAnswer": e.get("finalAnswer"),
                    "stoppedAt": e.get("stoppedAt"),
                }
                recs = [r for r in inv if r["record"]]
                if recs:
                    # Every record the agent received is shown to the judge, which grades against the one(s) the
                    # answer describes; partial class follows the last valid record.
                    rec = next((r["record"] for r in reversed(recs) if r["valid"]), recs[-1]["record"])
                    row["recordStatus"] = rec.get("status")
                    row["recordCount"] = len(recs)
                    row["partialClass"] = partial_class(rec)
                    budget = 150000 // len(recs)
                    blocks = "\n\n".join(
                        f"DATASET RECORD {i + 1} of {len(recs)} (runId {r['runId']}, input {clip(r['input'], 2000)}):\n"
                        f"{clip(r['record'], budget)}"
                        for i, r in enumerate(recs)
                    )
                    row["interpretation"] = judged(
                        f"interp|{arm}|{e['id']}",
                        lambda e=e, blocks=blocks, actor=actor: (
                            f"{RUBRIC[actor]}\n\nUSER QUESTION:\n{e['prompt']}\n\n{blocks}\n\n"
                            f"ASSISTANT ANSWER:\n{e.get('finalAnswer') or ''}"
                        ),
                    )
                if arm == "default" and not row["selected"]:
                    ref = reference.get(e["id"])
                    row["bypass"] = judged(
                        f"bypass|{arm}|{e['id']}",
                        lambda e=e, ref=ref, actor=actor: (
                            f"{BYPASS_RUBRIC}\n\nSPECIALISED TOOL CAPABILITY:\n{CAPABILITY[actor]}\n\n"
                            f"USER REQUEST:\n{e['prompt']}\n\nAGENT TOOL TRACE:\n{trace_summary(e)}\n\n"
                            f"REFERENCE RECORD FROM SPECIALISED TOOL (same request, other benchmark arm):\n"
                            f"{clip(ref, 60000) if ref else '(none available)'}\n\n"
                            f"AGENT FINAL ANSWER:\n{e.get('finalAnswer') or ''}"
                        ),
                    )
                rows.append(row)
            disc = [r for r in rows if r["rank"]]
            sel = [r for r in rows if r["selected"]]
            invs = [i for r in rows for i in r["invocations"]]
            interp = [r for r in rows if r.get("interpretation")]
            byp = [r for r in rows if r.get("bypass")]
            ranks = [r["rank"] for r in disc]
            m = {
                "relevant": len(rows),
                "discovery": {
                    "surfaced": len(disc),
                    "rate": pct(len(disc), len(rows)),
                    "top1": pct(sum(x <= 1 for x in ranks), len(rows)),
                    "top3": pct(sum(x <= 3 for x in ranks), len(rows)),
                    "top5": pct(sum(x <= 5 for x in ranks), len(rows)),
                    "meanRank": round(statistics.mean(ranks), 2) if ranks else None,
                    "medianRank": statistics.median(ranks) if ranks else None,
                    "searchedAtAll": sum(r["searched"] for r in rows),
                },
                "selection": {
                    "selected": len(sel),
                    "givenDiscovery": pct(sum(r["selected"] for r in disc), len(disc)),
                    "endToEnd": pct(len(sel), len(rows)),
                },
                "invocation": {
                    "calls": len(invs),
                    "firstAttemptValid": pct(
                        sum(r["invocations"][0]["valid"] for r in sel if r["invocations"]), len(sel)
                    ),
                    "eventualValid": pct(sum(any(i["valid"] for i in r["invocations"]) for r in sel), len(sel)),
                    "perCallValid": pct(sum(i["valid"] for i in invs), len(invs)),
                    "executionSuccess": pct(sum(i["executionSuccess"] for i in invs), len(invs)),
                },
                "interpretation": {
                    "graded": len(interp),
                    "correct": sum(r["interpretation"]["correct"] for r in interp),
                    "accuracy": pct(sum(r["interpretation"]["correct"] for r in interp), len(interp)),
                    "failureClasses": dict(
                        collections.Counter(
                            r["interpretation"].get("failureClass")
                            for r in interp
                            if not r["interpretation"]["correct"]
                        )
                    ),
                    "unverifiedAsConfirmed": sum(
                        bool(r["interpretation"].get("unverifiedAsConfirmed")) for r in interp
                    ),
                    "safetyLanguage": sum(bool(r["interpretation"].get("safetyLanguage")) for r in interp),
                },
                "abstention": {
                    "nonRelevantPrompts": len(non),
                    "falseDiscovery": sum(any(s["oursRank"].get(actor) for s in e["searches"]) for e in non),
                    "falseSelection": sum(actor in e.get("portfolioCalls", []) for e in non),
                    "falseInvocation": sum(any(r["actor"] == actor and r["runId"] for r in e["_inv"]) for e in non),
                    "falseSelectionRate": pct(sum(actor in e.get("portfolioCalls", []) for e in non), len(non)),
                },
                "latency": {
                    "actorRunTimeSecs": stats([i["runTimeSecs"] for i in invs]),
                    "toolRoundTripS": stats([i["toolLatencyS"] for i in invs]),
                    "episodeS": stats([r["elapsedS"] for r in rows]),
                },
                "firstSelections": dict(collections.Counter(r["firstSelection"] for r in rows)),
            }
            if arm == "default":
                nb = len(byp)
                m["bypass"] = {
                    "bypassed": nb,
                    "bypassRate": pct(nb, len(rows)),
                    "behavior": dict(collections.Counter(r["bypass"]["behavior"] for r in byp)),
                    "substitute": dict(collections.Counter(r["bypass"]["substitute"] for r in byp)),
                    "equivalentBypass": sum(r["bypass"]["substitute"] == "equivalent" for r in byp),
                    "equivalentBypassRate": pct(sum(r["bypass"]["substitute"] == "equivalent" for r in byp), len(rows)),
                    "factualErrors": sum(bool(r["bypass"].get("factualErrors")) for r in byp),
                    "safetyLanguage": sum(bool(r["bypass"].get("safetyLanguage")) for r in byp),
                }
            if True:
                pc = collections.defaultdict(lambda: [0, 0])
                for r in interp:
                    c = r.get("partialClass") or "other"
                    pc[c][0] += 1
                    pc[c][1] += r["interpretation"]["correct"]
                ph = [
                    r["interpretation"].get("partialHandling")
                    for r in interp
                    if r["interpretation"].get("partialHandling")
                ]
                m["partialAnalysis"] = {
                    "byClass": {
                        k: {"graded": v[0], "correct": v[1], "accuracy": pct(v[1], v[0])} for k, v in pc.items()
                    },
                    "partialHandling": {
                        f: sum(bool(h.get(f)) for h in ph)
                        for f in ("usesMatches", "statesMissingCoverage", "rejectsWhole", "assumesComplete")
                    },
                    "partialGraded": len(ph),
                }
            comp = collections.Counter()
            ahead = collections.Counter()
            best = {}
            for e in rel:
                for s in e["searches"]:
                    ours = s["oursRank"].get(actor)
                    for i, n in enumerate(s["ranking"]):
                        if n in BY_NAME:
                            continue
                        comp[n] += 1
                        best[n] = min(best.get(n, 99), i + 1)
                        if ours is None or i + 1 < ours:
                            ahead[n] += 1
            chosen = collections.Counter(
                r["firstSelection"]
                for r in rows
                if r["firstSelection"]
                and not key_of(r["firstSelection"])
                and r["firstSelection"] not in ("apify/web-fetch", "apify/rag-web-browser")
            )
            m["competitors"] = {
                "appearances": dict(comp.most_common(15)),
                "bestRank": {n: best[n] for n, _ in comp.most_common(15)},
                "outrankedOursOrOursAbsent": dict(ahead.most_common(15)),
                "selectedInsteadOfOurs": dict(chosen),
            }
            arm_out[actor] = {"metrics": m, "rows": rows}
        controls = [e for e in eps if e["group"] == "control"]
        arm_out["controls"] = {
            "n": len(controls),
            "surfacedOurs": {k: sum(any(s["oursRank"].get(k) for s in e["searches"]) for e in controls) for k in NAMES},
            "selectedOurs": {k: sum(k in e.get("portfolioCalls", []) for e in controls) for k in NAMES},
            "firstSelections": dict(collections.Counter(e["selected"] for e in controls)),
            "rows": [
                {
                    "id": e["id"],
                    "class": e["category"],
                    "selected": e["selected"],
                    "portfolioCalls": e.get("portfolioCalls"),
                    "surfaced": {k: [s["oursRank"].get(k) for s in e["searches"]] for k in NAMES},
                }
                for e in controls
            ],
        }
        rel_all = [e for e in eps if e["group"] in NAMES]
        summary["crossSelection"][arm] = {
            **{
                f"{k}SelectedForActor4Prompts": sum(k in e.get("portfolioCalls", []) for e in rel_all)
                for k in OTHER_PORTFOLIO
            },
            "actor4SelectedOnControls": sum("actor4" in e.get("portfolioCalls", []) for e in controls),
            "otherPortfolioSelectedOnControls": {
                k: sum(k in e.get("portfolioCalls", []) for e in controls) for k in OTHER_PORTFOLIO
            },
        }
        summary["arms"][arm] = arm_out
    for actor in NAMES:
        runs = [v["run"] for v in cache.values() if key_of(v["run"].get("actId")) == actor]
        ev = collections.Counter()
        for r in runs:
            ev.update(r.get("chargedEventCounts") or {})
        unexpected = [
            r["id"]
            for r in runs
            if (r.get("chargedEventCounts") or {}).get("apify-actor-start", 0) != 1
            or (r.get("chargedEventCounts") or {}).get(PRIMARY_EVENT[actor], 0)
            != int(bool(((cache.get(r["id"]) or {}).get("record") or {}).get("billing", {}).get("billable")))
        ]
        summary["billing"][actor] = {
            "actorStarts": len(runs),
            "eventCounts": dict(ev),
            "nominalValueUsd": round(sum(PRICE.get(k, 0) * v for k, v in ev.items()), 5),
            "platformUsageUsd": round(sum(r.get("usageTotalUsd") or 0 for r in runs), 5),
            "unexpectedChargeRuns": unexpected,
        }
    (RES / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
    for arm, eps in arms.items():
        for grp, fname in (("actor4", "actor4"), ("control", "controls")):
            with (RES / f"{fname}-{arm}.jsonl").open("w") as f:
                for e in eps:
                    if e["group"] == grp:
                        f.write(json.dumps({k: v for k, v in e.items() if k != "_inv"}, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                arm: {
                    k: v["metrics"] if "metrics" in v else {kk: vv for kk, vv in v.items() if kk != "rows"}
                    for k, v in d.items()
                }
                for arm, d in summary["arms"].items()
            },
            indent=1,
            default=str,
        )[:20000]
    )
    print(json.dumps({"cross": summary["crossSelection"], "billing": summary["billing"]}, indent=1))


if __name__ == "__main__":
    main()
