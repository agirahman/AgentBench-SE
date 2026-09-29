"""Fetch primary sources on SWE-bench Lite and the harness's budget conventions.

The user asked specifically about "SWE-bench Lite", which is a curated 300-instance
subset -- worth checking whether it changes anything methodological (it should not:
Lite shares the harness, only the instance set differs). Recorded from the repo's
own docs so the thesis can cite a primary source rather than a blog summary.
"""
import pathlib
import urllib.request

CACHE = pathlib.Path(".research_cache")
CACHE.mkdir(exist_ok=True)

BASE = "https://raw.githubusercontent.com/SWE-bench/SWE-bench/main"
SOURCES = {
    "lite_readme": f"{BASE}/docs/guides/datasets.md",
    "lite_blog": f"{BASE}/docs/guides/faq.md",
    "harness_eval": f"{BASE}/docs/guides/evaluation.md",
    "swebench_init": f"{BASE}/swebench/collect/utils.py",
}

for name, url in SOURCES.items():
    out = CACHE / f"{name}.txt"
    if out.exists():
        print(f"[cached] {name} ({out.stat().st_size} bytes)")
        continue
    try:
        with urllib.request.urlopen(url, timeout=40) as r:
            body = r.read()
        out.write_bytes(body)
        print(f"[ok] {name} -> {len(body)} bytes")
    except Exception as e:  # noqa: BLE001 - keep going, report at the end
        print(f"[FAIL] {name}: {e}")

# Report the Lite-relevant lines so the finding is quotable.
print("\n" + "=" * 74)
print("SWE-bench Lite mentions in the repo docs")
print("=" * 74)
for f in CACHE.glob("*.txt"):
    text = f.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        low = line.lower()
        if "lite" in low and len(line.strip()) > 20:
            print(f"\n[{f.name}] {line.strip()[:400]}")
