# -*- coding: utf-8 -*-
"""
observer/notifier.py — Akıllı bildirim
--------------------------------------
Kural: yalnızca ÖNEMLİ olaylar bildirilir (yeni rekor kazanç, oturum bitmek
üzere, sistem sorunu). Gürültü bastırılır (throttle: aynı tür bildirim en
fazla N saniyede bir).

Uçlar: webhook (Discord/Slack/Telegram bot için genel URL) ve/veya konsol.
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Optional

#: bildirime değer olay türleri + minimum seviye
NOTIFIABLE_LEVELS = {"WARNING", "ERROR", "CRITICAL", "SUCCESS"}
#: SUCCESS bildirimleri yalnızca kazanç rekoru kırılınca
SUCCESS_NOTIFY_MARKER = "rekor"


class Notifier:
    def __init__(self, cfg: dict, logger: Any):
        o = cfg.get("observer", {})
        self.webhook_url = o.get("webhook_url", "") or ""
        self.min_level = o.get("notify_min_level", "WARNING")
        self.throttle = float(o.get("notify_throttle_seconds", 300))
        self.log = logger
        self._last_sent: dict[str, float] = {}
        self.sent_count = 0

    def on_event(self, event: dict) -> None:
        """observer olay akışından çağrılır — önemli olayları dışarı bildirir."""
        level = event.get("level", "INFO")
        if level not in NOTIFIABLE_LEVELS:
            return
        if level == "SUCCESS" and SUCCESS_NOTIFY_MARKER not in event.get("message", "").lower():
            return
        key = f"{level}:{event.get('component', '')}"
        now = time.time()
        if now - self._last_sent.get(key, 0) < self.throttle:
            return
        self._last_sent[key] = now
        self.sent_count += 1
        text = f"[{event.get('level')}] {event.get('component')}: {event.get('message')}"
        print(f"  🔔 BİLDİRİM: {text}", flush=True)
        if self.webhook_url:
            self._send_webhook(text)

    def _send_webhook(self, text: str) -> None:
        try:
            payload = json.dumps({"text": text}).encode()
            req = urllib.request.Request(self.webhook_url, data=payload,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
            self.log.info("notifier", f"Webhook gönderildi: {text[:80]}")
        except Exception as e:
            self.log.warning("notifier", f"Webhook hatası: {e}")

    def snapshot(self) -> dict:
        return {"webhook_set": bool(self.webhook_url), "sent": self.sent_count,
                "throttle_seconds": self.throttle}
