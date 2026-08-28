"""Telegram report formatting. HTML parse mode."""
from html import escape

import config
from core.scanner import ScanResult


def _money(v: float) -> str:
    if v <= 0:
        return "n/a"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= div:
            return f"${v / div:.2f}{suf}"
    return f"${v:,.0f}"


def _dur(hours: float) -> str:
    if hours <= 0:
        return "n/a"
    if hours < 1:
        return f"{int(hours * 60)}m"
    if hours < 48:
        h = int(hours)
        m = int((hours - h) * 60)
        return f"{h}h {m:02d}m"
    return f"{hours / 24:.1f}d"


def _risk_label(ratio: float, low: float, high: float) -> str:
    if ratio <= low:
        return "Low Risk"
    if ratio <= high:
        return "Watch"
    return "High Risk"


def render(r: ScanResult) -> str:
    m = r.score.metrics
    meta = r.meta
    n = m["usable"]
    scanned = m["scanned"]

    conf = " \u26a0\ufe0f LOW CONFIDENCE" if r.score.low_confidence else ""
    lines = [
        f"\U0001f48e <b>${escape(meta.symbol)}</b>  |  MC: <code>{_money(meta.market_cap)}</code>",
        f"Liq: <code>{_money(meta.liquidity_usd)}</code>  |  "
        f"24h: <code>{meta.price_change_24h:+.1f}%</code>",
        f"Chain: Solana  |  Score: <b>{r.score.score:.0f}/100</b> "
        f"({r.score.verdict_icon} {r.score.verdict}){conf}",
        "",
        f"\U0001f4ca <b>Top Holder Breakdown</b> (Top {scanned} float, {n} profiled)",
        f"\u2022 Smart Money: <code>{m['smart_count']}/{n}</code> "
        f"({m['smart_ratio']:.0f}%)",
        f"\u2022 Avg Win Rate: <code>{m['avg_win_rate']:.1f}%</code> "
        f"(over {m['wallets_with_trades']} traders)",
        f"\u2022 Median Realised PnL: <code>{m['median_pnl_sol']:+.2f} SOL</code>",
        f"\u2022 Avg Hold Time: <code>{_dur(m['avg_hold_hours'])}</code> "
        f"({m['hold_above_threshold']}/{n} over 6h)",
        f"\u2022 Fresh Wallets (&lt;48h): <code>{m['fresh_count']}/{n}</code> "
        f"({_risk_label(m['fresh_ratio'] / 100, 0.15, 0.30)})",
        f"\u2022 Top 10 Concentration: <code>{m['top10_pct']:.1f}%</code>",
        f"\u2022 In LP: <code>{m['lp_pct']:.1f}%</code>  |  "
        f"Burned: <code>{m['burned_pct']:.1f}%</code>",
        "",
        "\u26a0\ufe0f <b>Risk Signals</b>",
    ]

    if m["bundled_wallets"]:
        lines.append(
            f"\u2022 \U0001f6a9 <b>{m['bundled_wallets']} bundled wallets</b> "
            f"across {m['bundle_groups']} funding source(s)")
    else:
        lines.append("\u2022 0 bundled wallets detected")

    if m["sync_entry_wallets"] >= config.BUNDLE_MIN_WALLETS:
        lines.append(
            f"\u2022 \U0001f6a9 {m['sync_entry_wallets']} holders entered within "
            "the same 30s window")

    if m["sniper_count"]:
        lines.append(f"\u2022 {m['sniper_count']}/{n} sniped the launch block")

    lines.append(
        f"\u2022 Liquid Gas: {m['gas_ratio']:.0f}% hold &gt; 1 SOL "
        f"(avg <code>{m['avg_sol_balance']:.2f}</code> SOL)")

    if m["selling_now"]:
        lines.append(
            f"\u2022 \U0001f4c9 {m['selling_now']}/{n} net sellers in the last hour")

    if meta.mint_authority:
        lines.append("\u2022 \U0001f6a9 <b>Mint authority ACTIVE</b> (supply "
                     "can be inflated)")
    if meta.freeze_authority:
        lines.append("\u2022 \U0001f6a9 <b>Freeze authority ACTIVE</b> "
                     "(transfers can be blocked)")
    if not meta.mint_authority and not meta.freeze_authority:
        lines.append("\u2022 \u2705 Mint and freeze authority revoked")

    if r.score.deductions:
        lines.append("")
        lines.append("\U0001f4c9 <b>Deductions applied</b>")
        for label, amt in r.score.deductions:
            lines.append(f"\u2022 <code>-{amt:.0f}</code> {escape(label)}")

    lines.append("")
    lines.append(f"<b>Verdict:</b> {_verdict_text(r)}")

    if r.warnings:
        lines.append("")
        lines.append("<i>" + escape(" ".join(r.warnings)) + "</i>")

    lines.append("")
    lines.append(
        f"<i>Data coverage {m['coverage_pct']:.0f}% | {r.elapsed:.0f}s | "
        f"PnL in SOL over {config.PNL_LOOKBACK_DAYS}d. Heuristic only, "
        f"not financial advice.</i>")

    return "\n".join(lines)


def _verdict_text(r: ScanResult) -> str:
    m = r.score.metrics
    s = r.score.score
    if m["bundled_wallets"]:
        return ("\U0001f6d1 Shared funding among top holders. The float is not "
                "independently held.")
    if r.meta.mint_authority:
        return ("\U0001f6d1 Mint authority is live. Holder quality is irrelevant "
                "until that is revoked.")
    if s >= 75:
        return ("\u2705 Favourable distribution with experienced traders holding "
                "float.")
    if s >= 55:
        return ("\U0001f7e1 Mixed. Some proven holders but the profile is not "
                "clean. Size accordingly.")
    if s >= 35:
        return ("\U0001f7e0 Speculative. Float is held by unproven or short-term "
                "wallets.")
    return "\U0001f6d1 Poor holder quality across the board."


def render_holders(r: ScanResult, limit: int = 20) -> str:
    lines = [f"\U0001f465 <b>${escape(r.meta.symbol)} holder detail</b>", ""]
    for i, h in enumerate(r.hset.holders[:limit], 1):
        p = next((x for x in r.profiles if x.address == h.owner), None)
        short = f"{h.owner[:4]}..{h.owner[-4:]}"
        if not p or not p.ok:
            lines.append(f"{i}. <code>{short}</code> {h.supply_pct:.2f}% "
                         f"| no data")
            continue
        tags = []
        if p.is_smart_money:
            tags.append("\U0001f9e0")
        if p.is_fresh:
            tags.append("\U0001f423")
        if p.is_sniper:
            tags.append("\U0001f3af")
        if p.sol_balance > config.CAPITAL_DEPTH_SOL:
            tags.append("\U0001f4b0")
        age = f"{p.age_hours / 24:.0f}d" if p.age_hours else "?"
        if not p.age_known and p.age_hours:
            age = f">{p.age_hours / 24:.0f}d"
        lines.append(
            f"{i}. <code>{short}</code> {h.supply_pct:.2f}% "
            f"| WR {p.win_rate:.0f}% ({p.closed_trades}t) "
            f"| {p.realized_pnl_sol:+.1f} SOL "
            f"| {p.sol_balance:.1f}\u25ce | {age} {''.join(tags)}")
    lines.append("")
    lines.append("<i>\U0001f9e0 smart money  \U0001f423 fresh  \U0001f3af sniper  "
                 "\U0001f4b0 deep pockets</i>")
    return "\n".join(lines)
