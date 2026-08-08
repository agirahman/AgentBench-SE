"""Tests for the cooperative control hooks added in Patch 5:
``should_abort`` / ``is_paused`` on ``run_experiments``."""

import time

from agentbench.core.experiments.runner import run_experiments

from test_tui_runner_backend import fake_issues, fake_strategies


def _callbacks():
    done = {"n": 0, "ids": []}

    def on_issue_complete(instance_id, strategy, elapsed, tokens, cost_usd,
                          success, status):
        done["n"] += 1
        done["ids"].append(f"{strategy}/{instance_id}")

    return done, on_issue_complete


def test_abort_stops_after_current_issue(tmp_path):
    done, cb = _callbacks()
    flag = {"abort": False}

    def should_abort():
        return flag["abort"]

    issues = fake_issues(5)
    strategies = fake_strategies(delay=0.02)

    def _abort_later():
        time.sleep(0.06)
        flag["abort"] = True

    import threading

    threading.Thread(target=_abort_later, daemon=True).start()
    df, exp_id = run_experiments(
        issues, strategies, base_dir=str(tmp_path),
        rate_limit_seconds=0.0, on_issue_complete=cb,
        should_abort=should_abort,
    )
    # 5 issues × 2 strategies = 10 tasks; abort lands after the first issue
    # pair, so between 2 and 8 completions (thread scheduling dependent).
    assert 2 <= done["n"] <= 8
    assert len(df) == done["n"]  # partial results still exported


def test_pause_blocks_between_issues(tmp_path):
    done, cb = _callbacks()
    pause = {"on": False}

    def is_paused():
        return pause["on"]

    issues = fake_issues(4)
    strategies = fake_strategies(delay=0.001)

    def _pause_soon():
        time.sleep(0.02)
        pause["on"] = True
        time.sleep(0.25)  # hold the gate
        pause["on"] = False

    import threading

    threading.Thread(target=_pause_soon, daemon=True).start()
    t0 = time.monotonic()
    df, exp_id = run_experiments(
        issues, strategies, base_dir=str(tmp_path),
        rate_limit_seconds=0.0, on_issue_complete=cb,
        is_paused=is_paused,
    )
    # Without pause the run would take ~8 * 0.001s ≈ instant; the 0.25s
    # pause gate makes it measurably slower while nothing is lost.
    assert time.monotonic() - t0 >= 0.2
    assert done["n"] == 8  # all tasks still complete
    assert len(df) == 8


def test_no_hooks_behaves_identically(tmp_path):
    done, cb = _callbacks()
    df, exp_id = run_experiments(
        fake_issues(3), fake_strategies(delay=0.001),
        base_dir=str(tmp_path), rate_limit_seconds=0.0,
        on_issue_complete=cb,
    )
    assert done["n"] == 6
    assert len(df) == 6
