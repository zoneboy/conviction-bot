# CLAUDE.md

Context for Claude Code working in this repo.

## What this is

A Telegram bot that scores Solana memecoin holder quality 0-100. Python,
asyncio, python-telegram-bot v21. Long-polling worker, no web server.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill TELEGRAM_TOKEN and HELIUS_API_KEY
python bot.py
```

Tests: `python tests/test_logic.py`

## Architecture

```
bot.py              Telegram handlers, cooldowns, progressive message editing
config.py           Every tunable constant. Change values here, not inline.
core/rpc.py         Helius transport: rate limit, retry, daily call budget
core/metadata.py    DexScreener token data + mint/freeze authority from RPC
core/holders.py     Top holder ingestion, structural LP filtering
core/profiler.py    Per-wallet PnL, win rate, hold duration, age, funder
core/bundles.py     Shared-funding and synchronised-entry cluster detection
core/scoring.py     Weighted 0-100 score with deductions
core/report.py      Telegram HTML report card
core/cache.py       TTL cache with single-flight dedupe
core/db.py          Optional Neon logging for weight calibration
core/validate.py    Base58 / EVM address parsing
```

Data flow: `bot.py` → `scanner.scan()` → metadata → holders → profiler (parallel)
→ bundles → scoring → report.

## Rules for changes

- **Never hardcode a threshold.** Everything tunable belongs in `config.py`
  with an env override.
- **Every new API call goes through `core/rpc.py`.** It enforces the rate limit
  and the daily budget. Direct `aiohttp` calls bypass both and will drain the
  free tier.
- **The LP filter is structural, not a list.** Individual Solana wallets are
  owned by the System Program; pools and vaults are owned by their program.
  Do not replace this with a hardcoded pool address list.
- **Only closed positions count toward win rate.** Unrealised gains are not
  evidence of skill. Do not "improve" this by including open positions.
- **PnL is denominated in SOL, deliberately.** Converting to USD would require
  historical prices, which are not available on the free tier.
- Keep wallet-level caching keyed on address alone. Native SOL balance is
  intentionally excluded from the cache because it moves too fast.

## Known gaps, good first tasks

- CEX wallet list in `core/blacklist.py` is unverified and incomplete.
- No retry when a wallet profile partially fails; it just drops to `ok=False`.
- `_results` in `bot.py` is an unbounded dict, it should be an LRU.
- No `/watch` command for re-scanning and diffing a score over time.
- EVM adapter not written. Scoring, cache, bundles and report are already
  chain-agnostic; only holders, profiler and rpc are Solana-specific.

## Do not

- Add trading, auto-buy, or wallet-signing functionality.
- Remove the "not financial advice" line from the report footer.
- Commit `.env`.
