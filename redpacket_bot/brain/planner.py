# -*- coding: utf-8 -*-
"""
brain/planner.py — Karar & planlama motoru (OTONOMİNİN KALBİ)
--------------------------------------------------------------
Kendini yönetme döngüsü (her plan_tick'te):

    1. DEĞERLENDİR  → oturum sağlığı, kuyruk durumu, başarı oranı,
                      hız denetleyici, öğrenilmiş kaynak/saat istatistikleri
    2. HEDEF BELİRLE → kuyruktan öncelikli kodları seç (queue.next_batch)
    3. EYLEM PLANLA  → gecikme süreleri, deneme sırası, acil durum kontrolleri
    4. UYGULA & GÖZLEMLE → claim et, sonucu hafızaya yaz, geri bildirim ver
    5. ÖĞREN & GELİŞ → kaynak skorları / en iyi saatleri rapora yansıt

Güvenlik şeffaflığı: her adım observer'a olay (event) olarak bildirilir;
şüpheli durumda (ban, ölü oturum, yüksek rate-limit oranı) plan durur ve raporlar.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from utils.helpers import VirtualClock


class Planner:
    def __init__(self, cfg: dict, queue: Any, connector: Any, session: Any,
                 delay: Any, memory: Any, observer: Any, clock: Optional[VirtualClock] = None):
        self.cfg = cfg
        self.queue = queue
        self.connector = connector
        self.session = session
        self.delay = delay
        self.memory = memory
        self.obs = observer
        self.clock = clock or VirtualClock(1.0)
        b = cfg.get("brain", {})
        self.rate_limit_ratio_threshold = float(b.get("rate_limit_ratio_threshold", 0.3))
        self.emergency_stop_on_ban = bool(b.get("emergency_stop_on_ban", True))
        self.plans_executed = 0
        self.paused = False
        self.pause_reason = ""

    # ------------------------------------------------------------------
    def plan_tick(self) -> dict:
        """Bir yönetim turu çalıştırır. Sonuç raporu döndürür (observer'ı besler)."""
        report = {"plans_executed": self.plans_executed, "actions": [],
                  "paused": self.paused, "pause_reason": self.pause_reason}

        # 1 — DEĞERLENDİR
        session_state = self.session.heartbeat(last_request_ok=True)
        if session_state in ("dead",):
            self._pause("Oturum ölü — yenileme başarısız. İnsan müdahalesi gerekli.")
            report["actions"].append("STOP: oturum ölü")
            self.obs.event("CRITICAL", "planner", "Oturum yenilenemedi, sistem durduruldu")
            return report
        if session_state in ("expiring", "expired"):
            self.obs.event("WARNING", "planner", f"Oturum {session_state} — yenileme planlandı")

        if self.paused:
            report["actions"].append("PAUSED (önceki acil durum bekleniyor)")
            return report

        # 2 — HEDEF BELİRLE (alt sınıflar kendi stratejisini kurabilir)
        batch = self.select_targets()
        report["batch"] = [i["code"] for i in batch]
        if self.paused:
            report["actions"].append("PAUSED (seçim sırasında acil durum)")
            return report
        if not batch:
            report["actions"].append("Kuyruk boş — tarayıcıdan yeni kod bekleniyor")
            self.obs.event("INFO", "planner", "Kuyruk boş, tur tamamlandı")
            return report

        # 3 & 4 — EYLEM PLANLA & UYGULA
        rl_strikes = self.delay.rate_limit_strikes
        outcomes = []
        for item in batch:
            if self.delay.emergency_stop and self.emergency_stop_on_ban:
                self._pause("Ban tespit edildi — güvenlik duruşu")
                break
            # savunma kancası: alt sınıflar (SafePlanner) istek öncesi son kontrol
            if not self.before_claim(item):
                self.obs.event("INFO", "planner",
                               f"{item['code']} atlandı (savunma kontrolü)")
                continue
            wait = self.delay.wait()
            self.clock.sleep(wait)
            self.obs.event("INFO", "planner",
                           f"Claim deneniyor: {item['code']} (gecikme {wait:.1f}s, "
                           f"kaynak: {item.get('source')})")
            self.queue.mark(item["code"], "claiming")
            result = self.connector.claim(item["code"])
            result["source"] = item.get("source")
            result["confidence"] = item.get("confidence")
            result["delay"] = wait

            # 5 — ÖĞREN & GELİŞ (sonuç işleme; alt sınıflar genişletebilir)
            outcome = self.handle_result(item, result)
            if outcome is None:  # acil durum → turu durdur
                break
            outcomes.append(outcome)

        # --- öğrenme geri bildirimi: hız sınırı oranı yüksekse planı yavaşlat
        rl_ratio = self._rate_ratio(outcomes)
        if rl_ratio > self.rate_limit_ratio_threshold:
            self.obs.event("WARNING", "planner",
                           f"Rate-limit oranı yüksek (%{rl_ratio:.0f}) — gecikme artırıldı, "
                           f"plan yeniden düzenleniyor")
            self.delay.delay = min(self.delay.max_delay, self.delay.delay * 1.4)

        self.plans_executed += 1
        report["outcomes"] = outcomes
        report["actions"].append(f"Plan uygulandı: {len(batch)} kod, "
                                 f"sonuç: {self._summarize(outcomes)}")
        self.obs.event("INFO", "planner",
                       f"Tur tamam: {len(batch)} kod işlendi, "
                       f"başarı oranı %{self.memory.success_rate():.1f}")
        return report

    # ------------------------------------------------------------------
    def select_targets(self) -> list[dict]:
        """Hedef seçimi — alt sınıflar (SafePlanner) risk/denge stratejisi kurar."""
        return self.queue.next_batch()

    def before_claim(self, item: dict) -> bool:
        """İstek öncesi son kontrol kancası — alt sınıflar genişletebilir."""
        return True

    def handle_result(self, item: dict, result: dict) -> Optional[str]:
        """Tek sonuç işleme — alt sınıflar savunma/istihbarat beslemesi ekler.

        Dönüş: sonuç kategorisi, ya da None (acil durum → tur durdurulur).
        """
        # 5 — ÖĞREN: hafızaya yaz, hız denetleyiciyi güncelle, kuyruğu işaretle
        self.memory.record_result(result)
        self.delay.on_result(result["category"])
        cat = result["category"]
        if cat == "success":
            self.queue.mark(item["code"], "success")
            self.obs.event("SUCCESS", "planner",
                           f"KOD KAZANILDI: {item['code']} (kaynak: {item.get('source')})")
        elif cat in ("expired", "invalid", "banned"):
            self.queue.mark(item["code"], cat)
            self.obs.event("WARNING" if cat == "banned" else "INFO",
                           "planner", f"{item['code']} → {cat}")
        elif cat == "session_expired":
            # oturum öldü: hemen yenilemeyi tetikle, başarısızsa güvenlik duruşu
            self.obs.event("WARNING", "planner",
                           f"{item['code']} → session_expired — oturum yenileniyor…")
            if self.session.renew():
                self.queue.mark(item["code"], "pending")
            else:
                self.queue.mark(item["code"], "session_expired")
                self._pause("Oturum yenilenemedi (session_expired) — durduruluyor")
                return None
        elif cat in ("rate_limited", "network_error"):
            # geçici hatalar: deneme hakkı kalmadıysa düşür, kaldıysa pending'e geri koy
            if item.get("attempts", 0) >= self.queue.max_retries:
                self.queue.mark(item["code"], cat)
                self.obs.event("WARNING", "planner",
                               f"{item['code']} deneme hakkı bitti → {cat}")
            else:
                self.queue.mark(item["code"], "pending")
        return cat

    # ------------------------------------------------------------------
    def insights(self) -> dict:
        """Öğrenilmiş bilgiler — observer/rapor katmanına verilir."""
        return {
            "top_sources": self.memory.top_sources(),
            "best_hours": self.memory.best_hours(),
            "success_rate": round(self.memory.success_rate(), 1),
        }

    def resume(self) -> None:
        self.paused = False
        self.pause_reason = ""
        self.delay.emergency_stop = False
        self.obs.event("INFO", "planner", "Planlama devam ettirildi (manuel onay)")

    # ------------------------------------------------------------------
    def _rate_ratio(self, outcomes: list[str]) -> float:
        if not outcomes:
            return 0.0
        return sum(1 for o in outcomes if o == "rate_limited") / len(outcomes)

    def _summarize(self, outcomes: list[str]) -> str:
        from collections import Counter
        return ", ".join(f"{k}:{v}" for k, v in Counter(outcomes).most_common())

    def _pause(self, reason: str) -> None:
        self.paused = True
        self.pause_reason = reason
        self.obs.event("CRITICAL", "planner", f"DURDURMA: {reason}")
