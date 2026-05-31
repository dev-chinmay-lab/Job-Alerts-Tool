"""
notifier.py
Telegram Bot API integration.
Sends richly formatted alerts with emoji, MarkdownV2 escaping,
rate-limiting, and retry logic.
"""

import asyncio
import logging
import time
from typing import Optional

import httpx
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log,
)

import config

logger = logging.getLogger(__name__)

TG_BASE = "https://api.telegram.org/bot{token}/{method}"

# Telegram allows ~30 messages/second for bots; we stay conservative
_MSG_INTERVAL = 0.5   # seconds between messages


# ── MarkdownV2 escaping ───────────────────────────────────────────────────────

_MD_SPECIAL = r"\_*[]()~`>#+-=|{}.!"

def _esc(text: str) -> str:
    """Escape a plain string for Telegram MarkdownV2."""
    if not text:
        return "N/A"
    for ch in _MD_SPECIAL:
        text = text.replace(ch, f"\\{ch}")
    return text


# ── Message formatter ─────────────────────────────────────────────────────────

def _format_job(job: dict) -> str:
    """
    Produce a single MarkdownV2 job alert message.
    job can be a dict (from scraper/filter) or a sqlite3.Row-turned-dict.
    """
    is_walkin = bool(job.get("is_walkin"))

    header = "🚶 *WALK\\-IN DRIVE*" if is_walkin else "💼 *Fresher Job Alert*"

    title      = _esc(job.get("title")      or "N/A")
    company    = _esc(job.get("company")    or "N/A")
    location   = _esc(job.get("location")   or "Pune")
    experience = _esc(job.get("experience") or "Fresher / 0–2 yrs")
    link       = job.get("link", "")
    source     = _esc((job.get("source") or "").upper())

    walkin_note = ""
    if is_walkin:
        walkin_note = "\n⚡ _No appointment needed — walk right in\\!_"

    msg = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 *{title}*\n"
        f"🏢 Company: {company}\n"
        f"📍 Location: {location}\n"
        f"📅 Experience: {experience}\n"
        f"🔗 [Apply Here]({link})"
        f"{walkin_note}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"_Source: {source}_"
    )
    return msg


def _format_summary(count: int, walkin_count: int) -> str:
    return (
        f"🔔 *New Job Alerts — Pune Freshers*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📋 {_esc(str(count))} new jobs found\n"
        f"🚶 {_esc(str(walkin_count))} walk\\-in drives\n"
    )


# ── HTTP send with retry ──────────────────────────────────────────────────────

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self._client: Optional[httpx.AsyncClient] = None
        self._last_send = 0.0

    def _url(self, method: str) -> str:
        return TG_BASE.format(token=self.token, method=method)

    async def _client_(self) -> httpx.AsyncClient:
        if not self._client or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    @retry(
        stop=stop_after_attempt(config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        retry=retry_if_exception_type(Exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _send_raw(self, text: str, parse_mode: str = "MarkdownV2") -> bool:
        # Rate limiting
        now = asyncio.get_event_loop().time()
        gap = _MSG_INTERVAL - (now - self._last_send)
        if gap > 0:
            await asyncio.sleep(gap)

        client = await self._client_()
        resp = await client.post(
            self._url("sendMessage"),
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": False,
            },
        )
        self._last_send = asyncio.get_event_loop().time()

        if resp.status_code == 429:
            retry_after = int(
                resp.json().get("parameters", {}).get("retry_after", 5)
            )
            logger.warning("Telegram rate-limited; sleeping %ds", retry_after)
            await asyncio.sleep(retry_after)
            raise httpx.HTTPStatusError(
                "429", request=resp.request, response=resp
            )

        if not resp.is_success:
            logger.error("Telegram API error %d: %s", resp.status_code, resp.text)
            return False

        return True

    async def send_job(self, job: dict) -> bool:
        try:
            msg = _format_job(job)
            return await self._send_raw(msg)
        except Exception as e:
            logger.error("Failed to send job alert: %s", e)
            return False

    async def send_summary(self, count: int, walkin_count: int) -> bool:
        try:
            msg = _format_summary(count, walkin_count)
            return await self._send_raw(msg)
        except Exception as e:
            logger.error("Failed to send summary: %s", e)
            return False

    async def send_text(self, text: str) -> bool:
        """Send a plain Markdown message (for status/error notifications)."""
        try:
            return await self._send_raw(text, parse_mode="Markdown")
        except Exception as e:
            logger.error("Failed to send text message: %s", e)
            return False

    async def send_jobs_batch(self, jobs: list[dict]) -> tuple[int, int]:
        """
        Send a batch of job alerts.
        Returns (sent_count, failed_count).
        """
        if not jobs:
            return 0, 0

        walkin_count = sum(1 for j in jobs if j.get("is_walkin"))
        await self.send_summary(len(jobs), walkin_count)

        sent, failed = 0, 0
        for job in jobs:
            ok = await self.send_job(job)
            if ok:
                sent += 1
            else:
                failed += 1

        logger.info("Telegram batch: %d sent, %d failed", sent, failed)
        return sent, failed

    async def test_connection(self) -> bool:
        client = await self._client_()
        try:
            resp = await client.get(self._url("getMe"))
            if resp.is_success:
                name = resp.json().get("result", {}).get("username", "?")
                logger.info("Telegram OK — bot: @%s", name)
                return True
            return False
        except Exception as e:
            logger.error("Telegram test failed: %s", e)
            return False

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ── Singleton getter ──────────────────────────────────────────────────────────

_notifier: Optional[TelegramNotifier] = None


def get_notifier() -> Optional[TelegramNotifier]:
    global _notifier
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — alerts disabled"
        )
        return None
    if _notifier is None:
        _notifier = TelegramNotifier(
            config.TELEGRAM_BOT_TOKEN,
            config.TELEGRAM_CHAT_ID,
        )
    return _notifier


# ── Sync wrappers for scheduler ───────────────────────────────────────────────

def send_jobs_sync(jobs: list[dict]) -> tuple[int, int]:
    notifier = get_notifier()
    if not notifier:
        return 0, 0
    return asyncio.run(notifier.send_jobs_batch(jobs))


def test_connection_sync() -> bool:
    notifier = get_notifier()
    if not notifier:
        return False
    return asyncio.run(notifier.test_connection())
