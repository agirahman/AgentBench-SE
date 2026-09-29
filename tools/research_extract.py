"""Extract the abstract and key claims from cached HTML (arXiv pages).

Kept separate from research_fetch.py so the cache stays the source of truth:
this only parses what is already on disk, so a citation can be re-checked
without another network call.

Usage: python tools/research_extract.py <cached-file> [--grep PATTERN]
"""
import html
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent


def strip_tags(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: research_extract.py <file> [--grep PATTERN] [--len N]")
        return
    path = Path(sys.argv[1])
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        print(f"missing {path}")
        return

    raw = path.read_text(encoding="utf-8", errors="replace")

    if "--grep" in sys.argv:
        pattern = sys.argv[sys.argv.index("--grep") + 1]
        limit = 200
        if "--len" in sys.argv:
            limit = int(sys.argv[sys.argv.index("--len") + 1])
        text = strip_tags(raw)
        hits = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if re.search(pattern, s, re.I)]
        print(f"{path.name}: {len(hits)} sentence(s) matching /{pattern}/\n")
        for h in hits[:20]:
            print(f"  • {h[:limit]}")
        return

    m = re.search(r'<blockquote class="abstract[^>]*>(.*?)</blockquote>', raw, re.S)
    if m:
        print("=== ABSTRACT ===")
        print(strip_tags(m.group(1)))
        print()
    title = re.search(r"<h1 class=\"title[^>]*>(.*?)</h1>", raw, re.S)
    if title:
        print("=== TITLE ===")
        print(strip_tags(title.group(1)))


if __name__ == "__main__":
    main()
