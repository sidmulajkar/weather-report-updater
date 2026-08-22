"""Telegram delivery with a PACED sender + DRY-RUN mode.

Correct API base (the old plan used telegram.org which is wrong):
  https://api.telegram.org/bot<TOKEN>/sendMessage
  https://api.telegram.org/bot<TOKEN>/sendPhoto

Verified rate limits (core.telegram.org/bots/faq):
  - ~1 msg/sec to a single chat
  - 20 msg/min to the same group
  - ~30 msgs/sec bulk (free); above -> HTTP 429 with retry_after
We pace at 1 msg/sec/chat and honour 429 retry_after.

DRY-RUN: if TELEGRAM_BOT_TOKEN is not set, we DO NOT send. Instead we write the
message + image paths to output/ and print them. This lets us verify the full
pipeline locally without a token (per your instruction).
"""
from __future__ import annotations
import os
import time
import json
import requests

API = "https://api.telegram.org"


def _token():
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def send_message(chat_id: str, text: str, dry_run: bool = False, pace: float = 1.0) -> dict:
    if dry_run or not _token():
        print(f"\n[DRY-RUN] sendMessage -> chat {chat_id}:\n{text}\n")
        return {"ok": True, "dry_run": True}
    url = f"{API}/bot{_token()}/sendMessage"
    # MarkdownV2 needs escaping; use HTML to avoid surprises in a report with special chars
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    return _post(url, payload, pace)


def send_photo(chat_id: str, photo_path: str, caption: str = "", dry_run: bool = False, pace: float = 1.0) -> dict:
    if dry_run or not _token():
        print(f"[DRY-RUN] sendPhoto -> chat {chat_id}: {photo_path} (caption={caption!r})")
        return {"ok": True, "dry_run": True}
    url = f"{API}/bot{_token()}/sendPhoto"
    with open(photo_path, "rb") as f:
        files = {"photo": f}
        data = {"chat_id": chat_id, "caption": caption}
        return _post_files(url, data, files, pace)


def _post(url, payload, pace):
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 429:
            wait = float(r.json().get("parameters", {}).get("retry_after", pace))
            time.sleep(wait)
            return _post(url, payload, pace)
        # Telegram rejects messages with malformed Markdown -> fall back to plain text
        if r.status_code == 400 and payload.get("parse_mode") == "Markdown":
            p2 = dict(payload); p2.pop("parse_mode", None)
            return _post(url, p2, pace)
        time.sleep(pace)
        return r.json()
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}


def verify_token() -> bool:
    """Return True if TELEGRAM_BOT_TOKEN is set and looks valid (non-empty)."""
    t = _token()
    return bool(t and len(t) > 10)


def _post_files(url, data, files, pace):
    try:
        r = requests.post(url, data=data, files=files, timeout=20)
        if r.status_code == 429:
            wait = float(r.json().get("parameters", {}).get("retry_after", pace))
            time.sleep(wait)
            return _post_files(url, data, files, pace)
        # caption with malformed Markdown -> retry without parse_mode
        if r.status_code == 400 and data.get("parse_mode") == "Markdown":
            d2 = dict(data); d2.pop("parse_mode", None)
            return _post_files(url, d2, files, pace)
        time.sleep(pace)
        return r.json()
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    # quick self-test (dry-run)
    send_message("TEST", "hello from weather-updater (dry-run)")
