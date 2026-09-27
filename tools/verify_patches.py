"""Verify captured patches: syntax status + real applicability to a clean tree.

For each patch in an experiment's patches/ dir:
  * patch_status  : the pipeline's own verdict (VALID/NORMALIZE/...)
  * apply_status  : semantic check against base_commit (APPLYABLE/...)
  * clean-apply   : ground truth — apply to a fresh checkout of base_commit

Ground truth is obtained by cloning the instance repo at base_commit into a temp
dir and running `git apply --check`. That is stronger than the pipeline's own
verdict, which validates against a working tree the agent may have modified.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_BASE = ROOT / "datasets" / "repos"


def find_checkout(repo: str, base_commit: str) -> Path | None:
    owner, _, name = repo.partition("/")
    for cand in (
        REPO_BASE / owner / name / base_commit,
        REPO_BASE / owner / base_commit,
    ):
        if (cand / ".git").exists():
            return cand
    for d in (REPO_BASE / owner).rglob(base_commit):
        if (d / ".git").exists():
            return d
    return None


def instance_of(patch_name: str) -> str:
    # django__django-10914_direct.txt -> django__django-10914
    stem = Path(patch_name).stem
    return re.sub(r"_(direct|planning|review)$", "", stem)


def load_dataset_meta() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return (instance -> base_commit, instance -> repo, instance -> test_patch).

    Uses the locally cached SWE-bench Lite dataset, so verification works even
    when the run was stopped before writing a CSV or manifest.
    """
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from dataset_loader import load_swe_bench_lite

        rows = load_swe_bench_lite()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] cannot load dataset ({type(e).__name__}: {e}); "
              "clean-apply checks will be skipped", file=sys.stderr)
        return {}, {}, {}

    base, repo, tp = {}, {}, {}
    for r in rows:
        iid = r.get("instance_id", "")
        base[iid] = r.get("base_commit", "")
        repo[iid] = r.get("repo", "")
        tp[iid] = r.get("test_patch", "") or ""
    return base, repo, tp


def main() -> int:
    exp = ROOT / "results" / sys.argv[1]
    patch_dir = exp / "patches"
    csv_path = exp / "generation_result.csv"

    dataset_base, dataset_repo, dataset_test_patch = load_dataset_meta()

    # Load pipeline verdicts. The CSV only exists after a completed run, so fall
    # back to the per-strategy prediction JSONL (written as a savepoint).
    verdicts: dict[tuple[str, str], dict] = {}
    if csv_path.exists():
        import csv

        with csv_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row.get("instance_id", ""), row.get("strategy", ""))
                verdicts[key] = row
    else:
        for jl in sorted((exp / "predictions").glob("*.jsonl")):
            if jl.name == "predictions.jsonl":
                continue
            for line in jl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (row.get("instance_id", ""), row.get("strategy", ""))
                verdicts[key] = row

    # base_commit/repo are not in the predictions, so read the manifest/experiment
    # config to map instance -> base_commit.
    inst_meta: dict[str, dict] = {}
    for cand in (exp / "manifest.json", exp / "experiment.yaml"):
        if not cand.exists():
            continue
        txt = cand.read_text(encoding="utf-8")
        if cand.suffix == ".json":
            try:
                data = json.loads(txt)
            except json.JSONDecodeError:
                continue
            items = data.get("issues") or data.get("instances") or []
            for it in items if isinstance(items, list) else []:
                if isinstance(it, dict) and it.get("instance_id"):
                    inst_meta[it["instance_id"]] = it
        else:
            # Minimal YAML: look for instance_id / base_commit pairs.
            cur: dict = {}
            for line in txt.splitlines():
                m = re.match(r"\s*instance_id:\s*(\S+)", line)
                if m:
                    cur = {"instance_id": m.group(1)}
                m2 = re.match(r"\s*base_commit:\s*(\S+)", line)
                if m2 and cur:
                    cur["base_commit"] = m2.group(1)
                    inst_meta[cur["instance_id"]] = cur
                m3 = re.match(r"\s*repo:\s*(\S+)", line)
                if m3 and cur:
                    cur["repo"] = m3.group(1).strip("'\"")

    rows = []
    for pf in sorted(patch_dir.glob("*.txt")):
        iid = instance_of(pf.name)
        strat = pf.stem.rsplit("_", 1)[-1]
        text = pf.read_text(encoding="utf-8", errors="replace")

        v = verdicts.get((iid, strat), {})
        rec = {
            "instance": iid,
            "strategy": strat,
            "bytes": len(text),
            "files": len(re.findall(r"^diff --git ", text, re.M)),
            "added": len(re.findall(r"^\+(?!\+\+)", text, re.M)),
            "removed": len(re.findall(r"^-(?!--)", text, re.M)),
            "patch_status": v.get("patch_status", "?"),
            "apply_status": v.get("apply_status", "?"),
            "clean_apply": "?",
        }

        # Ground truth: apply to a pristine checkout of base_commit.
        # base_commit comes from the local SWE-bench Lite dataset (cached), since
        # predictions do not carry it and a stopped run has no CSV.
        meta = inst_meta.get(iid, {})
        base = meta.get("base_commit") or dataset_base.get(iid, "")
        repo = meta.get("repo") or dataset_repo.get(iid, "")
        if not repo:
            repo = iid.replace("__", "/").rsplit("-", 1)[0]
        co = find_checkout(repo, base) if base else None
        if co is None:
            rec["clean_apply"] = "NO_CHECKOUT"
        else:
            tmp = Path(tempfile.mkdtemp(prefix="ab-verify-"))
            try:
                clone = tmp / "repo"
                subprocess.run(
                    ["git", "clone", "-q", "--no-hardlinks", "--shared", str(co), str(clone)],
                    capture_output=True, timeout=180,
                )
                subprocess.run(
                    ["git", "-C", str(clone), "checkout", "-q", base],
                    capture_output=True, timeout=180,
                )
                pf_tmp = tmp / "p.patch"
                pf_tmp.write_bytes(text.encode("utf-8"))
                r = subprocess.run(
                    ["git", "-C", str(clone), "apply", "--check", str(pf_tmp)],
                    capture_output=True, timeout=120,
                )
                rec["clean_apply"] = "OK" if r.returncode == 0 else f"FAIL({r.returncode})"
                if r.returncode != 0:
                    rec["apply_err"] = r.stderr.decode("utf-8", "replace").strip()[:120]
            except Exception as e:  # noqa: BLE001
                rec["clean_apply"] = f"ERR:{type(e).__name__}"
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        rows.append(rec)

    print(f"experiment: {exp.name}   patches: {len(rows)}")
    print()
    hdr = f"{'instance':28s} {'strat':9s} {'bytes':>6s} {'files':>5s} {'+':>4s} {'-':>4s}  {'status':10s} {'apply':13s} {'clean-apply':12s}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['instance']:28s} {r['strategy']:9s} {r['bytes']:6d} {r['files']:5d} "
            f"{r['added']:4d} {r['removed']:4d}  {r['patch_status']:10s} "
            f"{r['apply_status']:13s} {r['clean_apply']:12s}"
        )
        if r.get("apply_err"):
            print(f"{'':28s}   ^ {r['apply_err']}")

    print()
    n = len(rows)
    ok = sum(1 for r in rows if r["clean_apply"] == "OK")
    val = sum(1 for r in rows if r["patch_status"] == "VALID")
    app = sum(1 for r in rows if r["apply_status"] == "APPLYABLE")
    print(f"VALID: {val}/{n}   APPLYABLE(pipeline): {app}/{n}   CLEAN-APPLY(ground truth): {ok}/{n}")

    out = ROOT / "results" / exp.name / "verify_patches.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
