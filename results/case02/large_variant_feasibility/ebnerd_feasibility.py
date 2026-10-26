"""Feasibility check of EB-NeRD as the semi-synthetic base for Case 02.

Usage (repo root, after unzipping the small variant to data/raw/ebnerd_small/):
    uv run python results/case02/large_variant_feasibility/ebnerd_feasibility.py data/raw/ebnerd_small

Answers, on the 7-day behaviours split, the questions the spec's data decision turns on:
1. Per-user activity over 7 days: impressions (page views), in-view articles, clicks; quantiles,
   zero-click share, top-1% concentration.
2. The three estimand levels on the real population: clicks per user (E1), clicks per in-view
   article as a ratio of sums (E2), mean user-level rate (E3).
3. Naive-to-delta standard-error ratio for E2 on A/A splits of users, and the naive test's
   empirical rejection rate at nominal 5%: this decides whether the "1.5 times too narrow"
   headline survives on real data.
4. Quality-proxy availability: article-level columns that could define a low-quality tier
   without using the evaluation window's clicks.
Nothing here is package code; it is a scratch measurement for a design decision.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

root = Path(sys.argv[1])
split = "train"
beh = pd.read_parquet(root / split / "behaviors.parquet")
print(f"behaviours rows: {len(beh):,}; columns: {list(beh.columns)}")
print(f"impression_time span: {beh['impression_time'].min()} to {beh['impression_time'].max()}")

inview = beh["article_ids_inview"].map(len)
clicked = beh["article_ids_clicked"].map(len)
per_user = pd.DataFrame(
    {
        "page_views": beh.groupby("user_id").size(),
        "inview": inview.groupby(beh["user_id"]).sum(),
        "clicks": clicked.groupby(beh["user_id"]).sum(),
    }
)
n = len(per_user)
print(f"\nusers in split: {n:,}")
for col in ("page_views", "inview", "clicks"):
    q = per_user[col].quantile([0.5, 0.9, 0.99]).values
    print(
        f"{col:>10}: mean {per_user[col].mean():.1f}  median {q[0]:.0f}  p90 {q[1]:.0f}  "
        f"p99 {q[2]:.0f}  max {per_user[col].max():,}  sd {per_user[col].std():.1f}"
    )
print(f"share with zero clicks in the 7 days: {(per_user['clicks'] == 0).mean():.3f}")
print(f"share with at least one click: {(per_user['clicks'] > 0).mean():.3f}")
top = per_user.sort_values("inview", ascending=False).head(max(1, n // 100))
print(
    f"top 1% of users by in-view: {top['inview'].sum() / per_user['inview'].sum():.1%} of in-view, "
    f"{top['clicks'].sum() / per_user['clicks'].sum():.1%} of clicks"
)

C = per_user["clicks"].to_numpy(float)
M = per_user["inview"].to_numpy(float)
E1 = C.mean()
E2 = C.sum() / M.sum()
E3 = np.mean(C[M > 0] / M[M > 0])
print(f"\nE1 clicks per user {E1:.3f} (sd {C.std():.2f});  E2 ratio of sums {E2:.4f};  E3 mean user rate {E3:.4f}")
print(f"E3 / E2 = {E3 / E2:.2f}  (spec's parametric generator: 6.2% / 3.4% = 1.83)")


def delta_var(c: np.ndarray, m: np.ndarray) -> float:
    r = c.sum() / m.sum()
    cov = np.cov(c, m, ddof=1)
    return (cov[0, 0] - 2 * r * cov[0, 1] + r * r * cov[1, 1]) / (len(c) * m.mean() ** 2)


rng = np.random.default_rng(20260929)
ratios, rej_naive, rej_delta, rej_e1 = [], 0, 0, 0
S = 400
for _ in range(S):
    arm = rng.random(n) < 0.5
    c0, m0, c1, m1 = C[~arm], M[~arm], C[arm], M[arm]
    r0, r1 = c0.sum() / m0.sum(), c1.sum() / m1.sum()
    pi = (c0.sum() + c1.sum()) / (m0.sum() + m1.sum())
    se_naive = np.sqrt(pi * (1 - pi) * (1 / m0.sum() + 1 / m1.sum()))
    se_delta = np.sqrt(delta_var(c0, m0) + delta_var(c1, m1))
    ratios.append(se_delta / se_naive)
    rej_naive += abs(r1 - r0) / se_naive > 1.96
    rej_delta += abs(r1 - r0) / se_delta > 1.96
    rej_e1 += stats.ttest_ind(c1, c0, equal_var=False).pvalue < 0.05
mc = np.sqrt(0.05 * 0.95 / S)
print(f"\nA/A splits ({S}): delta/naive SE ratio median {np.median(ratios):.2f} (IQR {np.percentile(ratios, 25):.2f} to {np.percentile(ratios, 75):.2f})")
print(f"naive test rejects {rej_naive / S:.3f}, delta-method {rej_delta / S:.3f}, E1 Welch {rej_e1 / S:.3f}  (MC SE about {mc:.3f} at 5%)")
print("verdict on the headline: " + ("holds (ratio >= 1.4)" if np.median(ratios) >= 1.4 else "weak (ratio < 1.4): the real population's propensity variance is compressed by the click floor"))

art_path = root / "articles.parquet"
if art_path.exists():
    art = pd.read_parquet(art_path)
    print(f"\narticles: {len(art):,}; columns: {list(art.columns)}")
    for col in ("premium", "article_type", "category_str", "sentiment_label", "total_inviews", "total_pageviews", "total_read_time", "published_time"):
        if col in art.columns:
            s = art[col]
            desc = s.describe()[["mean", "50%", "max"]].to_dict() if pd.api.types.is_numeric_dtype(s) and s.dtype != bool else s.value_counts().head(5).to_dict()
            print(f"  {col}: {desc}")
    if {"total_inviews", "total_pageviews"} <= set(art.columns):
        ok = art["total_inviews"].fillna(0) > 0
        hist_ctr = (art.loc[ok, "total_pageviews"] / art.loc[ok, "total_inviews"])
        print(f"  historical pageviews/inviews per article: median {hist_ctr.median():.3f}, p10 {hist_ctr.quantile(0.1):.3f}, p90 {hist_ctr.quantile(0.9):.3f} on {ok.sum():,} articles")
        print("  candidate quality proxy: tercile of this ratio, computed on article-level totals; check the totals' window against the evaluation split before relying on it")
hist_path = root / split / "history.parquet"
if hist_path.exists():
    hist = pd.read_parquet(hist_path)
    print(f"\nhistory rows: {len(hist):,}; columns: {list(hist.columns)}  (21-day click history: the pre-period covariate for invariants and a CUPED case)")
