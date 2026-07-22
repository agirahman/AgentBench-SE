import pandas as pd

from view_results import build_strategy_difficulty_summary


def test_build_strategy_difficulty_summary_aggregates_metrics():
    df = pd.DataFrame(
        [
            {
                "strategy": "direct",
                "difficulty": "easy",
                "execution_time": 2.0,
                "total_tokens": 100,
                "cost_usd": 0.1,
                "success": True,
            },
            {
                "strategy": "direct",
                "difficulty": "hard",
                "execution_time": 4.0,
                "total_tokens": 200,
                "cost_usd": 0.2,
                "success": False,
            },
            {
                "strategy": "planning",
                "difficulty": "easy",
                "execution_time": 6.0,
                "total_tokens": 300,
                "cost_usd": 0.3,
                "success": True,
            },
        ]
    )

    summary = build_strategy_difficulty_summary(df)

    assert summary.loc[("direct", "easy"), "execution_time"] == 2.0
    assert summary.loc[("direct", "hard"), "success_rate"] == 0.0
    assert summary.loc[("planning", "easy"), "total_tokens"] == 300
