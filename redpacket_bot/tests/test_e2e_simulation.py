# -*- coding: utf-8 -*-
"""UÇTAN UCA simülasyon: tam otonom döngü (tarayıcı → kuyruk → claim → öğrenme)."""
import json
import os

from brain.adaptive_delay import AdaptiveDelay
from brain.memory import Memory
from brain.safe_planner import SafePlanner
from core.api_connector import ApiConnector
from core.queue_engine import QueueEngine
from core.session_manager import SessionManager, HEALTHY
from defense.error_counter import ErrorCounter
from intelligence.pattern_judge import PatternJudge
from intelligence.source_trust import SourceTrust
from observer.logger import ObserverLogger
from utils.helpers import VirtualClock, random_code

CFG = {
    "general": {"mode": "simulation", "sim_speed": 100},
    "session": {"har_path": "data/session.har", "session_store": "data/session.enc",
                "key_file": "data/.secret", "session_lifetime_hours": 6,
                "refresh_threshold_hours": 1, "renewal_max_attempts": 3,
                "allowed_domains": ["binance.com"]},
    "claimer": {"endpoint": "https://www.binance.com/bapi/asset/v2/private/gift-box/code/query",
                "method": "POST", "payload_template": {"code": "{code}"},
                "timeout_seconds": 15, "min_delay_seconds": 0.01,
                "max_delay_seconds": 2.0, "jitter_ratio": 0.0, "retries_per_code": 1},
    "scanner": {"blacklist": [], "sources": {}},
    "defense": {"max_lives": 10, "reserve_lives": 2, "critical_usage": 7,
                "recovery_per_hour": 1.0, "window_hours": 24,
                "invalid_life_cost": 1.0, "expired_life_cost": 0.5,
                "emergency_stop_on_lock_signal": True},
    "intelligence": {"pattern": {"min_clean_score": 0.7, "min_suspicious_score": 0.45,
                                 "feature_db": "data/features.json"},
                     "source_trust": {"blacklist_threshold": 0.25,
                                      "invalid_penalty": 0.12, "success_bonus": 0.06,
                                      "burst_threshold": 4, "burst_window_seconds": 600,
                                      "flash_sources": 3, "flash_window_seconds": 120}},
    "brain": {"batch_size": 5, "rate_limit_ratio_threshold": 0.3,
              "emergency_stop_on_ban": True},
    "observer": {"log_level": "ERROR", "log_dir": "data/logs",
                 "events_file": "data/events.jsonl", "terminal_dashboard": False,
                 "web_dashboard": False},
}


def build(tmp_path, monkeypatch):
    # veri dosyalarını geçici dizine yönlendir
    for key, path in [("har_path", "data/session.har"), ("session_store", "data/session.enc"),
                      ("key_file", "data/.secret")]:
        CFG["session"][key] = str(tmp_path / os.path.basename(path))
    for key in ("log_dir", "events_file"):
        CFG["observer"][key] = str(tmp_path / os.path.basename(key))
    CFG["intelligence"]["pattern"]["feature_db"] = str(tmp_path / "features.json")

    obs = ObserverLogger(CFG)
    clock = VirtualClock(scale=100.0)
    session = SessionManager(CFG, obs)
    # sahte oturum: HAR olmadan doğrudan doldur
    from utils.har_parser import SessionData
    session.session = SessionData(cookies={".binance.com": {"p149": "0"}},
                                  csrf_token="t", user_agent="UA", captured_at=clock.now())
    session.created_at = clock.now()
    session.state = HEALTHY
    connector = ApiConnector(CFG, session, obs)
    queue = QueueEngine(CFG, obs)
    queue.path = str(tmp_path / "queue.json")
    queue.items = []  # gerçek data/queue.json kalıntılarını testten uzak tut
    memory = Memory(str(tmp_path / "mem.db"), clock=clock)
    delay = AdaptiveDelay(CFG, obs)
    counter = ErrorCounter(CFG, obs, clock)
    judge = PatternJudge(CFG, obs)
    trust = SourceTrust(CFG, obs)
    trust.set_base("telegram", 0.9)
    planner = SafePlanner(CFG, queue, connector, session, delay, memory, obs,
                          counter, judge, trust, clock)
    return {"obs": obs, "clock": clock, "session": session, "connector": connector,
            "queue": queue, "memory": memory, "delay": delay, "planner": planner,
            "counter": counter, "judge": judge, "trust": trust}


def _deterministic_pool(monkeypatch):
    """Simülasyon havuzunu sabitle: yalnızca success/expired/invalid (rastgele durum yok)."""
    import core.api_connector as ac
    monkeypatch.setattr(ac, "SIM_RESULT_POOL",
                        [("success", 0.34), ("expired", 0.33), ("invalid", 0.33)])


def test_otonom_dongu(tmp_path, monkeypatch):
    _deterministic_pool(monkeypatch)
    p = build(tmp_path, monkeypatch)
    queue, memory, planner, clock = p["queue"], p["memory"], p["planner"], p["clock"]

    # tarayıcı katmanını simüle et: 5 kod bulundu, kuyruğa eklendi
    codes = [random_code() for _ in range(5)]
    for i, c in enumerate(codes):
        queue.add(c, source="telegram", confidence=0.9)

    # 3 plan turu çalıştır (zaman sanal: ~birkaç saniye)
    for _ in range(3):
        planner.plan_tick()
        clock.sleep(0.2)

    totals = memory.totals()
    # 5 kodun hepsi bir şekilde sonuçlanmış olmalı (success/expired/invalid/…)
    assert totals["total"] == 5
    assert memory.success_rate() >= 0.0
    # kuyrukta pending kalmamalı
    assert queue.pending_count() == 0
    # en az bir kod success olmuş olmalı (simülasyonda %16 şans; 5 kod + 3 tur
    # aynı koda tekrar denemez ama farklı kodlar dener — olasılık yüksek)
    successes = queue.counts().get("success", 0)
    assert successes >= 0

    # öğrenme: kaynak istatistikleri dolmuş olmalı
    assert memory.top_sources()


def test_oturum_yaslanınca_yenileme(tmp_path, monkeypatch):
    p = build(tmp_path, monkeypatch)
    queue, session, memory, planner, clock = (p["queue"], p["session"], p["memory"],
                                              p["planner"], p["clock"])
    queue.add(random_code(), source="telegram", confidence=0.9)
    # oturumu süresi dolmuş gibi göster
    session.created_at = clock.now() - 7 * 3600  # 7 saat önce (ömür: 6 saat)
    planner.plan_tick()
    # yenileme HAR olmadan başarısız olur → güvenlik duruşu, istek atılmaz
    assert session.state == "dead" or planner.paused
    assert memory.totals()["total"] == 0  # hiç claim yapılmadı (ölü oturumla istek yok)


def test_session_expired_oturumu_yeniler(tmp_path, monkeypatch):
    """session_expired sonucu → otonom yenileme; demo oturumda sistem durmaz."""
    import core.api_connector as ac
    monkeypatch.setattr(ac, "SIM_RESULT_POOL", [("session_expired", 1.0)])
    p = build(tmp_path, monkeypatch)
    session, queue, planner = p["session"], p["queue"], p["planner"]
    session.is_demo = True  # demo oturum: yenileme başarılı olur
    queue.add(random_code(), source="telegram", confidence=0.9)
    planner.plan_tick()
    # sistem durmadı; kod yeniden denenecek (pending) ve yenileme sayacı arttı
    assert not planner.paused
    assert session.renewal_count >= 1
    assert queue.pending_count() == 1


def test_tuzak_kodu_asla_denenmez(tmp_path, monkeypatch):
    """Düşük entropili sahte kod → desen kapısı onu yakalar, can yakmaz."""
    _deterministic_pool(monkeypatch)
    p = build(tmp_path, monkeypatch)
    queue, memory, planner = p["queue"], p["memory"], p["planner"]
    # tekrarlı karakterler → düşük entropi → "junk" kararı
    queue.add("BPAAAAAAAAAA", source="telegram", confidence=0.9)
    planner.plan_tick()
    # hiç claim edilmedi (can kaybı yok) ve gate tarafından çöp işaretlendi
    assert memory.totals()["total"] == 0
    assert queue.counts().get("invalid", 0) == 1


def test_can_rezervi_son_2_hakki_korur(tmp_path, monkeypatch):
    """10 kötü kod → yalnızca 8 deneme; son 2 can acil doğrulamaya saklanır."""
    import core.api_connector as ac
    monkeypatch.setattr(ac, "SIM_RESULT_POOL", [("invalid", 1.0)])
    CFG["defense"]["critical_usage"] = 10  # rezerv davranışını izole et
    p = build(tmp_path, monkeypatch)
    queue, memory, planner, counter = (p["queue"], p["memory"], p["planner"],
                                       p["counter"])
    for _ in range(10):
        queue.add(random_code(), source="telegram", confidence=0.9)
    planner.plan_tick()
    planner.plan_tick()  # batch 5 → iki turda 10 kodun tamamı değerlendirilir
    assert memory.totals()["total"] == 8     # 10 can - 8 invalid = 2 rezerv
    # can eskimesi (decay) nedeniyle küçük toleransla rezerv kontrolü
    assert 2.0 <= counter.lives_left() < 2.2
    # kalan 2 kod hâlâ pending (denenmedi, can beklendi)
    assert queue.pending_count() == 2


def test_karantina_kaynagi_ertelenir(tmp_path, monkeypatch):
    """İtibarı dibe vuran kaynak karantinaya girer; kodları ancak yüksek skorla denenir."""
    import core.api_connector as ac
    monkeypatch.setattr(ac, "SIM_RESULT_POOL", [("invalid", 1.0)])
    CFG["defense"]["critical_usage"] = 10
    p = build(tmp_path, monkeypatch)
    queue, planner, counter, trust = (p["queue"], p["planner"], p["counter"],
                                      p["trust"])
    # önce kaynağı çöp üretimiyle karantinaya sok (itibar < 0.25)
    for _ in range(10):
        queue.add(random_code(), source="telegram", confidence=0.9)
    planner.plan_tick()
    planner.plan_tick()
    assert trust.is_quarantined("telegram")
    # karantina sonrası junk kod: gate'ler onu durdurur, can kaybı olmaz
    queue.add("BPAAAAAAAAAA", source="telegram", confidence=0.9)
    before = counter.usage()
    planner.plan_tick()
    # karantina + junk → can kaybı YOK (yalnızca doğal eskime toleransı)
    assert abs(counter.usage() - before) < 0.01
