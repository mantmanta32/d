# -*- coding: utf-8 -*-
"""
brain/adaptive_delay.py — Kendini ayarlayan hız denetleyici
-----------------------------------------------------------
Hedef: "ne çok hızlanıp engel yiyelim, ne de yavaş kalalım."

Kural tabanlı PID benzeri kontrol:
- Her rate_limited → gecikme ×1.6 (yumuşak geri çekilme)
- Her success → gecikme ×0.92 (temkinli hızlanma)
- Her banned → gecikme ×3.0 + acil durum bayrağı (planner durur)
- Alt/üst sınırlar settings'ten gelir; jitter istekleri insansı yapar.
"""
from __future__ import annotations

import random
from typing import Any, Optional


class AdaptiveDelay:
    def __init__(self, cfg: dict, logger: Any):
        c = cfg.get("claimer", {})
        self.min_delay = float(c.get("min_delay_seconds", 1.0))
        self.max_delay = float(c.get("max_delay_seconds", 30.0))
        self.jitter = float(c.get("jitter_ratio", 0.25))
        self.log = logger
        self.delay = self.min_delay
        self.rate_limit_strikes = 0
        self.success_streak = 0
        self.emergency_stop = False

    def on_result(self, category: str) -> None:
        """Her claim sonucunda gecikme politikasını güncelle."""
        if category == "success":
            self.success_streak += 1
            self.rate_limit_strikes = 0
            # istikrarlı başarı → temkinli hızlanma (min sınırına kadar)
            self.delay = max(self.min_delay, self.delay * 0.92)
            if self.success_streak > 0 and self.success_streak % 10 == 0:
                self.log.info(f"Hız denetleyici: {self.success_streak} başarı serisi, "
                              f"gecikme {self.delay:.1f}s'ye düştü.")
        elif category == "rate_limited":
            self.rate_limit_strikes += 1
            self.success_streak = 0
            self.delay = min(self.max_delay, self.delay * 1.6)
            self.log.warning(f"Rate limit algılandı (strike #{self.rate_limit_strikes}) — "
                             f"gecikme {self.delay:.1f}s'ye çıkarıldı.")
        elif category == "banned":
            self.emergency_stop = True
            self.delay = min(self.max_delay * 2, self.delay * 3.0)
            self.log.critical("BAN algılandı! Acil durdurma bayrağı kuruldu.")
        elif category == "session_expired":
            # oturum sorunu hızla çözülmeli — gecikmeyi çok değiştirme
            self.success_streak = 0
        else:  # expired / invalid / network_error
            self.success_streak = 0

    def wait(self) -> float:
        """Bir sonraki istek öncesi beklenmesi gereken süre (jitter dahil)."""
        j = 1.0 + random.uniform(-self.jitter, self.jitter)
        return max(0.0, self.delay * j)

    def snapshot(self) -> dict:
        return {"current_delay": round(self.delay, 2),
                "min_delay": self.min_delay,
                "max_delay": self.max_delay,
                "rate_limit_strikes": self.rate_limit_strikes,
                "success_streak": self.success_streak,
                "emergency_stop": self.emergency_stop}
