"""Select judgments for manual review: every failed/ambiguous one plus a seeded 10% of passes.

python benchmarks/actor4/review_sample.py [--judge-model claude-sonnet-5-5]
"""

import argparse
import json
import math
import random
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "results" / "actor4"
SEED = 20261008


def classify(key: str, v: dict) -> str:
    if key.startswith("interp|"):
        if v.get("correct") is None or v.get("failureClass") == "judge_rubric_issue":
            return "ambiguous"
        if not v["correct"] or v.get("safetyLanguage") or v.get("enforceabilityOverclaim"):
            return "failed"
        return "pass"
    sub = v.get("substitute")
    if sub in ("failed", "materially_inferior") or v.get("factualErrors") or v.get("safetyLanguage"):
        return "failed"
    if sub == "inferior_but_usable" or sub is None:
        return "ambiguous"
    return "pass"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge-model", default="claude-sonnet-5-5")
    a = ap.parse_args()
    judg = json.loads((RES / f"judgments-{a.judge_model}.json").read_text())
    buckets: dict[str, list[str]] = {"failed": [], "ambiguous": [], "pass": []}
    for k, v in judg.items():
        buckets[classify(k, v)].append(k)
    passes = sorted(buckets["pass"])
    sample = sorted(random.Random(SEED).sample(passes, math.ceil(len(passes) * 0.1))) if passes else []
    out = RES / "manual-review.json"
    prev = json.loads(out.read_text())["items"] if out.exists() else {}
    items = {}
    for reason, keys in (("failed", buckets["failed"]), ("ambiguous", buckets["ambiguous"]), ("pass_sample", sample)):
        for k in sorted(keys):
            items[k] = prev.get(k) or {"reason": reason, "judge": judg[k], "review": None}
    out.write_text(
        json.dumps(
            {
                "judgeModel": a.judge_model,
                "seed": SEED,
                "counts": {r: len(b) for r, b in buckets.items()} | {"passSample": len(sample)},
                "items": items,
            },
            indent=1,
            ensure_ascii=False,
        )
    )
    print(json.dumps({r: len(b) for r, b in buckets.items()} | {"passSample": len(sample)}))


if __name__ == "__main__":
    main()
