# -*- coding: utf-8 -*-
"""
scanner/aggregators.py — Agregatör sayfa kaynağı
------------------------------------------------
Kodları günlük derleyen siteler (MEXC blog, MiningCombo, redpacketcode.com…).
Bunlar genelde "Code Is: XXXXXXXX" kalıbıyla yayınlar; o kalıp aranır, ardından
serbest metin taraması yapılır.

Güven puanı: 0.55 (agregatör — hızlı ama bazen hatalı/eski kod basar).
"""
from __future__ import annotations

import re
import time

from scanner.base import BaseScanner

DEFAULT_FEEDS = [
    ("mexc", "https://blog.mexc.com/crypto-knowledge/binance-red-packet-codes-today/"),
    ("miningcombo", "https://miningcombo.com/red-packet/"),
    ("redpacketcode", "https://www.redpacketcode.com/"),
]

AGG_PATTERN = re.compile(r"Code\s+Is:?\s*([A-Za-z0-9]{8,16})", re.I)


class AggregatorScanner(BaseScanner):
    kind = "aggregator"
    confidence = 0.55
    source_name = "aggregators"

    def __init__(self, cfg: dict, queue: Any, logger: Any):
        super().__init__(cfg, queue, logger)
        s = cfg.get("scanner", {}).get("sources", {}).get("aggregators", {})
        self.feeds = list(s.get("feeds", [])) or DEFAULT_FEEDS
        self._interval = float(s.get("interval_seconds", 300))

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def scan(self) -> list[str]:
        codes: list[str] = []
        for name, url in self.feeds:
            try:
                html = self.fetch(url)
            except Exception as e:
                self.log.warning(f"aggregators/{name}: {e}")
                continue
            text = re.sub(r"<[^>]+>", " ", html)
            codes += [c.upper() for c in AGG_PATTERN.findall(text)]
            codes += self.codes_from_text(text)
            time.sleep(1.0)
        seen: set[str] = set()
        out = []
        for c in codes:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out
