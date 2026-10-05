"""Pre-flight: will Modal be able to APPLY each patch?

Modal's contract (swebench/harness/modal_eval/run_evaluation_modal.py:263-290):

    cd /testbed && git apply -v /tmp/patch.diff
    # on failure:
    cd /testbed && patch --batch --fuzz=5 -p1 -i /tmp/patch.diff
    # both failing => APPLY_PATCH_FAIL

This script reproduces that exactly, so an APPLY_PATCH_FAIL is caught locally
instead of burning a cloud round-trip. Anything reported PASS here should not
fail on Modal for a patch-application reason; any remaining failure is then an
evaluation (semantic) failure, which is what we want the metric to measure.

Two details that matter for fidelity:

* The patch string is read from the predictions JSONL, because that is what Modal
  actually consumes — not from ``patches/*.txt``. The two differ on Windows: the
  .txt files are written by ``Path.write_text()`` and come out CRLF, while the
  JSONL round-trips the string as LF. Testing the CRLF copy would test the wrong
  artifact.
* GNU ``patch`` (needed for the --fuzz=5 fallback) is not on Windows, so the two
  commands run inside WSL, against a clone created on the Windows side and
  addressed via /mnt/<drive>.

Usage:
    python tools/preflight_modal.py <predictions.jsonl> [more.jsonl ...]
    python tools/preflight_modal.py --out <report.txt> <predictions.jsonl> ...
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_BASE = ROOT / "datasets" / "repos"

_args = sys.argv[1:]
# Optional --out: write the report as UTF-8 from Python. Redirecting PowerShell's
# `>` would emit UTF-16LE, which a later UTF-8 read turns into NUL-interleaved
# garbage (this bit the earlier monitors).
_OUT = None
if "--out" in _args:
    i = _args.index("--out")
    _OUT = Path(_args[i + 1])
    del _args[i : i + 2]
_buf: list[str] = []


def emit(line: str = "") -> None:
    _buf.append(line)


def flush() -> None:
    text = "\n".join(_buf) + "\n"
    if _OUT is not None:
        _OUT.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def to_wsl(path: Path) -> str:
    """D:\\a\\b -> /mnt/d/a/b (WSL mounts Windows drives under /mnt)."""
    p = str(path.resolve())
    drive, rest = p[0].lower(), p[2:].replace("\\", "/")
    return f"/mnt/{drive}{rest}"


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


def wsl(bash_cmd: str, timeout: int = 300) -> tuple[int, str]:
    r = subprocess.run(
        ["wsl", "-e", "bash", "-lc", bash_cmd],
        capture_output=True, timeout=timeout,
    )
    out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    return r.returncode, out


def check_patch(patch_text: str, checkout: Path, base_commit: str) -> tuple[str, str]:
    """Return (verdict, detail). verdict in {PASS_GIT, PASS_FUZZ, FAIL}."""
    tmp = Path(tempfile.mkdtemp(prefix="ab-pre-"))
    try:
        clone = tmp / "repo"
        # Clone + checkout on the Windows side (fast, local).
        #
        # `-c core.autocrlf=false` is essential for fidelity, not tidiness. This
        # machine has core.autocrlf=true, so a plain clone checks files out as
        # CRLF while Modal's Linux /testbed has LF. That mismatch made strict
        # `git apply` fail locally and the patch "pass" only via the fuzz fallback,
        # which would hide a genuine strict-apply failure. Forcing LF reproduces
        # what Modal actually sees.
        subprocess.run(
            ["git", "-c", "core.autocrlf=false", "clone", "-q", "--no-hardlinks", "--shared", str(checkout), str(clone)],
            capture_output=True, timeout=600,
        )
        subprocess.run(
            ["git", "-C", str(clone), "checkout", "-q", base_commit],
            capture_output=True, timeout=600,
        )
        # Write the patch with LF (newline="") so we test what Modal receives.
        pf = tmp / "patch.diff"
        with pf.open("w", encoding="utf-8", newline="") as f:
            f.write(patch_text)

        w_clone, w_patch = to_wsl(clone), to_wsl(pf)

        # Path 1: strict git apply (exactly Modal's first command).
        rc, out = wsl(f"cd '{w_clone}' && git -c core.autocrlf=false apply -v '{w_patch}'")
        if rc == 0:
            return "PASS_GIT", ""

        # Path 2: GNU patch with fuzz (exactly Modal's fallback).
        rc2, out2 = wsl(
            f"cd '{w_clone}' && git -c core.autocrlf=false checkout -q -- . ; "
            f"patch --batch --fuzz=5 -p1 -i '{w_patch}'"
        )
        if rc2 == 0:
            return "PASS_FUZZ", out.strip().splitlines()[-1][:160] if out.strip() else ""

        detail = (out2 or out).strip().replace("\n", " | ")[:240]
        return "FAIL", detail
    except Exception as e:  # noqa: BLE001
        return "ERROR", f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    jsonls = [Path(a) for a in _args]
    meta = load_dataset_meta()

    emit("Modal apply-contract pre-flight")
    emit("  cmd1: git apply -v /tmp/patch.diff")
    emit("  cmd2: patch --batch --fuzz=5 -p1 -i /tmp/patch.diff   (fallback)")
    emit()

    total = passed = 0
    failures: list[str] = []

    for jsonl in jsonls:
        emit(f"--- {jsonl}")
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            iid = rec.get("instance_id", "?")
            strat = rec.get("strategy", "?")
            patch = rec.get("model_patch", "")
            total += 1

            if not patch.strip():
                emit(f"  {iid:24s} {strat:9s} EMPTY_PATCH")
                failures.append(f"{iid}/{strat}: empty patch")
                continue

            info = meta.get(iid, {})
            repo, base = info.get("repo", ""), info.get("base_commit", "")
            co = find_checkout(repo, base) if (repo and base) else None
            if co is None:
                emit(f"  {iid:24s} {strat:9s} NO_CHECKOUT ({repo}@{base[:8]})")
                failures.append(f"{iid}/{strat}: no checkout")
                continue

            verdict, detail = check_patch(patch, co, base)
            if verdict in ("PASS_GIT", "PASS_FUZZ"):
                passed += 1
            else:
                failures.append(f"{iid}/{strat}: {verdict} {detail}")
            mark = {"PASS_GIT": "PASS", "PASS_FUZZ": "PASS(fuzz)", "FAIL": "FAIL", "ERROR": "ERROR"}[verdict]
            emit(f"  {iid:24s} {strat:9s} {mark:10s} {detail[:120]}")
        emit()

    emit("=" * 62)
    emit(f"applyable: {passed}/{total}")
    if failures:
        emit("")
        emit("NOT applyable (these would be APPLY_PATCH_FAIL on Modal):")
        for f in failures:
            emit(f"  - {f}")
    else:
        emit("All patches satisfy Modal's apply contract.")
    emit("=" * 62)
    return 0 if not failures else 1


if __name__ == "__main__":
    code = main()
    flush()
    sys.exit(code)
