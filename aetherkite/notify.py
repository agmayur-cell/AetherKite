"""
Telegram alerts: HTML-escaped, de-duplicated, sent from one background worker.
"""

import html
import json
import logging
import queue
import threading
import urllib.request
from typing import Optional, Tuple

log = logging.getLogger("aetherkite.notify")

esc = html.escape


def send_telegram(message: str, token: str, chat_id: str, timeout: float = 5.0) -> Tuple[bool, Optional[str]]:
    if not token or not chat_id:
        return False, "Telegram bot token and chat ID are required."
    try:
        payload = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML",
                              "disable_web_page_preview": True}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "AetherKite/2.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200, None
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        return False, str(e)


class Notifier:
    def __init__(self, sender=send_telegram, max_keys: int = 5000):
        self.sender = sender
        self._queue: "queue.Queue" = queue.Queue(maxsize=200)
        self._sent_keys = []
        self._sent_set = set()
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._thread = None

    def _worker(self):
        while True:
            message, token, chat_id = self._queue.get()
            self.sender(message, token, chat_id)
            self._queue.task_done()

    def _ensure_worker(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, name="telegram", daemon=True)
            self._thread.start()

    def notify(self, message: str, cfg: dict, dedupe_key: Optional[str] = None, force: bool = False) -> bool:
        """Queues a message if alerts are enabled (or `force`). Returns False if skipped."""
        if not force and not cfg.get("telegram_alerts_enabled"):
            return False
        token, chat_id = (cfg.get("telegram_bot_token") or "").strip(), (cfg.get("telegram_chat_id") or "").strip()
        if not token or not chat_id:
            return False
        with self._lock:
            if dedupe_key is not None:
                if dedupe_key in self._sent_set:
                    return False
                self._sent_set.add(dedupe_key)
                self._sent_keys.append(dedupe_key)
                if len(self._sent_keys) > self._max_keys:
                    self._sent_set.discard(self._sent_keys.pop(0))
        try:
            self._ensure_worker()
            self._queue.put_nowait((message, token, chat_id))
            return True
        except queue.Full:
            log.warning("Telegram queue full; dropping alert")
            return False
