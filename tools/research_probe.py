"""Fetch a set of candidate URLs for one research question and grep the hits.

Repos move files between releases, so a single guessed path often 404s. This
tries several candidates and reports which one worked, so a citation always
points at a URL that was actually fetched.

Usage: python tools/research_probe.py <group>
Groups: openhands | swebench_docs
"""
import re
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".research_cache"

GROUPS = {
    "openhands": {
        "urls": [
            "https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/openhands/core/config/agent_config.py",
            "https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/openhands/core/config/config_utils.py",
            "https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/openhands/agenthub/codeact_agent/codeact_agent.py",
            "https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/docs/usage/configuration-options.md",
            "https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/README.md",
        ],
        "patterns": ["max_iterations", "iteration", "MAX_ITERATIONS"],
    },
    "swebench_docs": {
        "urls": [
            "https://www.swebench.com/SWE-bench/",
            "https://raw.githubusercontent.com/SWE-bench/experiments/main/README.md",
            "https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/docs/faq.md",
            "https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/docs/guides/evaluation.md",
        ],
        "patterns": ["FAIL_TO_PASS", "PASS_TO_PASS", "test patch", "resolved"],
    },
    "minisweagent": {
        "urls": [
            "https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/src/minisweagent/agents/default.py",
            "https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/src/minisweagent/config/default.yaml",
            "https://raw.githubusercontent.com/SWE-agent/mini-SWE-agent/main/docs/faq.md",
        ],
        "patterns": ["step_limit", "cost_limit", "max_steps", "limit"],
    },
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in GROUPS:
        print("usage: research_probe.py <" + "|".join(GROUPS) + ">")
        return
    group = GROUPS[sys.argv[1]]
    CACHE.mkdir(exist_ok=True)

    for url in group["urls"]:
        name = "probe_" + re.sub(r"[^a-zA-Z0-9]+", "_", url)[-60:]
        path = CACHE / f"{name}.txt"
        if not (path.exists() and path.stat().st_size > 0):
            req = urllib.request.Request(url, headers={"User-Agent": "research"})
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    body = r.read().decode("utf-8", errors="replace")
                path.write_text(body, encoding="utf-8")
                print(f"OK   {url}  ({len(body)} bytes) -> {path.name}")
            except Exception as e:  # noqa: BLE001
                print(f"FAIL {url}: {e}")
                continue
        else:
            print(f"CACHE {url} -> {path.name}")

        text = path.read_text(encoding="utf-8", errors="replace")
        for pat in group["patterns"]:
            hits = [
                ln.strip()
                for ln in text.splitlines()
                if re.search(pat, ln, re.I)
            ]
            if hits:
                print(f"     /{pat}/ -> {len(hits)}")
                for h in hits[:8]:
                    print(f"       {h[:150]}")


if __name__ == "__main__":
    main()
