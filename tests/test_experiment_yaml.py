"""Round-trip tests for the hand-rolled experiment.yaml serializer.

There is no YAML dependency in this project, so ``_to_yaml`` is written by hand
and must produce output a standard loader reads back identically. Two bugs were
found here by accident, both of which silently corrupt the reproducibility
record rather than crashing:

* ``None`` rendered as the STRING "None" (yaml.safe_load returns 'None', not
  null), so "no targeted instance ids" looked like an id literally named None.
* An empty list rendered as a bare ``key:``, which loads as null instead of [],
  so a consumer doing ``len(cfg["agents"])`` would crash.

Neither was reachable before a None field and an empty list field were added, so
these tests exist to keep the next such field from silently degrading the file.
"""
import types

import yaml

from main import _save_experiment_config, _to_yaml


def test_none_round_trips_as_null():
    text = _to_yaml({"a": None})
    assert text.strip() == "a: null"
    assert yaml.safe_load(text) == {"a": None}


def test_empty_list_round_trips_as_list():
    text = _to_yaml({"agents": []})
    loaded = yaml.safe_load(text)
    assert loaded == {"agents": []}
    assert isinstance(loaded["agents"], list), "must not load as null"


def test_string_that_looks_like_null_is_quoted():
    """A literal 'None' string must not be confused with a real null."""
    text = _to_yaml({"a": "None"})
    loaded = yaml.safe_load(text)
    assert loaded["a"] == "None"
    assert loaded["a"] is not None


def test_nested_dicts_and_lists_round_trip():
    data = {
        "outer": {"inner": {"deep": 1}, "names": ["a", "b"]},
        "flags": [True, False],
        "counts": [0, 1, 2],
    }
    assert yaml.safe_load(_to_yaml(data)) == data


def test_strings_needing_quotes_survive_the_round_trip():
    data = {
        "flow": "{a: 1}",
        "colon": "key: value",
        "padded": "  spaced  ",
        "plain": "simple",
    }
    assert yaml.safe_load(_to_yaml(data)) == data


def test_real_config_round_trips_with_targeted_ids(tmp_path):
    """The whole generated config must load back as the same values.

    Covers the interaction the two bugs hid behind: a None-valued field next to
    an empty list, in the real config rather than a toy dict.
    """
    _save_experiment_config(
        str(tmp_path),
        types.SimpleNamespace(provider="deepseek"),
        issue_count=1,
        strategy_names=["review"],
        experiment_id="EXP-roundtrip-001",
        agents=[],
        repos={"django/django": 1},
    )
    cfg = yaml.safe_load((tmp_path / "experiment.yaml").read_text(encoding="utf-8"))
    assert cfg["dataset"]["instance_ids"] is None
    assert cfg["agents"] == []
    assert cfg["strategies"] == ["review"]
    assert cfg["tool_calling"]["enabled"] in (True, False)
