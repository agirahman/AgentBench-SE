import json
import re

from utils.logger import logger


def _is_valid_patch_syntax(text: str) -> bool:
    """Validasi struktur diff tanpa perlu repo target.

    Cek: prefix 'diff --git', tiap hunk header rapi (@@ -N,M +P,Q @@),
    dan jumlah baris context/added/removed per hunk cocok dengan count header.
    """
    if not text:
        return False

    lines = text.strip().split("\n")
    if not lines[0].startswith("diff --git"):
        return False

    HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("@@"):
            i += 1
            continue

        m = HUNK.match(line)
        if not m:
            logger.warning(f"Malformed hunk header: {line!r}")
            return False

        orig_count = int(m.group(2) or 1)
        new_count = int(m.group(4) or 1)

        actual_orig = 0
        actual_new = 0
        i += 1
        while i < len(lines) and not lines[i].startswith("@@"):
            l = lines[i]
            if l.startswith("diff --git"):
                break
            elif l.startswith(" "):
                actual_orig += 1
                actual_new += 1
            elif l.startswith("+"):
                actual_new += 1
            elif l.startswith("-"):
                actual_orig += 1
            elif l.startswith("\\") or l.startswith("Binary "):
                pass
            elif l.strip() == "--":
                break
            elif l == "":
                if i + 1 < len(lines) and lines[i + 1].startswith("@@"):
                    break
                else:
                    actual_orig += 1
                    actual_new += 1
            elif l.strip() and not l.startswith(("@@", "diff ", "--- ", "+++ ")):
                actual_orig += 1
                actual_new += 1
            else:
                logger.warning(f"Unexpected patch line: {l!r}")
                return False
            i += 1

        if actual_orig != orig_count or actual_new != new_count:
            logger.warning(
                "Hunk count mismatch: header claims "
                f"-{orig_count}/+{new_count}, got -{actual_orig}/+{actual_new}"
            )
            return False

    return True


def _normalize_newlines(text: str) -> str:
    """Konversi double-escape newline hasil kompresi JSON secara menyeluruh."""
    if "\\n" in text:
        text = text.replace("\\n", "\n")
    if "\\t" in text:
        text = text.replace("\\t", "\t")
    return text


def _clean_patch(text: str) -> str:
    """Strip, normalize newline, lalu validasi; kembalikan '' kalau gak valid."""
    if not text:
        return ""
    patch = _normalize_newlines(text).strip()
    if _is_valid_patch_syntax(patch):
        return patch
    logger.warning("Patch failed syntax validation")
    return ""


def extract_diff(response: str) -> str:
    """Ambil diff/patch dari response LLM.

    Alur:
      1. Cari markdown code block (toleran ```json / ```diff / ```patch / ```).
      2. Kalau isi berupa JSON, ambil field "patch".
      3. Fallback: seluruh response sebagai JSON.
      4. Last resort: raw text.
    Tiap hasil divalidasi strukturnya; kalau invalid kembalikan '' (empty).
    """
    if not response:
        return ""

    # 1. Markdown code block (toleran json/diff/patch)
    m = re.search(r"```(?:json|diff|patch)?\n(.*?)```", response, re.DOTALL)
    if m:
        content = m.group(1).strip()
        if content.startswith("{"):
            try:
                data = json.loads(content)
                if isinstance(data, dict) and "patch" in data:
                    return _clean_patch(str(data["patch"]))
            except json.JSONDecodeError:
                logger.warning("Markdown block looks like JSON but failed to parse")
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
