# 🧠 AI Agent Memory — AgentBench-SE

**Last Updated:** 2026-09-28 21:30 WIB
**Status:** Bug retry-reset-budget **SUDAH DIPERBAIKI** — siap run ulang 10 issue
**Active Branch:** `19/toolcall-commandcode`
**Detail sesi terakhir:** lihat [`HANDOFF_20260928.md`](HANDOFF_20260928.md)

---

## 📋 Current Project State

| Aspect | Status | Notes |
|--------|--------|-------|
| **Provider** | ✅ Done | OpenCode via 9router (`oc/space-bunny-free`), model gratis untuk testing |
| **Dataset** | ✅ Done | 50 issues: django(10)+sympy(10)+scikit(10)+matplotlib(10)+requests(6)+seaborn(4) |
| **Repo cache** | ✅ Done | 50 instance pristine di `datasets/repos/` |
| **Mekanisme patch** | ✅ Done | edit-then-diff: agen mengedit file, patch diambil dari `git diff` |
| **Tool calling** | ✅ Done | Loop bersama di `providers/tool_loop.py` (3 provider berbagi) |
| **Budget tool-turn** | ✅ Done | `agents/budget.py` — total sama per strategi, sekarang **40** |
| **Pre-flight validator** | ✅ Done | `tools/preflight_modal.py` — replikasi kontrak Modal secara lokal |
| **Rate-limit handling** | ✅ Done | Backoff 429 + circuit breaker |
| **Test suite** | ✅ Done | **207 lulus** (dari 199) |
| **Retry vs budget** | ✅ **FIXED** | Retry per-request di dalam loop (commit `dfc9fa8`) |
| **Konteks per turn** | ✅ Done | `TOOL_OUTPUT_MAX_CHARS=2000`, head+tail (dari 8000 head-only) |
| **Reviewer oracle** | ⚠️ Terbatas | `run_tests` selalu gagal; reviewer hanya bisa menalar |

---

## 🔑 Keputusan Kunci

| Keputusan | Tanggal | Alasan | Trade-off |
|-----------|---------|--------|-----------|
| Opsi A: edit-then-diff | 2026-09-27 | Model menulis diff sebagai teks sering cacat; mengedit file lalu `git diff` jauh lebih andal | Butuh tool calling |
| Pindah ke OpenCode/9router | 2026-09-27 | OpenRouter kena rate limit | Bergantung 9router lokal harus hidup |
| Bounded re-review (bukan auto-approve) | 2026-09-27 | Revisi tanpa re-review terbukti merugikan (EXP-007) | Menambah biaya token |
| Budget setara per strategi | 2026-09-28 | Cap per-`act()` membuat total jadi kecelakaan arsitektur; `direct` selalu terpotong | `review` tidak berubah, `direct` naik 3× |
| Reviewer tanpa oracle didokumentasikan sebagai temuan | 2026-09-28 | Lebih jujur daripada memoles angka | `review` tidak bisa lebih baik dari `planning` |
| Retry per-request, bukan per-loop | 2026-09-28 | `@with_retry` me-restart seluruh loop → eksplorasi hilang + budget ter-reset (99 call untuk budget 60) | Retry sekarang di dalam `tool_loop.py`, bukan dekorator |
| Budget 60 → 40 | 2026-09-28 | `direct` eksplorasi sampai dihentikan, bukan konvergen; 60 memakai ~4× token/waktu tanpa bukti akurasi naik (2/3 resolved di 20 maupun 60) | Angka **tidak komparabel** dengan EXP-20260927-010 |
| Potong konteks tool ke 2000 char | 2026-09-28 | 8000/turn × 60 turn ≈ 480 KB → request akhir melewati timeout | Detail menengah hilang; head+tail dipertahankan |

---

## 📊 Hasil Eksperimen Terakhir

### `EXP-20260927-010` — 3 issue, budget 20/40/60

| Strategi | Resolved | Apply fail |
|---|---|---|
| direct | 2/3 | 0 |
| planning | **3/3** | 0 |
| review | 2/3 | 0 |

**9/9 patch lolos `git apply` ketat, 0 `APPLY_PATCH_FAIL`** — kegagalan murni semantik.

Reviewer menyetujui **9/9** patch, tapi hanya ~5/9 resolved → `APPROVED` tidak punya daya beda.

### `EXP-20260928-001` — 10 issue, DIHENTIKAN di 10/30

Budget 60/60/60 (setara). Loop revisi **terpicu 1×** di 10924/review — pertama kali sejak perbaikan prompt, dan bekerja dengan benar.

Dihentikan karena bug retry-reset-budget (lihat handoff).

---

## ⚙️ Konfigurasi Aktif (`.env`)

| Key | Nilai | Catatan |
|---|---|---|
| `OPENCODE_MODEL` | `oc/space-bunny-free` | via 9router |
| `OPENCODE_BASE_URL` | `http://localhost:20128/v1` | 9router harus hidup |
| `TOOLCALL_ENABLED` | `true` | edit-then-diff |
| `TOTAL_TOOL_TURNS` | `40` | pool per strategi: direct 40; planning 20+20; review 13+13+14 |
| `MAX_TOOL_TURNS` | `20` | fallback per-act, hanya jika TOTAL=0 |
| `TOOL_OUTPUT_MAX_CHARS` | `2000` | cap per hasil tool, head+tail |
| `MAX_REVISION_TURNS` | `1` | batas revisi |
| `API_TIMEOUT` | `600` | dinaikkan dari 180 |
| `PROMPT_CACHE_LAYOUT` | `true` | prefix caching terbukti nyata (~90% hit) |
| `SOURCE_CONTEXT_ENABLED` | `false` | agen eksplorasi pakai tool |

---

## 🚨 Jebakan yang Sudah Memakan Waktu

1. **Env drift** — shell mengekspor `OPENCODE_API_KEY` berisi literal `${NINEROUTER_API_KEY}` (21 char) yang menimpa key asli di `.env` (35 char) → `401`. **Selalu** jalankan lewat `tools/run_with_env.py`, jangan `main.py` langsung.
   - Shell juga mengekspor `TOTAL_TOOL_TURNS=60` dan `API_TIMEOUT=180`, menimpa `.env` (40/600). Ini **pernah membuat test lulus palsu**: `test_strategies` assert literal `60` dan kebetulan cocok dengan nilai shell, bukan `.env`.
2. **Reload modul `config` di test** — `tests/test_response_utils.py` memanggil `importlib.reload(config)`, yang membuat `config.Config` jadi kelas **baru** sementara modul yang sudah `from config import Config` tetap memegang kelas **lama**. `monkeypatch.setattr("config.Config", ...)` akan **meleset**. Patch setiap modul yang memegang binding sendiri (`agents.budget`, `agents.base`, `agents.registry`, `agents.tools`, `strategies.review_strategy`).
3. **9router harus hidup** di `localhost:20128` sebelum run.
4. **Jangan `.strip()` sebuah diff** — baris konteks terakhir bisa berupa spasi; strip membuat hunk corrupt.
5. **Hash repo harus 40 karakter** — cache di `datasets/repos/<owner>/<name>/<commit>`.
6. **Working tree kotor saat run itu normal** — strategi berbagi satu repo per issue, jadi `git apply --check` gagal di tengah run.
7. **PowerShell 5.1** tidak mendukung `&&`; kutip bersarang sering gagal parse (tulis ke `.ps1` lalu `-File`).

---

## 📂 Path Penting

| Path | Fungsi |
|------|--------|
| `src/agents/budget.py` | Pool tool-turn per strategi |
| `src/providers/tool_loop.py` | Loop tool bersama 3 provider |
| `src/strategies/review_strategy.py` | Loop review + re-review revisi |
| `src/agents/tools.py` | Definisi tool + guard (termasuk `[tests unavailable]`) |
| `tools/run_with_env.py` | **Wrapper wajib** — membuat `.env` menang |
| `tools/preflight_modal.py` | Replikasi kontrak `git apply` Modal secara lokal |
| `docs/HANDOFF_20260928.md` | **Detail sesi terakhir + plan berikutnya** |
| `docs/RUNBOOK.md` | Prosedur menjalankan eksperimen |

---

## 💡 Tips untuk Sesi Berikutnya

1. **Baca `docs/HANDOFF_20260928.md` dulu** — berisi plan lengkap dan bug yang harus diperbaiki.
2. **Perbaiki bug retry sebelum run apa pun** — kalau tidak, budget tidak benar-benar ditegakkan.
3. **Selalu** lewat `tools/run_with_env.py`.
4. **Cek cap warning** di `results/EXP-*/logs/experiment.log` — kalau ada, ada yang terpotong.
5. **Verifikasi dengan `--resume`** bila run terputus.

---

## 📝 Catatan

- **Commit sudah di-push:** `4d89434`, `8e6db35`, `97a71f7`, `dfc9fa8` (branch `19/toolcall-commandcode`).
- **Temuan untuk skripsi:** reviewer tanpa execution feedback tidak menambah kemampuan verifikasi; test tersembunyi adalah oracle yang tidak bisa digantikan penalaran.
- **Temuan tambahan (2026-09-28):** retry yang membungkus loop — bukan request — membatalkan batas budget. Satu act bisa memakai 180 turn alih-alih 60. Ini kelas bug yang mudah terlewat karena tidak muncul sampai timeout benar-benar terjadi.

---

**Last working state:** commit `dfc9fa8` — 207 test lulus, retry per-request terpasang, budget 40 setara, `TOOL_OUTPUT_MAX_CHARS=2000`, bug retry-reset-budget terverifikasi hilang.
