import json
import re
from dataclasses import dataclass

from utils.logger import logger


@dataclass
class PatchResult:
    """Result dari extract_diff: patch string + status."""
    patch: str
    status: str  # VALID | INVALID_HUNK | PARSE_ERROR | NO_DIFF | EMPTY | PLACEHOLDER_ONLY


def _strip_blank_edges(text: str) -> str:
    """Trim blank edges WITHOUT eating significant trailing whitespace.

    A diff's final line may legitimately be a whitespace-only context line
    (``" "``). ``str.strip()`` deletes it, silently dropping one line from the
    hunk body, so a diff produced verbatim by ``git diff`` was counted short and
    wrongly condemned as HUNK_MISMATCH. Measured on a git-generated diff:
    counts were (4, 4) after strip versus the correct (5, 5) before it.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


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

    lines = _strip_blank_edges(text).split("\n")
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
    patch = _strip_blank_edges(_normalize_newlines(text))
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


def _handle_truncated(result: PatchResult, finish_reason: str) -> PatchResult:
    """Saat response ke-trim (finish_reason='length'), patch yang valid tetap dipertahankan.

    Patch yang berhasil terekstrak & lolos validasi dianggap lengkap walau sisa
    response terpotong. Hanya bila patch-nya sendiri tidak lengkap (tidak ada/
    tidak lolos validasi) maka status diturunkan ke TRUNCATED.
    """
    if finish_reason == "length" and result.status not in ("VALID", "NORMALIZE"):
        return PatchResult(patch="", status="TRUNCATED")
    return result


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

    # 1. Markdown code block (toleran label apa pun, mis. python/diff/patch/json)
    m = re.search(r"```(?:[A-Za-z0-9_-]+)?\s*\n(.*?)```", response, re.DOTALL)
    if m:
        content = _strip_blank_edges(m.group(1))
        if content.startswith("{"):
            try:
                data = json.loads(content)
                if isinstance(data, dict) and "patch" in data:
                    result = _clean_patch(str(data["patch"]))
                    return _handle_truncated(result, finish_reason)
            except json.JSONDecodeError:
                logger.warning("Markdown block looks like JSON but failed to parse")
                if finish_reason == "length":
                    return PatchResult(patch="", status="TRUNCATED")
                return PatchResult(patch="", status="PARSE_ERROR")
        return _handle_truncated(_clean_patch(content), finish_reason)

    # 2. JSON di seluruh response (tanpa markdown)
    try:
        data = json.loads(response)
        if isinstance(data, dict) and "patch" in data:
            return _handle_truncated(_clean_patch(str(data["patch"])), finish_reason)
    except (json.JSONDecodeError, ValueError):
        pass

    # 3. Last resort: raw text
    return _handle_truncated(_clean_patch(response), finish_reason)


# ---------------------------------------------------------------------------
# Semantic applicability check
# ---------------------------------------------------------------------------
#
# ``patch_status`` only proves the diff's *arithmetic* is well formed (hunk
# header counts match the body). It says nothing about whether the patch can be
# applied to the target file: a model that never saw the source can still emit a
# syntactically perfect hunk full of guessed lines, which normalisation will
# happily relabel VALID/NORMALIZE. ``apply_status`` measures the missing half.
#
# The SWE-bench harness itself tries ``git apply -v`` and falls back to
# ``patch --batch --fuzz=5 -p1``. GNU patch is not available on Windows, so
# --fuzz=5 cannot be reproduced locally; this check is therefore a prediction:
#   * NOT_APPLYABLE is a hard conclusion — a required ``-`` line is absent from
#     the target, or the target file does not exist at base_commit. Fuzz never
#     rescues either case.
#   * APPLYABLE means strict ``git apply --check`` already succeeds.
#   * NEEDS_FUZZ means every removed line exists but strict apply still fails.
#     This is an intentionally mixed bucket: it holds patches the fuzzy fallback
#     might place AND patches git rejects as corrupt, which fuzz cannot fix
#     either. Measured on EXP-20260824-005, most of it is the latter, so it is
#     reported as an upper bound and never counted as strictly applyable.

FILE_HEADER = re.compile(r"^\+\+\+ (?:b/)?(.+?)(?:\t.*)?$")
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

APPLYABLE = "APPLYABLE"
NEEDS_FUZZ = "NEEDS_FUZZ"
NOT_APPLYABLE = "NOT_APPLYABLE"
UNKNOWN = "UNKNOWN"


def _parse_hunks(patch: str) -> dict[str, list[tuple[int, list[str], list[str]]]]:
    """Group a unified diff into ``{path: [(orig_start, removed, added), ...]}``.

    Only the ``-`` (removed) and ``+`` (added) payload lines are captured; the
    removed lines are what must exist verbatim in the target file.

    New-file hunks (``--- /dev/null``) are skipped entirely: there is no target
    to verify, so they must never be judged unappliable.
    """
    files: dict[str, list[tuple[int, list[str], list[str]]]] = {}
    current: str | None = None
    removed: list[str] = []
    added: list[str] = []
    start = 0
    in_hunk = False
    new_file = False

    def flush() -> None:
        if in_hunk and current is not None and not new_file:
            files.setdefault(current, []).append((start, list(removed), list(added)))

    for line in patch.split("\n"):
        # Order matters. ``diff --git`` and ``@@`` are unambiguous markers and are
        # checked first, so a hunk body can never swallow the next file's header.
        # Only between a file header and its first ``@@`` (in_hunk False) do
        # ``--- ``/``+++ `` carry structural meaning; inside a hunk a removed line
        # whose content starts with ``--`` renders as ``--- ...`` and is payload.
        if line.startswith("diff --git"):
            flush()
            current, removed, added = None, [], []
            in_hunk, new_file = False, False
            continue
        m = HUNK_HEADER.match(line)
        if m:
            flush()
            removed, added, in_hunk = [], [], True
            start = int(m.group(1))
            continue
        if not in_hunk:
            if line.startswith("--- "):
                new_file = line[4:].strip() == "/dev/null"
            elif line.startswith("+++ "):
                m = FILE_HEADER.match(line)
                if m:
                    current = m.group(1).strip()
            continue
        # Hunk payload: "\ No newline" and context lines are ignored.
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    flush()
    return files


def _line_present(lines: list[str], needle: str) -> bool:
    """True if ``needle`` occurs anywhere in ``lines`` (blank lines always match).

    Exact match first, then a trailing-whitespace-tolerant comparison so that
    CRLF/space drift does not fabricate a NOT_APPLYABLE. Deliberately NOT a
    substring match: a substring hit would report a genuinely absent line as
    present and silently weaken the check this function exists to enforce.
    """
    if needle.strip() == "":
        return True
    if needle in lines:
        return True
    stripped = needle.rstrip()
    return any(l.rstrip() == stripped for l in lines)


def _strict_git_apply_ok(patch: str, root) -> bool | None:
    """Run ``git apply --check -p1`` against a CLEAN tree at HEAD.

    True/False, or None if git is unusable.

    The check runs against a temporary index seeded from HEAD rather than the
    working tree. This matters under the edit-then-diff mechanism: the agent has
    ALREADY applied its change to the working tree, so checking the patch there
    fails by construction ("patch does not apply") and every patch would be
    misreported. The SWE-bench harness applies the patch to a pristine checkout
    of base_commit, so HEAD is the correct reference.

    Returns None only when git could not be run at all or the directory is not a
    repository — i.e. when the result carries no information. Any other non-zero
    exit is a real rejection (``patch does not apply``, ``corrupt patch``, …) and
    returns False. Treating every rc>=128 as inconclusive would silently promote
    corrupt patches to APPLYABLE, which is exactly the failure mode this check
    exists to catch.

    The patch is piped as UTF-8 bytes: on Windows the default locale codec
    (cp1252) cannot encode arbitrary source text, and ``text=True`` would raise
    ``UnicodeEncodeError`` on any patch containing a non-Latin-1 character.
    """
    import os
    import subprocess
    import tempfile
    from pathlib import Path

    try:
        fd, tmp_index = tempfile.mkstemp(prefix="ab-applycheck-")
        os.close(fd)
    except OSError:
        return None

    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = tmp_index
    try:
        # `root` must BE a repository. git otherwise walks up to parent
        # directories and happily uses an unrelated one: measured on this
        # machine, a plain temp directory resolved to C:/Users/<user> because
        # that directory happened to be a git repo. Validating a patch against
        # the wrong repository produces a confident but meaningless verdict.
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(root), capture_output=True, timeout=30, env=env,
        )
        if top.returncode != 0:
            return None
        try:
            top_path = Path((top.stdout or b"").decode("utf-8", "replace").strip()).resolve()
        except (OSError, ValueError):
            return None
        if top_path != Path(root).resolve():
            return None

        seed = subprocess.run(
            ["git", "read-tree", "HEAD"],
            cwd=str(root), capture_output=True, timeout=30, env=env,
        )
        if seed.returncode != 0:
            # A broken/unborn HEAD (observed on one cached checkout) carries no
            # usable information about the patch.
            return None
        proc = subprocess.run(
            ["git", "apply", "--check", "--cached", "-p1"],
            input=patch.encode("utf-8", errors="replace"),
            cwd=str(root),
            capture_output=True,
            timeout=30,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.unlink(tmp_index)
        except OSError:
            pass

    if proc.returncode == 0:
        return True
    stderr = (proc.stderr or b"").decode("utf-8", "replace").lower()
    if "not a git repository" in stderr or "cannot change to" in stderr:
        return None
    return False


def _file_lines_at_head(root, path: str) -> list[str] | None:
    """Read ``path`` as of HEAD, or None if it cannot be read.

    The working tree must NOT be used: under edit-then-diff the agent's own edit
    is already there, so a line the patch intends to remove looks absent and the
    patch is wrongly condemned as NOT_APPLYABLE.
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            cwd=str(root), capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or b"").decode("utf-8", "replace").split("\n")


def validate_applicability(patch: str, repo_root) -> str:
    """Predict whether the SWE-bench harness can apply ``patch``.

    Returns one of APPLYABLE | NEEDS_FUZZ | NOT_APPLYABLE | UNKNOWN.
    ``repo_root`` may be a path or None; None yields UNKNOWN (no guess).

    Files are read from **HEAD**, never the working tree. Under edit-then-diff the
    agent has already written its change to the working tree, so a line the patch
    removes is genuinely gone there and the patch would be condemned as
    NOT_APPLYABLE by construction. The harness applies the patch to a pristine
    checkout of base_commit, which is what HEAD represents.

    ``NOT_APPLYABLE`` is deliberately conservative: it is only returned when a
    removed line exists **nowhere** in the target file. A line that exists but
    sits at a different offset than the hunk header claims yields ``NEEDS_FUZZ``,
    because ``patch --fuzz=5`` can still place it. Over-reporting
    NOT_APPLYABLE would undermine the metric this check exists to support.
    """
    if not patch or not patch.strip():
        return UNKNOWN
    if repo_root is None:
        return UNKNOWN

    from pathlib import Path

    root = Path(repo_root)
    if not root.is_dir():
        return UNKNOWN

    hunks_by_file = _parse_hunks(patch)
    if not hunks_by_file:
        return UNKNOWN

    verdict = APPLYABLE
    saw_real_file = False

    for path, hunks in hunks_by_file.items():
        # Prefer HEAD; fall back to the working tree only when the file is
        # untracked/absent at HEAD (a genuinely new file created by the patch).
        lines = _file_lines_at_head(root, path)
        if lines is None:
            target = root / path
            if not target.is_file():
                # The patch edits a file that does not exist at base_commit: it
                # can never apply. This is the dominant failure of NORMALIZE.
                return NOT_APPLYABLE
            try:
                lines = target.read_text(encoding="utf-8", errors="replace").split("\n")
            except OSError:
                return UNKNOWN
        saw_real_file = True

        for orig_start, removed, _added in hunks:
            if not removed:
                continue
            for needle in removed:
                # Hard failure only when the line is absent from the whole file;
                # fuzz cannot invent a line that is not there.
                if not _line_present(lines, needle):
                    return NOT_APPLYABLE
            # Every removed line exists somewhere. If the block does not sit
            # exactly where the header says, strict git apply fails and the
            # harness's fuzzy fallback is what would rescue it.
            exact = lines[orig_start - 1 : orig_start - 1 + len(removed)]
            if exact != removed:
                verdict = NEEDS_FUZZ

    if not saw_real_file:
        return UNKNOWN

    # Line check passed: confirm with the same strict step the harness tries
    # first (against a clean HEAD index). git unavailable / not a repo → keep
    # the line-based verdict.
    strict = _strict_git_apply_ok(patch, root)
    if strict is True:
        return APPLYABLE
    if strict is False:
        return NEEDS_FUZZ
    return verdict
