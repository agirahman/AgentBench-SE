"""Fetch the SWE-agent model config to find the documented budget defaults.

The agent code references ``model.per_instance_cost_limit`` and
``retry_loop.cost_limit``; those live in a separate config class, so this pulls
the model config file and greps for the defaults rather than guessing.

Usage: python tools/research_fetch_sweagent_model.py
"""
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".research_cache"

CANDIDATES = [
    "https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/sweagent/agent/models.py",
    "https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/sweagent/types.py",
    "https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/src/sweagent/agent/models.py",
    "https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/src/sweagent/agent/agents.py",
    "https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/docs/config/config.md",
]


def main() -> None:
    CACHE.mkdir(exist_ok=True)
    for url in CANDIDATES:
        name = url.rsplit("/", 1)[-1]
        path = CACHE / f"sweagent_{name}"
        if not (path.exists() and path.stat().st_size > 0):
            req = urllib.request.Request(url, headers={"User-Agent": "research"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read().decode("utf-8", errors="replace")
                path.write_text(body, encoding="utf-8")
                print(f"fetched {url} -> {len(body)} bytes")
            except Exception as e:  # noqa: BLE001
                print(f"FAILED {url}: {e}")
                continue
        text = path.read_text(encoding="utf-8", errors="replace")
        keys = [
            ln.strip()
            for ln in text.splitlines()
            if any(
                k in ln
                for k in (
                    "per_instance_cost_limit",
                    "total_cost_limit",
                    "cost_limit",
                    "step_limit",
                    "max_steps",
                )
            )
        ]
        if keys:
            print(f"\n--- {name}: {len(keys)} hit(s) ---")
            for k in keys[:30]:
                print(f"  {k[:170]}")


if __name__ == "__main__":
    main()
