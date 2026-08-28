"""Wallet profiler.

No paid PnL vendor. Win rate and realised PnL are reconstructed from Helius
parsed transactions and denominated in SOL, which removes the need for
historical USD prices entirely.

Method, per wallet:
  1. Pull the last N pages of parsed transactions (100 each).
  2. Reduce each transaction to a net delta: SOL/WSOL change, stablecoin
     change (converted at spot), and per-mint token change.
  3. Replay chronologically into a per-mint position ledger.
  4. A position closes when the balance drops under DUST_FRACTION of its peak.
     realised = SOL proceeds - SOL cost. Positive is a win.
  5. Hold duration = last sell timestamp - first buy timestamp.

Positions still open are excluded from win rate. That is deliberate: unrealised
gains are not evidence of skill.

Cached for 6h keyed on wallet address. The cache stores compact deltas, not raw
transactions, so memory stays flat. Native balance is deliberately NOT cached,
it is fetched fresh and batched by the scanner.
"""
import logging
import time
from dataclasses import dataclass, field

import config
from core.cache import AsyncCache
from core.rpc import client

log = logging.getLogger(__name__)

_history_cache = AsyncCache(config.CACHE_MAXSIZE, config.WALLET_CACHE_TTL, "wallet")

MIN_TRADE_SOL = 0.002  # below this a token movement is a transfer, not a trade


@dataclass
class TxDelta:
    ts: int
    sig: str
    quote_sol: float                       # + received SOL, - spent SOL
    tokens: dict[str, float] = field(default_factory=dict)
    inbound_from: str | None = None        # funder on a pure SOL inbound
    inbound_sol: float = 0.0
    slot: int = 0


@dataclass
class WalletHistory:
    address: str
    deltas: list[TxDelta] = field(default_factory=list)
    oldest_ts: int = 0
    age_known: bool = False
    total_sigs: int = 0
    funder: str | None = None
    funded_ts: int = 0
    ok: bool = False


@dataclass
class WalletProfile:
    address: str
    ok: bool = False
    sol_balance: float = 0.0
    age_hours: float = 0.0
    age_known: bool = False
    is_fresh: bool = False

    closed_trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    realized_pnl_sol: float = 0.0
    avg_hold_hours: float = 0.0
    hold_known: bool = False

    is_smart_money: bool = False
    has_depth: bool = False
    has_gas: bool = False

    funder: str | None = None
    funded_ts: int = 0

    token_entry_ts: int = 0
    is_sniper: bool = False
    token_netflow_1h: float = 0.0
    token_position_hours: float = 0.0

    note: str = ""


# --------------------------------------------------------------- extraction
def _extract(tx: dict, wallet: str, sol_price: float) -> TxDelta | None:
    ts = int(tx.get("timestamp") or 0)
    if not ts:
        return None
    sig = tx.get("signature") or ""
    quote = 0.0
    inbound_from = None
    inbound_sol = 0.0

    for nt in tx.get("nativeTransfers") or []:
        try:
            amt = float(nt.get("amount") or 0) / config.LAMPORTS
        except (TypeError, ValueError):
            continue
        if nt.get("toUserAccount") == wallet:
            quote += amt
            frm = nt.get("fromUserAccount")
            if frm and frm != wallet and amt > inbound_sol:
                inbound_from, inbound_sol = frm, amt
        if nt.get("fromUserAccount") == wallet:
            quote -= amt

    tokens: dict[str, float] = {}
    for tt in tx.get("tokenTransfers") or []:
        mint = tt.get("mint")
        try:
            amt = float(tt.get("tokenAmount") or 0)
        except (TypeError, ValueError):
            continue
        if not mint or amt == 0:
            continue
        if tt.get("toUserAccount") == wallet:
            tokens[mint] = tokens.get(mint, 0.0) + amt
        if tt.get("fromUserAccount") == wallet:
            tokens[mint] = tokens.get(mint, 0.0) - amt

    # Wrapped SOL is SOL.
    if config.SOL_MINT in tokens:
        quote += tokens.pop(config.SOL_MINT)

    # Stablecoin legs become the quote side, converted at spot. Imperfect for
    # old trades but the alternative is a paid historical price feed.
    if sol_price > 0:
        for stable in list(tokens):
            if stable in config.STABLES:
                usd = tokens.pop(stable) * config.STABLES[stable]
                quote += usd / sol_price

    if tx.get("feePayer") == wallet:
        try:
            quote -= float(tx.get("fee") or 0) / config.LAMPORTS
        except (TypeError, ValueError):
            pass

    tokens = {m: v for m, v in tokens.items() if abs(v) > 0}
    return TxDelta(ts=ts, sig=sig, quote_sol=quote, tokens=tokens,
                   inbound_from=inbound_from, inbound_sol=inbound_sol,
                   slot=int(tx.get("slot") or 0))


async def _load_history(address: str, sol_price: float) -> WalletHistory:
    hist = WalletHistory(address=address)

    # ---- age via signature pagination, capped
    sigs: list[dict] = []
    before = None
    exhausted = False
    for _ in range(config.AGE_PAGES_PER_WALLET):
        page = await client.get_signatures(address, limit=1000, before=before)
        if not page:
            exhausted = True
            break
        sigs.extend(page)
        if len(page) < 1000:
            exhausted = True
            break
        before = page[-1].get("signature")
    hist.total_sigs = len(sigs)
    if sigs:
        oldest = sigs[-1]
        hist.oldest_ts = int(oldest.get("blockTime") or 0)
        hist.age_known = exhausted
    if not sigs:
        hist.ok = False
        return hist

    # ---- recent parsed transactions
    deltas: list[TxDelta] = []
    before_sig = None
    for _ in range(config.TX_PAGES_PER_WALLET):
        txs = await client.parsed_transactions(address, before=before_sig, limit=100)
        if not txs:
            break
        for tx in txs:
            d = _extract(tx, address, sol_price)
            if d:
                deltas.append(d)
        if len(txs) < 100:
            break
        before_sig = txs[-1].get("signature")

    # ---- funding source: read the oldest slice of history directly
    if hist.total_sigs <= config.TX_PAGES_PER_WALLET * 100 and deltas:
        tail = deltas
    elif len(sigs) > 1:
        # before=<second oldest> returns the oldest slice, which contains the
        # funding transaction. One extra call buys accurate bundle detection.
        tail_txs = await client.parsed_transactions(
            address, before=sigs[-2].get("signature"), limit=25)
        tail = [d for d in (_extract(t, address, sol_price) for t in tail_txs) if d]
    else:
        tail = deltas

    for d in sorted(tail, key=lambda x: x.ts):
        if d.inbound_from and d.inbound_sol > 0.001:
            hist.funder = d.inbound_from
            hist.funded_ts = d.ts
            break

    hist.deltas = sorted(deltas, key=lambda d: d.ts)
    hist.ok = True
    return hist


# ------------------------------------------------------------------ ledger
def _replay(deltas: list[TxDelta], cutoff_ts: int) -> tuple[int, int, float, float]:
    """Return (closed_trades, wins, realized_pnl_sol, avg_hold_hours)."""
    positions: dict[str, dict] = {}
    closed = 0
    wins = 0
    pnl = 0.0
    durations: list[float] = []

    for d in deltas:
        if not d.tokens:
            continue
        # A trade needs a meaningful quote leg. Otherwise it is a transfer.
        if abs(d.quote_sol) < MIN_TRADE_SOL:
            continue
        for mint, qty in d.tokens.items():
            if mint in config.STABLES or mint == config.SOL_MINT:
                continue
            pos = positions.setdefault(
                mint, {"qty": 0.0, "peak": 0.0, "cost": 0.0,
                       "proceeds": 0.0, "first_buy": 0, "last": 0})
            if qty > 0 and d.quote_sol < 0:            # buy
                pos["qty"] += qty
                pos["cost"] += -d.quote_sol
                pos["peak"] = max(pos["peak"], pos["qty"])
                if not pos["first_buy"]:
                    pos["first_buy"] = d.ts
                pos["last"] = d.ts
            elif qty < 0 and d.quote_sol > 0:          # sell
                if pos["qty"] <= 0:
                    continue                            # no known cost basis
                pos["qty"] += qty
                pos["proceeds"] += d.quote_sol
                pos["last"] = d.ts
                if pos["qty"] <= pos["peak"] * config.DUST_FRACTION:
                    if pos["first_buy"] and pos["first_buy"] >= cutoff_ts:
                        realized = pos["proceeds"] - pos["cost"]
                        closed += 1
                        pnl += realized
                        if realized > 0:
                            wins += 1
                        dur = (pos["last"] - pos["first_buy"]) / 3600.0
                        if dur >= 0:
                            durations.append(dur)
                    positions[mint] = {"qty": 0.0, "peak": 0.0, "cost": 0.0,
                                       "proceeds": 0.0, "first_buy": 0, "last": 0}

    avg_hold = sum(durations) / len(durations) if durations else 0.0
    return closed, wins, pnl, avg_hold


async def profile_wallet(address: str, mint: str, pair_created_at: int,
                         sol_price: float) -> WalletProfile:
    p = WalletProfile(address=address)
    try:
        hist = await _history_cache.get_or_compute(
            address, lambda: _load_history(address, sol_price))
    except Exception as exc:  # noqa: BLE001
        log.warning("profile failed for %s: %s", address[:8], exc)
        p.note = "profile failed"
        return p

    if not hist.ok:
        p.note = "no history"
        return p

    now = int(time.time())
    cutoff = now - config.PNL_LOOKBACK_DAYS * 86400

    if hist.oldest_ts:
        p.age_hours = (now - hist.oldest_ts) / 3600.0
        p.age_known = hist.age_known
        # Unknown age means we hit the pagination cap, so the wallet is old.
        p.is_fresh = hist.age_known and p.age_hours < config.FRESH_WALLET_HOURS

    closed, wins, pnl, avg_hold = _replay(hist.deltas, cutoff)
    p.closed_trades = closed
    p.wins = wins
    p.realized_pnl_sol = pnl
    p.win_rate = (100.0 * wins / closed) if closed else 0.0
    p.avg_hold_hours = avg_hold
    p.hold_known = closed > 0

    p.is_smart_money = (closed >= config.SMART_MONEY_MIN_TRADES
                        and p.win_rate > config.SMART_MONEY_WIN_RATE)

    p.funder = hist.funder
    p.funded_ts = hist.funded_ts

    # ---- mint specific signals
    first_buy = 0
    netflow = 0.0
    for d in hist.deltas:
        qty = d.tokens.get(mint)
        if qty is None:
            continue
        if qty > 0 and not first_buy:
            first_buy = d.ts
        if d.ts >= now - 3600:
            netflow += qty
    p.token_entry_ts = first_buy
    p.token_netflow_1h = netflow
    if first_buy:
        p.token_position_hours = (now - first_buy) / 3600.0
    if first_buy and pair_created_at:
        p.is_sniper = (first_buy - pair_created_at) <= config.SNIPER_WINDOW_SECONDS

    # If no closed trades exist, fall back to the live position age so a
    # genuine long-term holder is not scored as an unknown.
    if not p.hold_known and p.token_position_hours > 0:
        p.avg_hold_hours = p.token_position_hours
        p.hold_known = True
        p.note = "hold time from open position"

    p.ok = True
    return p


async def fetch_native_balances(addresses: list[str]) -> dict[str, float]:
    """Batched and never cached. Balances move too fast to reuse."""
    if not addresses:
        return {}
    infos = await client.get_multiple_accounts(addresses, encoding="base64")
    out: dict[str, float] = {}
    for addr, info in zip(addresses, infos):
        lamports = (info or {}).get("lamports") or 0
        out[addr] = float(lamports) / config.LAMPORTS
    return out


def cache_stats() -> dict:
    return _history_cache.stats()
