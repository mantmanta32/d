# -*- coding: utf-8 -*-
"""
core/queue_engine.py — Kod kuyruğu & öncelik
--------------------------------------------
- Kuyruk kalıcıdır (data/queue.json): yeniden başlatmada kaybolmaz.
- Her kodun durumu: pending → claiming → success / expired / invalid /
  rate_limited / session_expired / banned / failed.
- Öncelik sıralaması (planner her tur çağırır):
    success > banned > rate_limited > session_expired > expired > invalid > pending
  yani kuyruk her zaman "en kârlı" sırada işlenir: önce başarılılar,
  en sonda çöp (invalid) — çöp kuyruğu şişirip hız sınırı yemesin.
"""
from __future__ import annotations

import json
import os
import random
from typing import Any, Optional

from utils.helpers import load_json, save_json

#: Öncelik sırası (inceleme/listeleme için): düşük sayı = daha kritik bilgi.
#: NOT: Bu tablo claim önceliğini belirlemez — claim her zaman yalnızca
#: "pending" kodları işler (terminal durumlar asla yeniden denenmez).
PRIORITY = {
    "success": 0,
    "banned": 1,            # hesabı kurtarmak için hızlıca bilinmeli
    "rate_limited": 2,
    "session_expired": 3,
    "expired": 4,
    "invalid": 5,
    "pending": 6,
    "claiming": 7,
    "failed": 8,
}

#: Bir koda kaç deneme hakkı var (settings.claimer.retries_per_code)
DEFAULT_MAX_RETRIES = 1


class QueueEngine:
    def __init__(self, cfg: dict, logger: Any):
        base = cfg.get("brain", {})
        self.path = os.path.join("data", "queue.json")
        self.max_retries = int(cfg.get("claimer", {}).get("retries_per_code", DEFAULT_MAX_RETRIES))
        self.batch_size = int(base.get("batch_size", 5))
        self.log = logger
        self.items: list[dict] = load_json(self.path, []) or []

    # ------------------------------------------------------------------
    def add(self, code: str, source: str, confidence: float = 1.0,
            found_at: Optional[str] = None, priority_bonus: float = 0.0) -> bool:
        """Kuyruğa kod ekler; zaten varsa eklemez."""
        if any(i["code"] == code for i in self.items):
            return False
        from utils.helpers import now_iso
        self.items.append({
            "code": code,
            "source": source,
            "confidence": round(confidence, 3),
            "found_at": found_at or now_iso(),
            "status": "pending",
            "attempts": 0,
            "priority_bonus": priority_bonus,
        })
        self.save()
        self.log.info(f"Kuyruğa eklendi: {code} (kaynak: {source}, güven: {confidence:.2f})")
        return True

    def mark(self, code: str, status: str, **extra) -> None:
        for i in self.items:
            if i["code"] == code:
                i["status"] = status
                i["attempts"] = i.get("attempts", 0) + 1
                i.update(extra)
                break
        self.save()

    # ------------------------------------------------------------------
    def next_batch(self) -> list[dict]:
        """Sıradaki iş parçası: yalnızca 'pending' kodlar, güven sırasıyla.

        Terminal durumlar (success/expired/invalid/banned) asla yeniden
        denenmez — kuyruk onları yalnızca istatistik için tutar. Küçük bir
        rastgelelik (jitter) eklenir ki eşit güvenli kodlarda sıra her tur
        değişsin.
        """
        pending = [i for i in self.items if i["status"] == "pending"]
        pending.sort(key=lambda i: -(i.get("confidence", 0.5) + random.uniform(-0.03, 0.03)))
        return pending[:self.batch_size]

    def review_order(self) -> list[dict]:
        """İnceleme sırası (panoda gösterim için): önce terminal kritik durumlar."""
        return sorted(self.items, key=lambda i: PRIORITY.get(i["status"], 9))

    def pending_count(self) -> int:
        return sum(1 for i in self.items if i["status"] == "pending")

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.items:
            out[i["status"]] = out.get(i["status"], 0) + 1
        return out

    def save(self) -> None:
        save_json(self.path, self.items)

    def snapshot(self) -> dict:
        return {"total": len(self.items), "pending": self.pending_count(),
                "counts": self.counts()}
