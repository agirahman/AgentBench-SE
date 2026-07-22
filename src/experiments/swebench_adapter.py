import json
import re
from dataclasses import dataclass

from utils.logger import logger


@dataclass
class PatchResult:
    """Result dari extract_diff: patch string + status."""
    patch: str
    status: str  # VALID | INVALID_HUNK | PARSE_ERROR | NO_DIFF | EMPTY | PLACEHOLDER_ONLY


def _count_hunk_body(body):
    """Hitung baris orig/new per hunk body.

    Include empty line sebagai context. Trailing empty lines sebelum '@@'
    di-strip dulu supaya sama dengan validator. Dipakai bersama validator
    dan auto-fix supaya hitungan tidak pernah drift.
    """
    body = list(body)
    while body and body[-1] == "":
        body.pop()
    actual_orig = actual_new = 0
    for l in body:
        if l.startswith(" "):
            actual_orig += 1
            actual_new += 1
        elif l.startswith("+"):
            actual_new += 1
        elif l.startswith("-"):
            actual_orig += 1
        elif l.startswith(("\\", "Binary ")):
            continue
        elif l.strip() == "--":
            break
        elif l == "":
            actual_orig += 1
            actual_new += 1
        elif l.strip() and not l.startswith(("@@", "diff ", "--- ", "+++ ")):
            actual_orig += 1
            actual_new += 1
        else:
            return None
    return actual_orig, actual_new


def _check_patch_syntax(text: str) -> str | None:
    """Validasi struktur diff, return None jika valid atau string status penyebab gagal.

    Cek: prefix 'diff --git', tiap hunk header rapi (@@ -N,M +P,Q @@),
    dan jumlah baris context/added/removed per hunk cocok dengan count header.
    """
    if not text:
        return "EMPTY"

    lines = text.strip().split("\n")
    if not lines[0].startswith("diff --git"):
        return "NO_DIFF"

    HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    MAX_OFFSET = 200

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("@@"):
            i += 1
            continue

        m = HUNK.match(line)
        if not m:
            return "MALFORMED_HEADER"

        orig_start = int(m.group(1))
        orig_count = int(m.group(2) or 1)
        new_start = int(m.group(3))
        new_count = int(m.group(4) or 1)

        has_real_context = False
        has_placeholder_only = True
        has_placeholder_added = False
        real_code_lines = 0

        actual_orig = 0
        actual_new = 0
        i += 1
        hunk_lines = []
        while i < len(lines) and not lines[i].startswith("@@"):
            l = lines[i]
            if l.startswith("diff --git"):
                break
            hunk_lines.append(l)
            if l.startswith(" "):
                real_code_lines += 1
                if l.strip() and not l.strip() in ("# ...", "# ...", "# ..."):
                    has_real_context = True
                    has_placeholder_only = False
            elif l.startswith("+"):
                added_text = l[1:].strip()
                if added_text:
                    if added_text in ("# ...", "# ...", "# ...", "# ...", "# ..."):
                        has_placeholder_added = True
                    else:
                        has_real_context = True
                        has_placeholder_only = False
            elif l.startswith("-"):
                removed_text = l[1:].strip()
                if removed_text and removed_text not in ("# ...", "# ...", "# ..."):
                    has_real_context = True
                    has_placeholder_only = False
            elif l.startswith("\\") or l.startswith("Binary "):
                pass
            elif l.strip() == "--":
                break
            elif l == "":
                if i + 1 < len(lines) and lines[i + 1].startswith("@@"):
                    break
            elif l.strip() and not l.startswith(("@@", "diff ", "--- ", "+++ ")):
                pass
            else:
                return "BAD_BODY"
            i += 1

        counts = _count_hunk_body(hunk_lines)
        if counts is None:
            return "BAD_BODY"
        actual_orig, actual_new = counts

        if has_placeholder_only and not has_real_context:
            return "PLACEHOLDER_ONLY"

        if has_placeholder_added and not has_real_context:
            return "PLACEHOLDER_ONLY"

        if abs(new_start - orig_start) > MAX_OFFSET:
            return "OFFSET_TOO_LARGE"

        if actual_orig != orig_count or actual_new != new_count:
            return "HUNK_MISMATCH"

    return None  # valid


def _is_valid_patch_syntax(text: str) -> bool:
    """Boolean shim di atas _check_patch_syntax (None == valid)."""
    return _check_patch_syntax(text) is None


def _normalize_newlines(text: str) -> str:
    """Konversi double-escape newline hasil kompresi JSON secara menyeluruh."""
    if "\\n" in text:
        text = text.replace("\\n", "\n")
    if "\\t" in text:
        text = text.replace("\\t", "\t")
    return text


def normalize_patch_headers(patch: str) -> str:
    """Hitung ulang actual baris per hunk, rewrite @@ header agar sesuai."""
    lines = patch.split("\n")
    result = []
    i = 0
    HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    while i < len(lines):
        line = lines[i]
        m = HUNK.match(line)
        if not m:
            result.append(line)
            i += 1
            continue

        body = []
        i += 1
        while i < len(lines) and not lines[i].startswith("@@"):
            if lines[i].startswith("diff --git"):
                break
            body.append(lines[i])
            i += 1

        counts = _count_hunk_body(body)
        if counts is None:
            result.append(f"@@ -{m.group(1)},{m.group(2) or 1} +{m.group(3)},{m.group(4) or 1} @@")
            result.extend(body)
            continue
        actual_orig, actual_new = counts

        orig_start = int(m.group(1))
        new_start = int(m.group(3))

        result.append(f"@@ -{orig_start},{actual_orig} +{new_start},{actual_new} @@")
        result.extend(body)

    return "\n".join(result)


def _clean_patch(text: str) -> PatchResult:
    """Strip, normalize newline, lalu validasi; normalize hunk headers kalau mismatch.

    Tambah trailing newline di akhir supaya git apply tidak gagal dengan
    'malformed patch at line N' (patch harus berakhir dengan \\n).
    """
    if not text:
        return PatchResult(patch="", status="EMPTY")
    patch = _normalize_newlines(text).strip()
    if not patch:
        return PatchResult(patch="", status="NO_DIFF")
    failure = _check_patch_syntax(patch)
    if failure is None:
        if not patch.endswith("\n"):
            patch += "\n"
        return PatchResult(patch=patch, status="VALID")
    fixed = normalize_patch_headers(patch)
    failure_fixed = _check_patch_syntax(fixed)
    if failure_fixed is None:
        logger.info("Hunk headers normalized successfully")
        if not fixed.endswith("\n"):
            fixed += "\n"
        return PatchResult(patch=fixed, status="NORMALIZE")
    logger.warning(f"Patch failed syntax validation even after normalization: {failure_fixed}")
    return PatchResult(patch="", status=failure_fixed)


def extract_diff(response: str, finish_reason: str = "") -> PatchResult:
    """Ambil diff/patch dari response LLM.

    Alur:
      1. Kalau finish_reason='length' (response ke-trim) → langsung TRUNCATED.
      2. Cari markdown code block (toleran ```json / ```diff / ```patch / ```).
      3. Kalau isi berupa JSON, ambil field "patch".
      4. Fallback: seluruh response sebagai JSON.
      5. Last resort: raw text.
    Tiap hasil divalidasi strukturnya; kalau invalid return PatchResult dengan status penyebab.
    """
    if not response:
        return PatchResult(patch="", status="EMPTY")
    if finish_reason == "length":
        return PatchResult(patch="", status="TRUNCATED")

    # 1. Markdown code block (toleran label apa pun, mis. python/diff/patch/json)
    m = re.search(r"```(?:[A-Za-z0-9_-]+)?\s*\n(.*?)```", response, re.DOTALL)
    if m:
        content = m.group(1).strip()
        if content.startswith("{"):
            try:
                data = json.loads(content)
                if isinstance(data, dict) and "patch" in data:
                    return _clean_patch(str(data["patch"]))
            except json.JSONDecodeError:
                logger.warning("Markdown block looks like JSON but failed to parse")
                return PatchResult(patch="", status="PARSE_ERROR")
        return _clean_patch(content)

    # 2. JSON di seluruh response (tanpa markdown)
    try:
        data = json.loads(response)
        if isinstance(data, dict) and "patch" in data:
            return _clean_patch(str(data["patch"]))
    except (json.JSONDecodeError, ValueError):
        pass

    # 3. Last resort: raw text
    return _clean_patch(response.strip())
