"""Optional Neon Postgres logging.

This exists for one reason: the scoring weights are currently a guess. Logging
every scan with the token price at scan time, then filling in the price at +1h
and +24h, gives you a labelled dataset. After a few hundred scans you can
regress score components against forward return and replace the guessed weights
with fitted ones.

Entirely optional. Without DATABASE_URL the bot runs normally and this module
turns into no-ops.
"""
import asyncio
import logging

import config

log = logging.getLogger(__name__)

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None

_pool = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id              BIGSERIAL PRIMARY KEY,
    scanned_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    mint            TEXT NOT NULL,
    symbol          TEXT,
    chain           TEXT NOT NULL DEFAULT 'solana',
    telegram_user   BIGINT,
    score           NUMERIC(5,2),
    raw_score       NUMERIC(5,2),
    verdict         TEXT,
    coverage_pct    NUMERIC(5,2),
    market_cap      NUMERIC,
    liquidity_usd   NUMERIC,
    price_usd       NUMERIC,
    price_1h        NUMERIC,
    price_24h       NUMERIC,
    components      JSONB,
    deductions      JSONB,
    metrics         JSONB,
    elapsed_sec     NUMERIC(6,2)
);
CREATE INDEX IF NOT EXISTS scans_mint_idx ON scans (mint);
CREATE INDEX IF NOT EXISTS scans_pending_1h_idx
    ON scans (scanned_at) WHERE price_1h IS NULL;

CREATE TABLE IF NOT EXISTS scan_holders (
    id              BIGSERIAL PRIMARY KEY,
    scan_id         BIGINT REFERENCES scans(id) ON DELETE CASCADE,
    rank            INT,
    address         TEXT NOT NULL,
    supply_pct      NUMERIC(8,4),
    sol_balance     NUMERIC(14,4),
    win_rate        NUMERIC(5,2),
    closed_trades   INT,
    realized_pnl_sol NUMERIC(14,4),
    avg_hold_hours  NUMERIC(10,2),
    age_hours       NUMERIC(12,2),
    is_fresh        BOOLEAN,
    is_smart_money  BOOLEAN,
    is_sniper       BOOLEAN,
    funder          TEXT
);
CREATE INDEX IF NOT EXISTS scan_holders_addr_idx ON scan_holders (address);
"""


async def init() -> None:
    global _pool
    if not config.DATABASE_URL or asyncpg is None:
        log.info("DB logging disabled")
        return
    try:
        _pool = await asyncpg.create_pool(
            config.DATABASE_URL, min_size=1, max_size=4, ssl="require")
        async with _pool.acquire() as con:
            await con.execute(SCHEMA)
        log.info("DB logging ready")
    except Exception as exc:  # noqa: BLE001
        log.warning("DB init failed, continuing without logging: %s", exc)
        _pool = None


async def close() -> None:
    if _pool:
        await _pool.close()


async def log_scan(result, telegram_user: int | None) -> int | None:
    if not _pool:
        return None
    import json
    s, meta, m = result.score, result.meta, result.score.metrics
    try:
        async with _pool.acquire() as con:
            scan_id = await con.fetchval(
                """INSERT INTO scans (mint, symbol, telegram_user, score,
                       raw_score, verdict, coverage_pct, market_cap,
                       liquidity_usd, price_usd, components, deductions,
                       metrics, elapsed_sec)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                   RETURNING id""",
                meta.mint, meta.symbol, telegram_user, s.score, s.raw_score,
                s.verdict, m.get("coverage_pct"), meta.market_cap,
                meta.liquidity_usd, meta.price_usd,
                json.dumps(s.components),
                json.dumps([{"label": a, "points": b} for a, b in s.deductions]),
                json.dumps(m), result.elapsed,
            )
            rows = []
            for i, h in enumerate(result.hset.holders, 1):
                p = next((x for x in result.profiles if x.address == h.owner), None)
                rows.append((
                    scan_id, i, h.owner, h.supply_pct,
                    p.sol_balance if p else 0, p.win_rate if p else None,
                    p.closed_trades if p else 0,
                    p.realized_pnl_sol if p else None,
                    p.avg_hold_hours if p else None,
                    p.age_hours if p else None,
                    p.is_fresh if p else None,
                    p.is_smart_money if p else None,
                    p.is_sniper if p else None,
                    p.funder if p else None,
                ))
            await con.executemany(
                """INSERT INTO scan_holders (scan_id, rank, address, supply_pct,
                       sol_balance, win_rate, closed_trades, realized_pnl_sol,
                       avg_hold_hours, age_hours, is_fresh, is_smart_money,
                       is_sniper, funder)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",
                rows)
            return scan_id
    except Exception as exc:  # noqa: BLE001
        log.warning("log_scan failed: %s", exc)
        return None


async def forward_price_worker(interval: int = 900) -> None:
    """Fill price_1h and price_24h so scores can be calibrated later."""
    if not _pool:
        return
    from core.metadata import get_token_meta
    while True:
        try:
            async with _pool.acquire() as con:
                for col, delay in (("price_1h", "1 hour"), ("price_24h", "24 hours")):
                    pending = await con.fetch(
                        f"""SELECT id, mint FROM scans
                            WHERE {col} IS NULL
                              AND scanned_at < now() - INTERVAL '{delay}'
                              AND scanned_at > now() - INTERVAL '7 days'
                            LIMIT 20""")
                    for row in pending:
                        meta = await get_token_meta(row["mint"])
                        if meta.price_usd > 0:
                            await con.execute(
                                f"UPDATE scans SET {col}=$1 WHERE id=$2",
                                meta.price_usd, row["id"])
                        await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("forward price worker: %s", exc)
        await asyncio.sleep(interval)
