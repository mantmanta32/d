# -*- coding: utf-8 -*-
"""
defense/error_counter.py — KESİN CAN SAYACI & GÜVENLİ SINIR KORUMASI
---------------------------------------------------------------------
Binance'in koruması: ~10 hatalı giriş → kilitlenme. Bu modül o sayacı
uygulama seviyesinde birebir tutar ve **asla 10'a vardırmaz**:

- Her hata türünün farklı bir "can maliyeti" var:
      invalid (biçim bozuk / hiç yok)  → 1.0 can  (kesin çöp → kaynak cezalandırılır)
      expired (dolmuş ama gerçekti)    → 0.5 can  (kaynak itibarlı kalabilir)
      rate_limited / lock sinyali      → can yemez ama acil geri çekilme tetikler
- Son `reserve_lives` can asla harcanmaz ("acil doğrulama" için saklanır).
- `critical_usage` aşıldığında yalnızca çok düşük riskli denemelere izin verilir.
- Canlar zamanla geri gelir (recovery_per_hour) — tıpkı Binance'teki sayacın
  sıfırlanmasının zaman alması gibi; eskime (decay) doğrusaldır.

Sayaç şeffaftır: her şey günlüklenir, kilitlenme sinyali görülürse
emergency_stop bayrağı kalkar ve planlayıcı durur.
"""
from __future__ import annotations

from typing import Any, Optional

from utils.helpers import VirtualClock


class ErrorCounter:
    def __init__(self, cfg: dict, logger: Any, clock: Optional[VirtualClock] = None):
        d = cfg.get("defense", {})
        self.max_lives = float(d.get("max_lives", 10))
        self.reserve_lives = float(d.get("reserve_lives", 2))
        self.critical_usage = float(d.get("critical_usage", 7))
        self.recovery_per_hour = float(d.get("recovery_per_hour", 1.0))
        self.window_hours = float(d.get("window_hours", 24))
        self.expired_cost = float(d.get("expired_life_cost", 0.5))
        self.invalid_cost = float(d.get("invalid_life_cost", 1.0))
        self.emergency_stop_on_lock = bool(d.get("emergency_stop_on_lock_signal", True))
        self.log = logger
        self.clock = clock or VirtualClock(1.0)

        #: (zaman, can maliyeti) kayıtları — can toplamı bunlardan hesaplanır
        self.failures: list[tuple[float, float]] = []
        self.emergency_stop = False
        self.retreat_strikes = 0
        self.total_lives_lost = 0.0
        self.last_category = ""

    # ------------------------------------------------------------------
    # Can hesaplama
    # ------------------------------------------------------------------
    def usage(self) -> float:
        """Şu anki toplam can kaybı (zamanla eskiyen hatalar düşülür)."""
        now = self.clock.now()
        window = self.window_hours * 3600.0
        total = 0.0
        for ts, cost in self.failures:
            age = max(0.0, now - ts)
            if age >= window:
                continue
            # doğrusal eskime: window sonunda maliyet sıfırlanır
            total += cost * (1.0 - age / window)
        return min(total, self.max_lives)

    def lives_left(self) -> float:
        return max(0.0, self.max_lives - self.usage())

    def can_attempt(self, risk: float = 0.0) -> bool:
        """Bir kod denenebilir mi? (risk 0..1; 1 = kesin çöp)

        Kurallar:
        1. Kilitlenme sinyali → HAYIR (acil duruş).
        2. Rezerv canlara dokunma → kalan can <= reserve+ε → HAYIR.
           (ε: can eskimesi/yuvarlama dalgalanması rezervi asla yemesin)
        3. Kritik eşik aşıldıysa yalnızca düşük riskli denemeler → HAYIR/yüksek risk.
        """
        if self.emergency_stop:
            return False
        if self.lives_left() <= self.reserve_lives + 0.1:
            return False
        if self.usage() >= self.critical_usage and risk > 0.3:
            return False
        return True

    # ------------------------------------------------------------------
    def record(self, category: str) -> None:
        """Her claim sonucu buraya beslenir — canları ve bayrakları günceller."""
        self.last_category = category
        now = self.clock.now()
        if category == "invalid":
            self.failures.append((now, self.invalid_cost))
            self.total_lives_lost += self.invalid_cost
            self.log.warning("defense",
                             f"CAN KAYBI (invalid, -{self.invalid_cost:.1f}): "
                             f"kalan {self.lives_left():.1f}/{self.max_lives:.0f}")
        elif category == "expired":
            # gerçek koddan geç kaldık: yarım can, kaynak cezalandırılmaz
            self.failures.append((now, self.expired_cost))
            self.total_lives_lost += self.expired_cost
            self.log.info("defense",
                          f"Yarım can (expired, -{self.expired_cost:.1f}): "
                          f"kalan {self.lives_left():.1f}/{self.max_lives:.0f}")
        elif category == "banned":
            if self.emergency_stop_on_lock:
                self.emergency_stop = True
            self.log.critical("defense", "KİLİTLENME SİNYALİ (banned) — acil duruş!")
        elif category == "rate_limited":
            self.retreat_strikes += 1
            self.log.warning("defense", f"GERİ ÇEKİLME sinyali (rate_limited) "
                                        f"#{self.retreat_strikes} — hız düşürülüyor")
        # success / session_expired / network_error → can yemez

    def clear_emergency(self) -> None:
        """Manuel onay sonrası acil duruşu kaldırır (planner.resume ile)."""
        self.emergency_stop = False
        self.retreat_strikes = 0
        self.log.info("defense", "Acil duruş kaldırıldı (manuel onay).")

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "max_lives": self.max_lives,
            "lives_left": round(self.lives_left(), 2),
            "usage": round(self.usage(), 2),
            "critical_usage": self.critical_usage,
            "reserve_lives": self.reserve_lives,
            "emergency_stop": self.emergency_stop,
            "retreat_strikes": self.retreat_strikes,
            "total_lives_lost": round(self.total_lives_lost, 2),
            "last_category": self.last_category,
            "recovery_per_hour": self.recovery_per_hour,
        }
