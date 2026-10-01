# TASK: Verifikasi independen — apakah pipeline SIAP untuk sweep 50 issue?

**JANGAN EDIT FILE SUMBER APA PUN.** Hanya laporkan. Kamu boleh membuat file laporan sendiri.

Repo: `D:\development\Skripsi2\AgantBech-SE`

## Konteks

Sweep 50 issue × 3 strategi = **150 run**, ~14–17 jam, ~$7–11 uang nyata. Tidak bisa
diulang sembarangan. Dua audit sebelumnya menemukan 5 blocker; semua diklaim sudah
diperbaiki. **Tugasmu: cari apa yang MASIH salah, dan verifikasi klaim perbaikan itu
benar-benar bekerja — bukan hanya terlihat benar.**

Aku (partner utama) baru saja menemukan bahwa **"421 test lulus" itu PALSU**: shell
mengekspor `MAX_REVISION_TURNS=1` yang menimpa `.env` (yang bernilai 4), sehingga 3 test
lulus karena kebetulan cocok dengan nilai shell. Itu artinya **test bisa lulus karena
lingkungan, bukan karena kode benar**. Anggap ini kelas bug yang mungkin ada di tempat lain.

## Fokusmu: BUKTI, bukan pembacaan kode

Untuk SETIAP klaim di bawah, jalankan perintahnya dan laporkan output **verbatim**. Kalau
klaimnya salah, tunjukkan perintah yang membuktikannya.

### 1. Apakah konfigurasi yang akan dijalankan sweep benar-benar bisa merevisi?

Klaim: `REVISION_TOOL_TURNS=48`, `MAX_REVISION_TURNS=4` → setiap act revisi dapat **6+6**.

- Jalankan `python tools/check_budget_fairness.py` (dengan `TOTAL_TOOL_TURNS`, `REVISION_TOOL_TURNS`, `MAX_REVISION_TURNS` DIHAPUS dari shell dulu — pakai `Remove-Item Env:\...` di PowerShell).
- Jalankan `python tools/verify_revision_rounds.py`.
- Jalankan `python tools/run_final_sweep.py --dry-run` dan periksa apakah `--set REVISION_TOOL_TURNS=` dan `--set MAX_REVISION_TURNS=` ADA di perintah yang dicetak.
- **Pertanyaan kunci:** apakah `tools/verify_revision_rounds.py` setuju dengan `tools/check_budget_fairness.py`? Kalau tidak, salah satunya masih salah. Aku baru saja memperbaiki `verify_revision_rounds.py` karena ia memakai `expected = 8 * rounds` — aritmetika yang MUSTAHIL (4 × 2 × 8 = 64) dan justru menandai nilai BENAR (48) sebagai salah. Periksa apakah perbaikannya benar.

### 2. Apakah test benar-benar tidak bergantung lingkungan?

Ini yang paling penting, karena baru saja terbukti tidak.

- Jalankan suite **dua kali** dan bandingkan jumlah lulus:
  - sekali dengan env bersih (`Remove-Item Env:\TOTAL_TOOL_TURNS, Env:\REVISION_TOOL_TURNS, Env:\MAX_REVISION_TURNS -ErrorAction SilentlyContinue`)
  - sekali dengan env KOTOR (`$env:TOTAL_TOOL_TURNS="40"; $env:REVISION_TOOL_TURNS="8"; $env:MAX_REVISION_TURNS="1"`)
- **Kalau jumlahnya berbeda, itu bug** — temukan test mana yang bergantung lingkungan.
- Grep `tests/` untuk setiap test yang memanggil `ReviewStrategy(...).run(` atau `strategy.run(` **tanpa** mem-pin `MAX_REVISION_TURNS` via `monkeypatch.setattr`. Laporkan masing-masing dan apakah ia akan gagal kalau `MAX_REVISION_TURNS=4`.
- Cari pola yang sama untuk `TOTAL_TOOL_TURNS` dan `REVISION_TOOL_TURNS`: test mana yang membaca nilai ambient alih-alih mem-pin?

### 3. Apakah model yang benar-benar dipanggil sweep adalah model BERBAYAR?

Klaim: `.env` punya `oc/space-bunny-free` (gratis), sweep meng-override ke `cbai/deepseek-v4.1-flash` (berbayar) via `--set`, dan override menang.

- Jalankan `python tools/check_effective_model.py`.
- Verifikasi **independen**: baca `tools/run_with_env.py` dan buktikan bahwa override diterapkan SETELAH `.env` dimuat. Kalau override diterapkan sebelum, `.env` akan menang dan RQ3 akan mengukur model gratis (biaya $0) — sementara semua check lain tetap lulus.
- Periksa `src/providers/opencode_provider.py` dan `src/config.py`: apakah `Config.OPENCODE_MODEL` benar-benar yang dipakai provider?

### 4. Apakah `--resume` benar-benar melanjutkan, bukan mengulang?

- `python tools/run_final_sweep.py --resume --dry-run` → harus exit 2 (butuh `--exp-id`).
- `python tools/run_final_sweep.py --resume --exp-id EXP-20260930-415 --dry-run` → periksa apakah `--resume` dan `--exp-id EXP-20260930-415` ADA di perintah yang dicetak.
- Jalankan `python tools/verify_resume_logic.py` — semua check harus lulus.
- Jalankan `python tools/verify_resume_keys_match.py` — periksa apakah baris jsonl NYATA punya `model_name_or_path`. Kalau tidak, `--resume` tidak akan mencocokkan key dan akan mengulang semua run.

### 5. Apakah angka di dokumen cocok dengan kenyataan?

Ini sering salah dan berbahaya karena orang mempercayai dokumen.

- `docs/MEMORY.md` mengklaim: **421 test lulus**. Jalankan suite dan cek angkanya.
- `docs/HANDOFF_20261001.md` mengklaim tabel verifikasi (preflight 50/50, fairness FAIR, revisi 6+6, biaya -5,28%). Jalankan tiap perintah dan cek.
- `docs/MEMORY.md` tabel `.env` mengklaim nilai-nilai tertentu. Bandingkan dengan `.env` NYATA (baca file, jangan percaya tabel).
- Laporkan **setiap** angka yang tidak cocok.

## Format laporan

Tulis ke `docs/AUDIT_FINAL_VERIFY.md`:

- Setiap klaim: **perintah yang dijalankan**, **output verbatim**, **verdict** (`TERVERIFIKASI` / `SALAH` / `TIDAK BISA DIVERIFIKASI`).
- Setiap ketidakcocokan: apa yang diklaim, apa yang sebenarnya, di file:baris mana.
- Blocker baru apa pun, diurutkan berdasarkan **blast radius**.
- Yang **tidak bisa** kamu verifikasi: nyatakan eksplisit. **Jangan menebak angka.**

Lalu balas dengan ringkasan **5 baris**: verdict per area + blocker apa pun.
