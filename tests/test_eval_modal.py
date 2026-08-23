import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from eval_modal import build_run_id


def test_build_run_id_from_exp_layout():
    pred = Path("results/EXP-20260819-002/predictions/direct.jsonl")
    assert build_run_id(pred) == "modal-direct-EXP-20260819-002"


def test_build_run_id_override_exp_id():
    pred = Path("results/EXP-20260819-002/predictions/planning.jsonl")
    assert build_run_id(pred, exp_id="EXP-99999999-999") == "modal-planning-EXP-99999999-999"


def test_build_run_id_without_exp_prefix():
    pred = Path("custom/predictions/review.jsonl")
    assert build_run_id(pred) == "modal-review"


def test_main_skips_when_results_exist(tmp_path):
    pred_dir = tmp_path / "EXP-20260819-002" / "predictions"
    pred_dir.mkdir(parents=True)
    pred_path = pred_dir / "direct.jsonl"
    pred_path.write_text("{}\n", encoding="utf-8")
    (pred_dir / "direct_results.json").write_text("{}", encoding="utf-8")

    tools = Path(__file__).parent.parent / "tools"
    result = subprocess.run(
        [sys.executable, str(tools / "eval_modal.py"), str(pred_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
    assert "[SKIP]" in result.stdout
    assert "run_id: modal-direct-EXP-20260819-002" in result.stdout


def test_main_force_bypasses_skip_guard(tmp_path):
    pred_dir = tmp_path / "EXP-20260819-002" / "predictions"
    pred_dir.mkdir(parents=True)
    pred_path = pred_dir / "direct.jsonl"
    pred_path.write_text("{}\n", encoding="utf-8")
    (pred_dir / "direct_results.json").write_text("{}", encoding="utf-8")

    tools = Path(__file__).parent.parent / "tools"
    result = subprocess.run(
        [sys.executable, str(tools / "eval_modal.py"), str(pred_path), "--force"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "[SKIP]" not in result.stdout
    assert "Loading predictions from" in result.stdout
