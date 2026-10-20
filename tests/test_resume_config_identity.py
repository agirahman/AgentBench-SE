"""A resume must not silently change the identity of the run it continues.

The runner's resume key is ``instance_id|model|thinking``. That protects the
skip decision but not the FOLDER: resuming with another model makes every
finished run look outstanding (so it is paid for twice) and writes a second
configuration into the same experiment's numbers, while ``experiment.yaml``
quietly starts describing the new settings. ``run_identity`` records the
provider/model/thinking that produced the folder, and ``--resume`` refuses to
proceed when the invocation disagrees with it.

Old experiments have no such block. Refusing them outright would strand real
work, so a folder with NO savepoint is allowed (nothing to mix) and a folder
WITH savepoints needs the explicit ``--allow-config-change`` opt-out.
"""
import types

import pytest
import yaml

import main as main_mod
from main import _check_resume_identity, _run_identity, _save_experiment_config

EXP = "EXP-20260101-001"


def _identity(provider="deepseek", model="m-1", thinking=False):
    return {"provider": provider, "model": model, "thinking": thinking}


def _write_yaml(exp_dir, *, identity, ids=("django__django-1",)):
    exp_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "experiment_meta": {"experiment_id": exp_dir.name},
        "provider": {"name": "deepseek", "model": (identity or {}).get("model")},
        "dataset": {"instance_ids": list(ids), "n_issues": len(ids)},
    }
    if identity is not None:
        data["run_identity"] = identity
    (exp_dir / "experiment.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# The check itself
# ---------------------------------------------------------------------------

def test_same_identity_resumes(tmp_path):
    _write_yaml(tmp_path / EXP, identity=_identity())
    assert _check_resume_identity(str(tmp_path), EXP, _identity()) is None


def test_changed_model_is_refused_and_named(tmp_path):
    _write_yaml(tmp_path / EXP, identity=_identity(model="m-1"))
    refusal = _check_resume_identity(str(tmp_path), EXP, _identity(model="m-2"))
    assert refusal, "a changed model must be refused"
    assert "model" in refusal
    assert "m-1" in refusal and "m-2" in refusal, (
        f"the message must name the concrete difference: {refusal}"
    )


def test_changed_thinking_is_refused(tmp_path):
    _write_yaml(tmp_path / EXP, identity=_identity(thinking=False))
    refusal = _check_resume_identity(str(tmp_path), EXP, _identity(thinking=True))
    assert refusal and "thinking" in refusal, (
        f"thinking changes the resume key too: {refusal}"
    )


def test_allow_config_change_bypasses_the_refusal(tmp_path):
    _write_yaml(tmp_path / EXP, identity=_identity(model="m-1"))
    assert _check_resume_identity(
        str(tmp_path), EXP, _identity(model="m-2"), allow_change=True) is None


def test_missing_folder_or_yaml_has_nothing_to_conflict_with(tmp_path):
    assert _check_resume_identity(str(tmp_path), EXP, _identity()) is None
    (tmp_path / EXP).mkdir()
    assert _check_resume_identity(str(tmp_path), EXP, _identity()) is None


def test_old_yaml_without_the_block_is_allowed_when_nothing_ran(tmp_path):
    _write_yaml(tmp_path / EXP, identity=None)
    assert _check_resume_identity(str(tmp_path), EXP, _identity()) is None


def test_old_yaml_with_savepoints_needs_the_opt_out(tmp_path):
    exp = tmp_path / EXP
    _write_yaml(exp, identity=None)
    (exp / "predictions").mkdir()
    (exp / "predictions" / "direct.jsonl").write_text(
        '{"instance_id": "django__django-1"}\n', encoding="utf-8")

    refusal = _check_resume_identity(str(tmp_path), EXP, _identity())
    assert refusal, "an unknown identity next to finished runs must be refused"
    assert "--allow-config-change" in refusal

    assert _check_resume_identity(
        str(tmp_path), EXP, _identity(), allow_change=True) is None


def test_an_empty_savepoint_file_is_not_evidence_of_a_run(tmp_path):
    exp = tmp_path / EXP
    _write_yaml(exp, identity=None)
    (exp / "predictions").mkdir()
    (exp / "predictions" / "direct.jsonl").write_text("", encoding="utf-8")
    assert _check_resume_identity(str(tmp_path), EXP, _identity()) is None


def test_a_yaml_whose_root_is_not_a_mapping_is_skipped_not_crashed(tmp_path):
    """A parseable file of the wrong SHAPE is "unreadable", not a mismatch.

    ``yaml.safe_load`` happily returns a list or a string; calling ``.get`` on it
    raised AttributeError, i.e. the guard crashed a resume over a file it is
    documented to tolerate.
    """
    exp = tmp_path / EXP
    exp.mkdir(parents=True)
    (exp / "experiment.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")

    assert _check_resume_identity(str(tmp_path), EXP, _identity()) is None

    (exp / "experiment.yaml").write_text("bare scalar\n", encoding="utf-8")
    assert _check_resume_identity(str(tmp_path), EXP, _identity()) is None


def test_a_legacy_csv_alone_counts_as_evidence_of_a_run(tmp_path):
    """Predictions may be gone while the CSV still holds the runs.

    The "never ran" classification used to read ``predictions/*.jsonl`` only, so a
    legacy folder with rows in ``generation_result.csv`` looked untouched and a
    resume could mix two configs without the opt-out.
    """
    exp = tmp_path / EXP
    _write_yaml(exp, identity=None)
    (exp / "generation_result.csv").write_text(
        "instance_id,strategy,model_patch\n"
        "django__django-1,direct,diff --git a b\n",
        encoding="utf-8",
    )

    refusal = _check_resume_identity(str(tmp_path), EXP, _identity())
    assert refusal, "a CSV with run rows is evidence the folder has run"
    assert "--allow-config-change" in refusal


def test_a_header_only_csv_is_not_evidence_of_a_run(tmp_path):
    """Control: the CSV is written before the first row, so a header proves nothing."""
    exp = tmp_path / EXP
    _write_yaml(exp, identity=None)
    (exp / "generation_result.csv").write_text(
        "instance_id,strategy,model_patch\n", encoding="utf-8")

    assert _check_resume_identity(str(tmp_path), EXP, _identity()) is None


def test_a_one_row_csv_without_a_trailing_newline_counts_as_a_run(tmp_path):
    """A single data row and no final newline must still be evidence.

    Reading exactly one line after the header treats that row as the header when
    there is no trailing newline, so a folder that HAS run looked untouched and
    a resume could mix configs without the opt-out.
    """
    exp = tmp_path / EXP
    _write_yaml(exp, identity=None)
    (exp / "generation_result.csv").write_text(
        "instance_id,strategy,model_patch\n"
        "django__django-1,direct,diff --git a b",
        encoding="utf-8",
    )

    refusal = _check_resume_identity(str(tmp_path), EXP, _identity())
    assert refusal, "a one-row CSV without a trailing newline is still a run"
    assert "--allow-config-change" in refusal


# ---------------------------------------------------------------------------
# End to end through main()
# ---------------------------------------------------------------------------

def _drive_main(monkeypatch, argv, *, run_tripwire=False):
    """Run main() with every side effect stubbed; returns (rc, ran)."""
    import sys

    class _Provider:
        model = "m-1"

        def health_check(self):
            return True

    issue = types.SimpleNamespace(
        instance_id="django__django-1", repo="django/django", difficulty="easy")
    calls = {"ran": False}

    def fake_run(*a, **k):
        if run_tripwire:
            raise AssertionError("run_experiments() ran despite the refusal")
        calls["ran"] = True
        import pandas as pd

        return pd.DataFrame([{
            "instance_id": "django__django-1", "strategy": "direct",
            "model": "m-1", "execution_time": 1.0, "inference_count": 1,
            "total_tokens": 1,
        }]), "EXP-20260101-001"

    monkeypatch.setattr(main_mod, "GeminiProvider", _Provider)
    monkeypatch.setattr(main_mod, "select_issues", lambda *a, **k: [issue])
    monkeypatch.setattr(main_mod, "build_agent_team", lambda p: {})
    for name in ("DirectStrategy", "PlanningStrategy", "ReviewStrategy"):
        monkeypatch.setattr(main_mod, name, lambda provider: object())
    monkeypatch.setattr(main_mod, "run_experiments", fake_run)
    monkeypatch.setattr(main_mod, "_save_experiment_config", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["main.py", *argv])
    return main_mod.main(), calls["ran"]


def _resume_argv(output, *extra):
    return ["--provider", "gemini", "--output", str(output),
            "--resume", "--exp-id", EXP, *extra]


def test_main_exits_2_before_running_on_a_identity_mismatch(
        monkeypatch, tmp_path):
    """The refusal must land BEFORE the first paid run, not after it."""
    import io

    from loguru import logger

    out = tmp_path / "results"
    recorded = _run_identity("gemini")
    recorded["model"] = "some-other-model"
    _write_yaml(out / EXP, identity=recorded)

    # loguru has its own sinks; caplog/capsys do not see them.
    seen = io.StringIO()
    sink_id = logger.add(seen, level="ERROR")
    try:
        rc, ran = _drive_main(monkeypatch, _resume_argv(out), run_tripwire=True)
    finally:
        logger.remove(sink_id)

    assert rc == 2, f"a changed model must exit 2, got {rc!r}"
    assert ran is False
    assert "some-other-model" in seen.getvalue(), (
        "the refusal must name the recorded model"
    )


def test_main_resumes_when_the_identity_matches(monkeypatch, tmp_path):
    out = tmp_path / "results"
    _write_yaml(out / EXP, identity=_run_identity("gemini"))
    rc, ran = _drive_main(monkeypatch, _resume_argv(out))
    assert rc is None and ran is True


def test_main_resumes_with_the_opt_out(monkeypatch, tmp_path):
    out = tmp_path / "results"
    recorded = _run_identity("gemini")
    recorded["model"] = "some-other-model"
    _write_yaml(out / EXP, identity=recorded)

    rc, ran = _drive_main(
        monkeypatch, _resume_argv(out, "--allow-config-change"))
    assert rc is None, f"--allow-config-change must let the resume continue: {rc!r}"
    assert ran is True


# ---------------------------------------------------------------------------
# D6: a same-id rewrite with different content keeps a .bak
# ---------------------------------------------------------------------------

def _save(tier, provider, *, ids=("django__django-1",)):
    _save_experiment_config(
        str(tier),
        types.SimpleNamespace(provider=provider, instance_ids=list(ids)),
        issue_count=len(ids),
        strategy_names=["direct"],
        experiment_id=EXP,
        agents=[],
        repos={"django/django": len(ids)},
        instance_ids=list(ids),
    )


def test_same_ids_but_changed_provider_keeps_a_bak(tmp_path):
    _save(tmp_path, "deepseek")
    first = (tmp_path / "experiment.yaml").read_text(encoding="utf-8")

    _save(tmp_path, "gemini")

    bak = tmp_path / "experiment.yaml.bak"
    assert bak.is_file(), (
        "the id set did not change, but the record of batch 1 was overwritten "
        "without a copy"
    )
    assert bak.read_text(encoding="utf-8") == first, (
        "the .bak must hold the previous configuration, not the new one"
    )
    assert yaml.safe_load(first)["run_identity"]["provider"] == "deepseek"


def test_an_identical_rewrite_writes_no_bak(tmp_path):
    _save(tmp_path, "deepseek")
    _save(tmp_path, "deepseek")
    assert not (tmp_path / "experiment.yaml.bak").exists(), (
        "identical content must not produce a .bak"
    )


def test_changed_ids_still_keep_a_bak(tmp_path):
    """The pre-existing behaviour must survive the content check."""
    _save(tmp_path, "deepseek")
    _save(tmp_path, "deepseek", ids=("django__django-1", "django__django-2"))
    assert (tmp_path / "experiment.yaml.bak").is_file()


def test_run_identity_is_recorded_in_the_yaml(tmp_path):
    _save(tmp_path, "deepseek")
    cfg = yaml.safe_load((tmp_path / "experiment.yaml").read_text(encoding="utf-8"))
    assert cfg["run_identity"] == _run_identity("deepseek")
    assert set(cfg["run_identity"]) == {"provider", "model", "thinking"}
