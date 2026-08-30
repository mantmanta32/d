# -*- coding: utf-8 -*-
"""
utils/crypto.py — Hassas veri şifreleme
---------------------------------------
Oturum çerezleri gibi gizli veriler diskte AÇIKTA tutulmaz:
- Öncelik: Fernet (AES-128-CBC + HMAC) — `cryptography` kütüphanesi.
- Yoksa: PBKDF2 ile türetilen anahtar akışıyla XOR (zayıf geri düşüş, uyarı basar).

Anahtar, `data/.secret` dosyasında 0600 izniyle tutulur.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from typing import Any

try:
    from cryptography.fernet import Fernet
    HAVE_FERNET = True
except ImportError:  # pragma: no cover
    HAVE_FERNET = False

MAGIC_FERNET = b"RBF1"  # RedPacketBot format v1 (Fernet)
MAGIC_XOR = b"RBFX1"    # geri düşüş XOR formatı


def _warn_fallback():
    if not HAVE_FERNET:
        import sys
        print("! UYARI: 'cryptography' kurulu değil — zayıf XOR geri düşüşü kullanılıyor.\n"
              "  GÜÇLÜ şifreleme için: pip install cryptography", file=sys.stderr)


def generate_key(path: str) -> bytes:
    """Yeni anahtar üretir ve dosyaya 0600 izniyle yazar."""
    key = Fernet.generate_key() if HAVE_FERNET else base64.urlsafe_b64encode(os.urandom(32))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def load_key(path: str) -> bytes:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Anahtar dosyası yok: {path} (önce generate_key çağrılmalı)")
    with open(path, "rb") as f:
        return f.read().strip()


def _xor_crypt(data: bytes, key: bytes) -> bytes:
    """PBKDF2 anahtar akışı ile XOR — yalnızca cryptography yoksa kullanılır."""
    nonce = secrets.token_bytes(16)
    stream = hashlib.pbkdf2_hmac("sha256", key, nonce, 200_000, dklen=len(data))
    return MAGIC_XOR + nonce + bytes(b ^ s for b, s in zip(data, stream))


def encrypt_bytes(data: bytes, key: bytes) -> bytes:
    if HAVE_FERNET:
        return MAGIC_FERNET + Fernet(key).encrypt(data)
    _warn_fallback()
    return _xor_crypt(data, key)


def decrypt_bytes(token: bytes, key: bytes) -> bytes:
    if token.startswith(MAGIC_FERNET):
        if not HAVE_FERNET:
            raise ValueError("Fernet ile şifrelenmiş veri var ama cryptography kütüphanesi kurulu değil")
        return Fernet(key).decrypt(token[len(MAGIC_FERNET):])
    if token.startswith(MAGIC_XOR):
        nonce, ct = token[len(MAGIC_XOR):][:16], token[len(MAGIC_XOR) + 16:]
        stream = hashlib.pbkdf2_hmac("sha256", key, nonce, 200_000, dklen=len(ct))
        return bytes(b ^ s for b, s in zip(ct, stream))
    raise ValueError("Bilinmeyen şifreleme formatı")


def encrypt_json(obj: Any, key: bytes) -> bytes:
    return encrypt_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"), key)


def decrypt_json(token: bytes, key: bytes) -> Any:
    return json.loads(decrypt_bytes(token, key).decode("utf-8"))
