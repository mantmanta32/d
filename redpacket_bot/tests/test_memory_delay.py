# -*- coding: utf-8 -*-
"""brain.memory & brain.adaptive_delay — öğrenme ve hız denetleyici testleri."""
from brain.adaptive_delay import AdaptiveDelay
from brain.memory import Memory


class FakeLog:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def critical(self, *a, **k): pass


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def hour(self, ts=None):
        import datetime
        return datetime.datetime.fromtimestamp(ts if ts else self.t).hour


def test_memory_istatistik(tmp_path):
    mem = Memory(str(tmp_path / "m.db"), clock=FakeClock())
    for i in range(10):
        mem.record_result({"code": f"C{i}", "category": "success" if i % 2 == 0 else "expired",
                           "source": "telegram", "confidence": 0.9})
    assert mem.totals()["success"] == 5
    assert mem.totals()["expired"] == 5
    assert 49.0 < mem.success_rate() < 51.0
    top = mem.top_sources()
    assert top and top[0]["source"] == "telegram"
    mem.close()


def test_adaptive_delay_öğrenme():
    d = AdaptiveDelay({"claimer": {"min_delay_seconds": 0.5, "max_delay_seconds": 30.0,
                                   "jitter_ratio": 0.0}}, FakeLog())
    assert d.delay == 0.5
    # başarı serisi → hızlanma (gecikme min'in üzerinde kaldığı sürece düşer)
    for _ in range(5):
        d.on_result("success")
    assert d.delay < 0.75
    # rate limit → yavaşlama
    d.on_result("rate_limited")
    assert d.delay > 0.75
    # ban → acil durum + gecikme ×3
    before = d.delay
    d.on_result("banned")
    assert d.emergency_stop
    assert d.delay == before * 3.0


def test_adaptive_delay_sinirlar():
    d = AdaptiveDelay({"claimer": {"min_delay_seconds": 1.0, "max_delay_seconds": 10.0,
                                   "jitter_ratio": 0.0}}, FakeLog())
    for _ in range(20):
        d.on_result("rate_limited")
    assert d.delay <= 10.0  # üst sınır asla aşılmaz
    for _ in range(200):
        d.on_result("success")
    assert d.delay >= 1.0  # alt sınır asla aşılmaz
