"""Statistical significance helpers for paired SWE-bench strategy comparison.

Two methods are provided because the thesis compares orchestration strategies
on the *same* instances (paired data):

* ``mcnemar_test`` — tests whether the difference in resolution rate between
  two strategies is statistically significant, accounting for the paired
  structure (only instances whose outcome differs between strategies count).
* ``wilson_ci`` — 95% confidence interval for a proportion, robust for the
  small per-strategy sample sizes (n=50) used in this project.

Both are scipy-backed and operate on a results DataFrame.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from scipy import stats


def wilson_ci(success: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score confidence interval for a binomial proportion.

    Returns (low, high). For n == 0 returns (0.0, 0.0).
    """
    if n <= 0:
        return (0.0, 0.0)
    phat = success / n
    z = stats.norm.ppf(1 - (1 - confidence) / 2)
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = (z * ((phat * (1 - phat) + z * z / (4 * n)) / n) ** 0.5) / denom
    return (max(0.0, center - half), min(1.0, center + half))


@dataclass
class McNemarResult:
    """Outcome of a paired McNemar comparison between two strategies."""

    strategy_a: str
    strategy_b: str
    n_paired: int
    both_resolved: int
    both_unresolved: int
    only_a: int
    only_b: int
    p_value: float
    significant: bool
    alpha: float = 0.05

    def __str__(self) -> str:
        return (
            f"{self.strategy_a} vs {self.strategy_b}: "
            f"only_{self.strategy_a}={self.only_a}, "
            f"only_{self.strategy_b}={self.only_b}, "
            f"p={self.p_value:.4f} "
            f"({'SIGNIFICANT' if self.significant else 'not significant'})"
        )


def mcnemar_test(
    df: pd.DataFrame,
    strategy_a: str,
    strategy_b: str,
    alpha: float = 0.05,
) -> McNemarResult:
    """Paired McNemar test on ``resolved`` for two strategies.

    Builds the paired contingency table keyed by instance_id and computes the
    exact binomial (or chi-square with continuity correction) p-value using
    only the discordant cells (instances resolved by exactly one strategy).
    """
    if "resolved" not in df.columns:
        raise ValueError(
            "mcnemar_test requires a 'resolved' column (from Modal eval). "
            "Run tools/eval_modal.py first."
        )

    sub = df[df["strategy"].isin([strategy_a, strategy_b])]
    if sub.empty:
        return McNemarResult(strategy_a, strategy_b, 0, 0, 0, 0, 0, 1.0, False, alpha)

    pivot = sub.pivot_table(
        index="instance_id", columns="strategy", values="resolved", aggfunc="first"
    )
    if strategy_a not in pivot.columns or strategy_b not in pivot.columns:
        return McNemarResult(strategy_a, strategy_b, 0, 0, 0, 0, 0, 1.0, False, alpha)

    a = pivot[strategy_a].fillna(False).astype(bool)
    b = pivot[strategy_b].fillna(False).astype(bool)

    both_resolved = int((a & b).sum())
    both_unresolved = int((~a & ~b).sum())
    only_a = int((a & ~b).sum())
    only_b = int((~a & b).sum())
    n_paired = both_resolved + both_unresolved + only_a + only_b

    # Discordant count b = only_a + only_b; use exact binomial on the
    # minimum discordant cell (conservative, standard for McNemar).
    discordant = only_a + only_b
    if discordant == 0:
        p_value = 1.0
    else:
        k = min(only_a, only_b)
        # Two-sided exact binomial test under H0: p=0.5.
        p_value = float(2 * min(1.0, stats.binom.cdf(k, discordant, 0.5)))

    return McNemarResult(
        strategy_a=strategy_a,
        strategy_b=strategy_b,
        n_paired=n_paired,
        both_resolved=both_resolved,
        both_unresolved=both_unresolved,
        only_a=only_a,
        only_b=only_b,
        p_value=p_value,
        significant=p_value < alpha,
        alpha=alpha,
    )


def resolution_ci_table(df: pd.DataFrame, confidence: float = 0.95) -> pd.DataFrame:
    """Per-strategy resolution rate with Wilson 95% CI.

    Returns a DataFrame indexed by strategy with columns:
    resolved, total, rate, ci_low, ci_high.
    """
    if "resolved" not in df.columns:
        df = df.copy()
        df["resolved"] = df["error"].fillna("").str.len() == 0
    rows = []
    for strategy, grp in df.groupby("strategy"):
        total = len(grp)
        resolved = int(grp["resolved"].sum())
        rate = resolved / total if total else 0.0
        low, high = wilson_ci(resolved, total, confidence)
        rows.append(
            {
                "strategy": strategy,
                "resolved": resolved,
                "total": total,
                "rate": rate,
                "ci_low": low,
                "ci_high": high,
            }
        )
    return pd.DataFrame(rows).set_index("strategy")


def cmd_significance(df: pd.DataFrame) -> None:
    """Print paired McNemar comparisons + per-strategy Wilson CIs."""
    if "resolved" not in df.columns:
        print(
            "No 'resolved' column found. Run tools/eval_modal.py to evaluate "
            "patches on SWE-bench Lite before significance analysis."
        )
        return

    strategies = sorted(df["strategy"].unique())
    print("=== Per-Strategy Resolution Rate (Wilson 95% CI) ===")
    print(resolution_ci_table(df).to_string())

    print("\n=== Paired McNemar Tests (alpha=0.05) ===")
    if len(strategies) < 2:
        print("Need at least 2 strategies to compare.")
        return
    for i in range(len(strategies)):
        for j in range(i + 1, len(strategies)):
            res = mcnemar_test(df, strategies[i], strategies[j])
            print(str(res))
