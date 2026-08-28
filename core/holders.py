"""Holder ingestion and non-LP filtering.

The core filter is structural rather than list-based. On Solana an individual
wallet is always owned by the System Program. Raydium vaults, Orca whirlpools,
pump.fun bonding curves, escrows and staking accounts are owned by their
program. One getMultipleAccounts call therefore removes every liquidity pool,
including ones that did not exist when this was written.

The named blacklists only handle the cases that rule cannot see: burn
addresses and CEX hot wallets, which are ordinary system accounts.
"""
import logging
from dataclasses import dataclass

import config
from core import blacklist
from core.rpc import client

log = logging.getLogger(__name__)


@dataclass
class Holder:
    owner: str
    amount: float
    supply_pct: float = 0.0
    token_accounts: int = 1


@dataclass
class HolderSet:
    holders: list[Holder]
    excluded: list[tuple[str, str]]      # (address, reason)
    lp_supply_pct: float
    burned_pct: float
    total_supply: float
    top10_pct: float
    circulating_supply: float


async def _resolve_owners(token_accounts: list[str]) -> dict[str, str]:
    """token account -> owner wallet."""
    infos = await client.get_multiple_accounts(token_accounts)
    out: dict[str, str] = {}
    for ta, info in zip(token_accounts, infos):
        if not info:
            continue
        parsed = ((info.get("data") or {}).get("parsed") or {}).get("info") or {}
        owner = parsed.get("owner")
        if owner:
            out[ta] = owner
    return out


async def _system_owned(addresses: list[str]) -> dict[str, bool]:
    """True when the address is a plain wallet (System Program owned)."""
    infos = await client.get_multiple_accounts(addresses, encoding="base64")
    out: dict[str, bool] = {}
    for addr, info in zip(addresses, infos):
        if info is None:
            # Never-initialised account with zero lamports. Treat as a wallet;
            # it cannot be a pool because pools always hold rent.
            out[addr] = True
            continue
        out[addr] = info.get("owner") == config.SYSTEM_PROGRAM
    return out


async def _candidates_from_das(mint: str, want: int) -> list[tuple[str, float]]:
    """Fallback: page DAS token accounts and sort by raw amount."""
    rows: dict[str, float] = {}
    for page in range(1, config.DAS_MAX_PAGES + 1):
        accounts = await client.get_token_accounts_das(
            mint, page=page, limit=config.DAS_PAGE_LIMIT)
        if not accounts:
            break
        for a in accounts:
            owner = a.get("owner")
            amt = a.get("amount")
            if not owner or amt is None:
                continue
            rows[a.get("address", owner)] = float(amt)
        if len(accounts) < config.DAS_PAGE_LIMIT:
            break
    ranked = sorted(rows.items(), key=lambda kv: kv[1], reverse=True)
    return ranked[: want * 4]


async def fetch_top_holders(mint: str, decimals: int, total_supply: float,
                            want: int = config.TOP_HOLDER_TARGET) -> HolderSet:
    largest = await client.get_token_largest_accounts(mint)
    excluded: list[tuple[str, str]] = []
    lp_amount = 0.0
    burned_amount = 0.0

    entries: list[tuple[str, float]] = []
    for row in largest:
        ta = row.get("address")
        ui = row.get("uiAmount")
        if ui is None and row.get("amount") is not None:
            ui = float(row["amount"]) / (10 ** decimals)
        if ta and ui:
            entries.append((ta, float(ui)))

    if not entries:
        return HolderSet([], [], 0.0, 0.0, total_supply, 0.0, total_supply)

    owners = await _resolve_owners([ta for ta, _ in entries])

    # Aggregate by owner: one wallet can hold several token accounts.
    by_owner: dict[str, Holder] = {}
    for ta, amt in entries:
        owner = owners.get(ta)
        if not owner:
            excluded.append((ta, "unresolved"))
            continue
        if owner in by_owner:
            by_owner[owner].amount += amt
            by_owner[owner].token_accounts += 1
        else:
            by_owner[owner] = Holder(owner=owner, amount=amt)

    # Structural + list filtering.
    sys_owned = await _system_owned(list(by_owner.keys()))
    keep: list[Holder] = []
    for owner, holder in by_owner.items():
        listed, reason = blacklist.classify(owner)
        if listed:
            excluded.append((owner, reason))
            if reason == "burn":
                burned_amount += holder.amount
            elif reason.startswith("lp") or reason.startswith("dex"):
                lp_amount += holder.amount
            continue
        if not sys_owned.get(owner, True):
            excluded.append((owner, "program-owned (LP/vault/escrow)"))
            lp_amount += holder.amount
            continue
        keep.append(holder)

    # Not enough individuals in the top 20 token accounts. Go wider.
    if len(keep) < max(10, want // 2):
        log.info("only %d individuals in top accounts, paging DAS", len(keep))
        extra = await _candidates_from_das(mint, want)
        if extra:
            raw_owners = await _resolve_owners([a for a, _ in extra])
            cand: dict[str, float] = {}
            for ta, raw in extra:
                owner = raw_owners.get(ta)
                if not owner or owner in by_owner:
                    continue
                cand[owner] = cand.get(owner, 0.0) + raw / (10 ** decimals)
            ranked = sorted(cand.items(), key=lambda kv: kv[1], reverse=True)
            ranked = ranked[: want * 2]
            if ranked:
                sys2 = await _system_owned([o for o, _ in ranked])
                for owner, amt in ranked:
                    if len(keep) >= want:
                        break
                    listed, reason = blacklist.classify(owner)
                    if listed:
                        excluded.append((owner, reason))
                        continue
                    if not sys2.get(owner, True):
                        excluded.append((owner, "program-owned (LP/vault/escrow)"))
                        lp_amount += amt
                        continue
                    keep.append(Holder(owner=owner, amount=amt))

    keep.sort(key=lambda h: h.amount, reverse=True)
    keep = keep[:want]

    circulating = max(total_supply - burned_amount, 1.0)
    for h in keep:
        h.supply_pct = 100.0 * h.amount / circulating

    top10_pct = sum(h.supply_pct for h in keep[:10])

    return HolderSet(
        holders=keep,
        excluded=excluded,
        lp_supply_pct=100.0 * lp_amount / max(total_supply, 1.0),
        burned_pct=100.0 * burned_amount / max(total_supply, 1.0),
        total_supply=total_supply,
        top10_pct=top10_pct,
        circulating_supply=circulating,
    )
