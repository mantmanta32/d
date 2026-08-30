# -*- coding: utf-8 -*-
"""
scanner/base.py — Kaynak tarayıcılarının ortak tabanı
-----------------------------------------------------
Her kaynak türü (Square, Telegram, forum, agregatör) bu sınıftan türer ve
yalnızca `scan()` gerçekleştirmesini yazar. Güven puanı (0–1) kaynak türüne
göre belirlenir:

    Telegram / özel topluluk  → 0.90  (en hızlı, en güvenilir)
    Resmi sayfa / Square       → 0.80
    Agregatör sayfa            → 0.55
    Forum / sosyal medya       → 0.40

Ortak görevler: metin indirme (isimsiz, UA'lı), kod çıkarma, akıllı
filtreleme, benzersizlik (seen seti), yeni kodları kuyruğa bildirme.
"""
from __future__ import annotations

import time
import urllib.request
from typing import Any, Optional

from utils.helpers import extract_codes

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

#: Kaynak türü → temel güven puanı
TYPE_CONFIDENCE = {
    "telegram": 0.90,
    "official": 0.80,
    "aggregator": 0.55,
    "forum": 0.40,
    "social": 0.35,
}


class BaseScanner:
    kind = "base"
    confidence = 0.5

    def __init__(self, cfg: dict, queue: Any, logger: Any):
        self.cfg = cfg
        self.queue = queue
        self.log = logger
        self.seen: set[str] = set()
        self.last_scan_at = 0.0
        self.scan_count = 0
        s = cfg.get("scanner", {})
        self.blacklist = set(s.get("blacklist", [])) | {
            "redpacket", "password", "username", "placeholder", "example",
            "testing", "campaign", "validity", "security", "verified",
            "exchange", "redeemed", "featured", "accounts", "download",
            "binance", "bonus", "claim", "gift", "promo", "reward",
        }

    # ------------------------------------------------------------------
    def scan(self) -> list[str]:
        """Alt sınıflar bu metodu doldurur → yeni (benzersiz) kod listesi."""
        raise NotImplementedError

    def tick(self, force: bool = False) -> list[dict]:
        """Dış dünyaya açık tek giriş: tarama yapar, yeni kodları kuyruğa ekler.

        Dönen liste: kuyruğa eklenen yeni kayıtlar.
        """
        interval = float(self.interval_seconds)
        if not force and self.last_scan_at and (time.time() - self.last_scan_at) < interval:
            return []
        self.last_scan_at = time.time()
        try:
            codes = self.scan()
        except Exception as e:
            self.log.warning(f"{self.name} tarama hatası: {e}")
            return []
        self.scan_count += 1
        added = []
        for code in codes:
            if code in self.seen:
                continue
            self.seen.add(code)
            if self.queue.add(code, source=self.name, confidence=self.confidence):
                added.append({"code": code, "source": self.name,
                              "confidence": self.confidence})
        if added:
            self.log.info(f"{self.name}: {len(added)} yeni kod bulundu — "
                          f"{', '.join(a['code'] for a in added[:5])}{'…' if len(added) > 5 else ''}")
        else:
            self.log.info(f"{self.name}: tarama tamam, yeni kod yok "
                          f"(toplam {len(self.seen)} benzersiz)")
        return added

    # ------------------------------------------------------------------
    # Yardımcılar
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        return getattr(self, "source_name", self.kind)

    @property
    def interval_seconds(self) -> float:
        return 120.0

    @staticmethod
    def fetch(url: str, timeout: float = 25.0) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")

    def codes_from_text(self, text: str) -> list[str]:
        return extract_codes(text, blacklist=self.blacklist)

    def snapshot(self) -> dict:
        return {"name": self.name, "kind": self.kind, "confidence": self.confidence,
                "seen": len(self.seen), "scans": self.scan_count,
                "last_scan_at": self.last_scan_at}
