"""Compare the pipeline's apply_status verdict against independent ground truth.

The pipeline records `apply_status` (its own `git apply --check` prediction). This
script re-derives the answer with no pipeline code in the loop:

  1. read the patch text,
  2. clone the instance's base_commit into a fresh temp dir,
  3. run `git apply --check` there.

Then it prints a confusion matrix. A mismatch in either direction is a bug worth
knowing about: a false APPLYABLE means the metric over-claims, a false
NOT_APPLYABLE means it under-claims and hides working patches.
"""

import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_BASE = ROOT / "datasets" / "repos"

# Buffer output and write it ourselves as UTF-8. PowerShell's `>` redirect emits
# UTF-16LE, which a later UTF-8 read turns into NUL-interleaved garbage.
_OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else None
_buf: list[str] = []


def emit(line: str = "") -> None:
    _buf.append(line)


def flush() -> None:
    text = "\n".join(_buf) + "\n"
    if _OUT is not None:
        _OUT.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def load_dataset_meta() -> dict[str, dict]:
    sys.path.insert(0, str(ROOT / "src"))
    from dataset_loader import load_swe_bench_lite

    return {r["instance_id"]: r for r in load_swe_bench_lite()}


def find_checkout(repo: str, base_commit: str) -> Path | None:
    owner, _, name = repo.partition("/")
    for cand in (REPO_BASE / owner / name / base_commit, REPO_BASE / owner / base_commit):
        if (cand / ".git").exists():
            return cand
    for d in (REPO_BASE / owner).rglob(base_commit):
        if (d / ".git").exists():
            return d
    return None


def ground_truth(patch_text: str, checkout: Path, base_commit: str) -> tuple[str, str]:
    """Return (verdict, detail) from a pristine clone. No pipeline code used."""
    tmp = Path(tempfile.mkdtemp(prefix="ab-gt-"))
    try:
        clone = tmp / "repo"
        subprocess.run(
            ["git", "clone", "-q", "--no-hardlinks", "--shared", str(checkout), str(clone)],
            capture_output=True, timeout=300,
        )
        subprocess.run(
            ["git", "-C", str(clone), "checkout", "-q", base_commit],
            capture_output=True, timeout=300,
        )
        pf = tmp / "p.patch"
        pf.write_bytes(patch_text.encode("utf-8"))
        r = subprocess.run(
            ["git", "-C", str(clone), "apply", "--check", str(pf)],
            capture_output=True, timeout=180,
        )
        if r.returncode == 0:
            return "APPLYABLE", ""
        return "NOT_APPLYABLE", r.stderr.decode("utf-8", "replace").strip()[:200]
    except Exception as e:  # noqa: BLE001
        return "ERROR", f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    exp = ROOT / "results" / sys.argv[1]
    meta = load_dataset_meta()

    rows = []
    with (exp / "generation_result.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)

    emit(f"experiment: {exp.name}   rows: {len(rows)}")
    emit()
    hdr = f"{'instance':24s} {'strat':9s} {'pipeline':13s} {'ground truth':13s} {'match':7s}"
    emit(hdr)
    emit("-" * len(hdr))

    agree = disagree = 0
    for r in rows:
        iid, strat = r["instance_id"], r["strategy"]
        pipeline = r.get("apply_status", "?")
        pf = exp / "patches" / f"{iid}_{strat}.txt"
        if not pf.exists():
            emit(f"{iid:24s} {strat:9s} {pipeline:13s} {'NO_PATCH':13s} {'-':7s}")
            continue

        info = meta.get(iid, {})
        repo, base = info.get("repo", ""), info.get("base_commit", "")
        co = find_checkout(repo, base) if (repo and base) else None
        if co is None:
            emit(f"{iid:24s} {strat:9s} {pipeline:13s} {'NO_CHECKOUT':13s} {'-':7s}")
            continue

        gt, detail = ground_truth(pf.read_text(encoding="utf-8"), co, base)
        same = "OK" if pipeline == gt else "MISMATCH"
        if pipeline == gt:
            agree += 1
        else:
            disagree += 1
        emit(f"{iid:24s} {strat:9s} {pipeline:13s} {gt:13s} {same:7s}")
        if detail:
            emit(f"{'':24s} {'':9s} ^ {detail}")
        rows[rows.index(r)]["ground_truth"] = gt

    emit()
    emit(f"agreement: {agree}/{agree + disagree}   mismatch: {disagree}")

    out = exp / "apply_status_comparison.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    emit(f"written: {out}")
    flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
