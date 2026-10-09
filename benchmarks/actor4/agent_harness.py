"""Benchmark harness for Actor #4 (adapted from the Actor #2/#3 portfolio harness).

Each prompt is given to a fresh LLM agent whose only tools are the ones the
hosted Apify MCP server exposes. The system prompt is neutral and never names
this Actor. Calls to Actors other than ours are recorded but not executed (to
avoid billing third-party runs); the episode ends at that point.

Usage:
    APIFY_TOKEN=... ANTHROPIC_API_KEY=... python benchmarks/actor4/agent_harness.py --provider anthropic \
        --model claude-haiku-5-5 --arm directed
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

MCP_URL = "https://mcp.apify.com/"
OURS_NAMES = {
    "actor4": "rhincodontypus/usace-waterway-transit-constraint-resolver",
    "actor2": "rhincodontypus/federal-rule-lifecycle-resolver",
    "actor3": "rhincodontypus/uscg-navigation-restriction-resolver",
}
OURS_ACTORS = {
    "rhincodontypus/usace-waterway-transit-constraint-resolver": "actor4",
    "yCe6FapeAhjNo6XiQ": "actor4",
    "rhincodontypus/federal-rule-lifecycle-resolver": "actor2",
    "k5WrcveaXwWLmTnl2": "actor2",
    "rhincodontypus/uscg-navigation-restriction-resolver": "actor3",
    "OCbjQ4jQhPeTh9VnF": "actor3",
}
# Generic retrieval tools the hosted server offers by default. In the default arm they are executed
# so the substitute answer can be graded; every other non-portfolio Actor is intercepted.
GENERIC_EXECUTABLE = {"apify/web-fetch", "apify/rag-web-browser"}
EXCLUDED_TOOLS = {"report-problem", "abort-actor-run"}
# Arm "default": every tool the hosted server offers by default (incl. web-fetch / RAG browser).
# Arm "store": the generic web-retrieval tools are withheld, so data can only come via Store Actors.
WEB_TOOLS = {"apify--web-fetch", "apify--rag-web-browser"}
ACTOR_RUNNING_TOOLS = {"call-actor"}
MAX_TURNS = 14
SYSTEM_PROMPT = (
    "You are an autonomous assistant. Use the available tools when doing so would improve accuracy or "
    "provide information you cannot reliably know yourself. Choose tools based only on their descriptions, "
    "schemas, pricing, and relevance to the user's request. Do not assume any particular tool should be "
    "preferred."
)
# Arm "directed": web tools withheld and the agent is told to work through Store Actors, so that
# Store discovery and selection can be measured. It never names or hints at any particular Actor.
DIRECTED_PROMPT = (
    "You are a helpful assistant that completes requests using Apify Store Actors. Before running "
    "any Actor, use search-actors to find candidate Actors for the request, choose the one that best "
    "fits, then run it and answer the user from its results. If no Actor fits the request, say so and "
    "answer directly without running one."
)
HERE = Path(__file__).parent


class MCP:
    def __init__(self, token: str, arm: str = "default") -> None:
        self.client = httpx.Client(timeout=120)
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._id = 0
        r = self.client.post(
            MCP_URL,
            headers=self.headers,
            json=self._req(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "baseline-harness", "version": "1"},
                },
            ),
        )
        init = self._parse(r)
        self.server_info = init["result"]["serverInfo"]
        self.headers["Mcp-Session-Id"] = r.headers["mcp-session-id"]
        self.headers["Mcp-Protocol-Version"] = "2025-06-18"
        self.client.post(MCP_URL, headers=self.headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = [
            t
            for t in self.rpc("tools/list", {})["tools"]
            if t["name"] not in EXCLUDED_TOOLS and not (arm in ("store", "directed") and t["name"] in WEB_TOOLS)
        ]

    def _req(self, method: str, params: dict) -> dict:
        self._id += 1
        return {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}

    @staticmethod
    def _parse(r: httpx.Response) -> dict:
        for line in r.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        return r.json()

    def rpc(self, method: str, params: dict) -> dict:
        msg = self._parse(self.client.post(MCP_URL, headers=self.headers, json=self._req(method, params)))
        if "error" in msg:
            return {"isError": True, "content": [{"type": "text", "text": json.dumps(msg["error"])}]}
        return msg["result"]

    def call(self, name: str, args: dict) -> dict:
        return self.rpc("tools/call", {"name": name, "arguments": args})


def actor_target(name: str, args: dict) -> str | None:
    if name in ACTOR_RUNNING_TOOLS:
        return str(args.get("actor", ""))
    if name.startswith("apify--") or "--" in name:
        return name.replace("--", "/", 1)
    return None


def ours_key(target: str | None) -> str | None:
    return OURS_ACTORS.get(target.split(":")[0]) if target else None


def is_ours(target: str | None) -> bool:
    return ours_key(target) is not None


RUN_GENERIC = {"on": False}


def executable(target: str | None) -> bool:
    generic = RUN_GENERIC["on"] and bool(target) and target.split(":")[0] in GENERIC_EXECUTABLE
    return is_ours(target) or generic


def search_ranking(result: dict) -> list[str]:
    sc = result.get("structuredContent") or {}
    return [a.get("fullName") for a in sc.get("actors", []) if a.get("fullName")]


def result_text(result: dict) -> str:
    return "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")


class Anthropic:
    def __init__(self, model: str, tools: list[dict], system: str = SYSTEM_PROMPT) -> None:
        self.model = model
        self.system = system
        self.client = httpx.Client(timeout=180)
        self.tools = [
            {"name": t["name"], "description": t.get("description", ""), "input_schema": t["inputSchema"]}
            for t in tools
        ]

    def start(self, prompt: str) -> list:
        return [{"role": "user", "content": prompt}]

    def step(self, messages: list) -> tuple[str, list[dict], dict]:
        r = self.client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.environ.get("ANTHROPIC_API_KEY") or os.environ["BENCHMARK_LLM_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4096,
                "system": self.system,
                "tools": self.tools,
                "messages": messages,
            },
        )
        if r.is_error:
            raise RuntimeError(f"LLM API {r.status_code}: {r.text[:800]}")
        d = r.json()
        messages.append({"role": "assistant", "content": d["content"]})
        text = "\n".join(b["text"] for b in d["content"] if b["type"] == "text")
        calls = [
            {"id": b["id"], "name": b["name"], "args": b["input"]} for b in d["content"] if b["type"] == "tool_use"
        ]
        return text, calls, d.get("usage", {})

    def add_results(self, messages: list, results: list[tuple[dict, str]]) -> None:
        messages.append(
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": c["id"], "content": out} for c, out in results],
            }
        )


class OpenAI:
    def __init__(self, model: str, tools: list[dict], system: str = SYSTEM_PROMPT) -> None:
        self.model = model
        self.system = system
        self.client = httpx.Client(timeout=180)
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", "")[:1024],
                    "parameters": t["inputSchema"],
                },
            }
            for t in tools
        ]

    def start(self, prompt: str) -> list:
        return [{"role": "system", "content": self.system}, {"role": "user", "content": prompt}]

    def step(self, messages: list) -> tuple[str, list[dict], dict]:
        r = self.client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={"model": self.model, "messages": messages, "tools": self.tools},
        )
        if r.is_error:
            raise RuntimeError(f"LLM API {r.status_code}: {r.text[:800]}")
        d = r.json()
        m = d["choices"][0]["message"]
        messages.append({k: v for k, v in m.items() if k in ("role", "content", "tool_calls")})
        calls = [
            {"id": tc["id"], "name": tc["function"]["name"], "args": json.loads(tc["function"]["arguments"] or "{}")}
            for tc in m.get("tool_calls") or []
        ]
        return m.get("content") or "", calls, d.get("usage", {})

    def add_results(self, messages: list, results: list[tuple[dict, str]]) -> None:
        for c, out in results:
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": out})


def run_episode(mcp: MCP, llm, prompt: dict) -> dict:
    ep: dict = {
        "id": prompt["id"],
        "prompt": prompt["prompt"],
        "group": prompt["group"],
        "expectedSelection": prompt["expectedSelection"],
        "category": prompt["category"],
        "portfolioCalls": [],
        "searches": [],
        "detailsFetched": [],
        "actorCalls": [],
        "selected": None,
        "selectionRationale": None,
        "finalAnswer": None,
        "turns": 0,
        "toolTrace": [],
        "usage": [],
    }
    messages = llm.start(prompt["prompt"])
    t0 = time.monotonic()
    for _ in range(MAX_TURNS):
        ep["turns"] += 1
        text, calls, usage = llm.step(messages)
        ep["usage"].append(usage)
        if not calls:
            ep["finalAnswer"] = text
            break
        results, stop = [], False
        for c in calls:
            target = actor_target(c["name"], c["args"])
            entry = {"tool": c["name"], "args": c["args"], "precedingText": text}
            if target and ep["selected"] is None:
                ep["selected"] = target.split(":")[0]
                ep["selectionRationale"] = text or None
            if is_ours(target):
                ep["portfolioCalls"].append(ours_key(target))
            if target and not executable(target):
                entry["executed"] = False
                ep["actorCalls"].append(
                    {"actor": target, "input": c["args"].get("input", c["args"]), "executed": False}
                )
                ep["toolTrace"].append(entry)
                stop = True
                results.append((c, "Not executed in this benchmark environment."))
                continue
            t_call = time.monotonic()
            res = mcp.call(c["name"], c["args"])
            entry["latencyS"] = round(time.monotonic() - t_call, 3)
            out = result_text(res)
            entry["isError"] = bool(res.get("isError"))
            entry["resultText"] = out[:20000]
            if c["name"] == "search-actors":
                ranking = search_ranking(res)
                ep["searches"].append(
                    {
                        "keywords": c["args"].get("keywords"),
                        "args": c["args"],
                        "ranking": ranking,
                        "oursRank": {k: ranking.index(v) + 1 if v in ranking else None for k, v in OURS_NAMES.items()},
                    }
                )
            elif c["name"] == "fetch-actor-details":
                ep["detailsFetched"].append(c["args"].get("actor"))
            if target:
                sc = res.get("structuredContent") or {}
                ep["actorCalls"].append(
                    {
                        "actor": target,
                        "input": c["args"].get("input"),
                        "executed": True,
                        "isError": entry["isError"],
                        "latencyS": entry["latencyS"],
                        "structured": sc,
                        "resultText": out[:4000],
                    }
                )
            ep["toolTrace"].append(entry)
            results.append((c, out[:60000] or "(empty result)"))
        if stop:
            ep["stoppedAt"] = "non-target actor call intercepted"
            break
        llm.add_results(messages, results)
    ep["elapsedS"] = round(time.monotonic() - t0, 2)
    return ep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["anthropic", "openai"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--arm", choices=["default", "store", "directed"], default="default")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    RUN_GENERIC["on"] = a.arm == "default"
    prompts = []
    spec = json.loads((HERE / "prompts.json").read_text())
    for p in spec["relevant"]:
        prompts.append({**p, "group": "actor4", "expectedSelection": OURS_NAMES["actor4"], "category": p["class"]})
    for p in spec["controls"]:
        prompts.append({**p, "group": "control", "expectedSelection": None, "category": p["class"]})
    if a.ids:
        prompts = [p for p in prompts if p["id"] in a.ids]
    out = Path(a.out or HERE.parent / "results" / "actor4" / f"raw-{a.arm}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(line)["id"] for line in out.read_text().splitlines()} if out.exists() else set()
    for p in prompts:
        if p["id"] in done:
            continue
        for attempt in range(4):
            try:
                mcp = MCP(os.environ["APIFY_TOKEN"], a.arm)
                break
            except (ValueError, KeyError, httpx.HTTPError):
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
        llm = (Anthropic if a.provider == "anthropic" else OpenAI)(
            a.model, mcp.tools, DIRECTED_PROMPT if a.arm == "directed" else SYSTEM_PROMPT
        )
        ep = run_episode(mcp, llm, p)
        ep.update(
            {
                "provider": a.provider,
                "model": a.model,
                "arm": a.arm,
                "systemPrompt": DIRECTED_PROMPT if a.arm == "directed" else SYSTEM_PROMPT,
                "mcpServer": mcp.server_info,
                "mcpTools": [t["name"] for t in mcp.tools],
                "startedAt": datetime.now(UTC).isoformat(),
            }
        )
        with out.open("a") as f:
            f.write(json.dumps(ep, ensure_ascii=False) + "\n")
        print(
            p["id"],
            "selected:",
            ep["selected"],
            "| searches:",
            [(s["keywords"], s["oursRank"]) for s in ep["searches"]],
            "|",
            ep["elapsedS"],
            "s",
            flush=True,
        )


if __name__ == "__main__":
    main()
