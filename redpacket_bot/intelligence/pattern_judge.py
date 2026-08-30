# -*- coding: utf-8 -*-
"""
intelligence/pattern_judge.py — BİÇİM & ANOMALİ DEDEKTÖRÜ (yargıç)
------------------------------------------------------------------
Bir kodu denemeden ÖNCE yargılar: gerçek Binance koduna ne kadar benziyor?

Statik kriterler (rastgeleliğe ters düşen yapaylık işaretleri):
- BP öneki + standart uzunluk (8–16)
- Shannon entropisi (tekrarlı/sıralı diziler düşük entropilidir)
- Harf/rakam oranı (gerçek kodlar dengelidir)
- Tekrar eden karakterler, ardışık diziler (ABC…, 123…), klavye desenleri

Öğrenen kara liste (zamanla kendini geliştirir):
- Hangi özellik (önek, uzunluk, rakam oranı, entropi aralığı) tarihte
  çok "invalid" ürettiyse o özelliği taşıyan yeni kodlar otomatik cezalandırılır.
  Veri: data/features.json (kalıcı).

Karar: score 0..1 → verdict: clean / suspicious / junk
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from utils.helpers import load_json, save_json, shannon_entropy

#: 3+ ardışık karakter (AAA, 111)
RE_REPEAT = re.compile(r"(.)\1\1")
#: ardışık alfabetik/sayısal diziler (küçük parçalar bile yapaylık işaretidir)
SEQUENCES = ["ABC", "BCD", "CDE", "DEF", "EFG", "FGH", "GHI", "HIJ", "IJK",
             "JKL", "KLM", "LMN", "MNO", "NOP", "OPQ", "PQR", "QRS", "RST",
             "STU", "TUV", "UVW", "VWX", "WXY", "XYZ",
             "012", "123", "234", "345", "456", "567", "678", "789", "890"]
#: klavye satırları (qwerty deseni)
KEYBOARD = ["QWERTY", "ASDF", "ZXCV", "POIU", "LKJH", "MNBV"]


class PatternJudge:
    def __init__(self, cfg: dict, logger: Any):
        i = cfg.get("intelligence", {}).get("pattern", {})
        self.min_clean = float(i.get("min_clean_score", 0.70))
        self.min_suspicious = float(i.get("min_suspicious_score", 0.45))
        self.feature_db = i.get("feature_db", "data/features.json") or "data/features.json"
        self.log = logger
        #: özellik demeti → {"invalid": n, "valid": n}
        self.features: dict[str, dict[str, int]] = load_json(self.feature_db, {}) or {}
        self.judged = 0

    # ------------------------------------------------------------------
    def judge(self, code: str) -> dict:
        """Kodu yargılar: skor + şüphe bayrakları + öğrenilmiş risk düzeltmesi."""
        self.judged += 1
        score = 0.50
        flags: list[str] = []
        c = code.upper()

        # --- biçim -------------------------------------------------------
        if c.startswith("BP") and 8 <= len(c) <= 16:
            score += 0.15
        else:
            flags.append("format_off")
            score -= 0.15

        # --- entropi ------------------------------------------------------
        e = shannon_entropy(c)
        if e >= 3.0:
            score += 0.15
        elif e < 2.4:
            flags.append("low_entropy")
            score -= 0.20

        # --- harf/rakam dengesi -------------------------------------------
        digits = sum(ch.isdigit() for ch in c)
        ratio = digits / max(1, len(c))
        if 0.2 <= ratio <= 0.6:
            score += 0.10
        else:
            flags.append("odd_digit_ratio")
            score -= 0.10

        # --- yapaylık işaretleri -------------------------------------------
        if RE_REPEAT.search(c):
            flags.append("repeated_chars")
            score -= 0.15
        if any(s in c for s in SEQUENCES):
            flags.append("sequential")
            score -= 0.10
        if any(k in c for k in KEYBOARD):
            flags.append("keyboard_pattern")
            score -= 0.10

        # --- öğrenilmiş özellik riski -------------------------------------
        # DİKKAT: ceza bilinçli olarak ZAYIFTIR — zehirli bir kaynak tüm
        # "BP…" kodlarını haksız yere karalayabilir (kendi kendini zehirleme).
        # Asıl ağır silah kaynak karantinasıdır; desen öğrenmesi yalnızca
        # güçlü ve tekrarlı kanıtla, hafif dokunuşla işler.
        learned = self.feature_risk(code)
        if learned > 0.0:
            score -= learned * 0.2
            flags.append(f"learned_risk_{learned:.2f}")

        score = max(0.0, min(1.0, score))
        if score >= self.min_clean:
            verdict = "clean"
        elif score >= self.min_suspicious:
            verdict = "suspicious"
        else:
            verdict = "junk"
        return {"code": code, "score": round(score, 3), "verdict": verdict,
                "flags": flags, "entropy": round(e, 3)}

    # ------------------------------------------------------------------
    def learn(self, code: str, category: str) -> None:
        """Sonucu özellik demetlerine işler — öğrenen kara listenin kalbi."""
        # expired = kod gerçekti (geç kaldık) → pozitif örnek
        is_valid = category in ("success", "expired")
        is_invalid = category == "invalid"
        if not (is_valid or is_invalid):
            return
        c = code.upper()
        # BİLİNÇLİ TASARIM: yalnızca SPESİFİK kovalar öğrenilir.
        # prefix1 ("B") ve len ("12") gibi genel kovalar TÜM gerçek kodlarda
        # ortaktır — onları öğrenmek kendi kendini zehirler.
        buckets = {
            "prefix3": c[:3],
            "digits": self._digit_bucket(c),
            "entropy": self._entropy_bucket(c),
        }
        for key, val in buckets.items():
            name = f"{key}:{val}"
            f = self.features.setdefault(name, {"invalid": 0, "valid": 0})
            if is_invalid:
                f["invalid"] += 1
            else:
                f["valid"] += 1
        self._persist()

    def feature_risk(self, code: str) -> float:
        """Öğrenilmiş özelliklerden kodun hata riski (0..1)."""
        c = code.upper()
        buckets = {
            "prefix3": c[:3],
            "digits": self._digit_bucket(c),
            "entropy": self._entropy_bucket(c),
        }
        risks = []
        for key, val in buckets.items():
            f = self.features.get(f"{key}:{val}")
            # kanıt eşiği: en az 3 örnek VE en az %60 hata oranı olmadan
            # bir deseni "kötü" ilan etme (tek seferlik şansı eler)
            if f and (f["invalid"] + f["valid"]) >= 3 and \
                    f["invalid"] / (f["invalid"] + f["valid"]) >= 0.6:
                risks.append(f["invalid"] / (f["invalid"] + f["valid"]))
        return (sum(risks) / len(risks)) if risks else 0.0

    # ------------------------------------------------------------------
    @staticmethod
    def _digit_bucket(code: str) -> str:
        d = sum(ch.isdigit() for ch in code) / max(1, len(code))
        if d < 0.2:
            return "low"
        if d <= 0.6:
            return "mid"
        return "high"

    @staticmethod
    def _entropy_bucket(code: str) -> str:
        e = shannon_entropy(code)
        if e >= 3.0:
            return "high"
        if e >= 2.4:
            return "mid"
        return "low"

    def _persist(self) -> None:
        save_json(self.feature_db, self.features)

    def snapshot(self) -> dict:
        return {"judged": self.judged,
                "learned_features": len(self.features),
                "min_clean": self.min_clean,
                "min_suspicious": self.min_suspicious}
