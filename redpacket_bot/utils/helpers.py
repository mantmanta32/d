# -*- coding: utf-8 -*-
"""
utils/helpers.py — Genel yardımcı araçlar
-----------------------------------------
- Kod çıkarma & akıllı filtreleme (entropi, kara liste, harf+rakam kontrolü)
- Zaman araçları (VirtualClock: simülasyon hızlandırması için sanal saat)
- JSON / dizin yardımcıları ve hassas veri maskeleme (redact)
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import string
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

#: Binance Red Packet kodları için varsayılan kalıp (settings'ten değiştirilebilir)
DEFAULT_CODE_REGEX = r"[A-Za-z0-9]{8,16}"

#: Bariz şekilde kod olmayan sözcükler (settings.blacklist ile genişletilir)
DEFAULT_BLACKLIST: set[str] = {
    "redpacket", "redpacketcode", "password", "username", "placeholder",
    "example", "testing", "campaign", "validity", "security", "verified",
    "exchange", "redeemed", "featured", "accounts", "download", "binance",
    "bonus", "claim", "gift", "promo", "reward", "crypto", "qwertyuiop",
    "1234567890", "abcdefghij", "aaaaaa", "bbbbbb", "cccccc", "dddddd",
}


def ensure_dirs(*paths: str) -> None:
    for p in paths:
        if p:
            os.makedirs(p, exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    ensure_dirs(os.path.dirname(path) or ".")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Kod filtreleme
# ---------------------------------------------------------------------------
def shannon_entropy(token: str) -> float:
    """Shannon entropisi: rastgele kodlar yüksek (~3.2+), tekrarlı diziler düşük çıkar."""
    if not token:
        return 0.0
    counts: dict[str, int] = {}
    for ch in token:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(token)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def looks_like_code(token: str, blacklist: Iterable[str] = (),
                    min_entropy: float = 2.4) -> bool:
    """Bir belirtecin gerçekten 'kod' olup olmadığını çok kriterle sınar."""
    t = token.strip()
    if len(t) < 6:
        return False
    low = t.lower()
    if any(low == b.lower() for b in blacklist):
        return False
    # Kodlar genelde harf + rakam karışımıdır; saf sayı/saf harf gürültüdür
    if not any(ch.isdigit() for ch in t) or not any(ch.isalpha() for ch in t):
        return False
    if shannon_entropy(t) < min_entropy:
        return False
    return True


def extract_codes(text: str, regex: str = DEFAULT_CODE_REGEX,
                  blacklist: Iterable[str] = DEFAULT_BLACKLIST,
                  min_entropy: float = 2.4) -> list[str]:
    """Metinden benzersiz, kod-benzeri belirteçleri çıkarır (sıra korunur)."""
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(regex, text):
        c = m.group(0)
        if c in seen:
            continue
        if looks_like_code(c, blacklist, min_entropy):
            seen.add(c)
            out.append(c)
    return out


def random_code(prefix: str = "BP", length: int = 10) -> str:
    """Simülasyon için rastgele kod üretir (BP + 10 alfanümerik = 12 karakter)."""
    alphabet = string.ascii_uppercase + string.digits
    return prefix + "".join(secrets.choice(alphabet) for _ in range(length))


# ---------------------------------------------------------------------------
# Gizlilik
# ---------------------------------------------------------------------------
def redact(value: Any) -> Any:
    """Uzun hassas belirteçleri (çerez değeri, token) maskeler; günlükler için güvenli."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"[A-Za-z0-9_\-\.]{20,}", "***", value)
    return value


def classify_keywords(body: str, table: list[tuple[str, str]]) -> Optional[str]:
    """Metni anahtar kelime tablosuna göre sınıflandırır (ilk eşleşme kazanır)."""
    low = body.lower()
    for keyword, label in table:
        if keyword.lower() in low:
            return label
    return None


# ---------------------------------------------------------------------------
# Sanal saat — simülasyonun kalbi
# ---------------------------------------------------------------------------
class VirtualClock:
    """Sanal saat: gerçek zamanı bir ölçekle çarpar.

    - Canlı modda scale=1 (gerçek zaman).
    - Simülasyonda scale=60 → 1 gerçek saniye = 1 sanal dakika;
      böylece günlerce süren otonom davranış dakikalar içinde izlenebilir.
    """

    def __init__(self, scale: float = 1.0):
        self.scale = scale
        self._t0 = time.time()

    def now(self) -> float:
        return self._t0 + (time.time() - self._t0) * self.scale

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def fmt(self, ts: Optional[float] = None) -> str:
        t = ts if ts is not None else self.now()
        return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")

    def hour(self, ts: Optional[float] = None) -> int:
        return datetime.fromtimestamp(ts if ts is not None else self.now()).hour
