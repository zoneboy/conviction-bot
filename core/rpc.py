"""Helius transport layer.

Handles: token-bucket rate limiting, bounded concurrency, exponential backoff
on 429/5xx, and a hard daily call budget so a runaway scan cannot drain the
free tier. Every outbound call goes through _guarded().
"""
import asyncio
import logging
import time
from datetime import date
from typing import Any

import aiohttp

import config

log = logging.getLogger(__name__)


class BudgetExhausted(RuntimeError):
    pass


class RateLimiter:
    """Simple async token bucket."""

    def __init__(self, per_second: float):
        self.interval = 1.0 / max(per_second, 0.1)
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next = max(now, self._next) + self.interval


class HeliusClient:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._limiter = RateLimiter(config.RPC_PER_SECOND)
        self._sem = asyncio.Semaphore(config.RPC_CONCURRENCY)
        self._day = date.today()
        self.calls_today = 0
        self.calls_total = 0
        self.errors = 0

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=45),
                headers={"Content-Type": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _charge(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self.calls_today = 0
        if self.calls_today >= config.DAILY_CALL_BUDGET:
            raise BudgetExhausted(
                f"Daily call budget of {config.DAILY_CALL_BUDGET} reached."
            )
        self.calls_today += 1
        self.calls_total += 1

    async def _guarded(self, method: str, url: str, **kw) -> Any:
        self._charge()
        session = await self.session()
        delay = 1.0
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                async with self._sem:
                    await self._limiter.acquire()
                    async with session.request(method, url, **kw) as resp:
                        if resp.status == 429:
                            raise aiohttp.ClientResponseError(
                                resp.request_info, resp.history, status=429,
                                message="rate limited",
                            )
                        if resp.status >= 500:
                            raise aiohttp.ClientResponseError(
                                resp.request_info, resp.history,
                                status=resp.status, message="server error",
                            )
                        if resp.status >= 400:
                            body = await resp.text()
                            log.warning("HTTP %s on %s: %s",
                                        resp.status, url.split("?")[0], body[:200])
                            return None
                        return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_err = exc
                self.errors += 1
                if attempt == 3:
                    break
                await asyncio.sleep(delay)
                delay *= 2
        log.warning("give up on %s: %s", url.split("?")[0], last_err)
        return None

    # ------------------------------------------------------------ JSON-RPC
    async def rpc(self, method: str, params: list) -> Any:
        payload = {"jsonrpc": "2.0", "id": method, "method": method,
                   "params": params}
        data = await self._guarded("POST", config.HELIUS_RPC, json=payload)
        if not data:
            return None
        if "error" in data:
            log.warning("rpc %s error: %s", method, data["error"])
            return None
        return data.get("result")

    async def get_token_supply(self, mint: str) -> dict | None:
        r = await self.rpc("getTokenSupply", [mint])
        return (r or {}).get("value")

    async def get_token_largest_accounts(self, mint: str) -> list[dict]:
        r = await self.rpc("getTokenLargestAccounts", [mint])
        return (r or {}).get("value") or []

    async def get_multiple_accounts(
        self, pubkeys: list[str], encoding: str = "jsonParsed"
    ) -> list[dict | None]:
        out: list[dict | None] = []
        for i in range(0, len(pubkeys), 100):
            chunk = pubkeys[i:i + 100]
            r = await self.rpc(
                "getMultipleAccounts",
                [chunk, {"encoding": encoding, "commitment": "confirmed"}],
            )
            out.extend((r or {}).get("value") or [None] * len(chunk))
        return out

    async def get_account_info(self, pubkey: str) -> dict | None:
        r = await self.rpc(
            "getAccountInfo",
            [pubkey, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        )
        return (r or {}).get("value")

    async def get_signatures(
        self, address: str, limit: int = 1000, before: str | None = None
    ) -> list[dict]:
        opts: dict[str, Any] = {"limit": limit}
        if before:
            opts["before"] = before
        r = await self.rpc("getSignaturesForAddress", [address, opts])
        return r or []

    async def get_token_accounts_das(
        self, mint: str, page: int, limit: int = 1000
    ) -> list[dict]:
        r = await self.rpc(
            "getTokenAccounts",
            [{"mint": mint, "page": page, "limit": limit,
              "options": {"showZeroBalance": False}}],
        )
        return (r or {}).get("token_accounts") or []

    # ------------------------------------------- Enhanced Transactions REST
    async def parsed_transactions(
        self, address: str, before: str | None = None, limit: int = 100,
        tx_type: str | None = None,
    ) -> list[dict]:
        url = (f"{config.HELIUS_REST}/addresses/{address}/transactions"
               f"?api-key={config.HELIUS_API_KEY}&limit={limit}")
        if before:
            url += f"&before={before}"
        if tx_type:
            url += f"&type={tx_type}"
        data = await self._guarded("GET", url)
        return data if isinstance(data, list) else []

    def stats(self) -> dict:
        return {
            "calls_today": self.calls_today,
            "calls_total": self.calls_total,
            "budget": config.DAILY_CALL_BUDGET,
            "errors": self.errors,
        }


client = HeliusClient()
