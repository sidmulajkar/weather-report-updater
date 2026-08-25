"""Telegram delivery with a PACED sender + DRY-RUN mode.

Correct API base (the old plan used telegram.org which is wrong):
  https://api.telegram.org/bot<TOKEN>/sendMessage
  https://api.telegram.org/bot<TOKEN>/sendPhoto

Verified rate limits (core.telegram.org/bots/faq):
  - ~1 msg/sec to a single chat
  - 20 msg/min to the same group
  - ~30 msgs/sec bulk (free); above -> HTTP 429 with retry_after
We pace at 1 msg/sec/chat and honour 429 retry_after.

DRY-RUN: if TELEGRAM_BOT_TOKEN is not set, we DO NOT send. Instead we
print the message + image paths to stdout. This lets us verify the full
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


def send_message(chat_id: str, text: str, dry_run: bool = False, pace: float = 1.0, parse_mode: str = None) -> dict:
    if dry_run or not _token():
        print(f"\n[DRY-RUN] sendMessage -> chat {chat_id}:\n{text}\n")
        return {"ok": True, "dry_run": True}
    url = f"{API}/bot{_token()}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
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


def send_animation(chat_id: str, animation_path: str, caption: str = "", dry_run: bool = False, pace: float = 1.0, supports_streaming: bool = False) -> dict:
    """Native Telegram animation delivery for looping GIFs / MP4s."""
    if dry_run or not _token():
        print(f"[DRY-RUN] sendAnimation -> chat {chat_id}: {animation_path} (caption={caption!r}, streaming={supports_streaming})")
        return {"ok": True, "dry_run": True}
    url = f"{API}/bot{_token()}/sendAnimation"
    with open(animation_path, "rb") as f:
        files = {"animation": f}
        data = {"chat_id": chat_id, "caption": caption, "supports_streaming": str(supports_streaming).lower()}
        return _post_files(url, data, files, pace)


def transcode_gif_to_mp4(gif_path: str, mp4_path: str) -> dict:
    """Transcode GIF to H.264 MP4 for smaller size + native Telegram looping."""
    import subprocess
    cmd = [
        "ffmpeg", "-y", "-i", gif_path,
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-crf", "23",
        "-preset", "fast",
        mp4_path,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return {"ok": True, "mp4": mp4_path}
        return {"ok": False, "error": r.stderr[-500:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _post(url, payload, pace):
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 429:
            wait = float(r.json().get("parameters", {}).get("retry_after", pace))
            time.sleep(wait)
            return _post(url, payload, pace)
        # Telegram rejects malformed HTML/Markdown -> fall back to plain text
        if r.status_code == 400 and payload.get("parse_mode") in ("Markdown", "HTML"):
            p2 = dict(payload)
            p2.pop("parse_mode", None)
            return _post(url, p2, pace)
        if not r.ok:
            print(f"[TELEGRAM-ERR] sendMessage HTTP {r.status_code}: {r.text[:300]}")
        else:
            print(f"[TELEGRAM-OK] sendMessage -> chat {payload.get('chat_id')}")
        time.sleep(pace)
        return r.json()
    except requests.RequestException as e:
        print(f"[TELEGRAM-ERR] sendMessage network failure: {e}")
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
        if not r.ok:
            print(f"[TELEGRAM-ERR] sendPhoto HTTP {r.status_code}: {r.text[:300]}")
        else:
            print(f"[TELEGRAM-OK] sendPhoto -> chat {data.get('chat_id')}")
        time.sleep(pace)
        return r.json()
    except requests.RequestException as e:
        print(f"[TELEGRAM-ERR] sendPhoto network failure: {e}")
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    # quick self-test (dry-run)
    send_message("TEST", "hello from weather-report-updater (dry-run)")
