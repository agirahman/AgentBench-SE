"""Anatomy of a run: what each act did, what it was granted, what it edited,
what the reviewer said, and how the run was interrupted.

This consolidates the one-off diagnostics written while investigating the budget
curve. Those answered one question each and were then abandoned; this keeps the
answers reproducible with a single command.

Usage
-----
    python tools/analyze_run_anatomy.py              # per_task curve experiments
    python tools/analyze_run_anatomy.py --all        # every experiment on disk
    python tools/analyze_run_anatomy.py --exp EXP-20260929-022
    python tools/analyze_run_anatomy.py --verdicts   # verdict-parsing audit only

What it reports, per run
------------------------
  * the act sequence, compressed (planner x9 -> executor x14 -> reviewer x26 ...)
  * turns GRANTED to each act, from the "hit max_tool_turns=N" warning
  * edits by the BASE executor act vs by the REVISION act
  * the reviewer's verdicts in order, and whether the strategy's own
    _extract_verdict() agrees with the verdict written in the text
  * retries by shape: provider failure vs model stall
  * empty patches and truncation

Why act boundaries are trustworthy
----------------------------------
The acts run in a fixed order (planner -> executor -> reviewer -> executor ->
reviewer), so the first reviewer call ends the BASE executor act and every
executor call after it belongs to the REVISION. That is how "the revision never
edited anything" was established -- and it is the measurement the budget redesign
has to move.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;]*m")
RUN_HDR = re.compile(r"\[(\d+)/(\d+)\] Running (\w+) on (\S+)")
TOOLCALL = re.compile(r"\[toolcall\] role=(\w+) tool=(\w+) args=(\{.*)$")
MAXTURNS = re.compile(r"Tool loop hit max_tool_turns=(\d+) for role=(\w+)")
VERDICT_IN_TEXT = re.compile(r'"verdict"\s*:\s*"([A-Z_]+)"')
EDIT_TOOLS = ("edit_file", "write_file")

# Retry shapes. Provider failures and model stalls are very different events:
# the first is infrastructure, the second is the model answering with neither a
# tool call nor any text (the loop then resends one request).
RETRY_SHAPES = {
    "provider error": re.compile(r"(Error code:|bad_gateway|fetch failed|RESOURCE_EXHAUSTED)", re.I),
    "provider retry": re.compile(r"attempt \d+/\d+ failed", re.I),
    "model stall": re.compile(r"returned a retryable result", re.I),
    "retries exhausted": re.compile(r"failed after \d+ attempts", re.I),
}


def read_log(path: pathlib.Path) -> list[str]:
    """Read a log, tolerating UTF-16 (PowerShell Tee-Object writes UTF-16LE)."""
    raw = path.read_bytes()
    txt = raw.decode("utf-16", errors="ignore") if raw[:2] == b"\xff\xfe" else raw.decode("utf-8", errors="ignore")
    return [ANSI.sub("", ln) for ln in txt.splitlines()]


def _load_extract_verdict():
    """The strategy's real verdict parser, so the audit cannot drift from it."""
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from strategies.review_strategy import _extract_verdict  # type: ignore
        return _extract_verdict, "imported from src/strategies/review_strategy.py"
    except Exception as e:  # noqa: BLE001
        def _extract_verdict(feedback: str) -> str:  # type: ignore
            if not feedback:
                return "NEEDS_REVISION"
            try:
                return json.loads(feedback).get("verdict", "NEEDS_REVISION")
            except json.JSONDecodeError:
                pass
            return "APPROVED" if "APPROVED" in feedback.upper()[:50] else "NEEDS_REVISION"
        return _extract_verdict, f"local copy (import failed: {e})"


def compress(roles: list[str]) -> str:
    out: list[list] = []
    for r in roles:
        if not out or out[-1][0] != r:
            out.append([r, 1])
        else:
            out[-1][1] += 1
    return " -> ".join(f"{r} x{n}" for r, n in out)


def split_runs(lines: list[str]) -> list[tuple[str, str, str, list[str]]]:
    """Split a log into (instance, strategy, label, segment) per run header."""
    marks: list[tuple[str, str, str, int]] = []
    for i, ln in enumerate(lines):
        m = RUN_HDR.search(ln)
        if m:
            inst = m.group(4)
            marks.append((inst, m.group(3), f"{inst.replace('django__django-','')}/{m.group(3)}", i))
    out = []
    for j, (inst, strat, label, a) in enumerate(marks):
        b = marks[j + 1][3] if j + 1 < len(marks) else len(lines)
        out.append((inst, strat, label, lines[a:b]))
    return out


def parse_calls(seg: list[str]) -> list[tuple[str, str, str]]:
    out = []
    for ln in seg:
        m = TOOLCALL.search(ln)
        if m:
            out.append((m.group(1), m.group(2), m.group(3)))
    return out


def act_breakdown(calls: list[tuple[str, str, str]]) -> dict:
    """Edits made by the base executor act vs the revision act."""
    first_reviewer = next((i for i, (r, _, _) in enumerate(calls) if r == "reviewer"), None)
    res = {"base_edits": 0, "rev_calls": 0, "rev_edits": 0, "rev_tools": [],
           "base_paths": [], "rev_paths": [], "has_revision": False}
    if first_reviewer is None:
        return res

    for role, tool, args in calls[:first_reviewer]:
        if role == "executor" and tool in EDIT_TOOLS:
            res["base_edits"] += 1
            p = re.search(r"'path':\s*'([^']+)'", args)
            res["base_paths"].append(p.group(1) if p else "?")
    res["has_revision"] = True

    seen_reviewer = False
    for role, tool, args in calls[first_reviewer:]:
        if role == "reviewer":
            seen_reviewer = True
        elif role == "executor" and seen_reviewer:
            res["rev_calls"] += 1
            res["rev_tools"].append(tool)
            if tool in EDIT_TOOLS:
                res["rev_edits"] += 1
                p = re.search(r"'path':\s*'([^']+)'", args)
                res["rev_paths"].append(p.group(1) if p else "?")
    return res


def verdicts_of(exp_dir: pathlib.Path, instance: str, strategy: str, extract) -> list[dict]:
    msgs = exp_dir / "artifacts" / instance / strategy / "messages.jsonl"
    if not msgs.exists():
        return []
    out = []
    for line in msgs.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("kind") != "result" or e.get("sender") != "reviewer":
            continue
        content = e.get("content") or ""
        if not content.strip():
            continue
        in_text = VERDICT_IN_TEXT.findall(content)
        out.append({
            "in_text": in_text,
            "parsed": extract(content),
            "starts_json": content.lstrip().startswith("{"),
            "head": content[:80],
        })
    return out


def retry_counts(seg: list[str]) -> Counter:
    c: Counter = Counter()
    for ln in seg:
        if "[toolcall]" in ln:      # args can contain numbers like 502; not an event
            continue
        for shape, pat in RETRY_SHAPES.items():
            if pat.search(ln):
                c[shape] += 1
                break
    return c


def experiment_dirs(sel: str | None, all_exp: bool) -> list[pathlib.Path]:
    dirs = []
    for d in sorted((ROOT / "results").glob("EXP-*")):
        if not (d / "logs" / "experiment.log").exists():
            continue
        if sel:
            if d.name == sel:
                dirs.append(d)
            continue
        y = d / "experiment.yaml"
        if all_exp:
            dirs.append(d)
            continue
        if y.exists() and "budget_mode: per_task" in y.read_text(encoding="utf-8", errors="ignore"):
            dirs.append(d)
    return dirs


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-run act anatomy, verdicts and retries.")
    ap.add_argument("--exp", help="one experiment id (e.g. EXP-20260929-022)")
    ap.add_argument("--all", action="store_true", help="every experiment, not just per_task")
    ap.add_argument("--verdicts", action="store_true", help="verdict-parsing audit only")
    args = ap.parse_args()

    extract, src = _load_extract_verdict()
    print(f"verdict parser: {src}\n")

    if args.verdicts:
        print("=" * 104)
        print("VERDICT-PARSING AUDIT -- does _extract_verdict agree with the text?")
        print("=" * 104)
        bad = 0
        total = 0
        for d in experiment_dirs(args.exp, True):
            for inst_dir in sorted((d / "artifacts").glob("*")):
                for strat_dir in sorted(p for p in inst_dir.iterdir() if p.is_dir()):
                    vs = verdicts_of(d, inst_dir.name, strat_dir.name, extract)
                    for v in vs:
                        total += 1
                        actual = v["in_text"][-1] if v["in_text"] else "(no json verdict)"
                        if actual != "(no json verdict)" and v["parsed"] != actual:
                            bad += 1
                            kind = ("FALSE REVISION (needless revision round)"
                                    if v["parsed"] == "NEEDS_REVISION" and actual == "APPROVED"
                                    else "FALSE APPROVAL (unreviewed patch ships)")
                            print(f"\n  {d.name} {inst_dir.name.replace('django__django-','')}/{strat_dir.name}")
                            print(f"    text says {actual}, parser says {v['parsed']}  <-- {kind}")
                            print(f"    starts with '{{': {v['starts_json']}")
                            print(f"    head: {v['head']!r}")
        print(f"\n  responses audited: {total}")
        print(f"  mismatches       : {bad}")
        return 0

    grand = Counter()
    for d in experiment_dirs(args.exp, args.all):
        y = d / "experiment.yaml"
        pool = "?"
        mode = "?"
        if y.exists():
            t = y.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"total_tool_turns:\s*(\d+)", t)
            pool = m.group(1) if m else "?"
            m = re.search(r"budget_mode:\s*(\S+)", t)
            mode = m.group(1) if m else "?"

        print("=" * 104)
        print(f"{d.name}   pool={pool}  mode={mode}")
        print("=" * 104)

        log = d / "logs" / "experiment.log"
        runs = split_runs(read_log(log))
        if not runs:
            print("  (no runs in log)\n")
            continue

        exp_retries: Counter = Counter()
        for inst, strat, label, seg in runs:
            calls = parse_calls(seg)
            roles = [c[0] for c in calls]
            bd = act_breakdown(calls)
            # Scan per line, not over the joined segment: joining removes the
            # newlines, so the role group runs into the next line's timestamp
            # ("executor" + "2026-..." -> "executor2026").
            granted = []
            for ln in seg:
                m = MAXTURNS.search(ln)
                if m:
                    granted.append((int(m.group(1)), m.group(2)))
            retries = retry_counts(seg)
            exp_retries.update(retries)

            patch_file = d / "artifacts" / inst / strat / "patch.txt"
            patch_len = len(patch_file.read_text(encoding="utf-8", errors="ignore")) if patch_file.exists() else None

            flags = []
            # Only a run that actually EXECUTED a revision can be blamed for the
            # revision not editing. A run that approved on the first review never
            # had a revision at all, and flagging it would be noise.
            if strat == "review" and bd["rev_calls"] > 0 and bd["rev_edits"] == 0:
                flags.append("REVISION MADE NO EDIT")
            if granted:
                flags.append("hit cap: " + ", ".join(f"{r}={n}" for n, r in granted))
            if patch_len == 0:
                flags.append("EMPTY PATCH")

            print(f"\n  {label}")
            print(f"    acts        : {compress(roles) if roles else '(none)'}   ({len(calls)} calls)")
            if strat == "review":
                print(f"    edits       : base={bd['base_edits']}  revision={bd['rev_edits']}"
                      f"   revision calls={bd['rev_calls']}")
                if bd["rev_paths"]:
                    print(f"    revision edited: {bd['rev_paths']}")
            if patch_len is not None:
                print(f"    patch       : {patch_len} chars")
            if retries:
                print(f"    retries     : {dict(retries)}")

            vs = verdicts_of(d, inst, strat, extract)
            if vs:
                chain = " -> ".join(v["parsed"] for v in vs)
                print(f"    verdicts    : {chain}")
                for v in vs:
                    actual = v["in_text"][-1] if v["in_text"] else None
                    if actual and actual != v["parsed"]:
                        print(f"      MISMATCH: text={actual} parser={v['parsed']}")
                        flags.append("VERDICT MISMATCH")

            if flags:
                print(f"    >> {'; '.join(flags)}")

            grand["runs"] += 1
            if strat == "review" and bd["rev_calls"] > 0:
                grand["review_with_revision"] += 1
                grand["revision_edits"] += bd["rev_edits"]
            grand["base_edits"] += bd["base_edits"]

        if exp_retries:
            print(f"\n  retries in {d.name}: {dict(exp_retries)}")
        print()

    print("=" * 104)
    print("TOTALS")
    print("=" * 104)
    print(f"  runs analysed                    : {grand['runs']}")
    print(f"  review runs that ran a revision  : {grand['review_with_revision']}")
    print(f"  EDITS made by revision acts      : {grand['revision_edits']}")
    print(f"  edits made by base executor acts : {grand['base_edits']}")
    if grand["review_with_revision"] and grand["revision_edits"] == 0:
        print("""
  The revision act has never changed a patch in these experiments. A revision
  that only reads cannot make the patch better, so any "review" result here is
  the BASE act's output plus one wasted round trip.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
