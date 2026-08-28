"""Address exclusion lists.

The primary LP/program filter is structural, not list based: an individual
wallet is always owned by the System Program. Anything else (Raydium vaults,
Orca whirlpools, pump.fun bonding curves, staking programs) is program owned
and gets dropped automatically in holders.py.

These lists cover the cases that filter cannot catch: burn addresses and
centralised exchange hot wallets, which are ordinary system accounts.

VERIFY THE CEX LIST BEFORE TRUSTING IT. Exchanges rotate hot wallets. Treat
this as a starting point and maintain it yourself.
"""

BURN_ADDRESSES = {
    "11111111111111111111111111111111",
    "1nc1nerator11111111111111111111111111111111",
    "So11111111111111111111111111111111111111112",
}

# Known DEX / launchpad programs. Used as a secondary label so the report can
# say *why* an address was excluded.
DEX_PROGRAMS = {
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM V4",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CPMM",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool",
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP": "Orca V1",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "Meteora Pools",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump.fun",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "Pump AMM",
    "SwaPpA9LAaLfeLi3a68M4DjnLqgtticKg6CnyNwgAC8": "Saber",
    "PhoeNiXZ8ByJGLkxNfZRnkUfjvmuYqLR89jjFHGqdXY": "Phoenix",
    "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX": "OpenBook",
    "MERLuDFBMmsHnsBPZw2sDQZHvXFMwp8EdjudcU2HKky": "Mercurial",
    "DjVE6JNiYqPL2QXyCUUh8rNjHrbz9hXHNYt99MQ59qw1": "Orca Aquafarm",
}

# Vault authorities that are system-adjacent and would otherwise slip through.
LP_AUTHORITIES = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1": "Raydium AMM Authority",
    "GThUX1Atko4tqhN2NaiTazWSeFWMuiUvfFnyJyUghFMJ": "Raydium Staking",
    "3uTzTX5GBSfbW7eM9R9k95H7Txe32Qw3Z25MtyD2dzwC": "Raydium Authority V2",
}

# Centralised exchange deposit / hot wallets. UNVERIFIED, maintain yourself.
CEX_WALLETS = {
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9": "Binance",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": "Binance",
    "2ojv9BAiHUrvsm9gxDe7fJSzbNZSJcxZvf8dqmWGHG8S": "Binance",
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS": "Coinbase",
    "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfQwSLWSprPicm": "Coinbase",
    "FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsznf4RiouN5": "Kraken",
    "5VCwKtCXgCJ6kit5FybXjvriW3xELsFDhYrPSqtJNmcD": "OKX",
    "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWjtW2": "Bybit",
    "u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w": "Gate.io",
    "GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE": "Coinbase Prime",
}

# Common protocol / infra accounts that hold tokens but are not traders.
KNOWN_NON_TRADERS = {
    "9yMwSPk9mrXSN7yDHUuZurAh1sjbJsfpUqjZ7SvVtdco": "Jupiter Fee",
    "6LXutJvKUw8Q5ue2gCgKHQdAN4suWW8awzFVC6XCguFx": "Jito Tip",
    "T1pyyaTNZsKv2WcRAB8oVnk93mLJw2XzjtVYqCsaHqt": "Jito Tip",
}


def classify(address: str) -> tuple[bool, str]:
    """Return (is_excluded, reason)."""
    if address in BURN_ADDRESSES:
        return True, "burn"
    if address in LP_AUTHORITIES:
        return True, f"lp:{LP_AUTHORITIES[address]}"
    if address in CEX_WALLETS:
        return True, f"cex:{CEX_WALLETS[address]}"
    if address in KNOWN_NON_TRADERS:
        return True, f"infra:{KNOWN_NON_TRADERS[address]}"
    if address in DEX_PROGRAMS:
        return True, f"dex:{DEX_PROGRAMS[address]}"
    return False, ""
