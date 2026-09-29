"""Verify a paper's claim against the paper's own text, from a cached copy.

Why this exists: a partner research brief cited "67% -> 82% with execution
feedback" for Olausson et al. (2023). Fetching the full text showed the string
"82%" never occurs in that paper and its GPT-4 gains are stated as "up to 8%".
An invented number in a thesis citation is worse than no number, so any figure
that ends up in the write-up should be checkable by re-running this tool.

Usage:
    python tools/verify_citations.py <arxiv_id> <claim text>
    python tools/verify_citations.py --self-test

Behaviour:
    - downloads https://arxiv.org/abs/<id> (and ar5iv full text when available)
      into .research_cache/ (gitignored), reusing the cache on later runs;
    - prints every percentage the abstract actually contains;
    - prints context windows around the claim's keywords so a human can judge
      whether the paper says what the brief says it says.

It cannot decide truth for you -- it puts the primary source in front of you.
"""
from __future__ import annotations

import argparse
import html
import pathlib
import re
import sys
import urllib.request

CACHE = pathlib.Path(".research_cache")
USER_AGENT = "AgentBench-SE citation check (research; contact: local)"


def _strip_html(raw: str) -> str:
    raw = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def _fetch(url: str, dest: pathlib.Path) -> str | None:
    """Return cached text, downloading it once if needed. None if unreachable."""
    if dest.exists():
        return dest.read_text(encoding="utf-8", errors="ignore")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read()
    except Exception as e:  # noqa: BLE001 - network is optional, report and move on
        print(f"  [unreachable] {url}: {e}")
        return None
    dest.write_bytes(body)
    return body.decode("utf-8", errors="ignore")


def load_text(arxiv_id: str) -> tuple[str, str]:
    """Return (abstract_page_text, full_text_or_empty)."""
    abs_page = _fetch(f"https://arxiv.org/abs/{arxiv_id}", CACHE / f"abs_{arxiv_id}.html")
    abs_text = _strip_html(abs_page) if abs_page else ""

    full_page = _fetch(
        f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}",
        CACHE / f"ar5iv_{arxiv_id}.html",
    )
    # ar5iv 404s for some papers; that is expected, not an error.
    full_text = _strip_html(full_page) if full_page else ""
    return abs_text, full_text


def report(arxiv_id: str, claim: str) -> int:
    abs_text, full_text = load_text(arxiv_id)
    if not abs_text and not full_text:
        print(f"no source text available for {arxiv_id} (offline?)")
        return 2

    print("=" * 74)
    print(f"arXiv:{arxiv_id}")
    print(f"CLAIM UNDER TEST: {claim}")
    print("=" * 74)

    m = re.search(r"Abstract:\s*(.{0,2000}?)(?:Submit|Comments:)", abs_text)
    abstract = m.group(1).strip() if m else ""
    if abstract:
        pcts = re.findall(r"\d+(?:\.\d+)?\s*(?:%|percent)", abstract)
        print(f"\npercentages actually in the abstract: {pcts or 'NONE'}")
        print(f"\nabstract:\n  {abstract[:900]}...")

    # Numbers asserted in the claim, checked for literal presence in the source.
    claimed_numbers = re.findall(r"\d+(?:\.\d+)?", claim)
    body = full_text or abs_text
    print(f"\nsource used for literal check: {'ar5iv full text' if full_text else 'abstract only'}")
    for num in claimed_numbers:
        hits = len(re.findall(rf"(?<!\d){re.escape(num)}(?!\d)", body))
        verdict = "PRESENT" if hits else "NOT FOUND"
        print(f"  number {num!r:>8} -> {verdict} ({hits} occurrence(s))")

    for kw in ("HumanEval", "pass@1", "without execution", "feedback", "improv"):
        for mm in re.finditer(re.escape(kw), body, flags=re.I):
            s = max(0, mm.start() - 200)
            print(f"\n[{kw}] ...{body[s:mm.start() + 260].strip()}...")
            break
    return 0


def self_test() -> int:
    """Pin the exact failure that motivated this tool.

    If this ever starts passing, the cached paper changed and the correction in
    docs/RESEARCH_VERIFIER_20260929.md should be revisited.
    """
    path = CACHE / "ar5iv_2306.09896.html"
    if not path.exists():
        print("SKIP: cache absent (run once with network to populate)")
        return 0
    text = _strip_html(path.read_text(encoding="utf-8", errors="ignore"))
    checks = [
        ("'82%' absent from Olausson", "82%" not in text),
        ("'up to 8%' present", re.search(r"up to 8\s*%", text) is not None),
    ]
    failed = 0
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        failed += 0 if ok else 1
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("arxiv_id", nargs="?", help="e.g. 2306.09896")
    ap.add_argument("claim", nargs="?", default="", help="the claim to check")
    ap.add_argument("--self-test", action="store_true", help="verify the known Olausson correction")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.arxiv_id:
        ap.print_help()
        return 1
    return report(args.arxiv_id, args.claim)


if __name__ == "__main__":
    sys.exit(main())
