# -*- coding: utf-8 -*-
"""
core/session_manager.py — Otonom oturum & kimlik yönetimi
----------------------------------------------------------
Sorumlulukları:
1. HAR dosyasından / kayıtlı şifreli depodan oturum bilgisini yükler.
2. Oturumun "yaşını" takip eder, süresi dolmak üzereyse YENİLEME tetikler.
3. Oturumu Fernet ile şifreleyerek diskte saklar (açık çerez yok).
4. Kritik oturum hatası görünce planner'ı uyarır → durur & raporlar.

Akış şeması:
    load() → healthy? → canlı istek yap
      ↑           ↓ hayır
      └─ renew() ← süresi doldu / süresi dolmak üzere (uyarı)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from utils import crypto
from utils.har_parser import SessionData, parse_har

HEALTHY = "healthy"          # normal
REFRESHING = "refreshing"    # yenileme sürecinde
EXPIRING = "expiring"        # süresi dolmak üzere → yenileme planla
EXPIRED = "expired"          # süresi doldu → yenileme şart
DEAD = "dead"                # yenileme başarısız → dur ve raporla


class SessionManager:
    def __init__(self, cfg: dict, logger: Any):
        self.cfg = cfg
        self.log = logger
        base = cfg.get("session", {})
        self.session_lifetime = float(base.get("session_lifetime_hours", 6)) * 3600
        self.refresh_threshold = float(base.get("refresh_threshold_hours", 1)) * 3600
        self.renewal_max_attempts = int(base.get("renewal_max_attempts", 3))
        self.allowed_domains = list(base.get("allowed_domains", ["binance.com"]))
        self.store_path = base.get("session_store", "data/session.enc")
        self.key_path = base.get("key_file", "data/.secret")
        self.har_path = base.get("har_path", "data/session.har")
        self.mode = cfg.get("general", {}).get("mode", "simulation")

        self.session: Optional[SessionData] = None
        self.state = HEALTHY
        self.created_at = 0.0
        self.renewal_count = 0
        self.is_demo = False
        self._renew_hook: Optional[Callable[[], Optional[SessionData]]] = None

    # ------------------------------------------------------------------
    # Yükleme
    # ------------------------------------------------------------------
    def load(self) -> bool:
        """Öncelik: şifreli depo → HAR dosyası. Hangisi kullanıldıysa raporla."""
        if os.path.exists(self.store_path):
            try:
                key = crypto.load_key(self.key_path)
                raw = crypto.decrypt_bytes(open(self.store_path, "rb").read(), key)
                self.session = SessionData(**json.loads(raw.decode("utf-8")))
                self.created_at = self.session.captured_at
                self.state = HEALTHY
                self.log.info(f"Oturum şifreli depodan yüklendi ({self.age_str()} yaşında, "
                              f"{len(self.session.all_cookies())} çerez).")
                return True
            except Exception as e:
                self.log.warning(f"Şifreli depo okunamadı ({e}) — HAR'a düşülüyor.")
        if os.path.exists(self.har_path):
            return self.import_har(self.har_path)
        # Son çare (YALNIZCA simülasyon): demo oturum — gerçek istek asla atılmaz,
        # ama tüm otonom döngü (tarayıcı → plan → öğrenme) uçtan uca denenebilir.
        if self.mode == "simulation":
            from utils.har_parser import SessionData
            import time as _t
            self.session = SessionData(cookies={".binance.com": {"p149": "0"}},
                                       csrf_token="demo", user_agent="RedPacketBot-Demo",
                                       captured_at=_t.time())
            self.created_at = _t.time()
            self.state = HEALTHY
            self.is_demo = True
            self.log.info("Simülasyon modunda DEMO oturum oluşturuldu (gerçek istek yapılmaz). "
                          "Gerçek oturum için: python3 main.py import-har data/session.har")
            return True
        self.log.error("Oturum kaynağı yok: ne şifreli depo ne HAR dosyası bulunamadı.")
        self.state = DEAD
        return False

    def import_har(self, path: str) -> bool:
        """HAR dosyasını ayrıştırır, şifreler ve depoya kaydeder."""
        try:
            data = parse_har(path, self.allowed_domains)
        except Exception as e:
            self.log.error(f"HAR ayrıştırılamadı: {e}")
            self.state = DEAD
            return False
        if not data.cookies and not data.csrf_token:
            self.log.warning("HAR'da Binance'e ait çerez/oturum bulunamadı — dosya "
                             "boş olabilir ya da yanlış profilden dışa aktarılmış.")
        self.session = data
        self.created_at = data.captured_at
        self.is_demo = False
        self._persist()
        self.state = HEALTHY
        self.log.info(f"HAR içe aktarıldı: {len(data.cookies)} alan adı, "
                      f"{len(data.all_cookies())} çerez, "
                      f"{'csrf var' if data.csrf_token else 'csrf yok'}.")
        return True

    # ------------------------------------------------------------------
    # Yaşam belirteci & yenileme
    # ------------------------------------------------------------------
    @property
    def age(self) -> float:
        """Oturum yaşı (saniye). Sanal saat üzerinden hesaplanır."""
        if self.created_at <= 0:
            return 0.0
        return max(0.0, self._clock().now() - self.created_at)

    def age_str(self) -> str:
        a = self.age
        return f"{int(a // 3600)}s {int(a % 3600 // 60)}dk"

    def remaining(self) -> float:
        return max(0.0, self.session_lifetime - self.age)

    def healthy(self) -> bool:
        if not self.session:
            return False
        if self.state in (DEAD, EXPIRED):
            return False
        return True

    def heartbeat(self, last_request_ok: bool) -> str:
        """Yaşam belirteci: her istek döngüsünde çağrılır.

        - Oturum ölmüşse → DEAD
        - Süresi dolmuşsa → EXPIRED + yenileme dene
        - Süresi dolmak üzereyse → EXPIRING + yenileme dene
        - Normal → HEALTHY
        """
        if not self.session:
            self.state = DEAD
            return DEAD
        remaining = self.remaining()
        if remaining <= 0:
            self.log.warning("Oturum süresi DOLDU — yenileme tetikleniyor.")
            self.state = EXPIRED
        elif remaining <= self.refresh_threshold:
            self.log.info(f"Oturum süresi dolmak üzere ({int(remaining // 60)}dk kaldı) — "
                          f"yenileme tetikleniyor.")
            self.state = EXPIRING
        else:
            self.state = HEALTHY
            return HEALTHY

        if self.renew():
            self.state = HEALTHY
            return HEALTHY
        self.log.error("Oturum yenilenemedi — durduruluyor (güvenlik: ölü oturumla istek yapılmaz).")
        self.state = DEAD
        return DEAD

    def renew(self) -> bool:
        """Oturumu yeniler.

        1. adım: kayıtlı yenileme kancası (planner'ın kurduğu akış).
        2. adım: HAR yeniden içe aktarımı (en azından depoyu tazeler).
        """
        if self.renewal_count >= self.renewal_max_attempts:
            self.log.error(f"Yenileme denemesi limiti aşıldı ({self.renewal_max_attempts}).")
            return False
        self.renewal_count += 1
        if self.is_demo:
            # demo oturum: "yenileme" = yaşı sıfırla (simülasyon asla ölmez)
            import time as _t
            self.created_at = _t.time()
            if self.session:
                self.session.captured_at = self.created_at
            self.log.info(f"Demo oturum yenilendi (simülasyon, deneme {self.renewal_count}).")
            return True
        if self._renew_hook is not None:
            try:
                fresh = self._renew_hook()
                if fresh is not None:
                    self.session = fresh
                    self.created_at = fresh.captured_at
                    self._persist()
                    self.log.info(f"Oturum yenilendi (deneme {self.renewal_count}).")
                    return True
            except Exception as e:
                self.log.warning(f"Yenileme kancası hatası: {e}")
        # Geri düşüş: HAR'ı yeniden oku
        if os.path.exists(self.har_path):
            if self.import_har(self.har_path):
                self.log.info("Oturum HAR yeniden içe aktarımıyla yenilendi.")
                return True
        return False

    # ------------------------------------------------------------------
    # Kimlik verileri
    # ------------------------------------------------------------------
    def cookie_header(self) -> str:
        if not self.session:
            return ""
        return self.session.cookie_header(self.allowed_domains)

    def csrf_token(self) -> Optional[str]:
        return self.session.csrf_token if self.session else None

    def user_agent(self) -> str:
        ua = self.session.user_agent if self.session else None
        return ua or ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

    def snapshot(self) -> dict:
        """Gözlemci/gösterge paneli için güvenli (maskeli) özet."""
        return {
            "state": self.state,
            "age_seconds": int(self.age),
            "remaining_seconds": int(self.remaining()),
            "renewal_count": self.renewal_count,
            "cookie_count": len(self.session.all_cookies()) if self.session else 0,
            "domains": list(self.session.cookies.keys()) if self.session else [],
            "has_csrf": bool(self.csrf_token()),
            "endpoints_seen": self.session.endpoints_seen if self.session else [],
            "har_source": self.session.source_file if self.session else "",
        }

    # ------------------------------------------------------------------
    # İç yardımcılar
    # ------------------------------------------------------------------
    def _persist(self) -> None:
        if not self.session:
            return
        os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
        if not os.path.exists(self.key_path):
            crypto.generate_key(self.key_path)
        key = crypto.load_key(self.key_path)
        blob = crypto.encrypt_json(self.session.__dict__, key)
        tmp = self.store_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, self.store_path)
        self.log.info(f"Oturum şifrelenerek kaydedildi: {self.store_path}")

    def _clock(self):
        """Sanal saat erişimi — döngü tarafından enjekte edilir."""
        return getattr(self, "clock", __import__("utils.helpers", fromlist=["VirtualClock"]).VirtualClock())
