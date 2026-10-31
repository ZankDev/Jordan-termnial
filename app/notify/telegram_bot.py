"""Thin wrapper over the Telegram Bot API (sendPhoto / editMessageCaption / the
getUpdates long-poll + answerCallbackQuery pair used by the refresh button).

Reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from the environment on every call (cheap,
and lets a restart pick up freshly-set values without any extra caching logic). If
either is unset, every function here is a silent no-op — this feature is fully
optional and must never be the reason the live trading loop breaks.

Every call is wrapped so a Telegram/network hiccup only logs and returns None/False —
it must never propagate into live_engine.live_loop() and take other accounts down
with it (same guarantee live_engine._tick_all() already gives per-account).
"""
from __future__ import annotations

import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT_SECONDS = 15
LONG_POLL_TIMEOUT_SECONDS = 25


def _config() -> tuple[str, str] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None
    return token, chat_id


def is_configured() -> bool:
    return _config() is not None


def _refresh_button(account_id: int) -> str:
    return json.dumps(
        {"inline_keyboard": [[{"text": "\U0001F504 Refresh", "callback_data": f"refresh:{account_id}"}]]}
    )


def send_trade_open(caption: str, photo_bytes: bytes, account_id: int | None = None) -> int | None:
    """Sends the annotated setup photo with the trade caption. Returns the new
    message_id (for later edits) or None if not configured / the send failed."""
    cfg = _config()
    if not cfg:
        return None
    token, chat_id = cfg
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    if account_id is not None:
        data["reply_markup"] = _refresh_button(account_id)
    try:
        resp = requests.post(
            API_URL.format(token=token, method="sendPhoto"),
            data=data,
            files={"photo": ("setup.png", photo_bytes, "image/png")},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()["result"]["message_id"]
    except Exception:
        logger.exception("Telegram sendPhoto failed")
        return None


def send_trade_close(caption: str, photo_bytes: bytes, account_id: int | None = None) -> int | None:
    """Sends the exit chart (chart_render.render_setup_chart with exit_time/exit_price
    set) as its own new photo message when a trade closes - the "second image" alongside
    the entry photo send_trade_open already sent, so the close is a picture too, not just
    an edited caption on the original message."""
    cfg = _config()
    if not cfg:
        return None
    token, chat_id = cfg
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    if account_id is not None:
        data["reply_markup"] = _refresh_button(account_id)
    try:
        resp = requests.post(
            API_URL.format(token=token, method="sendPhoto"),
            data=data,
            files={"photo": ("close.png", photo_bytes, "image/png")},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()["result"]["message_id"]
    except Exception:
        logger.exception("Telegram sendPhoto (close) failed")
        return None


def edit_caption(message_id: int, caption: str, account_id: int | None = None) -> None:
    cfg = _config()
    if not cfg:
        return
    token, chat_id = cfg
    data = {"chat_id": chat_id, "message_id": message_id, "caption": caption, "parse_mode": "HTML"}
    if account_id is not None:
        data["reply_markup"] = _refresh_button(account_id)
    try:
        resp = requests.post(
            API_URL.format(token=token, method="editMessageCaption"),
            data=data,
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("Telegram editMessageCaption failed")


def send_circuit_card(photo_bytes: bytes) -> None:
    """Sends the daily circuit-breaker hit card — all the info is baked into the image
    itself (see chart_render.render_circuit_card), so unlike send_trade_open this needs
    no caption and no refresh button."""
    cfg = _config()
    if not cfg:
        return
    token, chat_id = cfg
    try:
        resp = requests.post(
            API_URL.format(token=token, method="sendPhoto"),
            data={"chat_id": chat_id},
            files={"photo": ("circuit.png", photo_bytes, "image/png")},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("Telegram circuit-card send failed")


def send_photo(photo_bytes: bytes, caption: str = "") -> None:
    """A photo with a short plain caption (morning brief card). No-op if not configured; never raises."""
    cfg = _config()
    if not cfg:
        return
    token, chat_id = cfg
    try:
        resp = requests.post(API_URL.format(token=token, method="sendPhoto"), data={"chat_id": chat_id, "caption": caption[:1000]},
                             files={"photo": ("brief.png", photo_bytes, "image/png")}, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
    except Exception:
        logger.exception("Telegram photo send failed")


def send_photo_rich(photo_bytes: bytes, html: str, buttons: list[list[dict]] | None = None) -> int | None:
    """A photo with an HTML caption (up to 1024 chars) and optional inline buttons. Returns the message_id, or None (caller falls back to text)."""
    cfg = _config()
    if not cfg:
        return None
    token, chat_id = cfg
    data = {"chat_id": chat_id, "caption": html, "parse_mode": "HTML"}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        resp = requests.post(API_URL.format(token=token, method="sendPhoto"), data=data,
                             files={"photo": ("card.png", photo_bytes, "image/png")}, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()["result"]["message_id"]
    except Exception:
        logger.exception("Telegram rich photo send failed")
        return None


def send_album(images: list[bytes], caption: str = "") -> bool:
    """2-10 photos as one swipeable album (sendMediaGroup); the caption rides on the first. False if not configured or it failed."""
    cfg = _config()
    if not cfg or not (2 <= len(images) <= 10):
        return False
    token, chat_id = cfg
    try:
        media = [{"type": "photo", "media": f"attach://p{i}", **({"caption": caption[:1000]} if i == 0 else {})} for i in range(len(images))]
        resp = requests.post(API_URL.format(token=token, method="sendMediaGroup"), data={"chat_id": chat_id, "media": json.dumps(media)},
                             files={f"p{i}": (f"slide{i + 1}.png", b, "image/png") for i, b in enumerate(images)}, timeout=120)
        resp.raise_for_status()
        return True
    except Exception:
        logger.exception("Telegram album send failed")
        return False


def send_document(data: bytes, filename: str, caption: str = "", mime: str = "application/pdf") -> bool:
    """A file (PDF by default). False if not configured or the upload failed."""
    cfg = _config()
    if not cfg:
        return False
    token, chat_id = cfg
    try:
        resp = requests.post(API_URL.format(token=token, method="sendDocument"), data={"chat_id": chat_id, "caption": caption[:1000]},
                             files={"document": (filename, data, mime)}, timeout=120)
        resp.raise_for_status()
        return True
    except Exception:
        logger.exception("Telegram document send failed")
        return False


def send_text(text: str) -> None:
    """Plain text message (auto-account swaps / evaluation results). No-op if Telegram is
    not configured; never raises."""
    cfg = _config()
    if not cfg:
        return
    token, chat_id = cfg
    try:
        resp = requests.post(
            API_URL.format(token=token, method="sendMessage"),
            data={"chat_id": chat_id, "text": text},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("Telegram text send failed")


def send_rich(html: str, buttons: list[list[dict]] | None = None) -> int | None:
    """HTML-formatted message (bold, italic, code, links) with optional inline buttons and no link preview. Returns the message_id, or None."""
    cfg = _config()
    if not cfg:
        return None
    token, chat_id = cfg
    data = {"chat_id": chat_id, "text": html, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        resp = requests.post(API_URL.format(token=token, method="sendMessage"), data=data, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()["result"]["message_id"]
    except Exception:
        logger.exception("Telegram rich send failed")
        return None


def send_text_id(text: str) -> int | None:
    """Plain text message that returns its message_id so it can be edited later (live status messages). None on failure."""
    cfg = _config()
    if not cfg:
        return None
    token, chat_id = cfg
    try:
        resp = requests.post(API_URL.format(token=token, method="sendMessage"), data={"chat_id": chat_id, "text": text}, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()["result"]["message_id"]
    except Exception:
        logger.exception("Telegram text send failed")
        return None


def edit_text(message_id: int, text: str) -> None:
    cfg = _config()
    if not cfg:
        return
    token, chat_id = cfg
    try:
        resp = requests.post(API_URL.format(token=token, method="editMessageText"), data={"chat_id": chat_id, "message_id": message_id, "text": text}, timeout=TIMEOUT_SECONDS)
        if resp.status_code not in (400, 429):  # 400 = "message is not modified"; 429 = edited too fast, the next update catches up
            resp.raise_for_status()
    except Exception:
        logger.exception("Telegram editMessageText failed")


def send_text_buttons(text: str, keyboard: list[list[dict]]) -> int | None:
    """Text with an inline keyboard (WEALTH daily plan checklist). Returns the message_id, or None."""
    cfg = _config()
    if not cfg:
        return None
    token, chat_id = cfg
    try:
        resp = requests.post(API_URL.format(token=token, method="sendMessage"),
                             data={"chat_id": chat_id, "text": text, "reply_markup": json.dumps({"inline_keyboard": keyboard})}, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()["result"]["message_id"]
    except Exception:
        logger.exception("Telegram text+buttons send failed")
        return None


def edit_text_buttons(message_id: int, text: str, keyboard: list[list[dict]]) -> None:
    cfg = _config()
    if not cfg:
        return
    token, chat_id = cfg
    try:
        resp = requests.post(API_URL.format(token=token, method="editMessageText"),
                             data={"chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": json.dumps({"inline_keyboard": keyboard})},
                             timeout=TIMEOUT_SECONDS)
        if resp.status_code != 400:  # 400 = "message is not modified" when nothing changed
            resp.raise_for_status()
    except Exception:
        logger.exception("Telegram editMessageText failed")


def delete_message(message_id: int) -> None:
    """Removes a previously-sent message outright — used for trades that close at a
    loss, so only winners are left visible in the chat instead of an edited-in-place
    'CLOSED - LOSS' caption."""
    cfg = _config()
    if not cfg:
        return
    token, chat_id = cfg
    data = {"chat_id": chat_id, "message_id": message_id}
    try:
        resp = requests.post(
            API_URL.format(token=token, method="deleteMessage"), data=data, timeout=TIMEOUT_SECONDS
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("Telegram deleteMessage failed")


def get_updates(offset: int | None) -> list[dict]:
    """Long-polls for new updates (used only for callback_query button taps — the bot
    never needs regular chat messages). Returns [] on any failure or if unconfigured,
    so the listener loop's own retry/backoff handles hiccups uniformly."""
    cfg = _config()
    if not cfg:
        return []
    token, _ = cfg
    # callback_query = inline button taps; message = the owner's text replies (WEALTH's monthly credit-card question)
    params = {"timeout": LONG_POLL_TIMEOUT_SECONDS, "allowed_updates": json.dumps(["callback_query", "message"])}
    if offset is not None:
        params["offset"] = offset
    try:
        resp = requests.get(
            API_URL.format(token=token, method="getUpdates"),
            params=params,
            timeout=LONG_POLL_TIMEOUT_SECONDS + 10,
        )
        resp.raise_for_status()
        return resp.json().get("result") or []
    except Exception:
        logger.exception("Telegram getUpdates failed")
        return []


def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    """Clears the button's loading spinner in the Telegram client."""
    cfg = _config()
    if not cfg:
        return
    token, _ = cfg
    data = {"callback_query_id": callback_query_id}
    if text:
        data["text"] = text
    try:
        resp = requests.post(
            API_URL.format(token=token, method="answerCallbackQuery"), data=data, timeout=TIMEOUT_SECONDS
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("Telegram answerCallbackQuery failed")


def fetch_chat_id(token: str) -> int | None:
    """One-off helper for initial setup: after the user has sent their new bot one
    message, this reads back the resulting chat_id via getUpdates so they don't have
    to find it themselves."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates", timeout=TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        results = resp.json().get("result") or []
        if not results:
            return None
        return results[-1]["message"]["chat"]["id"]
    except Exception:
        logger.exception("Telegram getUpdates failed")
        return None
