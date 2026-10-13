"""Case 02 data-generating process: real control, simulated treatment (spec case-02.md v1.3).

The control arm is observed EB-NeRD reading behaviour (small variant, ``train`` split); the
ranking change, its effect and the negative-feedback guardrail are simulated on the same
users' rows. Each replicate, and the committed run, draws users with replacement from the
finite population (the base's 15,143 users); the draw is the analysis, clustering and
random-stream unit (spec sections 5, 6 and 10).

Random streams. Every random quantity is a function of uniforms from ``keyed_uniform(seed,
key, use)``, a counter-based generator: nested SplitMix64 finalisers (Steele, Lea and Flood
2014) over the seed, the use and the key, so one draw's values never depend on any other draw
in the frame, duplicated users share no treatment noise, and scenarios run with one seed share
every draw that their parameters do not change (common random numbers). Counts come from the
uniforms by an exact binomial inverse CDF (``binom_icdf``), so the fast path works on per-user
sufficient statistics and never touches individual in-views; the committed run's tables are
expanded from those counts with further keyed uses, and their counts equal the fast path's.

Mechanism, per treatment draw of population user i (spec sections 6 and 7):

* suppression: each old-tier in-view (article age at impression in the split's oldest tercile)
  other than the user's last in-view is removed independently with probability delta; a
  removed in-view takes its click with it. Kc ~ Bin(c_oc, delta) clicked and Ku ~ Bin(u_oc,
  delta) unclicked candidates are removed;
* click lift: each retained unclicked in-view becomes a click with probability
  q = min(1, tau p / (1 - p)), p the user's click rate over retained in-views, so that expected
  clicks per retained in-view are p (1 + tau) wherever the cap does not bind;
* guardrail (both arms, simulated): each in-view draws a negative-feedback action with
  probability r g_i, three times higher on old-tier in-views (capped at 1); g_i ~ Gamma(k,
  mean 1) per population user, keyed by a fixed population seed and rescaled so that its
  in-view-weighted mean is exactly 1.

Truths are the finite population's: the mean over its users of each user's expected potential
outcomes under (delta, tau), computed by exact enumeration over the two removal binomials
(``scenario_truths``) once per scenario, before any replicate.

Licence (spec section 6): the tables and the ``truth`` audit table carry per-user rows from
EB-NeRD and are written under ``data/generated/``, which is never tracked; the manifest
carries aggregates only.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from abforensics import __version__
from abforensics.simulation.checkout_incident import content_sha256

# Stream uses. Each is a separate keyed stream; adding a use never changes another's values.
USE = {
    "arm": 1,
    "population": 2,
    "remove_clicked": 3,
    "remove_unclicked": 4,
    "lift": 5,
    "feedback_old": 6,
    "feedback_other": 7,
    "heterogeneity": 8,
    "pick_removed": 9,
    "pick_lift": 10,
    "pick_feedback": 11,
    "delay": 12,
    "action": 13,
}
_U64 = np.uint64
_C1, _C2, _C3 = _U64(0x9E3779B97F4A7C15), _U64(0xBF58476D1CE4E5B9), _U64(0x94D049BB133111EB)
_S30, _S27, _S31, _S11 = _U64(30), _U64(27), _U64(31), _U64(11)
_MASK = (1 << 64) - 1

ARMS = ("control", "treatment")
FEEDBACK_ACTIONS = ("hide", "not_interested")
EXPERIMENT_START = pd.Timestamp("2023-05-18T00:00:00")  # first day of the base's train split


def _mix(x: np.ndarray) -> np.ndarray:
    """SplitMix64 finaliser on a uint64 array (wrapping arithmetic)."""
    with np.errstate(over="ignore"):
        x = x + _C1
        x = (x ^ (x >> _S30)) * _C2
        x = (x ^ (x >> _S27)) * _C3
        return x ^ (x >> _S31)


def _as_u64(name: str, value: Any) -> np.ndarray:
    arr = np.asarray(value)
    if arr.dtype.kind not in "iu":
        raise TypeError(f"{name} must be an integer array; got dtype {arr.dtype}")
    if arr.dtype.kind == "i" and arr.size and int(arr.min()) < 0:
        raise ValueError(f"{name} must be non-negative")
    return arr.astype(np.uint64)


def keyed_uniform(seed: int, key: Any, use: int, sub: Any | None = None) -> np.ndarray:
    """Uniforms on [0, 1) as a pure function of (seed, key, use[, sub]), elementwise in key.

    ``key`` is the draw index (or the population index for population-level uses); ``sub`` is
    an optional second counter, the in-view position within a draw. Every argument is hashed
    in its own SplitMix64 stage, so distinct tuples do not alias by arithmetic packing.
    """
    s = _mix(np.asarray([int(seed) & _MASK], dtype=np.uint64))
    s = _mix(s ^ _U64(int(use) & _MASK))
    h = _mix(_as_u64("key", key) ^ s[0])
    if sub is not None:
        h = _mix(h ^ _as_u64("sub", sub))
    return (h >> _S11).astype(np.float64) * 2.0**-53


def binom_icdf(u: Any, n: Any, p: Any) -> np.ndarray:
    """Binomial inverse CDF, vectorised: the smallest k with P(Bin(n, p) <= k) >= u.

    The pmf is accumulated upwards from k = 0 with the ratio recursion, on a shrinking set of
    unfinished elements, so the cost scales with the largest returned count rather than with
    n. Elements with p > 0.5 are drawn as n - Bin(n, 1 - p) at 1 - u (equal in distribution);
    elements whose pmf at 0 is below 1e-250 fall back to ``scipy.stats.binom.ppf``.
    """
    u, n, p = np.broadcast_arrays(
        np.asarray(u, dtype=np.float64),
        np.asarray(n, dtype=np.int64),
        np.asarray(p, dtype=np.float64),
    )
    shape = u.shape
    u, n, p = u.ravel(), n.ravel(), p.ravel()
    if np.any((p < 0) | (p > 1)) or np.any(n < 0):
        raise ValueError("binom_icdf needs n >= 0 and 0 <= p <= 1")
    out = np.zeros(u.size, dtype=np.int64)
    flip = p > 0.5
    pp = np.where(flip, 1.0 - p, p)
    uu = np.where(flip, 1.0 - u, u)
    live = np.flatnonzero((pp > 0) & (n > 0))  # pp <= 0.5 after the flip
    if live.size:
        nn, pr, uv = n[live], pp[live], uu[live]
        pmf = np.exp(nn * np.log1p(-pr))
        under = pmf < 1e-250  # subnormal or zero: the recursion would lose precision
        if under.any():
            out[live[under]] = stats.binom.ppf(uv[under], nn[under], pr[under]).astype(np.int64)
            keep = ~under
            live, nn, pr, uv, pmf = live[keep], nn[keep], pr[keep], uv[keep], pmf[keep]
        cdf = pmf.copy()
        ratio = pr / (1.0 - pr)
        k = 0
        go = uv > cdf
        live, nn, ratio, uv, pmf, cdf = live[go], nn[go], ratio[go], uv[go], pmf[go], cdf[go]
        while live.size:
            k += 1
            pmf = pmf * ((nn - k + 1) / k) * ratio
            cdf = cdf + pmf
            done = (uv <= cdf) | (k >= nn)
            out[live[done]] = k
            go = ~done
            live, nn, ratio, uv, pmf, cdf = live[go], nn[go], ratio[go], uv[go], pmf[go], cdf[go]
    out = np.where(flip, n - out, out)
    return out.reshape(shape)


# ----------------------------------------------------------------------------- base population


@dataclass(frozen=True)
class ParametricCounts:
    """Count-level population of ADR-07 Amendment 1, G1 to G6."""

    inviews: np.ndarray
    clicks: np.ndarray
    old_inviews: np.ndarray
    old_clicks: np.ndarray
    decile: np.ndarray


def parametric_uniforms(settings: Mapping[str, Any], seed: int) -> np.ndarray:
    """Fixed independent uniforms for G11 common random numbers."""
    return np.random.default_rng(seed).random((7, int(settings["population_users"])))


def prepare_parametric_draws(
    settings: Mapping[str, Any],
    seed: int,
    *,
    r: float | None = None,
    depth_off: bool = False,
    uniforms: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Cache G1 to G3 and G4's normal draw, independently of theta (G11)."""
    if uniforms is None:
        uniforms = parametric_uniforms(settings, seed)
    mu, sd = float(settings["mean_inviews"]), float(settings["sd_inviews"])
    minimum = int(settings["minimum_inviews"])
    dispersion = (mu - minimum) ** 2 / (sd**2 - (mu - minimum)) if r is None else r
    m = minimum + stats.nbinom.ppf(
        uniforms[0], dispersion, dispersion / (dispersion + mu - minimum)
    ).astype(np.int64)
    rank = np.empty(len(m), dtype=np.int64)
    rank[np.argsort(m, kind="stable")] = np.arange(len(m))
    decile = 10 * rank // len(m)
    share = np.asarray(settings["old_shares"])[decile]
    concentration = float(settings["old_share_concentration"])
    pi = (
        np.full(len(m), 1 / 3)
        if depth_off
        else stats.beta.ppf(uniforms[1], concentration * share, concentration * (1 - share))
    )
    return {
        "m": m,
        "old": binom_icdf(uniforms[2], m, pi),
        "decile": decile,
        "depth": np.log(m) - np.log(m).mean(),
        "normal": stats.norm.ppf(uniforms[3]),
        "uniforms": uniforms,
    }


def parametric_counts(
    settings: Mapping[str, Any],
    *,
    seed: int | None = None,
    uniforms: np.ndarray | None = None,
    theta: Any = None,
    sigma: float | None = None,
    r: float | None = None,
    depth_off: bool = False,
    prepared: Mapping[str, np.ndarray] | None = None,
) -> ParametricCounts:
    """G1 to G6, with G9 sweep overrides and G10 depth off."""
    from scipy.special import expit

    if prepared is None:
        prepared = prepare_parametric_draws(
            settings,
            int(settings["population_seed"] if seed is None else seed),
            r=r,
            depth_off=depth_off,
            uniforms=uniforms,
        )
    uniforms = prepared["uniforms"]
    a, b, sig, gamma = (
        [settings["theta"][name] for name in ("a", "b", "sigma", "gamma")]
        if theta is None
        else theta
    )
    if sigma is not None:
        sig = sigma
    m, old, decile = prepared["m"], prepared["old"], prepared["decile"]
    if depth_off:
        b, sig = 0.0, 0.0
    eta = a + b * prepared["depth"] + sig * prepared["normal"]
    c_other = binom_icdf(uniforms[4], m - old, expit(eta))
    c_old = binom_icdf(uniforms[5], old, expit(eta + gamma))
    zero = c_other + c_old == 0
    added_old = zero & (uniforms[6] < old / m)
    c_old = c_old + added_old
    c_other = c_other + (zero & ~added_old)
    return ParametricCounts(m, c_other + c_old, old, c_old, decile)


def parametric_statistics(counts: ParametricCounts, *, splits: int, seed: int) -> dict[str, float]:
    """G11/G12 targets, using package delta and naive standard errors."""
    if splits < 1:
        raise ValueError(f"splits must be at least 1, got {splits}")
    from abforensics.metrics.ratio import naive_event_rate, ratio_of_sums_arrays

    c, m = counts.clicks, counts.inviews
    rng = np.random.default_rng(seed)
    ratios = []
    for _ in range(splits):
        arm = rng.random(len(m)) < 0.5
        ct, mt, cc, mc = c[arm], m[arm], c[~arm], m[~arm]
        delta = ratio_of_sums_arrays(ct, mt, cc, mc, estimand="E2", method="delta")
        naive = naive_event_rate(int(ct.sum()), int(mt.sum()), int(cc.sum()), int(mc.sum()))
        ratios.append(delta.standard_error / naive.standard_error)
    return {
        "E2": float(c.sum() / m.sum()),
        "E3": float(np.mean(c / m)),
        "old_rate": float(counts.old_clicks.sum() / counts.old_inviews.sum()),
        "se_ratio": float(np.median(ratios)),
        "old_share": float(counts.old_inviews.sum() / m.sum()),
    }


def parametric_gate(values: Any, target: float, tolerance: float) -> dict[str, Any]:
    """G12: uncertainty of a median across independent populations."""
    values = np.asarray(values, dtype=float)
    median = float(np.median(values))
    se = float(np.std(values, ddof=1) / np.sqrt(len(values)) * 1.2533)
    used = abs(median - target) + 1.96 * se
    return {
        "median": median,
        "standard_error": se,
        "used": used,
        "target": target,
        "tolerance": tolerance,
        "pass": bool(used <= tolerance),
    }


def parametric_frames(counts: ParametricCounts, *, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """G7's placeholder layout, preserving all count-level sufficient statistics."""
    rng = np.random.default_rng(seed)
    m, old, co, c = counts.inviews, counts.old_inviews, counts.old_clicks, counts.clicks
    n = len(m)
    offsets = np.r_[0, np.cumsum(m)]
    user = np.repeat(np.arange(n), m)
    position = np.arange(int(m.sum())) - offsets[user]
    is_old = position < old[user]
    click = np.where(is_old, position < co[user], position - old[user] < c[user] - co[user])
    permutation = np.lexsort((rng.random(len(user)), user))
    is_old, click = is_old[permutation], click[permutation]
    cut = 9.2
    age = np.where(is_old, cut + rng.exponential(8, len(user)), rng.uniform(0, cut, len(user)))
    first = pd.Timestamp("2023-05-18").to_datetime64() + (
        rng.uniform(0, 7 * 24 * 60 * 60, n) * 1e9
    ).astype("timedelta64[ns]")
    page = position // 7
    impression_time = first[user] + (page * 30 * 60).astype("timedelta64[s]")
    page_offsets = np.r_[0, np.cumsum((m + 6) // 7)]
    iv = pd.DataFrame(
        {
            "user_id": user,
            "impression_id": page_offsets[user] + page,
            "impression_time": impression_time,
            "position": position % 7,
            "article_id": rng.integers(1, 5001, len(user)),
            "age_hours": age,
            "click": click,
        }
    )
    users = pd.DataFrame(
        {
            "user_id": np.arange(n),
            "device_type": rng.integers(1, 4, n),
            "is_subscriber": rng.random(n) < 0.1,
            "pre_period_clicks_21d": rng.poisson(20, n),
        }
    )
    return iv, users


@dataclass(frozen=True)
class RankingBase:
    """The finite population: per-user sufficient statistics and the in-views behind them.

    Per-user arrays are aligned on population index 0..N-1. In-view arrays are sorted by user,
    then impression time, impression id and list position, and sliced by ``offsets``.
    """

    user_id: np.ndarray
    inviews: np.ndarray  # m
    clicks: np.ndarray  # c
    old_inviews: np.ndarray  # m_old
    old_candidates_clicked: np.ndarray  # c_oc: old-tier, clicked, not the last in-view
    old_candidates_unclicked: np.ndarray  # u_oc
    assigned_at: np.ndarray  # first logged page view (datetime64[ns])
    device_type: np.ndarray
    is_subscriber: np.ndarray
    pre_period_clicks_21d: np.ndarray
    offsets: np.ndarray
    iv_article_id: np.ndarray
    iv_time: np.ndarray
    iv_age_hours: np.ndarray
    iv_click: np.ndarray
    iv_old: np.ndarray
    iv_candidate: np.ndarray
    tier_cut_hours: float
    summary: Mapping[str, Any]
    leaked_old_inviews: np.ndarray | None = None
    leaked_candidates_clicked: np.ndarray | None = None
    leaked_candidates_unclicked: np.ndarray | None = None

    def with_tier(self, tier: str) -> RankingBase:
        """The same population with the low-quality tier set to "age" (default) or "leaked"."""
        if tier == "age":
            return self
        if tier != "leaked":
            raise ValueError(f"tier must be 'age' or 'leaked'; got {tier!r}")
        if self.leaked_old_inviews is None:
            raise ValueError("this base has no leaked tier")
        return dataclasses.replace(
            self,
            old_inviews=self.leaked_old_inviews,
            old_candidates_clicked=self.leaked_candidates_clicked,
            old_candidates_unclicked=self.leaked_candidates_unclicked,
        )

    @property
    def n_users(self) -> int:
        return int(self.user_id.size)


def build_base(
    inviews: pd.DataFrame,
    users: pd.DataFrame,
    tier_cut_hours: float | None = None,
    summary: Mapping[str, Any] | None = None,
) -> RankingBase:
    """Build the population from one row per in-view and one row per user.

    ``inviews`` needs user_id, impression_id, impression_time, position, article_id,
    age_hours (NaN where the article's published time is unknown) and click; ``users`` needs
    user_id, device_type, is_subscriber and pre_period_clicks_21d. The old tier is age at or
    above the in-views' 2/3 age quantile unless ``tier_cut_hours`` is given; an in-view with
    unknown age is not old. The user's last in-view (latest page view by time, then impression
    id, then the largest list position) is never a suppression candidate.
    """
    iv = inviews.sort_values(
        ["user_id", "impression_time", "impression_id", "position"], kind="stable"
    ).reset_index(drop=True)
    age = iv["age_hours"].to_numpy(dtype=np.float64)
    known = ~np.isnan(age)
    cut = (
        float(np.quantile(age[known], 2.0 / 3.0))
        if tier_cut_hours is None
        else float(tier_cut_hours)
    )
    old = known & (age >= cut)
    uid = iv["user_id"].to_numpy(dtype=np.int64)
    user_ids, starts, m = np.unique(uid, return_index=True, return_counts=True)
    offsets = np.append(starts, uid.size).astype(np.int64)
    last = np.zeros(uid.size, dtype=bool)
    last[offsets[1:] - 1] = True
    click = iv["click"].to_numpy(dtype=bool)
    cand = old & ~last
    leaked = iv["leaked_low"].to_numpy(dtype=bool) if "leaked_low" in iv.columns else old
    leaked_cand = leaked & ~last
    code = np.repeat(np.arange(user_ids.size), m)

    def per_user(mask: np.ndarray) -> np.ndarray:
        return np.bincount(code, weights=mask, minlength=user_ids.size).astype(np.int64)

    u = users.set_index("user_id").reindex(user_ids)
    if u.isna().any().any():
        raise ValueError("users table lacks a row for some user with in-views")
    first_time = iv["impression_time"].to_numpy()[starts]
    info = {
        "users": int(user_ids.size),
        "inviews": int(uid.size),
        "clicks": int(click.sum()),
        "clicks_per_user": float(click.sum() / user_ids.size),
        "ctr_ratio_of_sums": float(click.sum() / uid.size),
        "user_rate_mean": float(np.mean(per_user(click) / m)),
        "old_tier_share": float(old.mean()),
        "leaked_old_share": float(leaked.mean()),
        "unknown_age_share": float((~known).mean()),
        "tier_cut_hours": cut,
        **dict(summary or {}),
    }
    return RankingBase(
        user_id=user_ids,
        inviews=m.astype(np.int64),
        clicks=per_user(click),
        old_inviews=per_user(old),
        old_candidates_clicked=per_user(cand & click),
        old_candidates_unclicked=per_user(cand & ~click),
        assigned_at=first_time,
        device_type=u["device_type"].to_numpy(),
        is_subscriber=u["is_subscriber"].to_numpy(dtype=bool),
        pre_period_clicks_21d=u["pre_period_clicks_21d"].to_numpy(dtype=np.int64),
        offsets=offsets,
        iv_article_id=iv["article_id"].to_numpy(dtype=np.int64),
        iv_time=iv["impression_time"].to_numpy(),
        iv_age_hours=age.astype(np.float32),
        iv_click=click,
        iv_old=old,
        iv_candidate=cand,
        tier_cut_hours=cut,
        summary=info,
        leaked_old_inviews=per_user(leaked),
        leaked_candidates_clicked=per_user(leaked_cand & click),
        leaked_candidates_unclicked=per_user(leaked_cand & ~click),
    )


def make_parametric_base(
    settings: Mapping[str, Any],
    *,
    r: float | None = None,
    sigma: float | None = None,
    depth_off: bool = False,
) -> RankingBase:
    """G7/G14: build the simulated population and verify its count-level round trip."""
    seed = int(settings["population_seed"])
    counts = parametric_counts(settings, r=r, sigma=sigma, depth_off=depth_off)
    inviews, users = parametric_frames(counts, seed=seed)
    base = build_base(
        inviews,
        users,
        tier_cut_hours=9.2,
        summary={"source": "simulated", "population_seed": seed, "layout": "placeholder"},
    )
    old_clicks = np.add.reduceat(base.iv_old & base.iv_click, base.offsets[:-1])
    for actual, expected in (
        (base.inviews, counts.inviews),
        (base.clicks, counts.clicks),
        (base.old_inviews, counts.old_inviews),
        (old_clicks, counts.old_clicks),
    ):
        if not np.array_equal(actual, expected):
            raise ValueError("G14: generated frame did not preserve population counts")
    return dataclasses.replace(
        base,
        leaked_old_inviews=None,
        leaked_candidates_clicked=None,
        leaked_candidates_unclicked=None,
    )


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_base(raw_dir: Path | str) -> RankingBase:
    """Read the EB-NeRD small ``train`` split and build the population (spec section 6).

    The click of an in-view is its article appearing in the page view's clicked list; clicked
    articles absent from the in-view list are ignored, as in the A18 mechanism check.
    """
    raw = Path(raw_dir)
    files = {
        "behaviors": raw / "train" / "behaviors.parquet",
        "history": raw / "train" / "history.parquet",
        "articles": raw / "articles.parquet",
    }
    for name, f in files.items():
        if not f.exists():
            raise FileNotFoundError(f"EB-NeRD {name} file not found at {f}; see the case README")
    beh = pd.read_parquet(
        files["behaviors"],
        columns=[
            "impression_id",
            "impression_time",
            "article_ids_inview",
            "article_ids_clicked",
            "user_id",
            "device_type",
            "is_subscriber",
        ],
    )
    art = pd.read_parquet(
        files["articles"],
        columns=["article_id", "published_time", "total_inviews", "total_pageviews"],
    )
    hist = pd.read_parquet(files["history"], columns=["user_id", "article_id_fixed"])
    lengths = beh["article_ids_inview"].map(len).to_numpy()
    row = np.repeat(np.arange(len(beh)), lengths)
    iv = pd.DataFrame(
        {
            "row": row,
            "user_id": beh["user_id"].to_numpy()[row],
            "impression_id": beh["impression_id"].to_numpy()[row],
            "impression_time": beh["impression_time"].to_numpy()[row],
            "position": np.concatenate([np.arange(k) for k in lengths])
            if len(lengths)
            else np.zeros(0, int),
            "article_id": np.concatenate(beh["article_ids_inview"].to_numpy()).astype(np.int64),
        }
    )
    ck = (
        beh[["article_ids_clicked"]]
        .assign(row=np.arange(len(beh)))
        .explode("article_ids_clicked")
        .dropna()
    )
    ck = pd.DataFrame(
        {
            "row": ck["row"].to_numpy(dtype=np.int64),
            "article_id": ck["article_ids_clicked"].to_numpy(dtype=np.int64),
        }
    ).drop_duplicates()
    ck["click"] = True
    iv = iv.merge(ck, on=["row", "article_id"], how="left")
    iv["click"] = iv["click"].eq(True)
    iv = iv.merge(art, on="article_id", how="left", validate="many_to_one")
    iv["age_hours"] = (iv["impression_time"] - iv["published_time"]).dt.total_seconds() / 3600.0
    ratio = (iv["total_pageviews"] / iv["total_inviews"]).where(iv["total_inviews"] > 0)
    has = ratio.notna().to_numpy()
    age = iv["age_hours"].to_numpy(dtype=np.float64)
    known = ~np.isnan(age)
    age_cut = float(np.quantile(age[known], 2.0 / 3.0))
    ratio_cut = float(np.quantile(ratio.to_numpy()[has], 1.0 / 3.0))
    iv["leaked_low"] = np.where(has, ratio.to_numpy() <= ratio_cut, known & (age >= age_cut))
    first = beh.sort_values(["user_id", "impression_time", "impression_id"]).drop_duplicates(
        "user_id"
    )
    pre = hist.assign(n=hist["article_id_fixed"].map(len)).groupby("user_id")["n"].sum()
    users = pd.DataFrame(
        {
            "user_id": first["user_id"].to_numpy(),
            "device_type": first["device_type"].to_numpy(dtype=np.int64),
            "is_subscriber": first["is_subscriber"].to_numpy(dtype=bool),
        }
    )
    users["pre_period_clicks_21d"] = users["user_id"].map(pre).fillna(0).astype(np.int64)
    summary = {
        "source": "EB-NeRD small, train split",
        "raw_md5": {k: _md5(f) for k, f in files.items()},
        "leaked_ratio_cut": ratio_cut,
        "without_totals_share": float((~has).mean()),
    }
    return build_base(
        iv.drop(columns=["row", "published_time", "total_inviews", "total_pageviews"]),
        users,
        summary=summary,
    )


def make_synthetic_base(
    n_users: int = 300, seed: int = 1, mean_inviews: float = 40.0
) -> RankingBase:
    """A small simulated population with the base's structure, for tests (never for figures)."""
    rng = np.random.default_rng(seed)
    m = 1 + rng.negative_binomial(0.8, 0.8 / (0.8 + mean_inviews), n_users)
    rate = np.clip(rng.beta(2.0, 18.0, n_users), 0.01, 0.95)
    uid = np.repeat(np.arange(1000, 1000 + n_users), m)
    n = int(m.sum())
    views = np.concatenate([np.arange(k) // 7 for k in m])
    pos = np.concatenate([np.arange(k) % 7 for k in m])
    start = pd.Timestamp("2023-05-18") + pd.to_timedelta(rng.random(n_users) * 72, "h")
    t = np.repeat(start.to_numpy(), m) + pd.to_timedelta(views * 30, "min").to_numpy()
    age = rng.exponential(8.0, n)
    age[rng.random(n) < 0.02] = np.nan
    p_click = np.repeat(rate, m) * np.where(age > 9.0, 0.5, 1.2)
    click = rng.random(n) < np.clip(p_click, 0, 0.99)
    first = np.r_[0, np.cumsum(m)[:-1]]
    click[first] = True  # every user clicks at least once, as on the base
    iv = pd.DataFrame(
        {
            "user_id": uid,
            "impression_id": uid * 1000 + views,
            "impression_time": t,
            "position": pos,
            "article_id": rng.integers(1, 5000, n),
            "age_hours": age,
            "click": click,
        }
    )
    users = pd.DataFrame(
        {
            "user_id": np.arange(1000, 1000 + n_users),
            "device_type": rng.integers(1, 4, n_users),
            "is_subscriber": rng.random(n_users) < 0.1,
            "pre_period_clicks_21d": rng.poisson(20, n_users),
        }
    )
    flip = rng.random(n) < 0.2
    iv["leaked_low"] = np.where(np.isnan(age), False, age > 9.0) ^ flip
    return build_base(iv, users, summary={"source": "synthetic (tests only)"})


# ----------------------------------------------------------------------------- parameters


@dataclass(frozen=True)
class RankingParameters:
    delta: float
    tau: float
    feedback_rate: float = 0.005
    low_quality_multiplier: float = 3.0
    heterogeneity_shape: float = 2.0
    population_seed: int = 20261001


def _num(name: str, value: Any, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | np.number):
        raise ValueError(f"{name} must be a number; got {value!r}")
    v = float(value)
    if not lo <= v <= hi:
        raise ValueError(f"{name} must lie in [{lo}, {hi}]; got {value!r}")
    return v


SCENARIO_EFFECTS = {"A": (False, False), "B": (True, False), "C": (False, True), "D": (True, True)}


def parameters_from_config(config: Mapping[str, Any], scenario: str = "D") -> RankingParameters:
    """Read the treatment and guardrail parameters; ``scenario`` switches delta and tau off.

    A: neither; B: suppression only; C: click lift only; D: both (the committed run).
    """
    if scenario not in SCENARIO_EFFECTS:
        raise ValueError(f"scenario must be one of {sorted(SCENARIO_EFFECTS)}; got {scenario!r}")
    tr, gs = config["treatment"], config["guardrail_simulation"]
    if tr.get("never_remove_last_inview") is not True:
        raise ValueError("treatment.never_remove_last_inview must be true (spec section 4, E3)")
    use_delta, use_tau = SCENARIO_EFFECTS[scenario]
    delta = _num("treatment.suppression_delta", tr["suppression_delta"], 0.0, 1.0)
    tau = _num("treatment.click_lift_tau", tr["click_lift_tau"], 0.0, 10.0)
    return RankingParameters(
        delta=delta if use_delta else 0.0,
        tau=tau if use_tau else 0.0,
        feedback_rate=_num(
            "guardrail_simulation.per_impression_rate", gs["per_impression_rate"], 0.0, 1.0
        ),
        low_quality_multiplier=_num(
            "guardrail_simulation.low_quality_multiplier", gs["low_quality_multiplier"], 0.0, 1000.0
        ),
        heterogeneity_shape=_num(
            "guardrail_simulation.heterogeneity_shape", gs["heterogeneity_shape"], 1e-6, 1e6
        ),
        population_seed=int(gs["population_seed"]),
    )


def guardrail_multipliers(base: RankingBase, prm: RankingParameters) -> np.ndarray:
    """g_i ~ Gamma(k, mean 1) per population user, rescaled to an in-view-weighted mean of 1."""
    u = keyed_uniform(prm.population_seed, np.arange(base.n_users), USE["heterogeneity"])
    k = prm.heterogeneity_shape
    g = stats.gamma.ppf(u, k, scale=1.0 / k)
    return g * base.inviews.sum() / float(np.dot(base.inviews, g))


def _feedback_probs(prm: RankingParameters, g: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r = prm.feedback_rate * g
    return np.minimum(1.0, prm.low_quality_multiplier * r), np.minimum(1.0, r)


def _lift_probability(cr: np.ndarray, m1: np.ndarray, tau: float) -> np.ndarray:
    """q = min(1, tau p / (1 - p)) with p = cr / m1; 0 where no unclicked in-view is retained."""
    unclicked = m1 - cr
    q = np.zeros(cr.shape, dtype=np.float64)
    ok = (unclicked > 0) & (tau > 0)
    q[ok] = np.minimum(1.0, tau * cr[ok] / unclicked[ok])
    return q


# ----------------------------------------------------------------------------- fast path


@dataclass(frozen=True)
class Draws:
    """One replicate at the draw level; arrays aligned on draw index."""

    draw_id: np.ndarray
    arm: np.ndarray  # 1 treatment, 0 control
    population_index: np.ndarray
    clicks: np.ndarray
    inviews: np.ndarray
    feedback: np.ndarray
    removed_clicked: np.ndarray
    removed_unclicked: np.ndarray
    lifted: np.ndarray
    feedback_old: np.ndarray
    feedback_other: np.ndarray

    def frame(self) -> pd.DataFrame:
        """Draw-level frame for the estimators: draw_id, arm label, clicks, inviews, feedback."""
        return pd.DataFrame(
            {
                "draw_id": self.draw_id,
                "arm": np.asarray(ARMS)[self.arm],
                "clicks": self.clicks,
                "inviews": self.inviews,
                "feedback": self.feedback,
            }
        )


def simulate_draws(
    base: RankingBase,
    prm: RankingParameters,
    seed: int,
    total_draws: int,
    g: np.ndarray | None = None,
) -> Draws:
    """Draw ``total_draws`` users with replacement, assign each to an arm with probability 1/2
    (arm sizes Binomial(total_draws, 0.5), arms independent), and simulate outcomes."""
    d = np.arange(int(total_draws), dtype=np.int64)
    arm = (keyed_uniform(seed, d, USE["arm"]) < 0.5).astype(np.int8)
    pop = np.minimum(
        (keyed_uniform(seed, d, USE["population"]) * base.n_users).astype(np.int64),
        base.n_users - 1,
    )
    m, c, m_old = base.inviews[pop], base.clicks[pop], base.old_inviews[pop]
    zeros = np.zeros(d.size, dtype=np.int64)
    kc, ku, lift = zeros.copy(), zeros.copy(), zeros.copy()
    t = np.flatnonzero(arm == 1)
    if prm.delta > 0 and t.size:
        kc[t] = binom_icdf(
            keyed_uniform(seed, d[t], USE["remove_clicked"]),
            base.old_candidates_clicked[pop[t]],
            prm.delta,
        )
        ku[t] = binom_icdf(
            keyed_uniform(seed, d[t], USE["remove_unclicked"]),
            base.old_candidates_unclicked[pop[t]],
            prm.delta,
        )
    m1 = m - kc - ku
    cr = c - kc
    if prm.tau > 0 and t.size:
        q = _lift_probability(cr[t], m1[t], prm.tau)
        lift[t] = binom_icdf(keyed_uniform(seed, d[t], USE["lift"]), m1[t] - cr[t], q)
    g = guardrail_multipliers(base, prm) if g is None else g
    p_old, p_other = _feedback_probs(prm, g[pop])
    old_kept = m_old - kc - ku
    f_old = binom_icdf(keyed_uniform(seed, d, USE["feedback_old"]), old_kept, p_old)
    f_other = binom_icdf(keyed_uniform(seed, d, USE["feedback_other"]), m - m_old, p_other)
    return Draws(
        draw_id=d,
        arm=arm,
        population_index=pop,
        clicks=cr + lift,
        inviews=m1,
        feedback=f_old + f_other,
        removed_clicked=kc,
        removed_unclicked=ku,
        lifted=lift,
        feedback_old=f_old,
        feedback_other=f_other,
    )


# ----------------------------------------------------------------------------- truths


def _binom_support(n: int, p: float, tail: float = 1e-13) -> tuple[np.ndarray, np.ndarray]:
    if n == 0 or p == 0.0:
        return np.zeros(1, dtype=np.int64), np.ones(1)
    lo = int(stats.binom.ppf(tail, n, p))
    hi = int(stats.binom.isf(tail, n, p)) + 1
    k = np.arange(max(lo, 0), min(hi, n) + 1)
    w = stats.binom.pmf(k, n, p)
    return k, w / w.sum()


def potential_outcomes(
    base: RankingBase, prm: RankingParameters, g: np.ndarray | None = None
) -> pd.DataFrame:
    """Each population user's expected potential outcomes under control and treatment.

    Exact enumeration over the two removal binomials (``scipy.stats.binom.pmf``, tails beyond
    1e-13 dropped and the weights renormalised), independent of the simulator's sampling code.
    """
    g = guardrail_multipliers(base, prm) if g is None else g
    n = base.n_users
    em1, ec1, er1 = np.empty(n), np.empty(n), np.empty(n)
    for i in range(n):
        m, c = int(base.inviews[i]), int(base.clicks[i])
        kc, wc = _binom_support(int(base.old_candidates_clicked[i]), prm.delta)
        ku, wu = _binom_support(int(base.old_candidates_unclicked[i]), prm.delta)
        m1 = (m - kc[:, None] - ku[None, :]).astype(np.float64)
        cr = np.broadcast_to((c - kc)[:, None], m1.shape).astype(np.float64)
        q = _lift_probability(cr, m1, prm.tau)
        c1 = cr + (m1 - cr) * q
        w = wc[:, None] * wu[None, :]
        em1[i], ec1[i], er1[i] = (w * m1).sum(), (w * c1).sum(), (w * c1 / m1).sum()
    p_old, p_other = _feedback_probs(prm, g)
    removed = prm.delta * (base.old_candidates_clicked + base.old_candidates_unclicked)
    other = base.inviews - base.old_inviews
    return pd.DataFrame(
        {
            "user_id": base.user_id,
            "expected_inviews_control": base.inviews.astype(np.float64),
            "expected_clicks_control": base.clicks.astype(np.float64),
            "expected_user_rate_control": base.clicks / base.inviews,
            "expected_feedback_control": p_old * base.old_inviews + p_other * other,
            "expected_inviews_treatment": em1,
            "expected_clicks_treatment": ec1,
            "expected_user_rate_treatment": er1,
            "expected_feedback_treatment": p_old * (base.old_inviews - removed) + p_other * other,
            "guardrail_multiplier": g,
        }
    )


def scenario_truths(po: pd.DataFrame) -> dict[str, float]:
    """The finite population's true relative lifts (spec sections 4 and 6)."""
    s = po.mean(numeric_only=True)
    return {
        "expected_relative_lift_clicks_per_user": float(
            s.expected_clicks_treatment / s.expected_clicks_control - 1
        ),
        "expected_relative_lift_ctr": float(
            (s.expected_clicks_treatment / s.expected_inviews_treatment)
            / (s.expected_clicks_control / s.expected_inviews_control)
            - 1
        ),
        "expected_relative_lift_user_rate": float(
            s.expected_user_rate_treatment / s.expected_user_rate_control - 1
        ),
        "expected_relative_lift_inviews_per_user": float(
            s.expected_inviews_treatment / s.expected_inviews_control - 1
        ),
        "expected_relative_lift_feedback_per_user": float(
            s.expected_feedback_treatment / s.expected_feedback_control - 1
        ),
        "control_clicks_per_user": float(s.expected_clicks_control),
        "control_ctr": float(s.expected_clicks_control / s.expected_inviews_control),
        "control_user_rate": float(s.expected_user_rate_control),
        "control_feedback_per_inview": float(
            s.expected_feedback_control / s.expected_inviews_control
        ),
    }


# ----------------------------------------------------------------------------- tables


def _rank_within(group: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Rank (0-based) of each row within its group, ordered by ``u``."""
    if group.max() >= 2**23:
        raise ValueError("group must be less than 2**23")
    # The key orders by group, then the top 40 bits of u, with ties broken by row order.
    key = (group.astype(np.uint64) << np.uint64(40)) | (u * 2.0**40).astype(np.uint64)
    order = np.argsort(key, kind="stable")
    g = group[order]
    start = np.r_[0, np.flatnonzero(g[1:] != g[:-1]) + 1]
    first = np.repeat(start, np.diff(np.r_[start, g.size]))
    rank = np.empty(group.size, dtype=np.int64)
    rank[order] = np.arange(group.size) - first
    return rank


@dataclass(frozen=True)
class RankingExperimentData:
    users: pd.DataFrame
    assignments: pd.DataFrame
    impressions: pd.DataFrame
    clicks: pd.DataFrame
    feedback: pd.DataFrame
    telemetry_health: pd.DataFrame
    truth: pd.DataFrame
    manifest: dict[str, Any]
    draws: Draws

    def tables(self) -> dict[str, pd.DataFrame]:
        return {
            "users": self.users,
            "assignments": self.assignments,
            "impressions": self.impressions,
            "clicks": self.clicks,
            "feedback": self.feedback,
            "telemetry_health": self.telemetry_health,
            "truth": self.truth,
        }


def expand_inviews(base: RankingBase, dr: Draws, seed: int) -> pd.DataFrame:
    """One row per (draw, in-view of the drawn user) with retained, click and feedback flags.

    Which in-views are removed, lifted and given feedback is chosen by the smallest keyed
    uniforms within each draw and class, so every per-draw count equals the fast path's.
    """
    pop = dr.population_index
    lengths = base.inviews[pop]
    row_draw = np.repeat(dr.draw_id, lengths)
    pos = np.arange(row_draw.size) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    iv = base.offsets[pop][row_draw - dr.draw_id[0]] + pos
    di = row_draw - dr.draw_id[0]
    treat = dr.arm[di] == 1
    click = base.iv_click[iv].copy()
    cand, old = base.iv_candidate[iv], base.iv_old[iv]
    # removal: clicked and unclicked candidates are separate classes (Kc, Ku)
    cls = np.where(treat & cand, np.where(click, 0, 1), 2)
    rank = _rank_within(di * 3 + cls, keyed_uniform(seed, row_draw, USE["pick_removed"], pos))
    removed = ((cls == 0) & (rank < dr.removed_clicked[di])) | (
        (cls == 1) & (rank < dr.removed_unclicked[di])
    )
    kept = ~removed
    # lift among retained unclicked in-views of treatment draws
    cls = np.where(treat & kept & ~click, 0, 1)
    rank = _rank_within(di * 2 + cls, keyed_uniform(seed, row_draw, USE["pick_lift"], pos))
    lifted = (cls == 0) & (rank < dr.lifted[di])
    click = (click & kept) | lifted
    # feedback among retained in-views, old tier and other separately (both arms)
    cls = np.where(kept, np.where(old, 0, 1), 2)
    rank = _rank_within(di * 3 + cls, keyed_uniform(seed, row_draw, USE["pick_feedback"], pos))
    fb = ((cls == 0) & (rank < dr.feedback_old[di])) | ((cls == 1) & (rank < dr.feedback_other[di]))
    return pd.DataFrame(
        {
            "draw_id": row_draw,
            "position": pos,
            "inview_index": iv,
            "kept": kept,
            "click": click,
            "lifted": lifted,
            "feedback": fb,
        }
    )


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


def simulate_ranking_experiment(
    config: Mapping[str, Any],
    seed: int | None = None,
    *,
    base: RankingBase | None = None,
    repo_root: Path | str | None = None,
) -> RankingExperimentData:
    """Generate the Case 02 tables and manifest for the committed scenario (D).

    ``base`` defaults to ``load_base(repo_root / config['base']['raw_dir'])``. The manifest's
    truths are the finite population's, computed before the draws are simulated.
    """
    if seed is None:
        if config.get("seed") is None:
            raise ValueError(
                "config seed is null: pass a seed (spec section 6 seed selection rule)"
            )
        seed = int(config["seed"])
    design = config["design"]
    if dict(design.get("allocation", {})) != {"control": 0.5, "treatment": 0.5}:
        raise ValueError("design.allocation must be {control: 0.5, treatment: 0.5}")
    total = int(design["total_draws"])
    delay = design["arrival_delay_hours"]
    d_min, d_max = float(delay["min"]), float(delay["max"])
    if base is None:
        base = load_base(Path(repo_root or ".") / config["base"]["raw_dir"])
    prm = parameters_from_config(config, "D")
    g = guardrail_multipliers(base, prm)
    po = potential_outcomes(base, prm, g)
    truths = scenario_truths(po)
    dr = simulate_draws(base, prm, seed, total, g)
    ex = expand_inviews(base, dr, seed)

    pop, arm_lbl = dr.population_index, np.asarray(ARMS)[dr.arm]
    users = pd.DataFrame(
        {
            "draw_id": dr.draw_id,
            "user_id": base.user_id[pop],
            "device_type": base.device_type[pop],
            "is_subscriber": base.is_subscriber[pop],
            "pre_period_clicks_21d": base.pre_period_clicks_21d[pop],
        }
    )
    assignments = pd.DataFrame(
        {"draw_id": dr.draw_id, "arm": arm_lbl, "assigned_at": base.assigned_at[pop]}
    )
    k = ex[ex["kept"].to_numpy()].reset_index(drop=True)
    ivx = k["inview_index"].to_numpy()
    k_time = base.iv_time[ivx]
    impressions = pd.DataFrame(
        {
            "impression_id": np.arange(1, len(k) + 1, dtype=np.int64),
            "draw_id": k["draw_id"].to_numpy(),
            "article_id": base.iv_article_id[ivx],
            "impression_time": k_time,
            "article_age_hours": base.iv_age_hours[ivx],
        }
    )
    ck = k["click"].to_numpy()
    u_delay = keyed_uniform(
        seed, k["draw_id"].to_numpy()[ck], USE["delay"], k["position"].to_numpy()[ck]
    )
    clicks = pd.DataFrame(
        {
            "impression_id": impressions["impression_id"].to_numpy()[ck],
            "draw_id": k["draw_id"].to_numpy()[ck],
            "clicked_at": k_time[ck],
        }
    )
    clicks["received_at"] = clicks["clicked_at"] + pd.to_timedelta(
        d_min + u_delay * (d_max - d_min), "h"
    )
    fb = k["feedback"].to_numpy()
    u_act = keyed_uniform(
        seed, k["draw_id"].to_numpy()[fb], USE["action"], k["position"].to_numpy()[fb]
    )
    feedback = pd.DataFrame(
        {
            "impression_id": impressions["impression_id"].to_numpy()[fb],
            "draw_id": k["draw_id"].to_numpy()[fb],
            "action": np.asarray(FEEDBACK_ACTIONS)[(u_act >= 0.5).astype(int)],
            "feedback_at": k_time[fb],
        }
    )
    day = pd.to_datetime(k_time).normalize()
    arm_code = dr.arm[k["draw_id"].to_numpy()]
    telemetry_health = (
        pd.DataFrame({"day": day, "arm_code": arm_code, "click": ck.astype(int)})
        .groupby(["day", "arm_code"], as_index=False)
        .agg(inviews=("click", "size"), clicks=("click", "sum"))
    )
    telemetry_health.insert(1, "arm", np.asarray(ARMS)[telemetry_health["arm_code"].to_numpy()])
    telemetry_health = telemetry_health.drop(columns="arm_code")
    truth = po.rename(columns={"user_id": "user_id"})

    raw_summary = {k_: v for k_, v in base.summary.items() if k_ != "raw_md5"}
    manifest: dict[str, Any] = {
        "case": "case02",
        "abforensics_version": __version__,
        "seed": int(seed),
        "scenario": "D",
        "parameters": _jsonable(prm.__dict__),
        "config": _jsonable(config),
        **truths,
        "truths_are": (
            "finite-population means of expected potential outcomes, computed before any draw"
        ),
        "base": _jsonable(raw_summary),
        "raw_md5": _jsonable(base.summary.get("raw_md5", {})),
        "realised": {
            "total_draws": total,
            "draws_treatment": int(dr.arm.sum()),
            "draws_control": int(total - dr.arm.sum()),
            "draws_per_population_user": float(total / base.n_users),
        },
        "row_counts": {
            name: int(len(t))
            for name, t in {
                "users": users,
                "assignments": assignments,
                "impressions": impressions,
                "clicks": clicks,
                "feedback": feedback,
                "telemetry_health": telemetry_health,
                "truth": truth,
            }.items()
        },
    }
    return RankingExperimentData(
        users, assignments, impressions, clicks, feedback, telemetry_health, truth, manifest, dr
    )


def write_case_data(
    data: RankingExperimentData, generated_dir: Path, manifest_path: Path
) -> dict[str, Any]:
    """Write the tables (Parquet, under an untracked directory) and the aggregate manifest."""
    generated_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    checksums: dict[str, str] = {}
    for name, frame in data.tables().items():
        frame.to_parquet(generated_dir / f"{name}.parquet", index=False)
        checksums[name] = content_sha256(frame)
    manifest = {
        **data.manifest,
        "files": {k: f"{k}.parquet" for k in checksums},
        "content_sha256": checksums,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest
