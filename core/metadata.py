"""Token metadata.

DexScreener is free, keyless, and gives market cap, liquidity, pair addresses
and pair creation time. Mint/freeze authority comes from the RPC because
DexScreener does not expose it and it is the single highest-value rug signal.
"""
import asyncio
import logging
from dataclasses import dataclass, field

import aiohttp

import config
from core.cache import AsyncCache
from core.rpc import client

log = logging.getLogger(__name__)

_token_cache = AsyncCache(2000, config.TOKEN_CACHE_TTL, "token")
_sol_price_cache = AsyncCache(4, 300, "solprice")


@dataclass
class TokenMeta:
    mint: str
    symbol: str = "UNKNOWN"
    name: str = "Unknown Token"
    price_usd: float = 0.0
    market_cap: float = 0.0
    fdv: float = 0.0
    liquidity_usd: float = 0.0
    pair_addresses: list[str] = field(default_factory=list)
    pair_created_at: int = 0          # unix seconds, 0 if unknown
    dexes: list[str] = field(default_factory=list)
    decimals: int = 0
    total_supply: float = 0.0
    mint_authority: str | None = None
    freeze_authority: str | None = None
    volume_24h: float = 0.0
    price_change_24h: float = 0.0


async def _fetch_dexscreener(mint: str) -> dict:
    session = await client.session()
    url = f"{config.DEXSCREENER}/tokens/{mint}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return {}
            return await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        log.warning("dexscreener failed: %s", exc)
        return {}


async def get_sol_price() -> float:
    async def _f() -> float:
        data = await _fetch_dexscreener(config.SOL_MINT)
        pairs = data.get("pairs") or []
        for p in pairs:
            if p.get("baseToken", {}).get("address") == config.SOL_MINT:
                try:
                    return float(p.get("priceUsd") or 0)
                except (TypeError, ValueError):
                    continue
        return 0.0

    try:
        return await _sol_price_cache.get_or_compute("sol", _f)
    except Exception:  # noqa: BLE001
        return 0.0


async def get_token_meta(mint: str) -> TokenMeta:
    async def _build() -> TokenMeta:
        meta = TokenMeta(mint=mint)
        ds, supply, acct = await asyncio.gather(
            _fetch_dexscreener(mint),
            client.get_token_supply(mint),
            client.get_account_info(mint),
            return_exceptions=True,
        )

        if isinstance(ds, dict):
            pairs = [p for p in (ds.get("pairs") or [])
                     if p.get("baseToken", {}).get("address") == mint]
            pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
                       reverse=True)
            if pairs:
                best = pairs[0]
                base = best.get("baseToken") or {}
                meta.symbol = (base.get("symbol") or "UNKNOWN").upper()
                meta.name = base.get("name") or meta.symbol
                meta.price_usd = _f(best.get("priceUsd"))
                meta.market_cap = _f(best.get("marketCap"))
                meta.fdv = _f(best.get("fdv"))
                meta.volume_24h = _f((best.get("volume") or {}).get("h24"))
                meta.price_change_24h = _f((best.get("priceChange") or {}).get("h24"))
                created = best.get("pairCreatedAt")
                if created:
                    meta.pair_created_at = int(created) // 1000
                meta.liquidity_usd = sum(
                    _f((p.get("liquidity") or {}).get("usd")) for p in pairs)
                meta.pair_addresses = [p["pairAddress"] for p in pairs
                                       if p.get("pairAddress")]
                meta.dexes = sorted({p.get("dexId", "?") for p in pairs})

        if isinstance(supply, dict) and supply:
            meta.decimals = int(supply.get("decimals") or 0)
            meta.total_supply = _f(supply.get("uiAmountString")
                                   or supply.get("uiAmount"))

        if isinstance(acct, dict) and acct:
            info = ((acct.get("data") or {}).get("parsed") or {}).get("info") or {}
            meta.mint_authority = info.get("mintAuthority")
            meta.freeze_authority = info.get("freezeAuthority")

        return meta

    return await _token_cache.get_or_compute(mint, _build)


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
