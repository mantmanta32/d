# -*- coding: utf-8 -*-
"""
intelligence/source_trust.py — KAYNAK İTİBARI & KARA LİSTE (istihbarat)
----------------------------------------------------------------------
"En çok paylaşılan = en çok kirletilen" — bu modül kaynakların itibarını
ölçer ve zehirlenmiş kaynakları karantinaya alır:

- Her kaynak, tarayıcı türünün temel güveniyle başlar (telegram 0.90 … forum 0.40).
- Sonuçlarla güncellenir:
      invalid        → itibar düşer  (kesin çöp üretiyor → cezalandır)
      expired        → itibar DEĞİŞMEZ (kod gerçekti, biz geç kaldık)
      success        → itibar artar
      banned         → itibar sıfırlanır (karantina)
- `blacklist_threshold` altına düşen kaynak "karantina"ya girer:
  kodları ancak çok yüksek desen skoruyla ve bol can varken denenir.

Ayrıca topluluk davranışını izler (queue üzerinden):
- burst: bir kaynak kısa sürede çok kod bastıysa → bot üretimi şüphesi
- flash: aynı kod aniden çok kaynakta görüldüyse → koordineli saldırı izi
"""
from __future__ import annotations

from typing import Any

#: tarayıcı türü → temel itibar (scanner.base.TYPE_CONFIDENCE ile uyumlu)
BASE_TRUST = {"telegram": 0.90, "official": 0.80, "aggregator": 0.55,
              "forum": 0.40, "social": 0.35, "unknown": 0.30}


class SourceTrust:
    def __init__(self, cfg: dict, logger: Any):
        i = cfg.get("intelligence", {}).get("source_trust", {})
        self.blacklist_threshold = float(i.get("blacklist_threshold", 0.25))
        self.invalid_penalty = float(i.get("invalid_penalty", 0.12))
        self.success_bonus = float(i.get("success_bonus", 0.06))
        self.burst_threshold = int(i.get("burst_threshold", 4))
        self.burst_window = float(i.get("burst_window_seconds", 600))
        self.flash_sources = int(i.get("flash_sources", 3))
        self.flash_window = float(i.get("flash_window_seconds", 120))
        self.log = logger

        #: kaynak adı → itibar
        self.trust: dict[str, float] = {}
        #: kaynak adı → son görülme zamanları (burst tespiti için)
        self._sightings: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    def set_base(self, source: str, confidence: float) -> None:
        """Tarayıcı katmanı kurulurken temel itibarı yükle."""
        if source not in self.trust:
            self.trust[source] = max(0.05, min(1.0, confidence))

    def trust_of(self, source: str) -> float:
        return self.trust.get(source, BASE_TRUST.get("unknown", 0.30))

    def effective_confidence(self, source: str) -> float:
        """Karantinadaki kaynaklar için 0 döner (planlayıcı bunu kullanır)."""
        t = self.trust_of(source)
        return 0.0 if t < self.blacklist_threshold else t

    def is_quarantined(self, source: str) -> bool:
        return self.trust_of(source) < self.blacklist_threshold

    # ------------------------------------------------------------------
    def record(self, source: str, category: str) -> None:
        """Claim sonucuna göre kaynak itibarını güncelle."""
        t = self.trust_of(source)
        if category == "invalid":
            t -= self.invalid_penalty
            if t < self.blacklist_threshold and self.trust.get(source, 1.0) >= self.blacklist_threshold:
                self.log.warning("intelligence",
                                 f"KAYNAK KARANTİNADA: '{source}' sürekli çöp üretiyor "
                                 f"(itibar {max(0,t):.2f}) — kodları ağır riskli işaretlendi.")
        elif category == "success":
            t += self.success_bonus
        elif category == "banned":
            t = 0.0
            self.log.critical("intelligence",
                              f"Kaynak '{source}' banned sonucu verdi — itibar sıfırlandı.")
        # expired → itibar DEĞİŞMEZ (kod gerçekti, geç kaldık)
        # rate_limited / session_expired / network → kaynağın suçu değil
        self.trust[source] = max(0.0, min(1.0, t))

    def note_observation(self, source: str, ts: float) -> None:
        """Bir kaynağın kod üretim zamanını kaydeder (burst tespiti)."""
        self._sightings.setdefault(source, []).append(ts)
        # eski kayıtları buda
        cutoff = ts - self.burst_window * 3
        self._sightings[source] = [t for t in self._sightings[source] if t >= cutoff]

    def is_burst(self, source: str, ts: float) -> bool:
        """Kaynak kısa sürede anormal çok kod bastı mı? (bot üretimi şüphesi)"""
        cutoff = ts - self.burst_window
        n = sum(1 for t in self._sightings.get(source, []) if t >= cutoff)
        return n >= self.burst_threshold

    def is_flash(self, code: str, sightings: list[tuple[str, float]], ts: float) -> bool:
        """Aynı kod aniden çok kaynakta mı patladı? (koordineli tuzak izi)

        sightings: (kaynak, zaman) listesi. flash = >=flash_sources kaynak,
        hepsi flash_window içinde. Not: çapraz kanıt (corroboration) iyidir ama
        patlama hızı kötüdür — farkı burada ayırıyoruz.
        """
        recent = [(s, t) for s, t in sightings if ts - t <= self.flash_window]
        sources = {s for s, _ in recent}
        return len(sources) >= self.flash_sources

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "trust": {k: round(v, 2) for k, v in sorted(self.trust.items(),
                                                        key=lambda kv: -kv[1])},
            "quarantined": [k for k, v in self.trust.items()
                            if v < self.blacklist_threshold],
            "blacklist_threshold": self.blacklist_threshold,
        }
