"""Memecoin Holder Conviction Bot.

Run: python bot.py
Requires TELEGRAM_TOKEN and HELIUS_API_KEY in the environment.
"""
import asyncio
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import config
from core import db, profiler, report, scanner, validate
from core.rpc import client
from core.scanner import ScanError

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

_last_scan: dict[int, float] = {}
_active: set[int] = set()
_results: dict[str, scanner.ScanResult] = {}

WELCOME = (
    "\U0001f48e <b>Holder Conviction Scanner</b>\n\n"
    "Send me a Solana contract address and I will profile the top holders: "
    "realised PnL, win rate, hold duration, wallet age, funding clusters and "
    "launch snipers, then score the float 0-100.\n\n"
    "<b>Usage</b>\n"
    "<code>/scan &lt;contract address&gt;</code>\n"
    "or just paste the address\n\n"
    "<b>Commands</b>\n"
    "/scan - run a scan\n"
    "/stats - cache and API budget\n"
    "/help - method and limitations\n\n"
    "A scan takes 40-120 seconds. PnL is reconstructed from on-chain swaps and "
    "denominated in SOL.\n\n"
    "<i>Heuristic tool. Not financial advice.</i>"
)

HELP = (
    "<b>How the score works</b>\n\n"
    "<code>35%</code> Smart money: holders with &gt;60% win rate over 5+ closed "
    "trades\n"
    "<code>25%</code> Hold profile: holders averaging &gt;6h per position\n"
    "<code>20%</code> Capital depth: holders with &gt;2 SOL liquid\n"
    "<code>20%</code> Distribution: top 10 non-LP holders under 18% of float\n\n"
    "<b>Deductions</b>\n"
    "<code>-30</code> more than 30% of holders under 48h old\n"
    "<code>-40</code> 3+ holders share a funding wallet\n"
    "<code>-25</code> mint authority still active\n"
    "<code>-20</code> freeze authority still active\n"
    "<code>-15</code> more than 30% sniped the launch block\n\n"
    "<b>Method</b>\n"
    "Win rate and PnL are rebuilt from parsed swaps and measured in SOL, so no "
    "historical USD prices are needed. Only closed positions count. Unrealised "
    "gains are not evidence of skill.\n\n"
    "<b>Limitations, read these</b>\n"
    "\u2022 Liquidity pools are filtered structurally (program-owned accounts), "
    "which catches every DEX. CEX hot wallets rely on a maintained list and may "
    "be incomplete.\n"
    "\u2022 History is capped at 300 transactions per wallet, so very active "
    "wallets are sampled, not fully audited.\n"
    "\u2022 Wallet age is capped at 2000 signatures of lookback.\n"
    "\u2022 A sophisticated deployer can defeat this by funding aged wallets "
    "from an exchange. Treat a high score as the absence of lazy red flags, "
    "not as a green light.\n"
    "\u2022 Data coverage is printed on every report. A high score on low "
    "coverage means nothing."
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(WELCOME, disable_web_page_preview=True)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(HELP, disable_web_page_preview=True)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    rpc = client.stats()
    wallet = profiler.cache_stats()
    txt = (
        "<b>Runtime stats</b>\n"
        f"API calls today: <code>{rpc['calls_today']}/{rpc['budget']}</code>\n"
        f"API calls total: <code>{rpc['calls_total']}</code>\n"
        f"Transport errors: <code>{rpc['errors']}</code>\n"
        f"Wallet cache: <code>{wallet['size']}</code> entries, "
        f"<code>{wallet['hit_rate']}%</code> hit rate\n"
        f"Deduped in-flight: <code>{wallet['dedupes']}</code>\n"
        f"Active scans: <code>{len(_active)}</code>"
    )
    await update.message.reply_html(txt)


async def _run_scan(update: Update, address: str, chain: str) -> None:
    msg = update.message
    user_id = msg.from_user.id

    if chain != "solana":
        await msg.reply_html(
            "EVM support is not enabled in this build. Solana only for now.\n"
            "<i>The scoring engine is chain agnostic, only the data adapter is "
            "missing.</i>")
        return

    now = time.time()
    if user_id in _active:
        await msg.reply_text("You already have a scan running. One at a time.")
        return
    wait = config.USER_COOLDOWN_SECONDS - (now - _last_scan.get(user_id, 0))
    if wait > 0:
        await msg.reply_text(f"Cooldown: {int(wait)}s left.")
        return

    _active.add(user_id)
    _last_scan[user_id] = now
    status = await msg.reply_html("\u23f3 Starting scan...")
    last_text = {"v": ""}

    async def progress(text: str) -> None:
        line = f"\u23f3 {text}"
        if line == last_text["v"]:
            return
        last_text["v"] = line
        try:
            await status.edit_text(line, parse_mode=ParseMode.HTML)
        except BadRequest:
            pass

    try:
        result = await scanner.scan(address, progress)
    except ScanError as exc:
        await status.edit_text(f"\u274c {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("scan crashed")
        await status.edit_text(f"\u274c Unexpected error: {type(exc).__name__}")
        return
    finally:
        _active.discard(user_id)

    _results[address] = result
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("\U0001f465 Holder detail",
                             callback_data=f"h:{address}"),
        InlineKeyboardButton("\U0001f4c8 Chart",
                             url=f"https://dexscreener.com/solana/{address}"),
    ]])
    await status.edit_text(report.render(result), parse_mode=ParseMode.HTML,
                           disable_web_page_preview=True, reply_markup=keyboard)
    asyncio.create_task(db.log_scan(result, user_id))


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    address, chain, err = validate.parse_input(update.message.text or "")
    if err:
        await update.message.reply_html(err)
        return
    await _run_scan(update, address, chain)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    address, chain, err = validate.parse_input(text)
    if err:
        return  # stay quiet on ordinary chat
    await _run_scan(update, address, chain)


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not q.data.startswith("h:"):
        return
    address = q.data[2:]
    result = _results.get(address)
    if not result:
        await q.message.reply_text("That scan expired. Run it again.")
        return
    await q.message.reply_html(report.render_holders(result),
                               disable_web_page_preview=True)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("handler error", exc_info=ctx.error)


async def post_init(app: Application) -> None:
    await db.init()
    app.bot_data["fp_task"] = asyncio.create_task(db.forward_price_worker())


async def post_shutdown(app: Application) -> None:
    task = app.bot_data.get("fp_task")
    if task:
        task.cancel()
    await client.close()
    await db.close()


def main() -> None:
    if not config.TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN is not set.")
    if not config.HELIUS_API_KEY:
        raise SystemExit("HELIUS_API_KEY is not set.")

    app = (Application.builder()
           .token(config.TELEGRAM_TOKEN)
           .post_init(post_init)
           .post_shutdown(post_shutdown)
           .build())

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)

    log.info("bot starting")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
