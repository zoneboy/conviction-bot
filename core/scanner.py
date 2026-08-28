"""Scan orchestration.

A full scan is 100 to 150 RPC calls and takes 40 to 120 seconds. That is too
long for a silent wait, so every stage reports through progress_cb and the bot
edits one message in place.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import config
from core import bundles, holders, metadata, profiler, scoring
from core.rpc import BudgetExhausted

log = logging.getLogger(__name__)

ProgressCb = Callable[[str], Awaitable[None]]


@dataclass
class ScanResult:
    mint: str
    meta: metadata.TokenMeta
    hset: holders.HolderSet
    profiles: list[profiler.WalletProfile]
    clusters: bundles.ClusterReport
    score: scoring.ScoreResult
    elapsed: float = 0.0
    warnings: list[str] = field(default_factory=list)


class ScanError(RuntimeError):
    pass


_scan_gate = asyncio.Semaphore(config.MAX_CONCURRENT_SCANS)


async def scan(mint: str, progress: ProgressCb | None = None) -> ScanResult:
    async def say(msg: str) -> None:
        if progress:
            try:
                await progress(msg)
            except Exception:  # noqa: BLE001
                pass

    started = time.monotonic()
    warnings: list[str] = []

    async with _scan_gate:
        try:
            await say("Fetching token metadata...")
            meta, sol_price = await asyncio.gather(
                metadata.get_token_meta(mint), metadata.get_sol_price())

            if meta.total_supply <= 0:
                raise ScanError(
                    "Could not read token supply. Check the mint address is a "
                    "Solana SPL token.")
            if not meta.pair_addresses:
                warnings.append("No DEX pair found. Token may be unlaunched or "
                                "trading only on a bonding curve.")
            if sol_price <= 0:
                warnings.append("SOL price unavailable, stablecoin trade legs "
                                "excluded from PnL.")

            await say(f"Found ${meta.symbol}. Loading holder set...")
            hset = await holders.fetch_top_holders(
                mint, meta.decimals, meta.total_supply)
            if not hset.holders:
                raise ScanError("No individual holders found after filtering "
                                "pools and burn addresses.")
            if len(hset.holders) < 10:
                warnings.append(
                    f"Only {len(hset.holders)} individual holders available, "
                    "ratios are noisy at this sample size.")

            addresses = [h.owner for h in hset.holders]
            await say(f"Profiling {len(addresses)} wallets (0/{len(addresses)})...")

            balances_task = asyncio.create_task(
                profiler.fetch_native_balances(addresses))

            sem = asyncio.Semaphore(config.WALLET_CONCURRENCY)
            done = 0
            lock = asyncio.Lock()

            async def one(addr: str) -> profiler.WalletProfile:
                nonlocal done
                async with sem:
                    p = await profiler.profile_wallet(
                        addr, mint, meta.pair_created_at, sol_price)
                async with lock:
                    done += 1
                    if done % 4 == 0 or done == len(addresses):
                        await say(f"Profiling {len(addresses)} wallets "
                                  f"({done}/{len(addresses)})...")
                return p

            results = await asyncio.gather(
                *(one(a) for a in addresses), return_exceptions=True)

            profiles: list[profiler.WalletProfile] = []
            for addr, r in zip(addresses, results):
                if isinstance(r, BaseException):
                    log.warning("wallet %s errored: %s", addr[:8], r)
                    profiles.append(profiler.WalletProfile(
                        address=addr, note="error"))
                else:
                    profiles.append(r)

            balances = await balances_task
            for p in profiles:
                p.sol_balance = balances.get(p.address, 0.0)
                p.has_depth = p.sol_balance > config.CAPITAL_DEPTH_SOL
                p.has_gas = p.sol_balance > config.LIQUID_GAS_SOL

            await say("Detecting clusters and scoring...")
            clusters = bundles.detect(profiles)
            score = scoring.compute(meta, hset, profiles, clusters)

            if score.low_confidence:
                warnings.append(
                    f"Only {score.metrics['usable']}/{score.metrics['scanned']} "
                    "wallets profiled successfully. Score confidence is low.")

            return ScanResult(
                mint=mint, meta=meta, hset=hset, profiles=profiles,
                clusters=clusters, score=score,
                elapsed=time.monotonic() - started, warnings=warnings,
            )

        except BudgetExhausted as exc:
            raise ScanError(
                f"Daily API budget reached. {exc} Resets at UTC midnight."
            ) from exc
