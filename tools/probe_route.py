"""Probe the 9router route a model is served from, before trusting it with money.

Consolidates what took many separate probes to learn, so the next person (or the
next session) does not have to rediscover it. Four questions, in the order that
a wrong answer costs the most:

1. WHICH KEY opens this route? 9router fronts several upstream accounts and the
   model prefix selects which credential is valid. Measured: `cmd/` and `oc/`
   accept COMMANDCODE_API_KEY, but `cbai/` accepts only the NINEROUTER credential
   -- and returns a plain 401 with the others, which reads like a broken key
   rather than a wrong route.

2. DOES IT SUPPORT TOOL CALLING? The whole pipeline is edit-then-diff: without
   tool calls the agent cannot touch a file and every run yields an empty patch.
   This is the check that must pass before a sweep, not after.

3. IS THE PRICE REAL? For a paid route a guessed card makes every dollar figure in
   the thesis fiction. The rate is derived from 9router's own usageHistory, and
   then verified line by line against requests it charged for.

4. IS THE CACHE DISCOUNT REAL? The API may report cache hits that the biller does
   not discount. Measured on cbai: two requests reported real hits (256 and 896
   cached tokens) and were charged charged/full-price = 1.0000. Applying the
   historical cached rate under-reported a run's cost by 2.05x -- in the direction
   that flatters a cost claim, which is the dangerous one.

Usage
-----
    python tools/probe_route.py --model cbai/deepseek-v4.1-flash
    python tools/probe_route.py --model cbai/deepseek-v4.1-flash --derive-rate
    python tools/probe_route.py --model cbai/deepseek-v4.1-flash --check-card
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import dotenv_values, load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)
DB = Path(os.environ.get("APPDATA", "")) / "9router" / "db" / "data.sqlite"
BASE = "http://localhost:20128/v1"


def mask(v: str) -> str:
    return f"{v[:10]}... (len={len(v)})" if v else "<empty>"


def open_ro() -> sqlite3.Connection:
    tmp = Path(tempfile.gettempdir()) / "9router_probe_ro.sqlite"
    shutil.copy2(DB, tmp)
    con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def try_keys(model: str) -> str | None:
    """Return the first credential in .env that this route accepts."""
    from openai import OpenAI

    print("=== 1. which key opens this route? ===")
    raw = dotenv_values(ROOT / ".env")
    candidates = {k: str(v) for k, v in raw.items()
                  if v and ("KEY" in k or "TOKEN" in k) and not str(v).startswith("$")}
    working = None
    for name, key in candidates.items():
        client = OpenAI(api_key=key, base_url=BASE)
        try:
            r = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": "hi"}],
                max_tokens=4, timeout=45,
            )
            print(f"  OK   {name:24s} {mask(key)}  -> "
                  f"{(r.choices[0].message.content or '')[:30]!r}")
            if working is None:
                working = name
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "status_code", None) or type(e).__name__
            print(f"  fail {name:24s} {mask(key)}  -> {code}")
    print()
    if working:
        print(f"  -> use {working} for this route")
    return working


def check_tools(model: str, key: str) -> None:
    """The must-pass check: can the model drive the edit-then-diff pipeline?"""
    from openai import OpenAI

    print("=== 2. tool calling (must pass before a run) ===")
    client = OpenAI(api_key=key, base_url=BASE)
    tools = [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the repository",
            "parameters": {"type": "object",
                           "properties": {"path": {"type": "string"}},
                           "required": ["path"]},
        },
    }]
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user",
                       "content": "Read the file django/db/models/expressions.py. Use the tool."}],
            tools=tools, max_tokens=300, timeout=90,
        )
        msg = r.choices[0].message
        tcs = getattr(msg, "tool_calls", None) or []
        print(f"  finish_reason : {r.choices[0].finish_reason}")
        print(f"  tool_calls    : {len(tcs)}")
        for tc in tcs:
            print(f"    - {tc.function.name}({tc.function.arguments})")
        if not tcs:
            print("  >>> NO TOOL CALL: this model cannot drive the pipeline as-is.")
        u = getattr(r, "usage", None)
        if u is None:
            print("  >>> NO usage object: cost would read $0 even on a paid route.")
        else:
            d = u.model_dump() if hasattr(u, "model_dump") else {}
            print(f"  usage         : prompt={d.get('prompt_tokens')} "
                  f"completion={d.get('completion_tokens')}")
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR: {type(e).__name__}: {e}")
    print()


def derive_rate(model: str) -> dict | None:
    """Solve the rate card from recorded charges, in stages.

    Staged rather than a single 3-parameter fit: most rows are 90%+ cached, so the
    regular and cached columns are nearly proportional and a joint fit trades one
    against the other (it reproduced the total to 2% while missing 61% of rows).
    Rows with cached_tokens == 0 pin regular+output independently; the cached rate
    then follows from the rest.
    """
    if not DB.exists():
        print(f"  no 9router database at {DB}")
        return None
    con = open_ro()
    name = model.split("/")[-1]
    rows = con.execute("""
        SELECT promptTokens pt, completionTokens ct, cost, tokens
        FROM usageHistory WHERE model LIKE ? AND cost > 0 AND promptTokens > 0
    """, (f"%{name}%",)).fetchall()
    if len(rows) < 3:
        print(f"  only {len(rows)} recorded rows for {name}; cannot derive a card")
        return None

    def parse(r):
        cached = 0
        try:
            t = json.loads(r["tokens"]) if r["tokens"] else {}
            for k in ("cached_tokens", "prompt_cache_hit_tokens", "cache_read_input_tokens"):
                if t.get(k):
                    cached = int(t[k])
                    break
        except Exception:  # noqa: BLE001
            pass
        cached = min(cached, r["pt"])
        return {"reg": r["pt"] - cached, "cached": cached,
                "out": r["ct"], "cost": float(r["cost"])}

    S = [parse(r) for r in rows]
    nocache = [s for s in S if s["cached"] == 0]
    print(f"  rows: {len(S)}  (without cache: {len(nocache)})")

    # Stage 1: regular + output from uncached rows.
    best = None
    for i in range(len(nocache)):
        for j in range(i + 1, len(nocache)):
            A, B = nocache[i], nocache[j]
            D = A["reg"] * B["out"] - B["reg"] * A["out"]
            if abs(D) < 1e-6:
                continue
            a = (A["cost"] * B["out"] - B["cost"] * A["out"]) / D
            c = (A["reg"] * B["cost"] - B["reg"] * A["cost"]) / D
            if a <= 0 or c <= 0 or a > 1e-4 or c > 1e-4:
                continue
            err = sum(abs(s["reg"] * a + s["out"] * c - s["cost"]) for s in nocache)
            if best is None or err < best[0]:
                best = (err, a, c)
    if best is None:
        print("  could not solve regular/output rates")
        con.close()
        return None
    _, a, c = best
    print(f"  regular_input = ${a*1e6:.6f} / 1M")
    print(f"  output        = ${c*1e6:.6f} / 1M")

    # Stage 2: cached rate from the rows that have one.
    bs = []
    for s in S:
        if s["cached"] <= 0:
            continue
        resid = s["cost"] - s["reg"] * a - s["out"] * c
        if resid > 0:
            bs.append(resid / s["cached"])
    b = statistics.median(bs) if bs else a
    print(f"  cached_input  = ${b*1e6:.6f} / 1M  (median of {len(bs)} rows)")
    print()
    print("  NOTE: this is what the route charged HISTORICALLY. Check it against")
    print("  --check-card before using it: the route may report cache hits that it")
    print("  then bills at full price (measured on cbai).")
    con.close()
    return {"input_per_million": round(a * 1e6, 6),
            "cached_input_per_million": round(b * 1e6, 6),
            "output_per_million": round(c * 1e6, 6)}


def check_card(model: str, key: str) -> None:
    """Make real requests and compare the charge with what our card predicts."""
    from openai import OpenAI

    print("=== 4. does the card reproduce what is actually charged? ===")
    client = OpenAI(api_key=key, base_url=BASE)
    started = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    reqs = []
    # Two requests sharing a prefix, so a cache hit is possible and the question
    # "is the reported hit actually discounted?" can be answered.
    prefix = "You are reviewing a code patch. The repository is django/django. " * 200
    for i in (1, 2):
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prefix + f"\nQ{i}: say OK"}],
            max_tokens=8, timeout=120,
        )
        u = r.usage
        d = u.model_dump() if hasattr(u, "model_dump") else {}
        details = d.get("prompt_tokens_details") or {}
        reqs.append({"pt": u.prompt_tokens, "ct": u.completion_tokens,
                     "cached": details.get("cached_tokens") or 0})
        print(f"  request {i}: prompt={u.prompt_tokens} completion={u.completion_tokens} "
              f"reported_cached={details.get('cached_tokens')}")
    time.sleep(3)
    ended = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    if not DB.exists():
        print("  no database to compare against")
        return
    con = open_ro()
    name = model.split("/")[-1]
    charged = con.execute("""
        SELECT SUM(cost) c, COUNT(*) n FROM usageHistory
        WHERE model LIKE ? AND timestamp >= ? AND timestamp <= ?
    """, (f"%{name}%", started, ended + ".999Z")).fetchone()
    if not charged or not charged["n"]:
        print("  no matching rows found; cannot compare")
        con.close()
        return

    from evaluation.cost import PricingTable
    card = PricingTable.get(model)
    total_full = total_disc = 0.0
    for q in reqs:
        total_full += q["pt"] * 0.14 / 1e6 + q["ct"] * 0.28 / 1e6
        total_disc += ((q["pt"] - q["cached"]) * 0.14 + q["cached"] * 0.002833) / 1e6 \
                      + q["ct"] * 0.28 / 1e6
    print()
    print(f"  bill charged (9router)          : ${charged['c']:.6f}  ({charged['n']} rows)")
    print(f"  if full price for every token   : ${total_full:.6f}")
    print(f"  if reported cache hits discounted: ${total_disc:.6f}")
    ratio = charged["c"] / total_full if total_full else 0
    print(f"  charged / full-price            : {ratio:.4f}")
    if abs(ratio - 1.0) < 0.02:
        print("  -> FULL PRICE was charged. A reported cache hit is NOT discounted")
        print("     on this route: set cached_input_per_million == input_per_million.")
    elif ratio < 0.9:
        print("  -> a discount WAS applied; the historical cached rate applies.")
    print(f"  card in PricingTable            : {card}")
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe a 9router route before spending money.")
    ap.add_argument("--model", required=True)
    ap.add_argument("--derive-rate", action="store_true")
    ap.add_argument("--check-card", action="store_true")
    ap.add_argument("--all", action="store_true", help="run every check")
    args = ap.parse_args()

    print("=" * 78)
    print(f"  ROUTE PROBE: {args.model}")
    print("=" * 78)
    print()

    key_name = None
    if args.all or not (args.derive_rate or args.check_card):
        key_name = try_keys(args.model)
    if args.all or args.check_card:
        if key_name is None:
            key_name = "OPENCODE_API_KEY"
        key = os.environ.get(key_name) or os.environ.get("OPENCODE_API_KEY", "")
        check_tools(args.model, key)
    if args.all or args.derive_rate:
        print("=== 3. derive the rate card from recorded charges ===")
        derive_rate(args.model)
        print()
    if args.all or args.check_card:
        key = os.environ.get("OPENCODE_API_KEY", "")
        check_card(args.model, key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
