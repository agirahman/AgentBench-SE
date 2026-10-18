"""The preflight gate must check the SAME batch the sweep is about to run.

Why it matters: a dirty checkout makes `git diff` capture changes the agent did
not make, which reads in the results as the agent solving the issue. The sweep
therefore GATES on the preflight rather than printing a reminder.

But the gate used to take its own default -- all 50 instances with a checkout --
regardless of `--limit`. So a 10-issue pilot was gated on 40 repositories it never
touches, and a green there said nothing about the 10 that would actually run. That
is the "control that does not cover the thing it claims to cover" failure: worse
than no control, because it is trusted (MEMORY, trap #20).

The fix passes --limit through, so both sides select "the first N by sorted id"
and the checked set IS the run set.

NOTE on how this is tested. An earlier version asserted the ARGUMENT-BUILDING
string appeared in the sweep's source. That test PASSED with the fix disabled --
commenting the branch out left the string in place -- so it was decoration, not a
check (MEMORY, trap #21). These tests instead RUN the sweep's argument builder and
intercept the subprocess, so the observable behaviour is what gets asserted.
"""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SWEEP = ROOT / "tools" / "run_final_sweep.py"


def _load_sweep_module():
    """Import run_final_sweep by path (tools/ is not a package)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_final_sweep_under_test", SWEEP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def captured_preflight(monkeypatch, tmp_path):
    """Run `main()` for real and capture the argv it hands the preflight.

    ``subprocess.call`` is replaced, so nothing is executed and no repo is touched.

    ``ROOT`` is redirected to ``tmp_path``. This fixture used to be the ONE sweep
    test that did not, and the consequence was not theoretical: ``main()`` writes
    ``ROOT/logs/sweep_started.json`` BEFORE it reaches the preflight, so every full
    suite overwrote the real bill window with fake data (issues ``inst-0``).

    Measured 2026-10-07: running just this file changed the file's SHA256 from
    075CD0F2... to C21C1CB9... while reporting "6 passed". The loss is silent by
    construction -- the file is valid JSON, so nothing downstream complains; only
    the money can no longer be reconciled.

    The same fix is what test_sweep_attempts_window.py:47 and
    test_sweep_only_batch.py:80 already do.

    The capture filters to the PREFLIGHT call specifically: main() also shells out
    to run_with_env.py for the sweep itself, which is invoked LAST. Returning the
    last call would assert against the sweep command instead of the gate -- and
    that mistake would still "pass" the negative tests for the wrong reason.
    """
    seen: list[list[str]] = []

    def fake_call(cmd, **kwargs):
        seen.append(list(cmd))
        return 0            # pretend the preflight passed

    monkeypatch.setattr(subprocess, "call", fake_call)

    mod = _load_sweep_module()
    # MUST come after _load_sweep_module(): the module computes ROOT at import
    # time, so patching before the load would be undone by it.
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    # Do not depend on the dataset cache for the gate test.
    monkeypatch.setattr(mod, "select_issues", lambda limit: [f"inst-{i}" for i in range(limit or 3)])

    def run(*argv):
        seen.clear()
        monkeypatch.setattr(sys, "argv", ["run_final_sweep.py", *argv])
        mod.main()
        preflight_calls = [c for c in seen if any("preflight_repos" in str(a) for a in c)]
        assert len(preflight_calls) == 1, (
            f"expected exactly one preflight invocation, saw {len(preflight_calls)}: {seen}"
        )
        return preflight_calls[0]

    return run


def test_limit_is_passed_through_to_the_preflight(captured_preflight):
    """A limited sweep must gate a limited preflight."""
    cmd = captured_preflight("--limit", "10")
    assert "--limit" in cmd, (
        f"the gate would check every instance instead of the batch: {cmd}"
    )
    assert cmd[cmd.index("--limit") + 1] == "10"


def test_no_limit_checks_everything(captured_preflight):
    """A full sweep must still check all instances."""
    cmd = captured_preflight()
    assert "--limit" not in cmd


def test_explicit_issue_list_is_not_silently_limited(captured_preflight):
    """--issues overrides --limit, and cannot be expressed as 'first N'.

    Passing --limit anyway would check a set that is not the run set while looking
    like it does -- the exact failure this file exists to prevent.
    """
    cmd = captured_preflight("--limit", "5", "--issues", "django__django-11019")
    assert "--limit" not in cmd, (
        "an explicit id list must not be turned into 'first N' silently"
    )


def test_smoke_run_is_not_silently_limited(captured_preflight):
    """--smoke runs 1 issue; --limit would be a different 1 only by coincidence."""
    cmd = captured_preflight("--limit", "5", "--smoke")
    assert "--limit" not in cmd


def test_preflight_accepts_the_limit_flag():
    """The flag must exist on the other side, or the pass-through silently fails."""
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "preflight_repos.py"), "--help"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert "--limit" in r.stdout, "preflight_repos.py does not accept --limit"


def test_the_selection_is_the_same_on_both_sides():
    """Both tools currently pick the SAME N instances -- verified, not assumed.

    The --limit pass-through rests on this, so it is measured rather than argued:
    the sweep sorts by instance_id, while the preflight iterates the dataset in its
    given order (preflight_repos.load_instances does NOT sort). Those would differ
    in general, but on the real SWE-bench Lite dataset both yield the same first N
    -- checked directly, 50 of 50 and the first 10 identical.

    This test pins the CURRENT agreement, not a guarantee. If the dataset order
    ever changes, it will fail and the fix must pass an explicit ID LIST instead of
    a count. That caveat is the point: the pass-through is correct today by
    measurement, and this test is what will notice if that stops being true.
    """
    from datasets import load_dataset

    sweep_src = SWEEP.read_text(encoding="utf-8")
    pf_src = (ROOT / "tools" / "preflight_repos.py").read_text(encoding="utf-8")

    assert "usable[:limit]" in sweep_src, "the sweep no longer truncates its list"
    assert "present[:limit]" in pf_src, "the preflight no longer truncates its list"
    # The sweep sorts explicitly; the preflight does not. That asymmetry is exactly
    # why the agreement below is measured on data instead of inferred from code.
    assert "sorted(ds" in sweep_src, "the sweep no longer sorts by instance_id"
    assert "sorted(" not in pf_src.split("def load_instances")[1].split("def ")[0], (
        "the preflight now sorts too -- the asymmetry this test documents is gone, "
        "so the reasoning above needs revisiting"
    )

    # And measure it, so a dataset reordering is caught.
    ds = load_dataset("SWE-bench/SWE-bench_Lite", split="test")
    ordered = [r["instance_id"] for r in sorted(ds, key=lambda r: r["instance_id"])]
    natural = [r["instance_id"] for r in ds]
    assert ordered[:10] == natural[:10], (
        "the dataset is no longer in sorted order, so a count is not enough to "
        "identify the batch -- pass explicit ids to the preflight instead"
    )


# ===========================================================================
# CANARY: no sweep test may touch the REAL bill window
# ===========================================================================

#: The real bill-window file, relative to the repo root. Holds the UTC windows
#: used by `tools/read_actual_bill.py --compare` to reconcile our accounting
#: against the money actually spent at 9router.
REAL_BILL_STATE = ROOT / "logs" / "sweep_started.json"


def _fingerprint(path: Path):
    """(exists, size, sha256, mtime_ns) -- everything that would reveal a write."""
    import hashlib

    if not path.exists():
        return (False, None, None, None)
    data = path.read_bytes()
    st = path.stat()
    return (True, len(data), hashlib.sha256(data).hexdigest(), st.st_mtime_ns)


def test_running_this_file_does_not_touch_the_real_bill_window(
    captured_preflight, monkeypatch, tmp_path
):
    """CANARY: the real logs/sweep_started.json must be byte-identical after a run.

    This is the regression test for a measured leak. ``main()`` writes
    ``ROOT/logs/sweep_started.json`` before it reaches the preflight, and this
    fixture used to be the only sweep test that did not redirect ``ROOT``. So every
    full suite overwrote the real bill window with fake data.

    Why a fingerprint and not just "the file exists": the damage is SILENT. The
    written file is valid JSON with plausible fields, so no downstream tool
    complains -- only the ability to reconcile the money is lost. Comparing the
    hash is the only thing that notices.

    Why this test and not only the source-level check below: this one FAILS IF THE
    BEHAVIOUR IS WRONG, whatever the code looks like. The source check catches a
    new test file that forgets the redirect; this one catches everything else.
    """
    before = _fingerprint(REAL_BILL_STATE)

    # Exercise the fixture for real: this is what would have clobbered the file.
    for argv in ((), ("--limit", "10"), ("--limit", "5", "--issues", "x")):
        captured_preflight(*argv)

    after = _fingerprint(REAL_BILL_STATE)

    assert after == before, (
        f"the REAL bill window changed while running a test!\n"
        f"  before: {before}\n"
        f"  after : {after}\n"
        f"  path  : {REAL_BILL_STATE}\n"
        f"A sweep test is writing to the real logs/ directory. Every test that "
        f"calls the sweep's main() MUST redirect mod.ROOT to tmp_path."
    )

    # And prove the redirect actually sent the write somewhere: the temp copy
    # should exist, so this test cannot pass merely because main() wrote nothing.
    tmp_state = tmp_path / "logs" / "sweep_started.json"
    assert tmp_state.exists(), (
        "main() wrote no state file at all under the redirected ROOT -- the "
        "redirect may have disabled the code path this canary is guarding, which "
        "would make the assertion above vacuous"
    )


def _sweep_module_names(src: str, tree: ast.AST) -> set[str]:
    """Names bound to the run_final_sweep module, resolving the 2-hop load.

    Module level (not nested in the test) so the LOGIC itself can be exercised
    against synthetic source -- that is what lets a mutation of the logic go red,
    instead of only a mutation of the test file's fixture.
    """
    sweep_specs: set[str] = set()
    sweep_names: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        fname = getattr(func, "attr", None) or getattr(func, "id", "") or ""
        rhs = ast.get_source_segment(src, node.value) or ""

        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if "spec_from_file_location" in fname:
                # Hop 1: remember WHICH file this spec points at.
                if "run_final_sweep" in rhs:
                    sweep_specs.add(target.id)
            elif "module_from_spec" in fname:
                # Hop 2: a module built from a sweep spec IS the sweep module.
                args = node.value.args
                if args and isinstance(args[0], ast.Name) and args[0].id in sweep_specs:
                    sweep_names.add(target.id)
            elif "run_final_sweep" in rhs or "sweep" in fname.lower():
                # A loader helper, or a direct load. `_load_sweep_module()` and
                # `_load_sweep()` both land here.
                sweep_names.add(target.id)
    return sweep_names


def _calls_sweep_main(tree: ast.AST, names: set[str]) -> bool:
    """True when the module calls ``<sweep module>.main()``."""
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "main"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id in names
        for n in ast.walk(tree)
    )


def _setattr_root_target(node: ast.AST, names: set[str]) -> str | None:
    """If ``node`` is ``setattr(<sweep name>, "ROOT", ...)``, return that name.

    The target must be one of the SWEEP module's names. A redirect of some other
    module says nothing about the sweep's state path, and accepting it would let
    an unprotected ``mod.main()`` hide behind an unrelated redirect (measured).
    """
    if not isinstance(node, ast.Call):
        return None
    fname = getattr(node.func, "attr", None) or getattr(node.func, "id", "") or ""
    if fname != "setattr" or len(node.args) < 2:
        return None
    target, attr = node.args[0], node.args[1]
    if (isinstance(target, ast.Name) and target.id in names
            and isinstance(attr, ast.Constant) and attr.value == "ROOT"):
        return target.id
    return None


def _redirect_targets(tree: ast.AST, names: set[str]) -> set[str]:
    """Sweep-module names redirected anywhere in the file via setattr(..., "ROOT").

    Collected file-wide because a redirect may live in a fixture while the call
    site is in a helper that RECEIVES the module (``_run(mod, argv)``) -- the
    shape of test_sweep_attempts_window.py and test_sweep_only_batch.py.
    """
    targets: set[str] = set()
    for node in ast.walk(tree):
        name = _setattr_root_target(node, names)
        if name:
            targets.add(name)
    return targets


def _redirects_directly(fn, names: set[str]) -> bool:
    """True when ``fn`` ITSELF performs a sweep-ROOT redirect.

    Nested function bodies are SKIPPED. ``ast.walk`` descends into them, so a
    naive "does this function mention the redirected name" test credited the
    OUTER function with its inner closure's contents -- which made an unprotected
    fixture look protected whenever its nested helper referenced ``mod``
    (measured: M5c stayed green). A function is only credited for what its own
    body does.
    """
    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue  # that function's body is checked on its own
            if _setattr_root_target(child, names):
                return True
            if walk(child):
                return True
        return False

    return walk(fn)


def _enclosing_chains(tree: ast.AST) -> dict[int, list]:
    """Map ``id(node) -> [outermost ... innermost] enclosing function defs."""
    chains: dict[int, list] = {}

    def visit(node, chain):
        chains[id(node)] = chain
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, chain + [child])
            else:
                visit(child, chain)

    visit(tree, [])
    return chains


def _function_params(fn) -> set[str]:
    """Parameter names of a function def (positional + keyword-only)."""
    args = fn.args
    names = {a.arg for a in list(args.args) + list(args.kwonlyargs)}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def _unsafe_main_calls(src: str, tree: ast.AST, names: set[str]) -> list[str]:
    """Functions that call ``<sweep>.main()`` without a ROOT redirect.

    Per-FUNCTION, not per-file. A file-level check is not enough: one redirect
    anywhere satisfies it, so removing the redirect from the FIXTURE stayed green
    as long as some other test in the same file still had one. That is the same
    class of hole as the old substring check, one level finer -- and a mutation
    run caught it (M5c stayed green while the bill file was corrupted).

    A calling function is safe when either:

      (a) it, or any enclosing function, redirects the sweep module's ROOT
          -- covers the fixture pattern, where the call is inside a nested
          ``run()`` within the redirecting fixture; or
      (b) it takes a parameter that the file redirects
          -- covers ``_run(mod, argv)``, a helper handed an already-redirected
          module by its fixture.

    Anything else is reported by function name.
    """
    chains = _enclosing_chains(tree)
    redirected = _redirect_targets(tree, names)
    unsafe: list[str] = []

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "main"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in names):
            continue

        chain = chains.get(id(node)) or []
        if not chain:
            unsafe.append("<module level>")
            continue
        innermost = chain[-1]

        # (a) this function, or any function ENCLOSING it, redirects the sweep
        #     module. Enclosing functions are credited because the fixture
        #     pattern redirects in the fixture and calls main() in a nested
        #     closure -- the redirect is genuinely in scope there.
        #
        #     `_redirects_directly` (not ast.walk) so a function is credited only
        #     for its OWN body: otherwise the outer fixture inherits its inner
        #     closure's contents and an unprotected call looks protected.
        if any(_redirects_directly(fn, names) for fn in chain):
            continue
        # (b) the helper pattern: a function that takes the module as a PARAMETER
        #     whose name the file redirects somewhere (`_run(mod, argv)` in
        #     test_sweep_only_batch.py / test_sweep_attempts_window.py). The
        #     fixture redirects `mod` and passes it in, so the redirect IS in
        #     effect -- flagging this would be a false positive on two real files.
        #
        #     Name-based, and therefore approximate: it cannot distinguish the
        #     fixture's local `mod` from the helper's parameter `mod`. That is
        #     acceptable because the check is a SAFETY NET for new files, and the
        #     behavioural canary is what proves the current ones are safe. A false
        #     NEGATIVE here is covered by the canary; a false POSITIVE would get
        #     this check disabled, which is the worse outcome.
        if _function_params(innermost) & redirected:
            continue

        unsafe.append(innermost.name)

    return sorted(set(unsafe))


def _audit_sweep_tests(tests_dir: Path) -> tuple[list[str], list[str]]:
    """Return (scanned, offenders) for every test file driving the sweep's main().

    ``offenders`` are files where SOME function calls the sweep's ``main()``
    without a ROOT redirect -- i.e. a path that would overwrite the real bill
    window when run.
    """
    offenders: list[str] = []
    scanned: list[str] = []

    for path in sorted(tests_dir.glob("test_*.py")):
        src = path.read_text(encoding="utf-8")
        if "run_final_sweep" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:  # pragma: no cover - a broken test file fails elsewhere
            continue

        names = _sweep_module_names(src, tree)
        if not names:
            continue
        if not _calls_sweep_main(tree, names):
            continue

        scanned.append(path.name)
        if _unsafe_main_calls(src, tree, names):
            offenders.append(path.name)

    return scanned, offenders


def test_every_sweep_test_redirects_root_before_calling_main():
    """SOURCE CHECK: every path that calls the sweep's main() must redirect ROOT.

    The canary above only observes THIS file. This check covers the whole suite: it
    finds every test module that calls ``main()`` ON THE SWEEP MODULE and requires an
    actual ``setattr(<sweep module>, "ROOT", ...)`` call protecting that path.

    Why AST for BOTH hops. Three separate traps, all hit in this file's history:

    1. A substring scan for "run_final_sweep" + "main()" gave a FALSE POSITIVE on
       test_interstrategy_fairness.py: it loads the sweep module AND calls
       ``.main()`` -- but on ``clean_repos``, a different module.

    2. ``if "ROOT" not in src`` gave a FALSE NEGATIVE. ROOT appears a dozen times
       in docstrings and comments, so DELETING the ``setattr`` line left the string
       "ROOT" behind and the check stayed GREEN while the leak returned. The canary
       above even warns that "a source scan can be satisfied by a comment" -- and
       this check was itself satisfiable that way. A mutation run proved it
       (M5/M9 stayed green while the bill file was corrupted).

    3. A FILE-LEVEL redirect check is still too weak, one level finer. It passes if
       ANY function in the file redirects ROOT, so removing the redirect from the
       FIXTURE stayed green as long as some other test in the same file still had
       one. A mutation run caught this too (M5c). So the check is now PER-FUNCTION:
       every function that calls ``main()`` must itself be protected, either by
       redirecting the sweep module or by receiving an already-redirected one.

    Paired with the behavioural canary on purpose: this check sees every file but
    reasons about source; the canary sees one file but observes real behaviour.
    """
    #: Files known to drive the sweep's main(). Asserted below so this check cannot
    #: silently stop covering a file -- a scan that finds nothing passes trivially.
    EXPECTED = {
        "test_sweep_preflight_scope.py",
        "test_sweep_attempts_window.py",
        "test_sweep_only_batch.py",
        "test_docker_gate_and_stall_watchdog.py",
    }

    scanned, offenders = _audit_sweep_tests(ROOT / "tests")

    assert set(scanned) == EXPECTED, (
        f"the set of files driving the sweep's main() changed.\n"
        f"  expected: {sorted(EXPECTED)}\n"
        f"  found   : {sorted(scanned)}\n"
        f"Update EXPECTED after verifying the new file redirects ROOT, or this "
        f"check silently stops covering it."
    )
    assert not offenders, (
        f"these test files call the sweep's main() without redirecting ROOT, so "
        f"they will overwrite the REAL logs/sweep_started.json when run:\n"
        f"  " + "\n  ".join(offenders) + "\n"
        f"Add `monkeypatch.setattr(mod, \"ROOT\", tmp_path)` to the fixture."
    )


# ---------------------------------------------------------------------------
# The CHECK LOGIC, tested against synthetic sources.
#
# Why this exists separately: the audit above only ever sees the REAL test files,
# which all currently redirect ROOT. So mutating the check's LOGIC -- e.g. making
# it collect no offenders (M8), or weakening the redirect test to a substring
# (M9) -- left it GREEN, because there was nothing for it to catch. A control
# that cannot fail on the input it is given is not a control.
#
# These cases feed the logic the input that MUST be rejected, so the logic itself
# is pinned. Each case is a small source string in the same shape the real
# fixtures use.
# ---------------------------------------------------------------------------

_LOADER = '''
import importlib.util
spec = importlib.util.spec_from_file_location("x", ROOT / "tools" / "run_final_sweep.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
'''


def _audit_source(src: str, tmp_path) -> tuple[list[str], list[str]]:
    """Run the audit against a synthetic test file."""
    d = tmp_path / "synthetic_tests"
    d.mkdir(exist_ok=True)
    (d / "test_synthetic.py").write_text(src, encoding="utf-8")
    return _audit_sweep_tests(d)


def test_check_logic_flags_a_missing_redirect(tmp_path):
    """M8: the audit MUST collect offenders. Without this, it always passes.

    A mutation that makes ``offenders`` stay empty would otherwise go unnoticed,
    because the real suite has no offenders to find.
    """
    src = _LOADER + '\ndef test_x(monkeypatch):\n    mod.main()\n'
    scanned, offenders = _audit_source(src, tmp_path)
    assert scanned == ["test_synthetic.py"], f"the file was not scanned: {scanned}"
    assert offenders == ["test_synthetic.py"], (
        f"a file calling sweep main() with NO redirect was not flagged: {offenders}"
    )


def test_check_logic_accepts_a_real_redirect(tmp_path):
    """The other half of M8: a correctly-redirected file must NOT be flagged."""
    src = (
        _LOADER
        + '\ndef test_x(monkeypatch, tmp_path):\n'
        + '    monkeypatch.setattr(mod, "ROOT", tmp_path)\n'
        + '    mod.main()\n'
    )
    scanned, offenders = _audit_source(src, tmp_path)
    assert scanned == ["test_synthetic.py"]
    assert offenders == [], (
        f"a correctly-redirected file was flagged as an offender: {offenders}"
    )


def test_check_logic_is_not_satisfied_by_a_comment(tmp_path):
    """M9: a ROOT mention in a comment/docstring must NOT count as a redirect.

    This is the exact hole the old ``if "ROOT" not in src`` had. The source below
    mentions ROOT three times and redirects nothing.
    """
    src = (
        '"""This module talks about ROOT and monkeypatch.setattr(mod, "ROOT", ...)'
        ' at length, in prose."""\n'
        + _LOADER
        + '\n# We really should setattr(mod, "ROOT", tmp_path) here someday.\n'
        + '\ndef test_x(monkeypatch):\n    mod.main()\n'
    )
    scanned, offenders = _audit_source(src, tmp_path)
    assert "test_synthetic.py" in scanned
    assert offenders == ["test_synthetic.py"], (
        f"comments/docstrings mentioning ROOT satisfied the check -- this is the "
        f"trap #21 hole. offenders={offenders}"
    )


def test_check_logic_ignores_a_redirect_of_a_DIFFERENT_module(tmp_path):
    """Redirecting some other module's ROOT must not count.

    The audit resolves WHICH module the name is bound to; a redirect on an
    unrelated module says nothing about the sweep's state path.
    """
    src = (
        'import importlib.util\n'
        'other = importlib.util.spec_from_file_location("o", "tools/clean_repos.py")\n'
        'other_mod = importlib.util.module_from_spec(other)\n'
        + _LOADER
        + '\ndef test_x(monkeypatch, tmp_path):\n'
        + '    monkeypatch.setattr(other_mod, "ROOT", tmp_path)\n'
        + '    mod.main()\n'
    )
    scanned, offenders = _audit_source(src, tmp_path)
    assert "test_synthetic.py" in scanned, (
        f"the sweep module was not resolved: {scanned}"
    )
    assert offenders == ["test_synthetic.py"], (
        f"a redirect of a DIFFERENT module satisfied the sweep check: {offenders}"
    )


def test_check_logic_ignores_a_file_that_does_not_call_sweep_main(tmp_path):
    """Loading the sweep module without calling its main() is out of scope.

    This is the test_interstrategy_fairness.py shape: it imports the sweep for
    build_cmd(), and calls .main() on a different module. It writes no state file,
    so flagging it would be a false positive.
    """
    src = _LOADER + '\ndef test_x(monkeypatch):\n    mod.build_cmd([], [], True)\n'
    scanned, offenders = _audit_source(src, tmp_path)
    assert scanned == [], f"a file that never calls sweep main() was scanned: {scanned}"
    assert offenders == []


def test_check_logic_is_per_function_not_per_file(tmp_path):
    """M5c: ONE unprotected call is enough, even if other tests in the file redirect.

    This is the hole a FILE-level check leaves. The source below has a redirect --
    in a DIFFERENT test -- and an unprotected call in the fixture. A file-level
    check passes; the correct answer is to flag it.

    Measured: removing the redirect from the real fixture stayed GREEN while the
    bill file was corrupted, because test_dry_run_writes_no_bill_window in the
    same file still had one.
    """
    src = (
        _LOADER
        # This test redirects properly...
        + '\ndef test_other(monkeypatch, tmp_path):\n'
        + '    monkeypatch.setattr(mod, "ROOT", tmp_path)\n'
        + '    mod.main()\n'
        # ...but this one does NOT, and it is the fixture the others use.
        + '\ndef captured_preflight(monkeypatch):\n'
        + '    def run():\n'
        + '        mod.main()\n'
        + '    return run\n'
    )
    scanned, offenders = _audit_source(src, tmp_path)
    assert "test_synthetic.py" in scanned
    assert offenders == ["test_synthetic.py"], (
        f"a file-level check accepted a file whose FIXTURE has no redirect, "
        f"because another test in the same file does. offenders={offenders}"
    )


def test_check_logic_accepts_a_redirect_in_an_enclosing_fixture(tmp_path):
    """The other half of M5c: the real fixture shape must NOT be flagged.

    test_sweep_preflight_scope.py redirects in the FIXTURE and calls main() inside
    a nested ``run()``. That is safe, and flagging it would be a false positive --
    which is why the check walks enclosing functions.
    """
    src = (
        _LOADER
        + '\ndef captured_preflight(monkeypatch, tmp_path):\n'
        + '    monkeypatch.setattr(mod, "ROOT", tmp_path)\n'
        + '    def run(*argv):\n'
        + '        mod.main()\n'
        + '    return run\n'
    )
    scanned, offenders = _audit_source(src, tmp_path)
    assert "test_synthetic.py" in scanned
    assert offenders == [], (
        f"a redirect in the enclosing fixture was not credited: {offenders}"
    )


def test_check_logic_accepts_a_helper_receiving_a_redirected_module(tmp_path):
    """The `_run(mod, argv)` helper shape must NOT be flagged.

    test_sweep_attempts_window.py and test_sweep_only_batch.py both redirect in a
    fixture and hand the module to a ``_run`` helper that calls ``main()``. The
    helper has no redirect of its own, so a naive per-function check would produce
    a false positive on two real files.
    """
    src = (
        _LOADER
        + '\ndef _run(mod, argv):\n'
        + '    mod.main()\n'
        + '\n@pytest.fixture\n'
        + 'def sweep_state(monkeypatch, tmp_path):\n'
        + '    monkeypatch.setattr(mod, "ROOT", tmp_path)\n'
        + '    return mod\n'
    )
    scanned, offenders = _audit_source(src, tmp_path)
    assert "test_synthetic.py" in scanned
    assert offenders == [], (
        f"the _run(mod, ...) helper pattern was flagged as unsafe: {offenders}"
    )


# ---------------------------------------------------------------------------
# M11: a --dry-run must not write a bill window at all.
#
# The state write sits AFTER the dry-run early-return, so a dry-run touches no
# state file. That ordering is load-bearing: --dry-run is the read-only preview
# an operator runs BEFORE deciding to spend money, and it is also what the
# readiness gate invokes. If it wrote a window, every preview would forge bill
# evidence -- and the readiness gate runs it on every check.
# ---------------------------------------------------------------------------

def test_dry_run_writes_no_bill_window(tmp_path, monkeypatch):
    """M11: --dry-run must not create or modify a state file."""
    import subprocess as _sp

    mod = _load_sweep_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(_sp, "call", lambda cmd, **kw: 0)
    monkeypatch.setattr(mod, "select_issues", lambda limit: ["inst-0"])
    monkeypatch.setattr(sys, "argv", ["run_final_sweep.py", "--limit", "1", "--dry-run"])

    rc = mod.main()

    assert rc == 0, f"a dry-run must exit 0, got {rc}"
    state = tmp_path / "logs" / "sweep_started.json"
    assert not state.exists(), (
        f"a --dry-run wrote a bill window at {state}. A preview must not create "
        f"bill evidence -- the readiness gate runs --dry-run on every check, so "
        f"this would forge a window on every gate run."
    )
    # And no archive either: nothing existed to archive, but a stray dated file
    # would mean the write path ran.
    logs_dir = tmp_path / "logs"
    strays = sorted(p.name for p in logs_dir.glob("*")) if logs_dir.exists() else []
    assert not strays, f"a dry-run left files behind: {strays}"


# ---------------------------------------------------------------------------
# K-1 / K-2: the archive step must PROTECT, not just be detected.
#
# The canary above DETECTS that the real bill file changed -- but by then it is
# already overwritten. These tests pin the pre-emptive protections in the sweep's
# archive path, so the file is never damaged in the first place.
# ---------------------------------------------------------------------------

_DATED_ARCHIVE = re.compile(r"^sweep_started\.\d{8}T\d{6}Z(\.[0-9a-f]{8})?\.json$")


def _dated_archives(d: Path) -> list[str]:
    """Live dated windows only -- excludes the .reconstructed_archived name."""
    if not d.exists():
        return []
    return sorted(p.name for p in d.glob("sweep_started*.json")
                  if _DATED_ARCHIVE.match(p.name))


def test_a_reconstruction_is_not_archived_as_a_live_window(tmp_path):
    """K-1/K-2: a RECONSTRUCTED state file must not be filed as a live window.

    The file itself says "Do NOT let a real sweep archive this as if it were a
    live window". Filing it under a sweep timestamp would misdate the evidence
    and drop the marker saying it is reconstructed.
    """
    mod = _load_sweep_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    state = logs / "sweep_started.json"
    state.write_text(json.dumps({
        "_RECONSTRUCTED": True,
        "started_utc": "2026-10-07T11:41:13Z",
        "finished_utc": "2026-10-07T13:11:56Z",
        "elapsed_minutes": 90.7,
    }, indent=2), encoding="utf-8")

    assert mod._is_reconstructed(state) is True, "the marker was not detected"
    name = mod.archive_previous_window(state)
    print(f"  archive -> {name!r}")

    assert not _dated_archives(logs), (
        f"the reconstruction was filed as a LIVE dated window: "
        f"{_dated_archives(logs)}"
    )
    saved = sorted(logs.glob("*reconstructed_archived*"))
    assert saved, "the reconstruction was not preserved anywhere"
    data = json.loads(saved[0].read_text(encoding="utf-8"))
    assert data.get("_RECONSTRUCTED") is True, "the marker was lost"


def test_a_live_window_is_still_archived(tmp_path):
    """The other half of K-1: real windows must still be archived."""
    mod = _load_sweep_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    state = logs / "sweep_started.json"
    state.write_text(json.dumps({
        "started_utc": "2026-10-06T16:14:32Z",
        "finished_utc": "2026-10-06T16:19:00Z",
        "elapsed_minutes": 4.7,
        "rc": 0,
    }, indent=2), encoding="utf-8")

    assert mod._is_reconstructed(state) is False
    mod.archive_previous_window(state)
    assert len(_dated_archives(logs)) == 1, (
        f"a live window was not archived: {_dated_archives(logs)}"
    )


def test_two_archives_in_the_same_second_do_not_collide(tmp_path):
    """The mechanism that LOST the 10-issue window must be closed.

    The old name was ``sweep_started.{now:%Y%m%dT%H%M%SZ}.json`` -- second
    resolution. Two main() calls in one second produced the same filename, so the
    second write destroyed the first. Measured: the archive step RAN for the
    10-issue window, and its slot was then reused.
    """
    mod = _load_sweep_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    state = logs / "sweep_started.json"

    for marker in ("AAAA", "BBBB"):
        state.write_text(json.dumps({
            "started_utc": f"2026-10-07T0{marker}T00:00:00Z",
            "elapsed_minutes": 5.0,
            "rc": 0,
        }, indent=2), encoding="utf-8")
        mod.archive_previous_window(state)

    archives = _dated_archives(logs)
    assert len(archives) == 2, (
        f"two different windows produced {len(archives)} archive(s): {archives}. "
        f"The name still collides, so a window can still be destroyed."
    )
    starts = {
        json.loads((logs / a).read_text(encoding="utf-8"))["started_utc"]
        for a in archives
    }
    assert len(starts) == 2, f"a window was overwritten: {starts}"


def test_a_corrupt_state_file_is_still_archived(tmp_path):
    """An unreadable file must not be mistaken for a reconstruction.

    Refusing to archive it would lose a real window over a transient read error,
    which is worse than the provenance problem _is_reconstructed guards.
    """
    mod = _load_sweep_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    state = logs / "sweep_started.json"
    raw = b"\xff\xfe not json at all"
    state.write_bytes(raw)

    assert mod._is_reconstructed(state) is False
    mod.archive_previous_window(state)
    archives = _dated_archives(logs)
    assert len(archives) == 1, "a corrupt state file was not archived"
    assert (logs / archives[0]).read_bytes() == raw, "the bytes were not preserved"


def test_two_reconstructions_do_not_collide(tmp_path):
    """K-2R: the reconstruction archive name must carry a content hash too.

    The live-window branch got a hash in K-2, but this branch kept a FIXED name
    (``sweep_started.reconstructed_archived.json``), which is last-write-wins:
    archiving a second, different reconstruction silently kept only the second.
    Inconsistent with the sibling branch and the same failure mode it fixed.
    """
    mod = _load_sweep_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    state = logs / "sweep_started.json"

    for window in ("2026-10-07T11:41:13Z", "2026-10-08T09:00:00Z"):
        state.write_text(json.dumps({
            "_RECONSTRUCTED": True,
            "started_utc": window,
            "finished_utc": "2026-10-07T13:11:56Z",
            "elapsed_minutes": 90.7,
        }, indent=2), encoding="utf-8")
        mod.archive_previous_window(state)

    saved = sorted(p.name for p in logs.glob("*reconstructed_archived*"))
    assert len(saved) == 2, (
        f"two different reconstructions produced {len(saved)} archive(s): {saved}. "
        f"The fixed name still collides, so the first is destroyed."
    )
    starts = {
        json.loads((logs / n).read_text(encoding="utf-8"))["started_utc"]
        for n in saved
    }
    assert starts == {"2026-10-07T11:41:13Z", "2026-10-08T09:00:00Z"}, (
        f"a reconstruction was overwritten: {starts}"
    )


# ---------------------------------------------------------------------------
# K-1R: the --resume path must ALSO preserve a reconstruction.
#
# The protection above is exercised by calling archive_previous_window()
# DIRECTLY, which does not cover the call site in main(). --resume was the hole:
# that branch folds the previous window into `attempts` and then overwrites the
# file, so it never reached the archival call. Measured with a real main() call:
# _RECONSTRUCTED went True -> None, the window was replaced, and ZERO archives
# were written. --resume is the normal way to continue EXP-20261007-003, so this
# is the path the operator would take.
#
# These tests drive main() END TO END. That is the point: the bug was in the
# wiring, so a test that calls the helper directly cannot see it.
# ---------------------------------------------------------------------------


def _drive_main(tmp_path, monkeypatch, argv, seed_reconstructed=True):
    """Run the sweep's real main() in a temp ROOT. Returns (rc, state, archives).

    ``subprocess.call`` is stubbed so nothing is launched, and ``ROOT`` is
    redirected so the real bill window is never touched.
    """
    mod = _load_sweep_module()
    root = tmp_path / "proj"
    (root / "logs").mkdir(parents=True)
    exp = root / "results" / "EXP-TEST"
    exp.mkdir(parents=True)
    (exp / "predictions").mkdir()
    (exp / "experiment.yaml").write_text("id: EXP-TEST\n", encoding="utf-8")

    state = root / "logs" / "sweep_started.json"
    if seed_reconstructed:
        state.write_text(json.dumps({
            "_RECONSTRUCTED": True,
            "started_utc": "2026-10-07T11:41:13Z",
            "finished_utc": "2026-10-07T13:11:56Z",
            "elapsed_minutes": 90.7,
            "rc": None,
            "_SOURCES": ["logs/_SWEEP_FULL.log"],
            "_FIELD_CONFIDENCE": {"started_utc": "exact"},
            "_DESTRUCTION_MECHANISM": "test fixture",
        }, indent=2), encoding="utf-8")
    else:
        state.write_text(json.dumps({
            "started_utc": "2026-10-06T16:14:32Z",
            "finished_utc": "2026-10-06T16:19:00Z",
            "elapsed_minutes": 4.7,
            "rc": 0,
        }, indent=2), encoding="utf-8")

    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "select_issues",
                        lambda limit: [f"inst-{i}" for i in range(limit or 3)])
    monkeypatch.setattr(subprocess, "call", lambda cmd, **kw: 0)
    monkeypatch.setattr(sys, "argv", ["run_final_sweep.py", *argv])

    try:
        rc = mod.main()
    except SystemExit as exc:
        rc = exc.code

    archives = sorted(p.name for p in (root / "logs").glob("sweep_started*")
                      if p.name != "sweep_started.json")
    return rc, json.loads(state.read_text(encoding="utf-8")), archives


def test_resume_preserves_a_reconstruction(tmp_path, monkeypatch):
    """K-1R: --resume must not destroy the reconstruction.

    The reproduction before the fix: main() with --resume left
    _RECONSTRUCTED=None, replaced the 11:41:13Z..13:11:56Z window, and wrote no
    marked copy at all.
    """
    rc, state, archives = _drive_main(
        tmp_path, monkeypatch, ["--resume", "--exp-id", "EXP-TEST", "--limit", "1"])

    assert rc == 0, f"main() failed: rc={rc}"
    marked = [a for a in archives if "reconstructed_archived" in a]
    assert marked, (
        f"--resume wrote no marked copy of the reconstruction; archives={archives}. "
        f"The evidence and its provenance fields are gone."
    )
    assert len(marked) == 1, (
        f"expected exactly one marked archive, got {len(marked)}: {marked}. "
        f"Two means the pre-check double-archived."
    )
    saved = json.loads((tmp_path / "proj" / "logs" / marked[0]).read_text(encoding="utf-8"))
    assert saved.get("_RECONSTRUCTED") is True, "the marker was lost in the copy"
    assert saved.get("started_utc") == "2026-10-07T11:41:13Z", (
        f"the preserved window is wrong: {saved.get('started_utc')}"
    )
    assert saved.get("_SOURCES"), "provenance (_SOURCES) was lost"
    assert saved.get("_FIELD_CONFIDENCE"), "provenance (_FIELD_CONFIDENCE) was lost"
    assert saved.get("_DESTRUCTION_MECHANISM"), "provenance was lost"

    # The new window is still recorded: preserving the old one must not stop the
    # sweep from writing its own.
    assert state.get("started_utc") != "2026-10-07T11:41:13Z", (
        "the resume did not write a new window"
    )
    assert any(a.get("started_utc") == "2026-10-07T11:41:13Z"
               for a in (state.get("attempts") or [])), (
        "the previous attempt was not folded into `attempts`"
    )


def test_a_live_window_on_resume_is_still_folded_not_archived(tmp_path, monkeypatch):
    """The K-1R pre-check must not change behaviour for ordinary windows.

    A live window on --resume is FOLDED into `attempts` (the B2 behaviour), not
    archived. The new pre-check keys on _is_reconstructed, so it must skip this
    case entirely.
    """
    rc, state, archives = _drive_main(
        tmp_path, monkeypatch, ["--resume", "--exp-id", "EXP-TEST", "--limit", "1"],
        seed_reconstructed=False)

    assert rc == 0, f"main() failed: rc={rc}"
    assert not archives, (
        f"a LIVE window on --resume was archived; it should be folded into "
        f"`attempts` instead: {archives}"
    )
    assert len(state.get("attempts") or []) == 2, (
        f"expected the previous attempt folded in plus this one: "
        f"{state.get('attempts')}"
    )


def test_a_reconstruction_on_a_fresh_sweep_is_archived_exactly_once(tmp_path, monkeypatch):
    """K-1D: main() must CALL the archive path -- and only once.

    Pins the WIRING. Every other archive test calls archive_previous_window()
    directly, so deleting the call site left all of them green: the archives
    would silently stop being written and the billing evidence would vanish with
    no test failing (measured by the reviewer: 21 passed with the call removed).

    The "exactly once" half matters because the K-1R fix adds a pre-check before
    the fresh-sweep branch; if that were not keyed on _is_reconstructed, a
    reconstruction would be archived twice, or filed as BOTH a marked copy and a
    dated live window.
    """
    rc, state, archives = _drive_main(
        tmp_path, monkeypatch, ["--limit", "1"], seed_reconstructed=True)

    assert rc == 0, f"main() failed: rc={rc}"
    marked = [a for a in archives if "reconstructed_archived" in a]
    dated = [a for a in archives if "reconstructed" not in a]
    assert marked, (
        f"main() did not archive the previous state file; archives={archives}. "
        f"The call site in main() was removed or bypassed."
    )
    assert len(marked) == 1, f"expected exactly one marked archive: {marked}"
    assert not dated, (
        f"the reconstruction was ALSO filed as a dated live window: {dated}. "
        f"A later sweep would misdate it and drop the marker."
    )


def test_a_live_window_on_a_fresh_sweep_is_archived(tmp_path, monkeypatch):
    """K-1D, other half: a LIVE window must still be archived by main().

    Guards against "fixing" the reconstruction case by making the archive call
    unconditional in a way that skips ordinary windows.
    """
    rc, state, archives = _drive_main(
        tmp_path, monkeypatch, ["--limit", "1"], seed_reconstructed=False)

    assert rc == 0, f"main() failed: rc={rc}"
    # Assert the DATED pattern specifically, not just "some file appeared": a
    # `.reconstructed_archived` name would satisfy a loose count while being the
    # wrong branch (it would misdate a live window as evidence).
    dated = _dated_archives(tmp_path / "proj" / "logs")
    assert len(dated) == 1, (
        f"main() did not create exactly one DATED archive of the live window; "
        f"got {dated} (all files: {archives}). The call site in main() was "
        f"removed or bypassed."
    )
    saved = json.loads((tmp_path / "proj" / "logs" / dated[0]).read_text(encoding="utf-8"))
    assert saved.get("started_utc") == "2026-10-06T16:14:32Z", (
        f"the wrong window was archived: {saved.get('started_utc')}"
    )
    assert not saved.get("_RECONSTRUCTED"), (
        "a live window was archived with the reconstruction marker"
    )


# ---------------------------------------------------------------------------
# A2: the nested-def rule in _redirects_directly must stay in force.
#
# `_redirects_directly` skips nested function bodies (line ~339: `continue`).
# Without it, ast.walk descends into a nested helper and credits the OUTER
# function with the inner closure's setattr -- so an outer function that calls
# main() with NO redirect of its own stops being reported.
#
# The `continue` is already in the code; what was missing was a test pinning it.
# Measured: removing it left the whole suite green while the synthetic case below
# leaked, because every existing synthetic case has the setattr at the SAME level
# as the main() call.
# ---------------------------------------------------------------------------

_A2_NESTED = _LOADER + '''

def test_synthetic(tmp_path, monkeypatch):
    mod = _load()

    def _nested_helper():
        monkeypatch.setattr(mod, "ROOT", tmp_path)

    mod.main()
'''


def test_check_logic_ignores_a_redirect_inside_a_NESTED_function(tmp_path):
    """A2: a nested helper's redirect must not protect the OUTER function.

    The outer function calls main() and never redirects anything itself; the
    setattr lives in a nested def that is never called before main(). Crediting
    the outer function would be a false negative of exactly the class this check
    exists to prevent: "a redirect exists somewhere, but is not in effect when
    main() runs".
    """
    scanned, offenders = _audit_source(_A2_NESTED, tmp_path)
    assert scanned == ["test_synthetic.py"], f"not scanned: {scanned}"
    assert offenders == ["test_synthetic.py"], (
        f"a nested helper's setattr(mod, 'ROOT', ...) was credited to the outer "
        f"function, so an unprotected main() call went unreported: {offenders}"
    )


def test_check_logic_credits_a_same_level_redirect(tmp_path):
    """Control for A2: a redirect at the SAME level as main() must still count.

    Without this, "flag everything" would satisfy the test above.
    """
    src = _LOADER + '''

def test_synthetic(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    mod.main()
'''
    scanned, offenders = _audit_source(src, tmp_path)
    assert scanned == ["test_synthetic.py"], f"not scanned: {scanned}"
    assert offenders == [], (
        f"a function that DOES redirect before calling main() was flagged: "
        f"{offenders}"
    )

