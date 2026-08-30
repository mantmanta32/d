# -*- coding: utf-8 -*-
"""core.queue_engine — kuyruk öncelik & durum testleri."""
import json

from core.queue_engine import QueueEngine, PRIORITY


class FakeLog:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def critical(self, *a, **k): pass


def make_queue(tmp_path, cfg=None):
    q = QueueEngine(cfg or {}, FakeLog())
    q.path = str(tmp_path / "queue.json")
    q.items = []
    return q


def test_ekleme_ve_tekrar_engeli(tmp_path):
    q = make_queue(tmp_path)
    assert q.add("BPAAAA1111", "telegram", 0.9)
    assert not q.add("BPAAAA1111", "forums", 0.4)  # aynı kod ikinci kez eklenmez
    assert q.pending_count() == 1


def test_oncelik_siralamasi(tmp_path):
    q = make_queue(tmp_path)
    q.items = [
        {"code": "X1", "status": "pending", "confidence": 0.9, "found_at": "2026-01-01T00:00:00"},
        {"code": "X2", "status": "success", "confidence": 0.1, "found_at": "2026-01-01T00:00:00"},
        {"code": "X3", "status": "invalid", "confidence": 0.9, "found_at": "2026-01-01T00:00:00"},
        {"code": "X4", "status": "pending", "confidence": 0.2, "found_at": "2026-01-01T00:00:00"},
        {"code": "X5", "status": "pending", "confidence": 0.7, "found_at": "2026-01-01T00:00:00"},
    ]
    batch = q.next_batch()
    codes = [i["code"] for i in batch]
    # yalnızca pending kodlar işlenir (terminal durumlar asla yeniden denenmez)
    assert "X2" not in codes and "X3" not in codes
    # pending'ler güven sırasıyla gelir (0.9 > 0.7 > 0.2; jitter ±0.03 arayı bozamaz)
    assert codes == ["X1", "X5", "X4"]


def test_mark_durum_gunceller(tmp_path):
    q = make_queue(tmp_path)
    q.add("BPBBBB2222", "square", 0.8)
    q.mark("BPBBBB2222", "success")
    assert q.counts()["success"] == 1
    assert q.pending_count() == 0


def test_priority_tablosu_tutarlı():
    # inceleme sırası: kritik terminal durumlar önce listelenir
    assert PRIORITY["success"] < PRIORITY["invalid"] < PRIORITY["pending"]
