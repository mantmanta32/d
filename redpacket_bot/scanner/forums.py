# -*- coding: utf-8 -*-
"""
scanner/forums.py — Forum & sosyal medya kaynağı
------------------------------------------------
- Arama motoru HTML araması (DuckDuckGo) ile X / Reddit / forum / haber
  sitelerindeki kod paylaşımlarını bulur (1. nesil kod_radar'ın stratejisi).
- Bölgesel sorgular (Hindistan, Rusya, Çin) eklenebilir — kodların bir kısmı
  yerel topluluklarda daha erken çıkar.
- `follow_hosts`: arama sonucundan güvenilen sitelerin sayfası da taranır.

Güven puanı: 0.40 (forum/sosyal — gürültü oranı yüksek, bu yüzden düşük).
"""
from __future__ import annotations

import re
import time
import urllib.parse

from scanner.base import BaseScanner

DEFAULT_QUERIES = [
    "binance red packet code today",
    "binance red packet code twitter",
    "binance red packet code reddit",
    "binance red packet code square",
]

FOLLOW_HOSTS = (
    "coingabbar.com", "bitrue.com", "mexc.com", "hokanews.com",
    "miningcombo.com", "quiknotes.in", "redpacketcode.com",
    "reddit.com", "bittime.com", "binance.com",
)


class ForumScanner(BaseScanner):
    kind = "forum"
    confidence = 0.40
    source_name = "forums"

    def __init__(self, cfg: dict, queue: Any, logger: Any):
        super().__init__(cfg, queue, logger)
        s = cfg.get("scanner", {}).get("sources", {}).get("forums", {})
        self.feeds = list(s.get("feeds", [])) or DEFAULT_QUERIES
        self._interval = float(s.get("interval_seconds", 300))
        self._ddg_sleep = 2.0

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def scan(self) -> list[str]:
        codes: list[str] = []
        for query in self.feeds:
            try:
                found, urls = self._ddg(query)
                codes += found
                for u in urls[:2]:  # güvenilen sitelerin sayfasını da tara
                    try:
                        codes += self.codes_from_text(self.fetch(u))
                    except Exception:
                        pass
                time.sleep(self._ddg_sleep)  # arama motorunu boğma
            except Exception as e:
                self.log.warning(f"forums/{query}: {e}")
        seen: set[str] = set()
        out = []
        for c in codes:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    # ------------------------------------------------------------------
    def _ddg(self, query: str) -> tuple[list[str], list[str]]:
        """DuckDuckGo HTML araması → (kodlar, güvenilen sitelerin URL'leri)."""
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        html = self.fetch(url)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
        codes: list[str] = []
        for s in snippets:
            codes += self.codes_from_text(re.sub(r"<[^>]+>", " ", s))
        urls: list[str] = []
        for href in re.findall(r'class="result__a"[^>]*href="([^"]+)"', html):
            m = re.search(r"uddg=([^&]+)", href)
            real = urllib.parse.unquote(m.group(1)) if m else href
            host = urllib.parse.urlparse(real).netloc.lower()
            if any(host.endswith(h) for h in FOLLOW_HOSTS):
                urls.append(real)
        seen: set[str] = set()
        out = []
        for c in codes:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out, urls
