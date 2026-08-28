"""Central configuration. Every tunable lives here."""
import os

try:  # local dev convenience; hosts inject real env vars instead
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------- credentials
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")  # optional override
DATABASE_URL = os.getenv("DATABASE_URL", "")        # optional Neon logging

HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
HELIUS_REST = "https://api.helius.xyz/v0"
DEXSCREENER = "https://api.dexscreener.com/latest/dex"

# ---------------------------------------------------------------- rate limits
RPC_PER_SECOND = float(os.getenv("RPC_PER_SECOND", "8"))
RPC_CONCURRENCY = int(os.getenv("RPC_CONCURRENCY", "5"))
WALLET_CONCURRENCY = int(os.getenv("WALLET_CONCURRENCY", "4"))
MAX_CONCURRENT_SCANS = int(os.getenv("MAX_CONCURRENT_SCANS", "2"))
USER_COOLDOWN_SECONDS = int(os.getenv("USER_COOLDOWN_SECONDS", "45"))

# Helius free tier is 1,000,000 credits/month. Budget defensively.
DAILY_CALL_BUDGET = int(os.getenv("DAILY_CALL_BUDGET", "6000"))

# ---------------------------------------------------------------- cache TTLs
WALLET_CACHE_TTL = int(os.getenv("WALLET_CACHE_TTL", str(6 * 3600)))
TOKEN_CACHE_TTL = int(os.getenv("TOKEN_CACHE_TTL", "120"))
SCAN_CACHE_TTL = int(os.getenv("SCAN_CACHE_TTL", "600"))
CACHE_MAXSIZE = int(os.getenv("CACHE_MAXSIZE", "20000"))

# ---------------------------------------------------------------- ingestion
TOP_HOLDER_TARGET = int(os.getenv("TOP_HOLDER_TARGET", "20"))
DAS_PAGE_LIMIT = 1000
DAS_MAX_PAGES = int(os.getenv("DAS_MAX_PAGES", "4"))
TX_PAGES_PER_WALLET = int(os.getenv("TX_PAGES_PER_WALLET", "3"))  # 100 tx/page
AGE_PAGES_PER_WALLET = int(os.getenv("AGE_PAGES_PER_WALLET", "2"))  # 1000 sig/page
PNL_LOOKBACK_DAYS = int(os.getenv("PNL_LOOKBACK_DAYS", "30"))

# ---------------------------------------------------------------- thresholds
SMART_MONEY_WIN_RATE = 60.0
SMART_MONEY_MIN_TRADES = 5
HOLD_DURATION_HOURS = 6.0
CAPITAL_DEPTH_SOL = 2.0
LIQUID_GAS_SOL = 1.0
FRESH_WALLET_HOURS = 48.0
SNIPER_WINDOW_SECONDS = int(os.getenv("SNIPER_WINDOW_SECONDS", "60"))
DUST_FRACTION = 0.02  # position considered closed below 2% of peak size

# supply distribution band
TOP10_GOOD_PCT = 18.0
TOP10_BAD_PCT = 25.0

# ---------------------------------------------------------------- score model
# Spec weights. Sum must be 100.
W_SMART_MONEY = 35.0
W_HOLD_DURATION = 25.0
W_CAPITAL_DEPTH = 20.0
W_SUPPLY_DIST = 20.0

# Spec hard deductions
DEDUCT_FRESH_CLUSTER = 30.0     # >30% of holders are fresh wallets
FRESH_CLUSTER_RATIO = 0.30
DEDUCT_BUNDLE = 40.0            # 3+ holders share a funding source
BUNDLE_MIN_WALLETS = 3

# Additional deductions (my recommendations). Set to 0 to disable.
DEDUCT_MINT_AUTHORITY = float(os.getenv("DEDUCT_MINT_AUTHORITY", "25"))
DEDUCT_FREEZE_AUTHORITY = float(os.getenv("DEDUCT_FREEZE_AUTHORITY", "20"))
DEDUCT_SNIPER_CLUSTER = float(os.getenv("DEDUCT_SNIPER_CLUSTER", "15"))
SNIPER_CLUSTER_RATIO = 0.30

# Coverage below this makes the score untrustworthy.
MIN_COVERAGE_RATIO = 0.60

VERDICT_BANDS = [
    (75, "HIGH CONVICTION", "\u2705"),
    (55, "MODERATE", "\U0001f7e1"),
    (35, "SPECULATIVE", "\U0001f7e0"),
    (0, "AVOID", "\U0001f6d1"),
]

SOL_MINT = "So11111111111111111111111111111111111111112"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
STABLES = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": 1.0,  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": 1.0,  # USDT
}
LAMPORTS = 1_000_000_000
