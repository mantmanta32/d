# -*- coding: utf-8 -*-
"""utils.helpers — kod çıkarma & filtreleme testleri."""
import pytest

from utils.helpers import (VirtualClock, extract_codes, looks_like_code,
                           random_code, redact, shannon_entropy)


def test_extract_codes_katisik_metin():
    text = "Yeni kod: BP7K2M9QX4 paylaşımı! Ayrıca 31536000 saniye kampanyası ve 1234567890 yok."
    codes = extract_codes(text)
    assert "BP7K2M9QX4" in codes
    # saf rakam (31536000, 1234567890) ve tekrarlı diziler elenmeli
    assert "31536000" not in codes
    assert "1234567890" not in codes


def test_looks_like_code_kriterleri():
    assert looks_like_code("BP7K2M9QX4")
    assert looks_like_code("Abc12345")
    assert not looks_like_code("password")          # kara liste
    assert not looks_like_code("12345678")          # saf rakam
    assert not looks_like_code("abcdefgh")          # saf harf
    assert not looks_like_code("aaaaaa11")          # düşük entropi


def test_entropi():
    assert shannon_entropy("AAAA") < shannon_entropy("Kx9Q2m")


def test_random_code_biçim():
    c = random_code()
    assert c.startswith("BP") and len(c) == 12


def test_redact():
    out = redact({"token": "a" * 40, "safe": "kısa"})
    assert out["token"] == "***"
    assert out["safe"] == "kısa"


def test_virtual_clock_scale():
    import time
    clk = VirtualClock(scale=60.0)
    t0 = clk.now()
    time.sleep(0.05)
    dt = clk.now() - t0
    assert 2.5 < dt < 4.5  # ~3 sanal saniye
