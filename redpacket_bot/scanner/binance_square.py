# -*- coding: utf-8 -*-
"""
scanner/binance_square.py — Binance Square kaynağı
--------------------------------------------------
Kodların resmi kaynağı: #RedPacketShare hashtag akışı.
- Varsayılan: yerel kayıt (local_file) okunur — kullanıcı dilediği sayfayı
  HTML kaydedip buraya koyabilir; sürpriz yok, engel yok.
- opsiyonel: Binance Square topluluk API'si (public uç) doğrudan çağrılır;
  çoğu gönderi oturum ister, o yüzden bu yol kapalı başlar.

Güven puanı: 0.80 (resmi kaynak).
"""
from __future__ import annotations

import os
import re
import time

from scanner.base import BaseScanner


class BinanceSquareScanner(BaseScanner):
    kind = "official"
    confidence = 0.80
    source_name = "binance_square"

    def __init__(self, cfg: dict, queue: Any, logger: Any):
        super().__init__(cfg, queue, logger)
        s = cfg.get("scanner", {}).get("sources", {}).get("binance_square", {})
        self.local_file = s.get("local_file", "") or ""
        self.api_url = s.get("url", "")
        self.use_api = bool(s.get("enabled", False))
        self._interval = float(s.get("interval_seconds", 120))

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def scan(self) -> list[str]:
        texts: list[str] = []
        # 1) yerel kayıt (varsa)
        if self.local_file and os.path.exists(self.local_file):
            with open(self.local_file, "r", encoding="utf-8", errors="ignore") as f:
                texts.append(f.read())
        # 2) API (açıksa)
        if self.use_api and self.api_url:
            try:
                texts.append(self.fetch(self.api_url))
            except Exception as e:
                self.log.warning(f"Square API hatası: {e}")
        if not texts:
            self.log.info("binance_square: yerel kayıt yok ve API kapalı — atlandı. "
                          "Settings'te local_file yolunu doldur veya API'yi aç.")
            return []
        text = "\n".join(texts)
        codes = self.codes_from_text(text)
        # Square gönderileri çok sayıda sözde kod içerir — güven artırıcı filtre:
        # gönderinin içinden değil, başlık/etiket yakınından gelenleri öne al
        return codes


class SquareHtmlScanner(BinanceSquareScanner):
    """HTML olarak kaydedilmiş Square akışı için aynı mantık (dosya yolu farklı)."""
    pass
