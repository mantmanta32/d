# -*- coding: utf-8 -*-
"""
brain/safe_planner.py — GÜVENLİ PLANLAYICI (savaşçı beyni)
----------------------------------------------------------
Planner'ın alt sınıfı: "az sayıda hatayla en çok doğruya ulaşma" stratejisi.

select_targets() — denemeden ÖNCE üç aşamalı filtre:
    1. PATTERN GATE  : desen yargıcı "junk" dediyse kod asla denenmez
                       (anında kuyrukta çöp işaretlenir — hakkını yedirtmez)
    2. SOURCE GATE   : karantinadaki kaynaktan gelen kod ancak çok yüksek
                       desen skoruyla ve bol can varken denenebilir
    3. RISK/VALUE    : risk = (1 - kaynak güveni) × (1 - desen skoru)
                       değer = beklenen başarı × kazanç büyüklüğü
                       sıralama: risk/yarar oranına göre (en verimli önce)

handle_result() — denemeden SONRA:
    - can sayacını besler (invalid=1 can, expired=0.5 can, banned=acil duruş)
    - kaynak itibarını günceller (çöp üreten kaynak karantinaya girer)
    - desen yargıcına öğretir (hangi özellikler hatalı → öğrenen kara liste)
    - görülme sıklığı/burst tespiti yapar (bot üretimi şüphesi)
"""
from __future__ import annotations

from typing import Any, Optional

from brain.planner import Planner
from utils.helpers import VirtualClock


class SafePlanner(Planner):
    def __init__(self, cfg: dict, queue: Any, connector: Any, session: Any,
                 delay: Any, memory: Any, observer: Any,
                 counter: Any, judge: Any, trust: Any,
                 clock: Optional[VirtualClock] = None):
        super().__init__(cfg, queue, connector, session, delay, memory, observer, clock)
        self.counter = counter
        self.judge = judge
        self.trust = trust
        #: hangi kodların hangi kaynaklarda görüldüğü → flash tespiti
        self._sightings: dict[str, list[tuple[str, float]]] = {}
        self.gated_junk = 0
        self.gated_quarantine = 0
        self.gated_lives = 0

    # ------------------------------------------------------------------
    def select_targets(self) -> list[dict]:
        """Savunma katmanından geçmiş hedef listesi (risk/değer sıralı)."""
        # 0 — acil durum & can kontrolü (kilitlenmeden önce geri çekil)
        if self.counter.emergency_stop:
            self._pause("Can sayacı acil duruşta — kod denemesi durduruldu")
            return []
        if not self.counter.can_attempt(risk=0.0):
            self.obs.event("WARNING", "safe_planner",
                           f"Can rezervine ulaşıldı ({self.counter.lives_left():.1f} kaldı) — "
                           f"deneme yok, canların geri gelmesi bekleniyor")
            return []

        candidates = []
        for item in self.queue.next_batch():
            code = item["code"]
            source = item.get("source", "unknown")
            judgement = self.judge.judge(code)

            # GATE 1 — desen: kesin çöp asla denenmez
            if judgement["verdict"] == "junk":
                self.queue.mark(code, "invalid", gate="pattern",
                                judge_score=judgement["score"])
                self.gated_junk += 1
                self.obs.event("INFO", "safe_planner",
                               f"TUZAK AYIKLANDI (desen): {code} "
                               f"(skor {judgement['score']:.2f}, "
                               f"{','.join(judgement['flags'][:3])})")
                continue

            # GATE 2 — kaynak karantinası
            quarantined = self.trust.is_quarantined(source)
            if quarantined and judgement["score"] < 0.85:
                self.queue.mark(code, "pending", gate="quarantine")
                self.gated_quarantine += 1
                self.obs.event("INFO", "safe_planner",
                               f"Karantina kaynağı ertelendi: {code} "
                               f"(kaynak {source}, skor {judgement['score']:.2f})")
                continue

            # GATE 3 — can koruması: rezerv canlar yalnızca düşük riske açık
            risk = self._risk_of(source, code, judgement["score"])
            if not self.counter.can_attempt(risk=risk):
                self.gated_lives += 1
                continue

            candidates.append({
                **item,
                "judge_score": judgement["score"],
                "judge_verdict": judgement["verdict"],
                "risk": risk,
                "value": self._value_of(item, judgement["score"]),
            })

        # risk/değer oranına göre sırala (en verimli önce)
        candidates.sort(key=lambda i: (i["risk"] / max(1e-6, i["value"]),
                                       -i["judge_score"]))
        return candidates

    # ------------------------------------------------------------------
    def handle_result(self, item: dict, result: dict) -> Optional[str]:
        """Sonucu işler + savunma/istihbarat katmanlarını besler."""
        cat = result["category"]
        source = item.get("source", "unknown")
        code = item["code"]
        ts = self.clock.now()

        # 1 — savunma: can sayacı
        self.counter.record(cat)
        if self.counter.emergency_stop:
            self.queue.mark(code, cat)
            self._pause("Kilitlenme sinyali (banned) — güvenlik duruşu")
            return None

        # 2 — istihbarat: kaynak itibarı
        self.trust.record(source, cat)
        self.trust.note_observation(source, ts)

        # 3 — öğrenen desen kara listesi
        self.judge.learn(code, cat)

        # 4 — flash (koordineli tuzak) tespiti
        self._sightings.setdefault(code, []).append((source, ts))
        if self.trust.is_flash(code, self._sightings[code], ts):
            self.obs.event("WARNING", "safe_planner",
                           f"FLASH ŞÜPHESİ: {code} kısa sürede çok kaynakta patladı "
                           f"(koordineli tuzak olabilir)")

        # 5 — kuyruk işaretleme & olay (üst sınıfın davranışı)
        return super().handle_result(item, result)

    def before_claim(self, item: dict) -> bool:
        """HER istek öncesi can kontrolü — tur içinde bile rezerv asla aşılmaz.

        Seçim anındaki kontrol ile yetinmeyip, istek anındaki güncel can
        durumuna bakar: "10 can var, hepsini bu turda harcayabilirim" hatası
        böylece imkânsız olur.
        """
        if not self.counter.can_attempt(risk=item.get("risk", 0.0)):
            self.obs.event("INFO", "safe_planner",
                           f"{item['code']} ertelendi: can rezervi/kritik eşik "
                           f"({self.counter.lives_left():.1f} can kaldı)")
            self.gated_lives += 1
            return False
        return True

    # ------------------------------------------------------------------
    def _risk_of(self, source: str, code: str, judge_score: float) -> float:
        """Bir kodun hata riski: kaynak itibarı + desen skoru + öğrenilmiş risk."""
        source_risk = 1.0 - self.trust.effective_confidence(source)
        pattern_risk = 1.0 - judge_score
        learned = self.judge.feature_risk(code)  # öğrenilmiş özellik riski
        return min(1.0, 0.5 * source_risk + 0.4 * pattern_risk + 0.1 * learned)

    def _value_of(self, item: dict, judge_score: float) -> float:
        """Beklenen değer: kaynak güveni × desen skoru × büyüklük tahmini."""
        size_bonus = min(1.0, len(item.get("code", "")) / 16.0)
        return (self.trust.effective_confidence(item.get("source", "unknown"))
                * judge_score * (0.8 + 0.2 * size_bonus))

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "gated_junk": self.gated_junk,
            "gated_quarantine": self.gated_quarantine,
            "gated_lives": self.gated_lives,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
        }
