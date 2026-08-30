# -*- coding: utf-8 -*-
"""
brain/memory.py — Öğrenme hafızası (kalıcı, SQLite)
----------------------------------------------------
Sistemin "günler geçtikçe öğrenmesi" burada saklanır:

- results:      her claim denemesinin ham sonucu (zaman, kaynak, kod, kategori)
- source_stats: kaynak kalite istatistikleri (kaç kod, kaç başarı, skor)
- hour_stats:   saat dilimi verimliliği (hangi saatlerde başarı oranı yüksek)
- delay_history: adaptif gecikme geçmişi

Bu tablolar sayesinde planner şunları öğrenir:
  "Bu saatler daha dolu"  → hour_stats
  "Bu kaynak daha kaliteli" → source_stats
  "Bu gecikme aralığında engel yok" → delay_history
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Optional

from utils.helpers import now_iso


class Memory:
    def __init__(self, path: str = "data/memory.db", clock: Any = None):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # check_same_thread=False: tarayıcı/planlayıcı thread'leri ayrı çalışır;
        # SQLite kendi kilidiyle eşzamanlı erişimi zaten serileştirir.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.clock = clock
        self._migrate()

    def _migrate(self) -> None:
        cur = self.conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            code TEXT NOT NULL,
            source TEXT,
            confidence REAL,
            category TEXT NOT NULL,
            message TEXT,
            delay REAL
        );
        CREATE TABLE IF NOT EXISTS source_stats (
            source TEXT PRIMARY KEY,
            found INTEGER DEFAULT 0,
            claimed INTEGER DEFAULT 0,
            success INTEGER DEFAULT 0,
            score REAL DEFAULT 0.0,
            updated REAL
        );
        CREATE TABLE IF NOT EXISTS hour_stats (
            hour INTEGER PRIMARY KEY,
            attempts INTEGER DEFAULT 0,
            success INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS delay_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            delay REAL,
            last_category TEXT
        );
        """)
        self.conn.commit()

    # ------------------------------------------------------------------
    def _now(self) -> float:
        if self.clock is not None:
            return self.clock.now()
        return time.time()

    def record_result(self, result: dict) -> None:
        """Claim sonucunu kaydeder + ilgili istatistikleri günceller."""
        cat = result.get("category", "invalid")
        ts = self._now()
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO results (ts, code, source, confidence, category, message, delay) "
            "VALUES (?,?,?,?,?,?,?)",
            (ts, result.get("code"), result.get("source"), result.get("confidence"),
             cat, (result.get("message") or "")[:300], result.get("delay")),
        )
        # kaynak istatistikleri
        src = result.get("source") or "unknown"
        cur.execute("SELECT * FROM source_stats WHERE source=?", (src,))
        row = cur.fetchone()
        if row:
            found = row["found"] + 1
            claimed = row["claimed"] + (1 if cat != "duplicate" else 0)
            success = row["success"] + (1 if cat == "success" else 0)
        else:
            found, claimed, success = 1, 1, 1 if cat == "success" else 0
        # skor: başarı oranı ağırlıklı + hacim bonusu
        score = (success / max(1, found)) * 100 + min(20, found)
        cur.execute(
            "INSERT OR REPLACE INTO source_stats (source, found, claimed, success, score, updated) "
            "VALUES (?,?,?,?,?,?)",
            (src, found, claimed, success, round(score, 2), ts),
        )
        # saat istatistikleri
        hour = self._hour_of(ts)
        cur.execute("INSERT OR REPLACE INTO hour_stats (hour, attempts, success) "
                    "VALUES (?, COALESCE((SELECT attempts FROM hour_stats WHERE hour=?),0)+1, "
                    "COALESCE((SELECT success FROM hour_stats WHERE hour=?),0)+?)",
                    (hour, hour, hour, 1 if cat == "success" else 0))
        self.conn.commit()

    def _hour_of(self, ts: float) -> int:
        if self.clock is not None:
            return self.clock.hour(ts)
        import datetime
        return datetime.datetime.fromtimestamp(ts).hour

    # ------------------------------------------------------------------
    def top_sources(self, limit: int = 5) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM source_stats ORDER BY score DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]

    def best_hours(self, limit: int = 3) -> list[dict]:
        """En yüksek başarı oranlı saatler (yeterli denemesi olanlar)."""
        rows = self.conn.execute(
            "SELECT hour, attempts, success, ROUND(success*100.0/attempts,1) AS rate "
            "FROM hour_stats WHERE attempts >= 3 ORDER BY rate DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def success_rate(self) -> float:
        row = self.conn.execute("SELECT COUNT(*) n, SUM(category='success') s FROM results").fetchone()
        n, s = row["n"], row["s"] or 0
        return (s / n * 100.0) if n else 0.0

    def recent_results(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM results ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def totals(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) total, SUM(category='success') success, "
            "SUM(category='expired') expired, SUM(category='invalid') invalid, "
            "SUM(category='rate_limited') rate_limited, "
            "SUM(category='session_expired') session_expired, "
            "SUM(category='banned') banned FROM results").fetchone()
        return {k: (v or 0) for k, v in dict(row).items()}

    def close(self) -> None:
        self.conn.close()
