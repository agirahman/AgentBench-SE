"""Fetch and cache primary sources for the budget/verifier research.

web_search/web_fetch are out of credits, so this uses direct HTTP from the
shell. Everything is cached under .research_cache/ so a re-read is free and the
citations can be re-checked later without re-fetching.

Usage: python tools/research_fetch.py <name> <url> [--grep PATTERN]
"""
import re
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".research_cache"

SOURCES = {
    "swebench_readme": "https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/README.md",
    "swebench_harness": "https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/swebench/harness/run_evaluation.py",
    "swebench_grading": "https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/swebench/harness/grading.py",
    "sweagent_readme": "https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/README.md",
    "sweagent_config": "https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/config/default.yaml",
    "sweagent_agent": "https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/sweagent/agent/agents.py",
    "minisweagent_readme": "https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/README.md",
    "openhands_readme": "https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/README.md",
    "openhands_config": "https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/openhands/core/config/agent_config.py",
    "agentless_readme": "https://raw.githubusercontent.com/OpenAutoCoder/Agentless/main/README.md",
    "autocoderover_readme": "https://raw.githubusercontent.com/nus-apr/auto-code-rover/main/README.md",
    "metr_time_horizons": "https://arxiv.org/abs/2503.14499",
    "selfrefine": "https://arxiv.org/abs/2303.17651",
    "reflexion": "https://arxiv.org/abs/2303.11366",
    "selfconsistency": "https://arxiv.org/abs/2203.11171",
    "codet": "https://arxiv.org/abs/2207.10397",
    "swebench_paper": "https://arxiv.org/abs/2310.06770",
    "judge_bias": "https://arxiv.org/abs/2306.05685",
}


def fetch(name: str, url: str) -> Path:
    CACHE.mkdir(exist_ok=True)
    ext = ".html" if url.endswith(("/",)) or "arxiv.org/abs" in url else ".txt"
    path = CACHE / f"{name}{ext}"
    if path.exists() and path.stat().st_size > 0:
        print(f"cached: {path} ({path.stat().st_size} bytes)")
        return path
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 research"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", errors="replace")
        path.write_text(body, encoding="utf-8")
        print(f"fetched: {url} -> {path} ({len(body)} bytes)")
    except Exception as e:  # noqa: BLE001
        print(f"FAILED {url}: {type(e).__name__}: {e}")
    return path


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: research_fetch.py <name|all> [--grep PATTERN]")
        print("\navailable:")
        for k in sorted(SOURCES):
            print(f"  {k}")
        return

    names = list(SOURCES) if sys.argv[1] == "all" else [sys.argv[1]]
    pattern = None
    if "--grep" in sys.argv:
        pattern = sys.argv[sys.argv.index("--grep") + 1]

    for name in names:
        url = SOURCES.get(name)
        if not url:
            print(f"unknown source: {name}")
            continue
        path = fetch(name, url)
        if pattern and path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = [
                ln.strip()
                for ln in text.splitlines()
                if re.search(pattern, ln, re.I)
            ]
            print(f"  grep /{pattern}/ -> {len(hits)} hit(s)")
            for h in hits[:25]:
                print(f"    {h[:160]}")


if __name__ == "__main__":
    main()
