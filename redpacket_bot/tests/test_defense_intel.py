# -*- coding: utf-8 -*-
"""defense & intelligence — can sayacı, desen yargıcı, kaynak itibarı testleri."""
from defense.error_counter import ErrorCounter
from intelligence.pattern_judge import PatternJudge
from intelligence.source_trust import SourceTrust


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
        return 12


CFG = {"defense": {"max_lives": 10, "reserve_lives": 2, "critical_usage": 7,
                   "recovery_per_hour": 1.0, "window_hours": 24,
                   "invalid_life_cost": 1.0, "expired_life_cost": 0.5,
                   "emergency_stop_on_lock_signal": True},
       "intelligence": {"pattern": {"min_clean_score": 0.7, "min_suspicious_score": 0.45},
                        "source_trust": {"blacklist_threshold": 0.25,
                                         "invalid_penalty": 0.12, "success_bonus": 0.06,
                                         "burst_threshold": 4, "burst_window_seconds": 600,
                                         "flash_sources": 3, "flash_window_seconds": 120}}}


# ---------------------------------------------------------------------------
# CAN SAYACI
# ---------------------------------------------------------------------------
def test_can_sayaci_maliyetler():
    c = ErrorCounter(CFG, FakeLog(), FakeClock())
    c.record("invalid")                 # -1.0
    c.record("invalid")                 # -1.0
    c.record("expired")                 # -0.5 (gerçek koddan geç kaldık)
    assert c.usage() == 2.5
    assert c.lives_left() == 7.5


def test_can_eskimesi_zamanla_iyilesir():
    clk = FakeClock()
    c = ErrorCounter(CFG, FakeLog(), clk)
    c.record("invalid")                 # t=1000, 1 can
    clk.t += 12 * 3600                  # 12 saat sonra: yarısı geri geldi
    assert abs(c.usage() - 0.5) < 1e-6


def test_rezerv_asla_harcanmaz():
    c = ErrorCounter(CFG, FakeLog(), FakeClock())
    for _ in range(8):                  # 8 can yendi → 2 kaldı
        c.record("invalid")
    assert not c.can_attempt(risk=0.0)  # rezervde: deneme yok


def test_kritik_esik_riski_engeller():
    c = ErrorCounter(CFG, FakeLog(), FakeClock())
    for _ in range(7):                  # kritik eşik 7'de
        c.record("invalid")
    assert c.can_attempt(risk=0.1)      # düşük risk hâlâ denenebilir
    assert not c.can_attempt(risk=0.5)  # yüksek risk durdurulur


def test_ban_acil_durusturur():
    c = ErrorCounter(CFG, FakeLog(), FakeClock())
    c.record("banned")
    assert c.emergency_stop
    assert not c.can_attempt(risk=0.0)


# ---------------------------------------------------------------------------
# DESEN YARGICI
# ---------------------------------------------------------------------------
def test_judge_gercek_kod_temiz():
    j = PatternJudge(CFG, FakeLog())
    r = j.judge("BP7K2M9QX4A1")   # rastgele görünümlü, dengeli
    assert r["verdict"] == "clean"
    assert r["score"] >= 0.7


def test_judge_sahte_kod_cop():
    j = PatternJudge(CFG, FakeLog())
    r = j.judge("BPAAAAAAAAAA")   # tekrarlı, düşük entropi
    assert r["verdict"] == "junk"
    assert "repeated_chars" in r["flags"]


def test_judge_ogrenen_kara_liste(tmp_path):
    CFG["intelligence"]["pattern"]["feature_db"] = str(tmp_path / "f.json")
    j = PatternJudge(CFG, FakeLog())
    # "BP7K…" önekli 3 kod invalid olursa o önek riskli öğrenilir
    for code in ("BP7KAAAAAA1", "BP7KBBBBBB2", "BP7KCCCCCC3"):
        j.learn(code, "invalid")
    assert j.feature_risk("BP7KD12345X") > 0.5  # aynı önek → riskli
    assert j.feature_risk("ZZ9QXY12345") == 0.0  # farklı önek → etkilenmez


# ---------------------------------------------------------------------------
# KAYNAK İTİBARI
# ---------------------------------------------------------------------------
def test_kaynak_karantinaya_girer():
    t = SourceTrust(CFG, FakeLog())
    t.set_base("telegram", 0.9)
    for _ in range(6):                # 6 × 0.12 = 0.72 düşüş → 0.18
        t.record("telegram", "invalid")
    assert t.is_quarantined("telegram")
    assert t.effective_confidence("telegram") == 0.0


def test_expired_itibar_yemez_success_artirir():
    t = SourceTrust(CFG, FakeLog())
    t.set_base("forums", 0.4)
    t.record("forums", "expired")     # gerçek koddan geç kaldık
    assert t.trust_of("forums") == 0.4
    t.record("forums", "success")
    assert t.trust_of("forums") == 0.46


def test_burst_ve_flash_tespiti():
    t = SourceTrust(CFG, FakeLog())
    ts = 1000.0
    for i in range(5):                # 5 kod kısa sürede → bot üretimi şüphesi
        t.note_observation("agg", ts + i * 60)
    assert t.is_burst("agg", ts + 5 * 60)
    # flash: aynı kod 3 farklı kaynakta anında patladı
    sightings = [("a", ts), ("b", ts + 10), ("c", ts + 20)]
    assert t.is_flash("BPX12345", sightings, ts + 30)
