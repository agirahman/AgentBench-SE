# TASK: Buru test yang rapuh terhadap lingkungan — di SELURUH repo

**JANGAN EDIT FILE SUMBER APA PUN** kecuali file test yang kamu tulis sendiri untuk
membuktikan temuan. Laporkan semuanya.

Repo: `D:\development\Skripsi2\AgantBech-SE`

## Kenapa tugas ini ada

Baru saja terbukti **"421 test lulus" itu PALSU**. Shell mengekspor `MAX_REVISION_TURNS=1`,
yang menimpa `.env` (bernilai 4). Tiga test lulus karena kebetulan cocok dengan nilai
**shell**, bukan karena kode benar. Ketika shell dibersihkan, ketiganya GAGAL.

Ini kelas bug yang berbahaya karena: **suite hijau memberi rasa aman yang salah**, dan
sweep 14 jam bisa berjalan dengan asumsi yang tidak pernah diuji.

Aku sudah memperbaiki 3 test yang kutemukan. **Tugasmu: cari sisanya.**

## Cara kerja yang benar

Jalankan suite **dua kali** dan bandingkan:

```powershell
# RUN A — env bersih
Remove-Item Env:\TOTAL_TOOL_TURNS, Env:\REVISION_TOOL_TURNS, Env:\MAX_REVISION_TURNS -ErrorAction SilentlyContinue
.venv\Scripts\python.exe -m pytest tests/ -q

# RUN B — env kotor
$env:TOTAL_TOOL_TURNS="40"; $env:REVISION_TOOL_TURNS="8"; $env:MAX_REVISION_TURNS="1"
.venv\Scripts\python.exe -m pytest tests/ -q
```

**Kalau jumlahnya berbeda → ada test yang rapuh.** Temukan yang mana.

## Yang harus kamu cari

### 1. Test yang membaca Config ambient alih-alih mem-pin

Grep `tests/` untuk pola ini, dan untuk **masing-masing** tentukan apakah hasilnya berubah
kalau nilai ambient berbeda:

- Test yang memanggil `.run(issue)` pada strategi (yang membaca `Config.TOTAL_TOOL_TURNS`,
  `Config.REVISION_TOOL_TURNS`, `Config.MAX_REVISION_TURNS`, `Config.BUDGET_MODE`,
  `Config.BUDGET_FLOOR_PER_ACT`) **tanpa** `monkeypatch.setattr` untuk nilai itu.
- Test yang mem-assert angka keras (mis. `== 40`, `== 32`, `== 8`, `== 3`) yang hanya benar
  untuk satu konfigurasi.
- Test yang memakai `monkeypatch.setenv` — itu **menulis** env, jadi ia bisa **mencemari**
  test berikutnya kalau tidak dibersihkan. Periksa apakah ada yang bocor.

### 2. Urutan test yang mengubah hasil

`pytest` menjalankan test dalam urutan tertentu. Kalau ada test yang mengubah `Config`
secara global (bukan lewat monkeypatch), test setelahnya bisa terpengaruh.

- Jalankan suite dengan `-p no:randomly` (kalau plugin ada) dan tanpa, bandingkan.
- Coba jalankan satu file test **sendirian** vs **setelah file lain**, bandingkan.
- Cari `importlib.reload(config)` di tests/ — MEMORY mencatat ini pernah membuat
  `monkeypatch.setattr("config.Config", ...)` meleset karena kelas lama vs baru. Periksa
  apakah masih ada.

### 3. Test yang lulus karena kebetulan, bukan karena benar

Ini yang paling sulit dan paling berharga. Contoh bentuknya:

- Assert `>=` atau `<=` yang begitu longgar sehingga selalu benar.
- Test yang tidak pernah menjalankan jalur yang diklaimnya (mis. revisi tidak pernah
  terpicu, jadi test "revisi mengedit" sebenarnya tidak menguji apa pun).
- `try/except` di dalam test yang menelan kegagalan.
- Test yang mem-assert pada mock, bukan pada perilaku nyata.

Untuk masing-masing, **buktikan** dengan mengubah nilai yang seharusnya membuatnya gagal:
kalau test tetap lulus, test itu tidak menguji apa pun.

**Contoh yang sudah terbukti di repo ini:** `tools/verify_revision_rounds.py` memakai
`expected = 8 * rounds` — aritmetika yang mustahil (4 × 2 × 8 = 64), sehingga ia menandai
nilai BENAR (48) sebagai salah. Checker yang mengulang konstanta yang dihafal alih-alih
menurunkan syaratnya akan mereproduksi bug yang seharusnya ia temukan. **Cari pola ini di
tempat lain.**

### 4. Klaim test vs perilaku nyata

Untuk test yang mengklaim sesuatu tentang perilaku (mis. "revisi mengedit", "planner
memakai tool", "patch kosong tidak dihitung"), verifikasi klaim itu terhadap **artefak run
nyata** di `results/EXP-*/artifacts/`, bukan hanya terhadap mock.

## Format laporan

Tulis ke `docs/AUDIT_TEST_ROBUSTNESS.md`:

- Untuk setiap test rapuh: **nama test**, **file:baris**, **bukti** (output pytest sebelum
  dan sesudah perubahan env), **dampak** kalau tidak diperbaiki.
- Untuk setiap test yang "lulus karena kebetulan": bukti eksperimen yang menunjukkan ia
  tidak bisa gagal.
- Ringkasan: berapa test yang rapuh, berapa yang tidak menguji apa pun.
- Yang **tidak bisa** kamu verifikasi: nyatakan eksplisit. **Jangan menebak.**

Lalu balas dengan ringkasan **5 baris**.
