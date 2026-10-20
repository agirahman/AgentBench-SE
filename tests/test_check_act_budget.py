"""``--all`` must not select experiments whose only artifact is an empty file.

The artifact writer now creates ``messages.jsonl`` for every run, empty included.
A bare ``rglob`` match therefore picked up folders that hold no act data at all,
and the tool reported on experiments it should have skipped.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_act_budget", ROOT / "tools" / "check_act_budget.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_empty_messages_are_not_counted(tmp_path):
    mod = _load()
    art = tmp_path / "EXP-x" / "artifacts" / "i" / "direct"
    art.mkdir(parents=True)
    (art / "messages.jsonl").write_text("", encoding="utf-8")

    assert mod._has_messages(tmp_path / "EXP-x") is False


def test_a_non_empty_messages_file_is_counted(tmp_path):
    mod = _load()
    art = tmp_path / "EXP-x" / "artifacts" / "i" / "direct"
    art.mkdir(parents=True)
    (art / "messages.jsonl").write_text('{"sender": "direct"}\n', encoding="utf-8")

    assert mod._has_messages(tmp_path / "EXP-x") is True
