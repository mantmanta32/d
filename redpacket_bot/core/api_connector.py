# -*- coding: utf-8 -*-
"""
core/api_connector.py — İstek/yanıt & hata analizi
--------------------------------------------------
- Oturum çerezleriyle Binance özel uç noktasına (gift-box/code/query) istek atar.
- Yanıtları sınıflandırır: success / expired / invalid / rate_limited /
  session_expired / banned / network_error.
- `dry_run=True` simülasyon modu: ağa çıkmaz, gerçekçi sonuçlar üretir —
  böylece tüm otonom döngü gerçek hesapla riske girmeden test edilir.

İstek şekli:
    POST {endpoint}
    Cookie: {session cookie header}
    X-CSRFToken: {csrf}   (varsa)
    User-Agent: {har UA}
    {"code": "BPABCDEF1234"}
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from utils.helpers import redact

#: Hata sınıflandırma tablosu: yanıt metnindeki anahtar kelime → kategori
KEYWORD_TABLE = [
    ("banned", "banned"),
    ("restricted", "banned"),
    ("risk control", "banned"),
    ("too many requests", "rate_limited"),
    ("rate limit", "rate_limited"),
    ("frequent", "rate_limited"),
    ("expired", "expired"),
    ("has expired", "expired"),
    ("ended", "expired"),
    ("invalid", "invalid"),
    ("not exist", "invalid"),
    ("incorrect", "invalid"),
    ("login", "session_expired"),
    ("session", "session_expired"),
    ("unauthorized", "session_expired"),
    ("expire", "session_expired"),
    ("network", "network_error"),
]

SIM_RESULT_POOL = [
    ("success", 0.16),
    ("expired", 0.34),
    ("invalid", 0.42),
    ("rate_limited", 0.05),
    ("session_expired", 0.02),
    ("banned", 0.01),
]


class ApiConnector:
    def __init__(self, cfg: dict, session: Any, logger: Any):
        c = cfg.get("claimer", {})
        self.endpoint = c.get("endpoint", "https://www.binance.com/bapi/asset/v2/private/gift-box/code/query")
        self.method = c.get("method", "POST")
        self.payload_template = c.get("payload_template", {"code": "{code}"})
        self.timeout = float(c.get("timeout_seconds", 15))
        self.dry_run = cfg.get("general", {}).get("mode", "simulation") == "simulation"
        self.session = session
        self.log = logger
        self.last_request_at = 0.0
        self.hourly_count = 0

    # ------------------------------------------------------------------
    def claim(self, code: str) -> dict:
        """Tek kodu kullanmayı dener; her zaman sınıflandırılmış sonuç döndürür."""
        if self.dry_run:
            return self._claim_sim(code)
        return self._claim_real(code)

    # ------------------------------------------------------------------
    # Gerçek istek yolu (canlı mod)
    # ------------------------------------------------------------------
    def _claim_real(self, code: str) -> dict:
        if not self.session.healthy():
            return {"code": code, "status": "session_expired", "category": "session_expired",
                    "message": "Oturum sağlıklı değil — istek yapılmadı."}
        body = json.dumps({k: v.format(code=code) for k, v in self.payload_template.items()}).encode()
        headers = {
            "User-Agent": self.session.user_agent(),
            "Content-Type": "application/json",
            "Referer": "https://www.binance.com/",
            "Origin": "https://www.binance.com",
            "Accept": "application/json",
        }
        cookie = self.session.cookie_header()
        if cookie:
            headers["Cookie"] = cookie
        csrf = self.session.csrf_token()
        if csrf:
            headers["X-CSRFToken"] = csrf
            headers["X-Requested-With"] = "XMLHttpRequest"

        req = urllib.request.Request(self.endpoint, data=body, headers=headers, method=self.method)
        self.last_request_at = time.time()
        self.hourly_count += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "ignore")
            return self.classify(code, raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "ignore")
            result = self.classify(code, raw)
            if e.code in (401, 403):
                result["category"] = "session_expired" if e.code == 401 else "banned"
                result["status"] = result["category"]
            result["http_code"] = e.code
            return result
        except Exception as e:
            return {"code": code, "status": "network_error", "category": "network_error",
                    "message": str(e)}

    # ------------------------------------------------------------------
    # Simülasyon yolu — ağa çıkmaz
    # ------------------------------------------------------------------
    def _claim_sim(self, code: str) -> dict:
        r = random.random()
        acc = 0.0
        for status, prob in SIM_RESULT_POOL:
            acc += prob
            if r <= acc:
                break
        messages = {
            "success": "Claim successful",
            "expired": "This gift box has expired",
            "invalid": "Invalid code",
            "rate_limited": "Too many requests, please slow down",
            "session_expired": "Session has expired, please login",
            "banned": "Your account has been restricted",
        }
        return {"code": code, "status": status, "category": status,
                "message": messages[status], "simulated": True}

    # ------------------------------------------------------------------
    def classify(self, code: str, raw: str) -> dict:
        """Ham yanıtı sınıflandırır — hata öğrenmenin temel taşı."""
        low = raw.lower()
        category = "invalid"
        for keyword, label in KEYWORD_TABLE:
            if keyword in low:
                category = label
                break
        return {"code": code, "status": category, "category": category,
                "message": raw[:200], "raw": redact(raw[:500])}

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "endpoint": self.endpoint,
            "last_request_at": self.last_request_at,
            "hourly_count": self.hourly_count,
        }
