# -*- coding: utf-8 -*-
"""
utils/har_parser.py — HAR (HTTP Archive) ayrıştırıcı
----------------------------------------------------
Tarayıcı DevTools'undan dışa aktarılan .har dosyasından oturum bilgisini çıkarır:
- Binance alan adlarına ait çerezler (istenen çerezler + Set-Cookie yanıtları)
- csrftoken başlığı / çerezi
- User-Agent
- Görülen uç noktalar (endpoint keşfi — hangi API'lerin kullanıldığı)

Kullanım:  python3 main.py import-har data/session.har
"""
from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SessionData:
    """HAR'dan çıkarılan oturum bilgisi (çerezler alan adına göre gruplu)."""
    cookies: dict[str, dict[str, str]] = field(default_factory=dict)
    csrf_token: Optional[str] = None
    user_agent: Optional[str] = None
    endpoints_seen: list[str] = field(default_factory=list)
    captured_at: float = 0.0
    source_file: str = ""

    def all_cookies(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for d in self.cookies.values():
            out.update(d)
        return out

    def cookie_header(self, domains: list[str]) -> str:
        """Yalnızca verilen alan adlarına ait çerezlerle tek Cookie başlığı üretir.

        Not: Tarayıcı da çerezleri alan adına göre gönderir; accounts.binance.com
        çerezleri www.binance.com'a gitmez — aynı kuralı burada uyguluyoruz.
        """
        wanted = {d.lstrip(".").lower() for d in domains}
        parts: list[str] = []
        for key, vals in self.cookies.items():
            if key.lstrip(".").lower() in wanted:
                for name, value in vals.items():
                    parts.append(f"{name}={value}")
        return "; ".join(parts)


def _domain_of(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _is_allowed(domain: str, allowed: list[str]) -> bool:
    d = domain.lstrip(".").lower()
    return any(d == a.lstrip(".").lower() or d.endswith("." + a.lstrip(".").lower())
               for a in allowed)


def _collect_cookies(entries: list, allowed: list[str]) -> dict[str, dict[str, str]]:
    cookies: dict[str, dict[str, str]] = {}

    def put(domain: str, name: str, value: str):
        if not name or not domain or not _is_allowed(domain, allowed):
            return
        # anahtarları tutarlı yap: ".binance.com" biçimi (Set-Cookie + istek çerezleri aynı)
        key = domain if domain.startswith(".") else "." + domain
        cookies.setdefault(key, {})[name] = value

    for e in entries:
        req = e.get("request", {})
        domain = _domain_of(req.get("url", ""))
        for c in req.get("cookies", []):
            put(c.get("domain") or domain, c.get("name", ""), c.get("value", ""))
        # Set-Cookie yanıt başlıkları da toplanır (oturum çerezleri buradan gelebilir)
        resp = e.get("response", {})
        for h in resp.get("headers", []):
            if h.get("name", "").lower() == "set-cookie":
                pair = h.get("value", "").split(";")[0]
                if "=" in pair:
                    n, v = pair.split("=", 1)
                    put(domain, n.strip(), v.strip())
    return cookies


def _collect_meta(entries: list) -> tuple[Optional[str], Optional[str]]:
    csrf: Optional[str] = None
    ua: Optional[str] = None
    for e in entries:
        req = e.get("request", {})
        for h in req.get("headers", []):
            name = h.get("name", "").lower()
            if "csrf" in name:
                csrf = csrf or h.get("value")
            if name == "user-agent":
                ua = ua or h.get("value")
        for c in req.get("cookies", []):
            if "csrf" in c.get("name", "").lower():
                csrf = csrf or c.get("value")
    return csrf, ua


def parse_har(path: str, allowed_domains: list[str]) -> SessionData:
    """HAR dosyasını okur ve SessionData olarak döndürür."""
    with open(path, "r", encoding="utf-8") as f:
        har = json.load(f)
    entries = har.get("log", {}).get("entries", [])
    data = SessionData(
        cookies=_collect_cookies(entries, allowed_domains),
        captured_at=time.time(),
        source_file=path,
    )
    data.csrf_token, data.user_agent = _collect_meta(entries)
    for e in entries:
        url = e.get("request", {}).get("url", "")
        if _is_allowed(_domain_of(url), allowed_domains):
            p = urllib.parse.urlparse(url).path
            if p not in data.endpoints_seen:
                data.endpoints_seen.append(p)
    return data
