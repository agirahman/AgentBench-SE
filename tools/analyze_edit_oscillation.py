"""Is the long run making PROGRESS or OSCILLATING?

A long run has three possible shapes, and they mean very different things for
the budget curve:

  PROGRESS     each edit moves toward a solution. A bigger pool helps.
  OSCILLATION  edits undo each other (add an import, remove it, add it back).
               A bigger pool does NOT help -- it funds thrashing. If this is what
               the long runs are, then "more budget" is not "more capability",
               and the flat curve would have a MECHANISM rather than being a
               bare observation.
  DEAD END     the model is stuck on something it cannot do. More budget is
               wasted.

Detects oscillation by looking for edits that reverse a previous edit: the same
file, where a later old_string equals an earlier new_string (or vice versa).
That is the signature of a revert.
"""
from __future__ import annotations

import ast
import pathlib
import re
from collections import Counter, defaultdict

ANSI = re.compile(r"\x1b\[[0-9;]*m")
RUN = re.compile(r"\[(\d+)/(\d+)\] Running (\w+) on (\S+)")
EDIT = re.compile(r"\[toolcall\] role=(\w+) tool=(edit_file|write_file) args=(\{.*)$")
TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def parse_edits(log: pathlib.Path, only_last_run: bool = True) -> list[dict]:
    lines = [ANSI.sub("", ln) for ln in log.read_text(encoding="utf-8", errors="ignore").splitlines()]
    start = 0
    if only_last_run:
        for i, ln in enumerate(lines):
            if RUN.search(ln):
                start = i
    edits = []
    for ln in lines[start:]:
        m = EDIT.search(ln)
        if not m:
            continue
        ts = TS.match(ln)
        raw = m.group(3)
        try:
            args = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            continue
        edits.append({
            "ts": ts.group(1)[-8:] if ts else "?",
            "agent": m.group(1),
            "tool": m.group(2),
            "path": args.get("path", ""),
            "old": args.get("old_string", "") or "",
            "new": args.get("new_string", "") or args.get("content", "") or "",
        })
    return edits


def norm(s: str) -> str:
    """Whitespace-insensitive form, so cosmetic churn is not counted as a revert."""
    return re.sub(r"\s+", " ", s).strip()


def main() -> int:
    dirs = sorted((ROOT / "results").glob("EXP-20260929*"))
    d = dirs[-1]
    log = d / "logs" / "experiment.log"
    print(f"log: {log}")
    edits = parse_edits(log)
    print(f"\nedits in the in-flight run: {len(edits)}")
    if not edits:
        return 0

    print(f"\n{'#':>3}{'time':>10}{'file':<32}{'old_len':>9}{'new_len':>9}")
    for i, e in enumerate(edits):
        print(f"{i:>3}{e['ts']:>10}{e['path'][-30:]:<32}{len(e['old']):>9}{len(e['new']):>9}")

    # --- oscillation detection -------------------------------------------
    print("\n" + "=" * 88)
    print("REVERTS: a later edit whose old_string equals an earlier edit's new_string")
    print("=" * 88)
    by_file: dict[str, list[dict]] = defaultdict(list)
    for e in edits:
        by_file[e["path"]].append(e)

    reverts = 0
    for path, es in by_file.items():
        for j, later in enumerate(es):
            for earlier in es[:j]:
                if not norm(later["old"]) or not norm(earlier["new"]):
                    continue
                if norm(later["old"]) == norm(earlier["new"]) and norm(later["new"]) == norm(earlier["old"]):
                    reverts += 1
                    print(f"  {path}")
                    print(f"    #{es.index(earlier):>2} {earlier['ts']}  set -> {norm(earlier['new'])[:90]!r}")
                    print(f"    #{j:>2} {later['ts']}  REVERTED")
                    break

    print(f"\ntotal exact reversions: {reverts}")

    # --- edits per file: churn concentration ------------------------------
    print("\n" + "=" * 88)
    print("EDITS PER FILE (churn concentration)")
    print("=" * 88)
    for path, n in Counter(e["path"] for e in edits).most_common():
        print(f"  {n:>3}x  {path}")

    # --- repeated attempts on the same target -----------------------------
    print("\n" + "=" * 88)
    print("REPEATED EDIT TARGETS (same normalized new_string written more than once)")
    print("=" * 88)
    seen: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(edits):
        if norm(e["new"]):
            seen[norm(e["new"])[:120]].append(i)
    repeats = {k: v for k, v in seen.items() if len(v) > 1}
    for k, idxs in list(repeats.items())[:10]:
        print(f"  edits {idxs}: {k[:110]!r}")
    print(f"\ndistinct new_strings: {len(seen)}, written more than once: {len(repeats)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
