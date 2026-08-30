#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance Red Packet - KOD RADARI (cok kaynakli)
-----------------------------------------------
Kaynaklar:
  - Agregator sayfalar : MEXC, MiningCombo, redpacketcode.com
  - Telegram kanallari : t.me/s mirror'lari
  - Binance Square     : dogrudan sayfa + arama uzerinden
  - X / Reddit / forum : DuckDuckGo HTML aramasi (snippet + sonuc linkleri)
  - Bolgesel           : Hindistan / Cin / Rusya / ABD arama sorgulari

Yeni kod bulunca: konsola basar, Telegram bildirimi atar,
claim_queue.json'a yazar (api_claim.py buradan okur).

KULLANIM:
    python3 kod_radar.py --once
    python3 kod_radar.py
    python3 kod_radar.py --queue

BU BETIK HICBIR BINANCE HESAP BILGISI ISTMEZ.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"
SEEN_PATH = BASE / "seen_codes.json"
QUEUE_PATH = BASE / "claim_queue.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

CODE_RE = re.compile(r"\b(BP[A-Z0-9]{8}|[A-Z0-9]{8})\b")
AGG_RE = re.compile(r"Code\s+Is:?\s*([A-Z0-9]{8,10})\b")
STOPWORDS = {
    "CAMPAIGN", "VALIDITY", "PASSWORD", "SECURITY", "VERIFIED",
    "EXCHANGE", "REDEEMED", "FEATURED", "ACCOUNTS", "DOWNLOAD",
}

# takip edilip sayfasi indirilecek domain'ler (arama sonuclarindan)
FOLLOW_HOSTS = (
    "coingabbar.com", "bitrue.com", "mexc.com", "hokanews.com",
    "miningcombo.com", "quiknotes.in", "redpacketcode.com",
    "reddit.com", "bittime.com", "binance.com",
)

SOURCES = {
    # --- sayfalar -----------------------------------------------------------
    "mexc":            {"type": "page", "url": "https://blog.mexc.com/crypto-knowledge/binance-red-packet-codes-today/"},
    "miningcombo":     {"type": "page", "url": "https://miningcombo.com/red-packet/"},
    "telegram":        {"type": "page", "url": "https://t.me/s/binance_red_packet_code_daily_0"},
    "telegram_z":      {"type": "page", "url": "https://t.me/s/binance_red_packetz"},
    "square":          {"type": "page", "url": "https://www.binance.com/en/square/hashtag/redpacketshare"},
    # --- aramalar (X, Reddit, Square, bolgesel forumlar) ---------------------
    "ddg_en":     {"type": "ddg", "query": "binance red packet code today", "follow": True},
    "ddg_x":      {"type": "ddg", "query": "binance red packet code twitter"},
    "ddg_reddit": {"type": "ddg", "query": "binance red packet code reddit"},
    "ddg_square": {"type": "ddg", "query": "binance red packet code square"},
    "ddg_in":     {"type": "ddg", "query": "binance red packet code today india", "follow": True},
    "ddg_ru":     {"type": "ddg", "query": "binance красный конверт код"},
    "ddg_cn":     {"type": "ddg", "query": "binance 红包 码"},
}

DEFAULT_CFG = {
    "interval_seconds": 120,
    "sources": list(SOURCES.keys()),
    "telegram_notify": {"enabled": False, "bot_token": "", "chat_id": ""},
}


# ---------------------------------------------------------------------------
def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if CFG_PATH.exists():
        try:
            stored = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        except Exception:
            stored = {}
        for k, v in stored.items():
            if k == "sources" and isinstance(v, list):
                # eski config'lerde yeni kaynaklar eksik kalmasin: birlesim
                cfg["sources"] = list(dict.fromkeys(v + DEFAULT_CFG["sources"]))
            else:
                cfg[k] = v
    return cfg


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def strip_tags(html):
    return re.sub(r"<[^>]+>", " ", html)


def clean(codes):
    out = []
    for c in codes:
        if c in STOPWORDS or c in out:
            continue
        out.append(c)
    return out


def codes_free(text):
    """Serbest metin: BP kodlar her zaman; 8'li kodlar harf+rakam icerirse."""
    found = []
    for m in CODE_RE.finditer(text):
        c = m.group(1)
        if not c.startswith("BP"):
            has_digit = any(ch.isdigit() for ch in c)
            has_alpha = any(ch.isalpha() for ch in c)
            if not (has_digit and has_alpha):
                continue  # 31536000 / 00000057 gibi sayi coplugunu ele
        found.append(c)
    return clean(found)


def codes_agg(text):
    return clean([c.upper() for c in AGG_RE.findall(text)])


def parse_page(html):
    text = strip_tags(html)
    seen = []
    for c in codes_agg(text) + codes_free(text):
        if c not in seen:
            seen.append(c)
    return seen


# ---------------------------------------------------------------------------
# DuckDuckGo HTML
# ---------------------------------------------------------------------------
def ddg_search(query):
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    html = fetch(url)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    codes = []
    for s in snippets:
        codes += codes_free(strip_tags(s))
    urls = []
    for href in re.findall(r'class="result__a"[^>]*href="([^"]+)"', html):
        m = re.search(r"uddg=([^&]+)", href)
        real = urllib.parse.unquote(m.group(1)) if m else href
        host = urllib.parse.urlparse(real).netloc.lower()
        if any(host.endswith(h) for h in FOLLOW_HOSTS):
            urls.append(real)
    return clean(codes), urls


# ---------------------------------------------------------------------------
def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def notify(cfg, msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    tg = cfg.get("telegram_notify", {})
    if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
        url = f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage"
        payload = json.dumps({"chat_id": tg["chat_id"], "text": msg}).encode()
        try:
            req = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:
            print(f"  (telegram bildirim hatasi: {e})", flush=True)


def scan_once(cfg):
    seen = load_json(SEEN_PATH, {})
    new_entries = []
    new_total = 0
    follows_left = 4  # tur basina en fazla 4 takip edilen sayfa
    for src in cfg.get("sources", []):
        spec = SOURCES.get(src)
        if not spec:
            continue
        try:
            if spec["type"] == "page":
                codes = parse_page(fetch(spec["url"]))
            else:  # ddg
                time.sleep(cfg.get("ddg_sleep_seconds", 2))  # bot blogunu asmamak icin
                codes, urls = ddg_search(spec["query"])
                if spec.get("follow"):
                    for u in urls[:2]:
                        if follows_left <= 0:
                            break
                        follows_left -= 1
                        try:
                            codes += parse_page(fetch(u))
                        except Exception:
                            pass
                    codes = clean(codes)
        except Exception as e:
            print(f"  ! {src}: {e}", flush=True)
            continue
        fresh = [c for c in codes if c not in seen]
        if not fresh:
            print(f"  - {src}: {len(codes)} kod, yeni yok", flush=True)
            continue
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for c in fresh:
            seen[c] = now
            new_entries.append({"code": c, "status": "pending", "found_at": now, "source": src})
        new_total += len(fresh)
        notify(cfg, f"YENI KOD ({src}): " + ", ".join(fresh))
    save_json(SEEN_PATH, seen)
    # eszamanlilik: claim dongusu durumlari guncellemis olabilir ->
    # kuyrugu taze oku, sadece YENI kodlari ekle, mevcut durumlara dokunma
    queue = load_json(QUEUE_PATH, [])
    existing = {x["code"] for x in queue}
    for e in new_entries:
        if e["code"] not in existing:
            queue.append(e)
            existing.add(e["code"])
    save_json(QUEUE_PATH, queue)
    return new_total


def main():
    cfg = load_cfg()
    if "--queue" in sys.argv:
        queue = load_json(QUEUE_PATH, [])
        pending = [q for q in queue if q["status"] == "pending"]
        print(f"Bekleyen kod: {len(pending)}")
        for q in pending:
            print(f"  {q['code']}  ({q['source']}, {q['found_at']})")
        return
    if "--once" in sys.argv:
        n = scan_once(cfg)
        print(f"Tek tarama bitti. Yeni kod: {n}")
        return
    print(f"Radar basladi. {len(cfg['sources'])} kaynak, "
          f"aralik: {cfg['interval_seconds']}s. Ctrl+C ile durdur.")
    while True:
        try:
            scan_once(cfg)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"  ! dongu hatasi: {e}", flush=True)
        time.sleep(cfg.get("interval_seconds", 120))


if __name__ == "__main__":
    main()
