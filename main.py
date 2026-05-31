#!/usr/bin/env python3
"""
Pune Fresher Job Alert System
==============================
Usage:
  python main.py                  # Start 24/7 scheduler (runs immediately + every 2h)
  python main.py --once           # Run pipeline once and exit
  python main.py --test-telegram  # Send a test Telegram message and exit
  python main.py --stats          # Print DB stats and exit
  python main.py --interval 4     # Override interval (hours)
  python main.py --no-run-now     # Start scheduler without immediate first run
"""

import argparse
import asyncio
import logging
import sys

# ── Logging setup (before any imports that log) ───────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("job_alert.log", encoding="utf-8"),
    ],
)
# Silence noisy third-party loggers
for _noisy in ("httpx", "httpcore", "playwright", "urllib3", "hpack"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("main")


def main():
    parser = argparse.ArgumentParser(
        description="Pune Fresher Job Alert System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--once",          action="store_true",
                        help="Run pipeline once and exit")
    parser.add_argument("--test-telegram", action="store_true",
                        help="Test Telegram bot and send a test message")
    parser.add_argument("--stats",         action="store_true",
                        help="Print database statistics")
    parser.add_argument("--interval",      type=int, default=None,
                        help="Scrape interval in hours (default from config)")
    parser.add_argument("--no-run-now",    action="store_true",
                        help="Do not run pipeline immediately on scheduler start")
    args = parser.parse_args()

    # ── Imports here so logging is set up first ───────────────────────────────
    import database
    import notifier
    import config

    database.init_db()

    # ── --test-telegram ───────────────────────────────────────────────────────
    if args.test_telegram:
        print("Testing Telegram connection…")
        n = notifier.get_notifier()
        if not n:
            print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in .env")
            sys.exit(1)

        async def _test():
            ok = await n.test_connection()
            if ok:
                await n.send_text(
                    "✅ *Pune Fresher Job Alert*\n"
                    "Bot is connected and ready\\! 🚀"
                )
                print("✅ Test message sent successfully.")
            else:
                print("❌ Connection failed. Check your bot token.")
            await n.close()
            return ok

        success = asyncio.run(_test())
        sys.exit(0 if success else 1)

    # ── --stats ───────────────────────────────────────────────────────────────
    if args.stats:
        stats = database.get_stats()
        print("\n📊 Database Statistics")
        print("─" * 35)
        print(f"  Total jobs    : {stats['total']}")
        print(f"  Alerts sent   : {stats['sent']}")
        print(f"  Pending alerts: {stats['pending']}")
        print(f"  Walk-in jobs  : {stats['walkins']}")
        print("─" * 35)
        sys.exit(0)

    # ── Banner ────────────────────────────────────────────────────────────────
    print("""
╔══════════════════════════════════════════════╗
║   🎓 Pune Fresher Job Alert System           ║
║   Runs 24/7 — Telegram alerts on new jobs    ║
╚══════════════════════════════════════════════╝
    """)
    logger.info("Bot token set  : %s", "YES" if config.TELEGRAM_BOT_TOKEN else "NO ⚠")
    logger.info("Chat ID set    : %s", "YES" if config.TELEGRAM_CHAT_ID   else "NO ⚠")

    # ── --once ────────────────────────────────────────────────────────────────
    if args.once:
        from scheduler import run_pipeline
        summary = run_pipeline()
        logger.info("One-shot complete: %s", summary)
        sys.exit(0)

    # ── 24/7 scheduler ────────────────────────────────────────────────────────
    from scheduler import JobAlertScheduler
    sched = JobAlertScheduler(interval_hours=args.interval)
    sched.start(run_now=not args.no_run_now)


if __name__ == "__main__":
    main()
