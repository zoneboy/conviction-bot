"""Insider cluster detection.

Three signals, cheapest first:

1. Shared funder. Group holders by the wallet that first funded them. Three or
   more sharing one funder is a dev bundle. This is the -40 deduction.
2. Funding burst. Wallets funded within the same short window by the same
   source, which is what Disperse and Jito bundle tooling produces.
3. Synchronised entry. Holders whose first buy of this token lands in the same
   slot cluster, catching bundles funded from a CEX to defeat signal 1.

Signal 3 is the one that survives a careful deployer, so it is reported even
when 1 and 2 are clean.
"""
from dataclasses import dataclass, field

import config
from core import blacklist
from core.profiler import WalletProfile


@dataclass
class ClusterReport:
    bundled_wallets: int = 0
    bundle_groups: list[dict] = field(default_factory=list)
    funding_bursts: int = 0
    sync_entry_wallets: int = 0
    sync_entry_groups: list[dict] = field(default_factory=list)
    bundle_detected: bool = False


def detect(profiles: list[WalletProfile]) -> ClusterReport:
    rep = ClusterReport()
    usable = [p for p in profiles if p.ok]

    # ---- 1 & 2: shared funder
    by_funder: dict[str, list[WalletProfile]] = {}
    for p in usable:
        if not p.funder:
            continue
        # CEX withdrawals are not evidence of a bundle, everyone uses Binance.
        listed, reason = blacklist.classify(p.funder)
        if listed and reason.startswith(("cex", "infra")):
            continue
        by_funder.setdefault(p.funder, []).append(p)

    for funder, group in by_funder.items():
        if len(group) >= config.BUNDLE_MIN_WALLETS:
            rep.bundle_detected = True
            rep.bundled_wallets += len(group)
            times = sorted(p.funded_ts for p in group if p.funded_ts)
            burst = bool(times) and (times[-1] - times[0]) <= 600
            if burst:
                rep.funding_bursts += 1
            rep.bundle_groups.append({
                "funder": funder,
                "wallets": [p.address for p in group],
                "count": len(group),
                "same_burst": burst,
            })

    # ---- 3: synchronised first entry into this token
    # Sort by key only. Tuple comparison would fall through to the profile
    # object on ties, and ties are precisely the bundle case.
    entries = sorted(((p.token_entry_ts, p) for p in usable if p.token_entry_ts),
                     key=lambda t: t[0])
    i = 0
    while i < len(entries):
        window = [entries[i]]
        j = i + 1
        while j < len(entries) and entries[j][0] - entries[i][0] <= 30:
            window.append(entries[j])
            j += 1
        if len(window) >= config.BUNDLE_MIN_WALLETS:
            rep.sync_entry_wallets += len(window)
            rep.sync_entry_groups.append({
                "window_seconds": window[-1][0] - window[0][0],
                "wallets": [p.address for _, p in window],
                "count": len(window),
            })
            i = j
        else:
            i += 1

    return rep
