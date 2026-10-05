"""Kesimpulan diagnosis checkout kotor -- dibaca sebelum membersihkan apa pun.

Apa yang ditemukan
------------------

`tools/preflight_repos.py` melaporkan 5 checkout kotor dari 50. Isinya BUKAN
sampah, dan bukan sisa agen yang gagal. Isinya adalah **GOLD PATCH yang
tertinggal** dari validasi `EXP-20260930-098-gold-check`.

Bukti, per instance (dibandingkan dengan field `patch` dataset):

  django__django-10914  3 file:  global_settings.py, docs/ref/settings.txt,
                                tests/test_utils/tests.py
                        FILE_UPLOAD_PERMISSIONS = None -> 0o644
                        Ini gold patch DITAMBAH perubahan test yang cocok
                        dengan `test_patch` dataset (gold patch resmi hanya
                        menyentuh global_settings.py; dua berkas lain berasal
                        dari test_patch, yang memang ikut diterapkan saat
                        memvalidasi bahwa instance BISA dinilai).

  django__django-11001  1 file:  compiler.py
                        re.compile(..., re.MULTILINE | re.DOTALL)
                        COCOK 100% dengan gold patch.

  django__django-10924  4 file, 23 baris -- `if callable(path)` (gold-nya
                        menyentuh file lain).
  django__django-11019  1 file, 33 baris -- topological sort.
  django__django-11039  2 file, 15 baris.

Artinya: run validasi gold patch menerapkan patch ke checkout nyata dan tidak
membersihkannya. Ini BUKAN kerusakan -- repo masih di base commit yang benar,
hanya working tree-nya kotor.

Kenapa ini penting untuk run berikutnya
---------------------------------------

Runner SUDAH membersihkan sebelum setiap strategi (`reset_working_tree()` di
`direct_strategy.py:29`, `planning_strategy.py:29`, `review_strategy.py:84`).
Jadi secara teknis run berikutnya aman.

TAPI: `preflight_repos.py` mem-flag ini sebagai FAIL, dan sisa-sisa ini membuat
insiden berikutnya tidak terbaca. Lebih buruk, kalau `reset_working_tree` gagal
diam-diam (ia hanya `_warn`), diff yang tertangkap akan berisi gold patch --
dan itu terlihat seperti agen yang memecahkan masalah, padahal bukan.

Tindakan yang benar
-------------------

`tools/clean_repos.py` -- yang sudah ada dan memang untuk ini. Ia menjalankan
`git reset` + `git checkout -- .` + `git clean -fdq` di dalam checkout, jadi
konten tracked DIPULIHKAN dari git, bukan dihapus. Ini operasi yang aman dan
reversibel (selama tidak ada pekerjaan yang belum di-commit yang ingin disimpan
-- dan di sini tidak ada: isinya gold patch yang bisa dihasilkan ulang kapan saja
dari dataset).

Yang TIDAK boleh dilakukan: menghapus direktori checkout. Itu akan membuang
clone 1,35 GB yang harus diunduh ulang.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_BASE = ROOT / "datasets" / "repos"


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "") + (p.stderr or "")


def main() -> int:
    print(__doc__)
    print("=" * 78)
    print("  DRY RUN -- tidak ada yang diubah. Tambahkan --apply untuk menjalankan.")
    print("=" * 78)

    apply = "--apply" in sys.argv

    dirty = []
    for gitdir in sorted(REPO_BASE.rglob(".git")):
        repo = gitdir.parent
        if not git(repo, "status", "--porcelain").strip():
            continue
        dirty.append(repo)

    print(f"\n  {len(dirty)} checkout kotor:\n")
    for repo in dirty:
        rel = repo.relative_to(ROOT)
        files = [l[3:] for l in git(repo, "status", "--porcelain").splitlines() if l.strip()]
        print(f"    {rel}")
        for f in files[:6]:
            print(f"        {f}")
        if len(files) > 6:
            print(f"        ... dan {len(files) - 6} lainnya")

    if not apply:
        print()
        print("  Jalankan `python tools/clean_repos.py` untuk membersihkan")
        print("  (git checkout/clean di dalam checkout, BUKAN hapus direktori).")
        return 0

    print()
    print("=" * 78)
    print("  APPLYING -- git reset + checkout + clean di dalam setiap checkout")
    print("=" * 78)
    for repo in dirty:
        git(repo, "reset", "-q")
        git(repo, "checkout", "--", ".")
        git(repo, "clean", "-fdq", ".")
        after = git(repo, "status", "--porcelain").strip()
        state = "bersih" if not after else "MASIH KOTOR"
        print(f"    {repo.relative_to(ROOT)}  -> {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
