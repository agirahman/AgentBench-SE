"""Compare the smoke-test patch against the EXP-003 review patch for one instance.

Answers "did the reserve change the outcome?" by diffing the two patches and
reporting the key markers, plus whether a cap was hit in either run.
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

INST = "django__django-11001"


def verdicts(exp: str) -> list[str]:
    p = ROOT / f"results/{exp}/artifacts/{INST}/review/messages.jsonl"
    out = []
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        c = d.get("content") or ""
        if "verdict" in c:
            m = re.search(r'"verdict"\s*:\s*"([^"]+)"', c)
            out.append(m.group(1) if m else c[:120])
    return out


def patch(exp: str) -> str:
    p = ROOT / f"results/{exp}/patches/{INST}_review.txt"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def caps(exp: str) -> list[str]:
    log = ROOT / f"results/{exp}/logs/experiment.log"
    if not log.exists():
        return []
    return [
        ln.split("|")[-1].strip()
        for ln in log.read_text(encoding="utf-8", errors="replace").splitlines()
        if "hit max_tool_turns" in ln
    ]


def markers(text: str) -> dict[str, bool]:
    return {
        "re.DOTALL": "DOTALL" in text,
        "compiler.py touched": "db/models/sql/compiler.py" in text,
        "touches tests/": "tests/" in text,
        "os.scandir": "scandir" in text,
    }


def main() -> None:
    old, new = "EXP-20260928-003", "EXP-20260929-001"
    for exp in (old, new):
        p = patch(exp)
        print(f"=== {exp} ===")
        print(f"  verdicts: {verdicts(exp)}")
        print(f"  cap hits: {caps(exp) or 'none'}")
        print(f"  patch: {len(p.splitlines())} lines")
        for k, v in markers(p).items():
            print(f"    {k:22s} {v}")
        print()

    import difflib

    diff = list(
        difflib.unified_diff(
            patch(old).splitlines(),
            patch(new).splitlines(),
            fromfile=old,
            tofile=new,
            lineterm="",
        )
    )
    print(f"=== patch diff ({len(diff)} lines) ===")
    for line in diff[:60]:
        print(line)


if __name__ == "__main__":
    main()
