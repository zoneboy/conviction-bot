# Holder Conviction Scanner

Telegram bot that profiles the top non-LP holders of a Solana token and scores
the float 0-100 on holder quality.

Built to run on the Helius free tier with no paid data vendor.

---

## Why there is no Birdeye dependency

Birdeye's wallet PnL and win-rate endpoints are paid. Rather than gate the main
feature behind a subscription, win rate and realised PnL are reconstructed from
Helius parsed transactions and denominated in **SOL**, which removes the need
for historical USD prices entirely.

Method per wallet:

1. Pull the last 300 parsed transactions.
2. Reduce each to a net delta: SOL/WSOL change, stablecoin change converted at
   spot, and per-mint token change.
3. Replay chronologically into a per-mint position ledger.
4. A position closes when the balance falls under 2% of its peak.
   `realised = SOL proceeds - SOL cost`. Positive is a win.
5. Hold duration = last sell minus first buy.

Open positions are excluded from win rate. Unrealised gains are not evidence of
skill.

---

## Setup

1. **Bot token** from [@BotFather](https://t.me/BotFather).
2. **Helius key**: sign up at helius.dev, free plan, copy the API key.
3. Copy `.env.example` to `.env` and fill in `TELEGRAM_TOKEN` and
   `HELIUS_API_KEY`.

```bash
pip install -r requirements.txt
python bot.py
```

### Deploying

This is a long-running process, so Netlify and GitHub Actions will not work.
Railway or Render is the fit:

1. Push the repo to GitHub.
2. Railway, New Project, Deploy from GitHub repo.
3. Add `TELEGRAM_TOKEN` and `HELIUS_API_KEY` under Variables.
4. Railway reads `Procfile` and runs the worker. No exposed port needed,
   the bot uses long polling.

---

## API budget

One scan of 20 holders costs roughly:

| Stage | Calls |
|---|---|
| Metadata, supply, mint authority | 3 |
| Largest accounts + owner resolution + system-owner check | 3 |
| Native balances (batched) | 1 |
| Signature pagination for age | 20 to 40 |
| Parsed transaction pages | 20 to 60 |
| Funding source lookups | 0 to 20 |
| **Total** | **~50 to 130** |

`DAILY_CALL_BUDGET` defaults to 6000, giving roughly 50 to 100 cold scans a
day. The wallet cache has a 6h TTL, so repeat scans of trending tokens cost a
fraction of that. `/stats` shows live usage and cache hit rate.

To stretch further, lower `TX_PAGES_PER_WALLET` to 2 and
`AGE_PAGES_PER_WALLET` to 1. You lose some PnL depth and age precision.

---

## How liquidity pools are filtered

Structurally, not with a list. On Solana every individual wallet is owned by
the System Program. Raydium vaults, Orca whirlpools, Meteora bins, pump.fun
bonding curves, escrows and staking accounts are owned by their program. A
single `getMultipleAccounts` call removes all of them, including pools that did
not exist when this was written.

`core/blacklist.py` only handles what that rule cannot see: burn addresses and
CEX hot wallets, which are ordinary system accounts. **The CEX list is
unverified and needs maintaining.** A missed exchange wallet shows up as a
single whale with a strange trade history.

---

## Scoring

| Component | Weight |
|---|---|
| Smart money (>60% win rate, 5+ closed trades) | 35 |
| Hold profile (>6h average) | 25 |
| Capital depth (>2 SOL liquid) | 20 |
| Supply distribution (top 10 under 18%) | 20 |

Deductions: `-30` fresh wallet cluster, `-40` shared funding, `-25` live mint
authority, `-20` live freeze authority, `-15` sniper concentration. All are
env-tunable, set to 0 to disable.

### The weights are a guess

Nothing has validated 35/25/20/20. That is what `core/db.py` is for. Set
`DATABASE_URL` to a Neon connection string and every scan is logged with its
component breakdown plus the token price at scan time, +1h and +24h.

After a few hundred scans:

```sql
SELECT
  width_bucket(score, 0, 100, 5) AS score_band,
  count(*),
  round(avg((price_24h - price_usd) / nullif(price_usd, 0) * 100), 1) AS avg_ret_24h
FROM scans
WHERE price_24h IS NOT NULL
GROUP BY 1 ORDER BY 1;
```

If high bands do not outperform low bands, the weights are wrong and you now
have the data to fix them. Regress the four component values in
`components` JSONB against forward return.

---

## Known limitations

- Transaction history is capped at 300 per wallet. Very active wallets are
  sampled, not audited.
- Wallet age lookback is capped at 2000 signatures. Beyond that a wallet is
  reported as "older than" and treated as established.
- Stablecoin trade legs are converted at the *current* SOL price, not the price
  at trade time. Fine over 30 days, wrong over years.
- Token-to-token swaps with no SOL or stablecoin leg are skipped.
- **A careful deployer defeats this** by funding aged wallets from an exchange
  and staggering entries. A high score means the obvious red flags are absent,
  not that the token is safe.

Data coverage is printed on every report. A high score on 40% coverage means
nothing, and the bot says so.

---

## Adding EVM later

The scoring engine, cache, cluster detection and report layer are all chain
agnostic. Only three things are Solana-specific:

- `core/holders.py` (token account model)
- `core/profiler.py` (transaction shape)
- `core/rpc.py` (transport)

Write an Alchemy or Moralis adapter exposing the same `HolderSet` and
`WalletProfile` dataclasses and the rest works unchanged. Note that Base has no
free equivalent to Helius parsed transactions, so PnL will need log decoding
against Uniswap V2/V3 swap events.

---

Heuristic tool. Not financial advice.
