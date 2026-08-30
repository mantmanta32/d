# -*- coding: utf-8 -*-
"""utils.crypto & utils.har_parser — şifreleme ve HAR ayrıştırma testleri."""
import json

from utils import crypto
from utils.har_parser import parse_har

SAMPLE_HAR = {
    "log": {
        "entries": [
            {
                "request": {
                    "url": "https://www.binance.com/bapi/asset/v2/private/gift-box/code/query",
                    "headers": [
                        {"name": "User-Agent", "value": "Mozilla/5.0 TestUA"},
                        {"name": "X-CSRFToken", "value": "csrf_abc123"},
                        {"name": "Cookie", "value": "p149=0; p151=1"},
                    ],
                    "cookies": [
                        {"name": "p149", "value": "0", "domain": ".binance.com"},
                        {"name": "p151", "value": "1", "domain": ".binance.com"},
                    ],
                },
                "response": {"headers": [
                    {"name": "Set-Cookie", "value": "session_token=SECRET123; Path=/; HttpOnly"},
                ]},
            },
            {
                "request": {
                    "url": "https://example.com/other",
                    "headers": [],
                    "cookies": [{"name": "evil", "value": "x", "domain": "example.com"}],
                },
                "response": {"headers": []},
            },
        ]
    }
}


def test_crypto_roundtrip(tmp_path):
    key_path = str(tmp_path / ".secret")
    key = crypto.generate_key(key_path)
    blob = crypto.encrypt_json({"cookie": "gizli-deger-123", "csrf": "tok"}, key)
    out = crypto.decrypt_json(blob, key)
    assert out == {"cookie": "gizli-deger-123", "csrf": "tok"}
    # ham veri anahtar olmadan okunmamalı (format başlığı kontrolü)
    assert b"gizli-deger-123" not in blob


def test_har_parser(tmp_path):
    p = tmp_path / "s.har"
    p.write_text(json.dumps(SAMPLE_HAR), encoding="utf-8")
    data = parse_har(str(p), ["binance.com"])
    assert data.csrf_token == "csrf_abc123"
    assert data.user_agent == "Mozilla/5.0 TestUA"
    assert data.cookies[".binance.com"]["p149"] == "0"
    # Set-Cookie yanıt başlığı da toplanmalı
    assert data.cookies[".binance.com"]["session_token"] == "SECRET123"
    # yabancı alan adı çerezleri ELENMELİ
    assert "example.com" not in data.cookies
    assert "example.com" not in data.cookie_header(["binance.com"])
    # uç nokta keşfi
    assert any("gift-box/code/query" in e for e in data.endpoints_seen)
