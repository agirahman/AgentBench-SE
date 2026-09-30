# AUDIT — `run_tests`: is the read-only guarantee real?

**Repo:** `D:\development\Skripsi2\AgantBech-SE`
**Sifat:** READ-ONLY. Tidak ada file sumber yang diubah. Skrip scratch pakai prefix `.verify_p7c_*`.
**Data yang dipakai:** `results/EXP-20260930-332/artifacts/*/*/tool_calls.jsonl` (pilot 200-turn) dan
`results/EXP-20260930-215/artifacts/*/*/tool_calls.jsonl` (pilot 40-turn).

---

## Ringkasan jawaban

**Ya — sebuah role read-only (`reviewer`) dapat menulis ke repository lewat `run_tests`, dan tulisan itu
bisa mencapai patch final.** Ini bukan konvensi yang ditegakkan; ini lubang nyata. `planner` aman
(ia tidak diberi shell sama sekali). Guard yang ada **tidak satu pun** mencegah tulisan.

**Tetapi:** dari 63 panggilan `run_tests` nyata di kedua pilot, **tidak ada satu pun** yang dilakukan
oleh role read-only. Satu-satunya write nyata dilakukan oleh `executor` (role yang memang boleh menulis),
dan write itu **tidak** mencapai patch. Jadi lubangnya nyata dan dapat dieksploitasi, tetapi **belum
pernah terpicu** di data yang ada.

**Koreksi atas classifier yang mencurigakan:** ya, classifier sebelumnya salah. `python -m pytest ... 2>&1 | tail -20`
adalah **test**, bukan write. Angka "write" yang lama terlalu besar karena bug classifier, bukan karena
perilaku model.

---

## 1. Perilaku persis `run_tests`

Implementasi: `src/agents/tools.py:331-402`. Signature `run_tests(command: str = "") -> str`.

| Aspek | Detail | file:line |
|---|---|---|
| Root sandbox | `root = _repo_root()` — repo instance yang sedang aktif, atau `TOOLCALL_REPO_DIR` | `tools.py:350` |
| Root hilang | `return f"[error] repo dir not found: {root}"` | `tools.py:351-352` |
| Command kosong | diganti `_guess_test_command(root)` (deteksi `tests/runtests.py`, `pytest.ini`, dll.) | `tools.py:354-356`, `405-422` |
| **Repeat guard** | Menghitung kemunculan command identik di `_RECENT_TEST_COMMANDS`; jika `>= _MAX_REPEATS` (`=2`) → `return "[stop] ..."` **tanpa menjalankan** | `tools.py:317-328`, `358-366` |
| Eksekusi | `subprocess.run(command, shell=True, cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)` | `tools.py:368-378` |
| Timeout | `except subprocess.TimeoutExpired: return "[error] command timed out (180s limit)"` | `tools.py:379-380` |
| Exception lain | `return f"[error] cannot run command: {e}"` | `tools.py:381-382` |
| Gabung output | `out = (proc.stdout or "") + (proc.stderr or "")` | `tools.py:384` |
| **Output cap** | `limit = 4000`; jika lebih, **potong dari DEPAN**: `out = out[-limit:]` | `tools.py:385-388` |
| Deteksi env-failure | Jika output cocok salah satu `_ENV_FAILURE_MARKERS` (`"no module named"`, `"command not found"`, dst.) → return teks `"[tests unavailable] ..."` yang menyuruh agen berhenti | `tools.py:300-314`, `390-399` |
| Return normal | `f"[exit code {proc.returncode}]\n{out}{suffix}"` | `tools.py:401-402` |
| Reset guard | `reset_test_guard()` dipanggil `reset_working_tree()` per strategy-run → guard bercakupan **satu act** | `tools.py:425-427`, `663` |

**Catatan penting:** `run_tests` **tidak** memanggil `_safe_path()`. Tool itu menerima `command` mentah
dan menjalankannya dengan `shell=True`. Nama parameternya `command`, bukan `path`, dan deskripsi schema-nya
(`tools.py:822-827`) hanya *menyarankan* test runner — tidak memaksanya.

**Tidak ada validasi isi command.** Tidak ada daftar putih perintah, tidak ada penolakan `sed -i`,
`git checkout`, redirect, `python -c "open(...,'w')"`, atau apa pun.

### Pemberian tool per role

`src/agents/tools.py:751-762`:

```python
AGENT_TOOLS = {
    "direct":   ["read_file", "grep", "list_files", "run_tests",
                 "edit_file", "write_file", "git_diff", "reset_repo"],
    "planner":  ["read_file", "grep", "list_files"],
    "executor": ["read_file", "grep", "list_files", "run_tests",
                 "edit_file", "write_file", "git_diff", "reset_repo"],
    "reviewer": ["read_file", "grep", "run_tests", "git_diff"],
}
```

Diverifikasi dengan menjalankan `get_tools_for_agent()` (`.verify_p7c_e2e.py`):

```
direct    read_only=False has_run_tests=True
planner   read_only=True  has_run_tests=False
executor  read_only=False has_run_tests=True
reviewer  read_only=True  has_run_tests=True     <-- read-only TAPI punya shell
```

Jadi premis tugas ini benar: **`planner` dan `reviewer` tidak punya `edit_file`/`write_file`**, tetapi
`reviewer` **punya `run_tests`**, yang merupakan shell arbitrer.

---

## 2. Bisakah role read-only menulis?

### `planner` — TIDAK

`planner` hanya punya `read_file`, `grep`, `list_files`. Tidak ada jalur eksekusi sama sekali. Read-only-nya
**ditegakkan secara struktural**.

### `reviewer` — YA, dan terverifikasi dengan eksekusi

`reviewer` punya `run_tests`. Dijalankan terhadap git repo sementara (`.verify_p7c_e2e.py`):

```
2. As the REVIEWER role: use run_tests to modify a tracked file
  run_tests("python -c \"open('core.py','w').write('def f():\n    return 999  # authored by reviewer\n')\"")
    -> '[exit code 0]\n'
  core.py on disk now: '1: def f():\n2:     return 999  # authored by reviewer'

3. As the REVIEWER role: use run_tests to create a NEW file
  run_tests("python -c \"open('reviewer_added.py','w').write('BACKDOOR = True\n')\"")
    -> '[exit code 0]\n'

4. Does capture_diff (the patch pipeline) include both?
  core.py modification in patch : True
  reviewer_added.py in patch    : True
```

Command-command yang bisa dipakai reviewer untuk menulis (semua lolos tanpa hambatan):

```
python -c "open('target.py','w').write(open('evil.txt').read())"
python -c "import os; os.remove('django/forms/widgets.py')"
sed -i 's/return True/return False/' django/forms/widgets.py
git checkout -- django/forms/widgets.py
echo 'x = 1' > django/conf/global_settings.py
python -c "import shutil; shutil.rmtree('tests')"
git apply evil.patch
```

**Keluar dari sandbox repo** — `run_tests` bahkan tidak dibatasi ke repo (`.verify_p7c_e2e.py` §5):

```
5. Can run_tests write OUTSIDE the repo? (_safe_path is NOT applied)
  run_tests("python -c \"open(r'C:\...\Temp\p7c_outside_marker.txt','w').write('escaped')\"")
    -> '[exit code 0]\n'
  file created OUTSIDE repo: True
```

Bandingkan: `write_file`/`edit_file` **dibatasi** `_safe_path()` (`tools.py:190-200`), yang menolak
`/tmp/verify_merge.py` dengan pesan *"outside the allowed repo directory"* (terbukti di data nyata,
`EXP-20260930-215/.../direct` L17). `run_tests` **tidak punya batas itu sama sekali**.

### Apakah ada guard yang mencegahnya?

Diuji satu per satu (`.verify_p7c_e2e.py` §6):

| Guard | Efek terhadap write |
|---|---|
| Repeat-command guard | **Tidak mencegah.** Hanya memblokir command yang **identik** ke-3 kali. Write dengan ejaan berbeda selalu lolos. Diverifikasi: panggilan ke-1 dan ke-2 jalan, ke-3 diblokir. |
| Env-failure detection | **Tidak mencegah.** Ia hanya menulis ulang **teks hasil** setelah command dijalankan. Diverifikasi: `sneaky.py` tetap tertulis meski hasilnya `"[tests unavailable] ..."`. |
| Timeout 180 s | Tidak relevan; write selesai dalam milidetik. |
| Output cap 4000 | Tidak relevan. |
| `_safe_path` | **Tidak dipanggil** oleh `run_tests`. |
| Validasi role saat dispatch | **Tidak ada.** Lihat di bawah. |

### Lubang kedua yang lebih dalam: `execute_tool` tidak memeriksa role

`src/agents/tools.py:903-913`:

```python
def execute_tool(name: str, arguments: dict) -> str:
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return f"[error] unknown tool: {name}"
    ...
```

Tidak ada parameter `role`, tidak ada pengecekan daftar-izin. `tool_loop.py:475` memanggil
`execute_tool(name, args)` dengan **nama apa pun yang dikembalikan model**. Diverifikasi
(`.verify_p7c_dispatch.py`):

```
reviewer's granted tools: ['read_file', 'grep', 'run_tests', 'git_diff']
execute_tool('write_file', {...}) -> '[ok] created reviewer_authored.py (16 chars)'
execute_tool('edit_file',  {...}) -> '[ok] edited core.py ...'
execute_tool('reset_repo', {})    -> '[ok] repository reset to HEAD'
```

Artinya, bahkan **jika** `run_tests` diperketat, seorang reviewer yang mengeluarkan nama tool di luar
grant-nya tetap akan dieksekusi. Grant di `AGENT_TOOLS` hanya menyaring **daftar schema yang dikirim ke
model**, bukan apa yang boleh dijalankan.

Satu-satunya rem adalah prompt: `src/agents/base.py:127-132` memilih `READONLY_TOOL_SYSTEM_PROMPT` untuk
role non-editing, dan prompt itu berbunyi *"You may NOT modify files."* Itu instruksi, bukan penegakan.

---

## 3. Pengukuran dari data nyata

Sumber: 63 baris dengan `tool == "run_tests"` di kedua pilot. Dump mentah seluruh 63 command:
`.verify_p7c_dump_out.txt`. Klasifikasi final: `.verify_p7c_final.py`.

**Classifier divalidasi lebih dulu** (ini yang diminta tugas). `python -m pytest tests/x.py -q 2>&1 | tail -20`
→ diklasifikasi **test** (benar). Kontrol negatif lain juga lulus (`.verify_p7c_final.py`):

```
[OK] want=test  got=test   python -m pytest tests/x.py -q 2>&1 | tail -20
[OK] want=test  got=test   python -m pytest tests/x.py >/dev/null 2>&1
[OK] want=probe got=probe  git rev-parse HEAD
[OK] want=probe got=probe  ls -la
[OK] want=probe got=probe  python -c "import ast; ast.parse(open('x.py').read())"
[OK] want=write got=write  python -c "open('x.py','w').write('a')"
[OK] want=write got=write  sed -i "s/a/b/" x.py
[OK] want=write got=write  git checkout -- .
[OK] want=write got=write  echo hi > out.txt
[OK] want=probe got=probe  python -c "print(1)" 2>&1; echo EXIT=$?
[OK] want=probe got=probe  print('OLD ->', x)          # '>' di dalam string, bukan redirect
```

**Peringatan metodologis:** classifier pertama saya menghasilkan **13 "write" palsu**. Penyebabnya: regex
redirect cocok pada literal `->` di dalam `print('OLD ->', ...)` milik reviewer. Setelah memperbaiki
aturan redirect (mensyaratkan karakter sebelum `>` adalah spasi/awal-string/digit/quote), jumlah write turun
ke **1**. Ini kemungkinan besar penyebab yang sama dengan hasil mencurigakan pada pengecekan sebelumnya.
**Jangan pakai angka write dari classifier yang belum divalidasi terhadap kontrol negatif ini.**

### Hitungan per role per eksperimen

**EXP-20260930-332** (pilot 200-turn; total 42 panggilan `run_tests`):

| role | test | probe | write | total |
|---|---|---|---|---|
| `direct` | 3 | 0 | 0 | 3 |
| `executor` | 10 | 15 | **1** | 26 |
| `reviewer` | 3 | 10 | **0** | 13 |
| `planner` | 0 | 0 | 0 | 0 |

**EXP-20260930-215** (pilot 40-turn; total 21 panggilan `run_tests`):

| role | test | probe | write | total |
|---|---|---|---|---|
| `direct` | 0 | 0 | 0 | 0 |
| `executor` | 11 | 3 | 0 | 14 |
| `reviewer` | 2 | 5 | 0 | 7 |
| `planner` | 0 | 0 | 0 | 0 |

Per strategi (untuk referensi):

| exp | strategy | test | probe | write |
|---|---|---|---|---|
| 332 | direct | 3 | 0 | 0 |
| 332 | planning | 5 | 15 | **1** |
| 332 | review | 8 | 10 | 0 |
| 215 | direct | 0 | 0 | 0 |
| 215 | planning | 5 | 3 | 0 |
| 215 | review | 8 | 5 | 0 |

### Catatan tentang `direct` = 0 di pilot 215

Ini **bukan** karena `direct` memilih untuk tidak memakai `run_tests`, tetapi karena **tool-nya memang
belum diberikan** saat pilot itu berjalan:

- `direct` mendapat `run_tests` di commit **`8d134ab`** ("fix(tools): give every strategy the same tool
  reach"), author/commit time **2026-09-30 23:19:12 +0700 = 16:19:12 UTC**.
- `EXP-20260930-215` mulai **2026-09-30T13:55:03Z** → **sebelum** commit → `direct` tanpa `run_tests` → 0 panggilan.
- `EXP-20260930-332` mulai **2026-09-30T16:33:18Z** → **sesudah** commit → `direct` dengan `run_tests` → 3 panggilan.

Jadi angka 0 vs 3 pada `direct` mengukur **grant**, bukan pilihan strategi. Ini konsisten dengan alasan
yang tertulis di `tools.py:726-736` dan di `tests/test_agent_tools.py:297-317`.

---

## 4. Setiap write nyata yang ditemukan

**Jumlah write nyata di kedua pilot: 1 (satu).** Tidak ada lagi.

### WRITE #1 — `EXP-20260930-332`, `django__django-11019`, strategi `planning`, **role `executor`**, L46

Command lengkap:

```
python.exe -c "import os; [os.remove(f) for f in ['_t.py','_t2.py','_t3.py','_t4.py']]"
```

Konteks (L44-L47 di `tool_calls.jsonl`):

```
L44 executor write_file '_t4.py'
L45 executor run_tests  'python.exe _t4.py'          -> '[exit code 0]\n[...] BUG FIXED'
L46 executor run_tests  'python.exe -c "import os; [os.remove(f) for f in [\'_t.py\',...]]"'
L47 executor git_diff   ''                           -> diff files: []
```

**Apakah mencapai patch final? TIDAK.**

- Command itu menghapus `_t.py`..`_t4.py`, yaitu file scratch yang **dibuat sendiri** oleh executor memakai
  `write_file` (L27, L33, L40, L42, L44) untuk menguji logika merge. Ini pembersihan diri, bukan sabotase.
- File-file itu **untracked**. Menghapus file untracked tidak muncul di `git diff`.
- `patch.txt` untuk run itu hanya berisi `django/forms/widgets.py`. Tidak ada `_t*.py`.
- Diverifikasi menyeluruh (`.verify_p7c_patch.py` §2): **0 dari 30 patch** di kedua pilot memuat file scratch.

**Catatan penting:** write ini dilakukan oleh `executor` — role yang **memang boleh** menulis — dan ia
sudah punya `write_file`/`edit_file`. Jadi `run_tests` di sini tidak menambah kemampuan baru apa pun;
ia hanya dipakai sebagai `os.remove` alih-alih alat yang tepat. Bukan pelanggaran read-only.

### Write lain yang saya periksa dan BUKAN write

- `python.exe -c "import os; ..."` adalah satu-satunya yang cocok pola mutasi.
- `python -c "import ast; ast.parse(open('...').read())"` — `open` tanpa mode `'w'`, hanya baca → **probe**.
- `python -c "... op.search(...).group(1) ..."` (11 panggilan oleh reviewer 332/11001) — regex murni → **probe**.
- `python -c "... print('OLD ->', ...)"` (3 panggilan reviewer 215/11001) — literal `->` dalam string → **probe**.
  Inilah yang tadinya salah diklasifikasi.
- `cd $(git rev-parse --show-toplevel ...) && python -c ...` → **probe** (git rev-parse read-only).
- `python << 'EOF' ... EOF` → **probe** (heredoc ke stdin, bukan redirect file).
- `python -m pytest ... 2>&1 | tail -N` dan `>/dev/null 2>&1` → **test**.

---

## 5. Apakah write role read-only bisa mencapai patch? (uji konstruktif)

Karena tidak ada write reviewer di data nyata, saya membuktikannya secara konstruktif dengan
memanggil `run_tests` dan `capture_diff` yang asli terhadap git repo sementara.

### 5a. Jalur "approve" — write reviewer TIDAK mencapai patch

`review_strategy.py:141` mengambil `initial_diff` **sebelum** act reviewer (baris 149). Jika reviewer
APPROVED, `patch_text = initial_patch` (baris 235-238) — yaitu capture pra-reviewer. Diverifikasi
(`.verify_p7c_leak.py` CASE A):

```
reviewer wrote backdoor.py and rewrote core.py via run_tests
SHIPPED patch contains backdoor.py : False
SHIPPED patch contains return 666  : False
```

Write-nya tetap ada di working tree, tetapi tidak masuk patch yang dikirim, dan strategi berikutnya
memanggil `reset_working_tree()` lebih dulu (`review_strategy.py:88`), jadi tidak menular.

### 5b. Jalur "revision" — write reviewer MASUK ke patch yang dikirim

`review_strategy.py:201` mengambil `revised_patch = finalize_patch(...)` **sesudah** act reviewer,
dan baris 235 mengirim kandidat yang disetujui. Diverifikasi 3/3 (`.verify_p7c_leak2.py`):

```
trial 1: revised files: ['backdoor.py', 'core.py']   LEAK: True
trial 2: revised files: ['backdoor.py', 'core.py']   LEAK: True
trial 3: revised files: ['backdoor.py', 'core.py']   LEAK: True
```

Patch yang dikirim bila re-review APPROVED:

```
diff --git a/backdoor.py b/backdoor.py
new file mode 100644
--- /dev/null
+++ b/backdoor.py
@@ -0,0 +1 @@
+BACKDOOR=True
diff --git a/core.py b/core.py
...
```

**Jalur ini nyata dan pernah aktif:** di `EXP-20260930-215/django__django-11019/review` urutan
agennya `planner → executor → reviewer → executor → reviewer` — artinya **satu ronde revisi benar-benar
terjadi** (`.verify_p7c_rev.py` §1). Reviewer di run itu tidak melakukan write, jadi tidak ada kebocoran
di data. Tetapi kondisinya sudah terpenuhi; hanya perilaku model yang kebetulan tidak memicunya.

**Ringkas:** seorang reviewer yang menolak patch sambil menulis lewat `run_tests` akan menyuntikkan
tulisannya ke kandidat revisi, dan kandidat itu dikirim jika re-review menyetujuinya.

### 5c. Apakah model bisa memakai tool di luar grant-nya?

Ya. `.verify_p7c_dispatch.py` §2: `execute_tool("write_file", ...)` dan `execute_tool("edit_file", ...)`
berhasil dieksekusi tanpa mengetahui role pemanggil. `tool_loop.py:467-475` meneruskan nama apa pun
dari respons model.

---

## 6. Konsekuensi desain — ditegakkan atau konvensional?

**Konvensional, bukan ditegakkan.**

Premis strategi `review` adalah *"reviewer tidak mengarang kode"*. Premis itu ditopang oleh **tiga
lapis yang semuanya lunak**:

1. **Grant tool** (`AGENT_TOOLS`) — menyaring daftar schema yang dikirim ke model, tetapi **tidak**
   memeriksa apa yang dijalankan. `execute_tool` tidak punya parameter role.
2. **System prompt** (`READONLY_TOOL_SYSTEM_PROMPT`, `system_prompts.py`) — berbunyi *"You may NOT modify
   files."* Ini instruksi bahasa, bukan penegakan.
3. **Tidak adanya `edit_file`/`write_file`** — benar dan struktural, tetapi tidak relevan karena
   `run_tests` menyediakan shell arbitrer, dan itu cukup untuk menulis apa pun.

Yang benar-benar ditegakkan hanya satu hal: **`planner` tidak punya jalur eksekusi sama sekali**.
Untuk `reviewer`, batasnya **konvensional**.

Konsekuensi metodologis: klaim tesis bahwa "reviewer menilai tanpa mengarang kode" **tidak dijamin oleh
harness**. Ia benar untuk data yang ada (0 write reviewer dari 20 panggilan reviewer), tetapi hasil itu
adalah **perilaku model**, bukan invarian sistem. Sebuah run yang reviewer-nya memutuskan menulis akan
menghasilkan patch yang berisi kode karangan reviewer, dan tidak ada apa pun di pipeline yang menandainya.

---

## 7. Grant `run_tests` untuk `direct`

Riwayat `AGENT_TOOLS` (`git log -S'"run_tests"' -- src/agents/tools.py`):

| commit | tanggal | `direct` | `planner` | `executor` | `reviewer` |
|---|---|---|---|---|---|
| `df9c35c` (per-agent assignment awal) | 2026-08-23 | read/grep/list | read/grep/list | read/grep/list/**run_tests** | read/grep/**run_tests** |
| `35ea817` (guard thrashing) | 2026-09-27 | (tidak mengubah grant) | | | |
| **`8d134ab`** (parity) | **2026-09-30 23:19 +0700** | **+run_tests +reset_repo** | | | |
| `af0f16b` | 2026-09-30 23:32 | (tidak mengubah grant) | | | |

**Kapan `direct` mendapat `run_tests`:** commit `8d134ab`, 2026-09-30 23:19:12 +0700.

**Apakah grant itu lebih luas dari yang dimaksud?** Menurut alasan di `tools.py:726-736` dan commit
message `8d134ab`, **tidak** — tujuannya eksplisit: menyamakan kemampuan verifikasi antar-strategi,
karena pilot menunjukkan `run_tests` dipanggil 8x oleh planning dan 13x oleh review tetapi 0x oleh direct,
dan angka 0 itu mengukur grant, bukan pilihan. Grant-nya adalah **satu tool tambahan untuk satu role
yang sudah boleh menulis**, jadi ia tidak memperluas permukaan bahaya read-only.

**Namun ada satu efek samping yang perlu dicatat:** `8d134ab` **juga** memberi `direct` `reset_repo`
(dan `run_tests`). `direct` sekarang bisa menghapus seluruh working tree-nya. Itu disengaja
(didokumentasikan di `tools.py:738-750`), tetapi `direct` adalah strategi satu-act: jika ia
`reset_repo` setelah mengedit dan tidak mengedit lagi, patch-nya kosong. Dua pilot di sini tidak
memperlihatkan hal itu, tetapi mekanismenya ada.

`reviewer` **tidak** mendapat `reset_repo` (diverifikasi: `.verify_p7c_e2e.py` §7), sehingga seorang
reviewer yang menulis lewat `run_tests` **tidak bisa** membersihkan jejaknya dengan `reset_repo` —
ia hanya bisa menulis, bukan menghapus riwayatnya.

---

## 8. Yang BELUM bisa saya pastikan

Saya nyatakan eksplisit; tidak ada estimasi.

1. **Apakah `run_tests` pernah dipakai untuk menulis di eksperimen LAIN di luar dua pilot ini.**
   Saya hanya memeriksa `EXP-20260930-332` dan `EXP-20260930-215`, sesuai instruksi. Repo punya
   eksperimen lain (mis. `EXP-20260930-030`, `EXP-20260929-022`) yang belum saya buka
   `tool_calls.jsonl`-nya. Jumlah write di seluruh riwayat **tidak dapat saya tetapkan** dari data
   yang saya periksa. Cara memastikan: jalankan classifier yang sudah divalidasi (`.verify_p7c_final.py`)
   terhadap `results/*/artifacts/*/*/tool_calls.jsonl`.

2. **Apakah model benar-benar akan mengeluarkan nama tool di luar grant-nya.** Saya membuktikan
   `execute_tool` **menerima** panggilan seperti itu, tetapi saya tidak menemukan satu pun kasus nyata
   di mana seorang reviewer mengeluarkan `write_file`/`edit_file`. Karena provider hanya mengirim
   schema yang diizinkan, model umumnya tidak tahu tool itu ada. Frekuensinya **tidak dapat saya ukur**.

3. **Apakah ada write reviewer yang tidak terlihat sebagai write di `tool_calls.jsonl`.** Saya
   mengklasifikasi **teks** command. Jika sebuah command menulis lewat mekanisme yang tidak saya
   modelkan (mis. kode Python di dalam file yang dibuat lalu dijalankan), klasifikasi berbasis teks
   tidak akan menangkapnya. Saya tidak menemukan pola seperti itu di 63 command, tetapi saya tidak
   dapat membuktikan ketiadaannya secara menyeluruh.

4. **Efek write reviewer yang di-approve (jalur 5a) terhadap strategi berikutnya.** Saya
   menyimpulkan tidak menular karena `reset_working_tree()` dipanggil di awal setiap strategi, tetapi
   saya tidak menjalankan urutan multi-strategi end-to-end untuk memastikannya.

5. **Apakah `write_file` yang saya jalankan di repo temp meninggalkan efek pada repo proyek.** Tidak —
   semua uji memakai `tempfile.mkdtemp()`, dan `set_repo_root()` di-set ke direktori itu. Repo proyek
   tidak tersentuh (`git status --porcelain` bersih untuk file sumber).

---

## 9. Ringkasan temuan

| # | Temuan | Tingkat |
|---|---|---|
| 1 | `reviewer` (role read-only) dapat menulis apa pun ke repo lewat `run_tests`, dan tulisannya masuk ke patch yang dikirim pada jalur revisi. Terverifikasi 3/3. | **KRITIS** |
| 2 | `run_tests` tidak dibatasi ke repo (`_safe_path` tidak dipakai) — bisa menulis ke mana saja di filesystem. Terverifikasi. | **KRITIS** |
| 3 | `execute_tool` tidak memvalidasi role; model yang mengeluarkan tool di luar grant-nya akan dieksekusi. Terverifikasi. | **SERIUS** |
| 4 | Tidak ada satu pun guard `run_tests` (repeat, env-failure, timeout, cap) yang mencegah write. Terverifikasi satu per satu. | **SERIUS** |
| 5 | Klaim "reviewer tidak mengarang kode" hanya dijaga oleh prompt, bukan oleh sistem. | **SERIUS** (konsekuensi metodologis) |
| 6 | Tidak ada write nyata oleh role read-only di kedua pilot (0 dari 20 panggilan reviewer); 1 write nyata, oleh `executor`, tidak mencapai patch. | **Informasi** |
| 7 | Classifier "write" pada pengecekan sebelumnya terlalu besar (13 palsu → 1 nyata); penyebabnya `->` di dalam string. | **Koreksi metode** |

### Catatan: modifikasi konkuren oleh proses lain

Selama audit ini, `git status` menunjukkan `src/agents/base.py`, `src/prompts/*.md` dalam keadaan
**modified**, dan muncul file baru (`src/prompts/planner_tools.md`, `tests/test_prompt_variants.py`,
`tools/check_run_tests_usage.py`, dll.). **Itu bukan dari sesi ini** — saya tidak mengubah file sumber
apa pun.

Yang relevan: **`src/agents/tools.py` TIDAK berubah** (`git diff --name-only src/agents/tools.py` kosong).
Jadi seluruh temuan di laporan ini (`run_tests`, `execute_tool`, `AGENT_TOOLS`, `_safe_path`) tetap
valid apa adanya. Perubahan di `base.py` hanya menambah `warnings.warn` saat varian prompt tool hilang;
blok pemilihan system prompt di `base.py:127-132` yang saya sitasi **tidak berubah**.

### Usul (tidak diimplementasikan — tugas ini READ-ONLY)

1. **Tegakkan grant di titik dispatch.** Beri `execute_tool(name, arguments, role)` dan tolak nama yang
   tidak ada di `AGENT_TOOLS[role]`. Ini menutup temuan #3 dan membuat `AGENT_TOOLS` menjadi batas nyata,
   bukan sekadar daftar schema.
2. **Batasi `run_tests` untuk role non-editing.** Untuk `reviewer`, jalankan hanya test runner yang
   dikenali (pytest/unittest/runtests.py), atau jalankan dengan sandbox yang menolak write
   (mis. filesystem read-only), atau teruskan command melalui parser yang menolak `>`/`>>`/`sed -i`/
   `git checkout|apply|reset|clean`/`os.remove`/`open(...,'w')`.
3. **Terapkan `_safe_path`-style containment** ke `run_tests` (minimal `cwd` tidak boleh dipakai untuk
   keluar repo; lebih baik jalankan di sandbox).
4. **Perbaiki urutan capture di `review_strategy.py`.** Capture `revised_patch` **sebelum** act reviewer
   mana pun yang mendahuluinya, atau reset working tree ke state pasca-executor sebelum mengambil diff
   revisi, sehingga tulisan reviewer tidak bisa masuk.
5. **Catat pelanggaran.** Jika seorang role non-editing memanggil tool tulis atau command yang menulis,
   catat ke artefak run agar dapat diaudit, alih-alih dibiarkan senyap.
