# -*- coding: utf-8 -*-
"""
scanner/demo.py — Demo kaynak (yalnızca simülasyon modu)
--------------------------------------------------------
Gerçek ağ erişimi olmadan otonom döngüyü göstermek için sentetik kod üretir.
Kaynağı "demo_telegram" olarak etiketler — böylece hafıza/öğrenme istatistikleri
de gerçekçi şekilde dolar. Canlı modda otomatik devre dışı kalır.
"""
from __future__ import annotations

import random
from typing import Any

from scanner.base import BaseScanner
from utils.helpers import random_code


class DemoScanner(BaseScanner):
    kind = "telegram"
    confidence = 0.90
    source_name = "demo_telegram"

    def __init__(self, cfg: dict, queue: Any, logger: Any):
        super().__init__(cfg, queue, logger)
        self._interval = 20.0
        self.enabled = bool(cfg.get("demo", {}).get("enabled", True))
        if not self.enabled:
            self.log.info("demo_telegram: demo kaynağı ayardan kapatılmış.")

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def scan(self) -> list[str]:
        if not self.enabled:
            return []
        # her taramada 0–2 arası "yeni yayınlanmış" kod
        n = random.randint(0, 2)
        return [random_code() for _ in range(n)]
