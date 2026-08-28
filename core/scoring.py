"""Conviction scoring.

Weights are exactly as specified: 35 smart money, 25 hold duration, 20 capital
depth, 20 supply distribution, with the two hard deductions.

Two things worth knowing before you trust the number:

1. Every component is scored only over wallets that profiled successfully. If
   coverage falls below MIN_COVERAGE_RATIO the score is returned but flagged
   as low confidence. A 78 built on 6 of 20 wallets is not a 78.

2. The weights are an untested prior. scoring.explain() returns the full
   component breakdown, and db.py logs it with forward price. Once you have a
   few hundred scans you can fit these weights against outcomes instead of
   guessing them.
"""
from dataclasses import dataclass, field

import config
from core.bundles import ClusterReport
from core.holders import HolderSet
from core.metadata import TokenMeta
from core.profiler import WalletProfile


@dataclass
class ScoreResult:
    score: float = 0.0
    raw_score: float = 0.0
    verdict: str = "AVOID"
    verdict_icon: str = "\U0001f6d1"
    components: dict[str, float] = field(default_factory=dict)
    deductions: list[tuple[str, float]] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    coverage: float = 0.0
    low_confidence: bool = False


def _ratio(count: int, total: int) -> float:
    return (count / total) if total else 0.0


def _supply_component(top10_pct: float) -> float:
    """Full marks under 18%, linear decay to zero at 25%+."""
    if top10_pct <= 0:
        return 0.0
    if top10_pct < config.TOP10_GOOD_PCT:
        return 1.0
    if top10_pct >= config.TOP10_BAD_PCT * 2:
        return 0.0
    if top10_pct <= config.TOP10_BAD_PCT:
        span = config.TOP10_BAD_PCT - config.TOP10_GOOD_PCT
        return 1.0 - 0.5 * (top10_pct - config.TOP10_GOOD_PCT) / span
    span = config.TOP10_BAD_PCT
    return max(0.0, 0.5 * (1.0 - (top10_pct - config.TOP10_BAD_PCT) / span))


def compute(meta: TokenMeta, hset: HolderSet, profiles: list[WalletProfile],
            clusters: ClusterReport) -> ScoreResult:
    res = ScoreResult()
    scanned = len(profiles)
    usable = [p for p in profiles if p.ok]
    n = len(usable)
    res.coverage = _ratio(n, scanned)
    res.low_confidence = res.coverage < config.MIN_COVERAGE_RATIO

    if n == 0:
        res.metrics = {"scanned": scanned, "usable": 0}
        res.verdict, res.verdict_icon = "NO DATA", "\u2753"
        return res

    # ---------------------------------------------------- components
    smart = [p for p in usable if p.is_smart_money]
    smart_ratio = _ratio(len(smart), n)

    hold_ok = [p for p in usable
               if p.hold_known and p.avg_hold_hours > config.HOLD_DURATION_HOURS]
    hold_ratio = _ratio(len(hold_ok), n)

    depth = [p for p in usable if p.sol_balance > config.CAPITAL_DEPTH_SOL]
    depth_ratio = _ratio(len(depth), n)

    supply_factor = _supply_component(hset.top10_pct)

    res.components = {
        "smart_money": config.W_SMART_MONEY * smart_ratio,
        "hold_duration": config.W_HOLD_DURATION * hold_ratio,
        "capital_depth": config.W_CAPITAL_DEPTH * depth_ratio,
        "supply_distribution": config.W_SUPPLY_DIST * supply_factor,
    }
    base = sum(res.components.values())
    res.raw_score = base

    # ---------------------------------------------------- deductions
    fresh = [p for p in usable if p.is_fresh]
    fresh_ratio = _ratio(len(fresh), n)
    if fresh_ratio > config.FRESH_CLUSTER_RATIO:
        res.deductions.append(
            (f"Fresh wallet cluster ({len(fresh)}/{n} under 48h)",
             config.DEDUCT_FRESH_CLUSTER))

    if clusters.bundle_detected:
        res.deductions.append(
            (f"Shared funding across {clusters.bundled_wallets} holders",
             config.DEDUCT_BUNDLE))

    if meta.mint_authority and config.DEDUCT_MINT_AUTHORITY:
        res.deductions.append(
            ("Mint authority still active (supply can be inflated)",
             config.DEDUCT_MINT_AUTHORITY))

    if meta.freeze_authority and config.DEDUCT_FREEZE_AUTHORITY:
        res.deductions.append(
            ("Freeze authority still active (transfers can be blocked)",
             config.DEDUCT_FREEZE_AUTHORITY))

    snipers = [p for p in usable if p.is_sniper]
    sniper_ratio = _ratio(len(snipers), n)
    if sniper_ratio > config.SNIPER_CLUSTER_RATIO and config.DEDUCT_SNIPER_CLUSTER:
        res.deductions.append(
            (f"Sniper concentration ({len(snipers)}/{n} bought at launch)",
             config.DEDUCT_SNIPER_CLUSTER))

    res.score = max(0.0, min(100.0, base - sum(d for _, d in res.deductions)))

    for floor, label, icon in config.VERDICT_BANDS:
        if res.score >= floor:
            res.verdict, res.verdict_icon = label, icon
            break

    # ---------------------------------------------------- metrics
    with_trades = [p for p in usable if p.closed_trades > 0]
    with_hold = [p for p in usable if p.hold_known]
    gas = [p for p in usable if p.sol_balance > config.LIQUID_GAS_SOL]
    selling = [p for p in usable if p.token_netflow_1h < 0]

    res.metrics = {
        "scanned": scanned,
        "usable": n,
        "smart_count": len(smart),
        "smart_ratio": 100 * smart_ratio,
        "avg_win_rate": (sum(p.win_rate for p in with_trades) / len(with_trades)
                         if with_trades else 0.0),
        "wallets_with_trades": len(with_trades),
        "avg_hold_hours": (sum(p.avg_hold_hours for p in with_hold) / len(with_hold)
                           if with_hold else 0.0),
        "hold_above_threshold": len(hold_ok),
        "median_pnl_sol": _median([p.realized_pnl_sol for p in with_trades]),
        "fresh_count": len(fresh),
        "fresh_ratio": 100 * fresh_ratio,
        "depth_count": len(depth),
        "gas_count": len(gas),
        "gas_ratio": 100 * _ratio(len(gas), n),
        "avg_sol_balance": sum(p.sol_balance for p in usable) / n,
        "top10_pct": hset.top10_pct,
        "lp_pct": hset.lp_supply_pct,
        "burned_pct": hset.burned_pct,
        "sniper_count": len(snipers),
        "bundled_wallets": clusters.bundled_wallets,
        "bundle_groups": len(clusters.bundle_groups),
        "sync_entry_wallets": clusters.sync_entry_wallets,
        "selling_now": len(selling),
        "coverage_pct": 100 * res.coverage,
    }
    return res


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    mid = len(s) // 2
    if len(s) % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def explain(res: ScoreResult) -> str:
    lines = [f"base {res.raw_score:.1f}"]
    for k, v in res.components.items():
        lines.append(f"  {k}: {v:.1f}")
    for label, amt in res.deductions:
        lines.append(f"  -{amt:.0f} {label}")
    lines.append(f"final {res.score:.1f}")
    return "\n".join(lines)
