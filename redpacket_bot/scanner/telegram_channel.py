# -*- coding: utf-8 -*-
"""
scanner/telegram_channel.py — Telegram kanal kaynağı
----------------------------------------------------
- Genel kanalların t.me/s/<kanal> aynasından HTML okunur (oturum gerektirmez,
  kanal genel ise çalışır).
- Kullanıcı settings'te kanal listesini doldurur; ayrıca API tabanlı özel
  kanal erişimi için `api_id/api_hash` alanları da desteklenir (boşsa atlanır).

Güven puanı: 0.90 (en hızlı kaynak türü — kodlar genelde burada ilk yayınlanır).
"""
from __future__ import annotations

import re
import time

from scanner.base import BaseScanner


class TelegramChannelScanner(BaseScanner):
    kind = "telegram"
    confidence = 0.90
    source_name = "telegram"

    def __init__(self, cfg: dict, queue: Any, logger: Any):
        super().__init__(cfg, queue, logger)
        s = cfg.get("scanner", {}).get("sources", {}).get("telegram", {})
        self.channels = list(s.get("channels", []))
        self.api_id = s.get("api_id", "") or ""
        self.api_hash = s.get("api_hash", "") or ""
        self._interval = float(s.get("interval_seconds", 60))

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def scan(self) -> list[str]:
        if not self.channels:
            self.log.info("telegram: kanal listesi boş — settings.json'da "
                          "'channels' dizisini doldur (örn. ['binance_red_packet_code_daily_0']).")
            return []
        codes: list[str] = []
        for ch in self.channels:
            ch = ch.strip().lstrip("@")
            if not ch:
                continue
            url = f"https://t.me/s/{ch}"
            try:
                html = self.fetch(url)
            except Exception as e:
                self.log.warning(f"telegram/{ch}: {e}")
                continue
            # t.me/s aynasında mesajlar <div class="tgme_widget_message_text"> içindedir
            blocks = re.findall(r'class="tgme_widget_message_text"[^>]*>(.*?)</div>', html, re.S)
            if not blocks:
                blocks = [html]
            for b in blocks:
                codes += self.codes_from_text(re.sub(r"<[^>]+>", " ", b))
        # benzersizleştir (sıra korunur)
        seen: set[str] = set()
        out = []
        for c in codes:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out
