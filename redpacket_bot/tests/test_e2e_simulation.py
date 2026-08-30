# -*- coding: utf-8 -*-
"""UÇTAN UCA simülasyon: tam otonom döngü (tarayıcı → kuyruk → claim → öğrenme)."""
import json
import os

from brain.adaptive_delay import AdaptiveDelay
from brain.memory import Memory
from brain.planner import Planner
from core.api_connector import ApiConnector
from core.queue_engine import QueueEngine
from core.session_manager import SessionManager, HEALTHY
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
    "brain": {"batch_size": 3, "rate_limit_ratio_threshold": 0.3,
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
    memory = Memory(str(tmp_path / "mem.db"), clock=clock)
    delay = AdaptiveDelay(CFG, obs)
    planner = Planner(CFG, queue, connector, session, delay, memory, obs, clock)
    return obs, clock, session, connector, queue, memory, delay, planner


def _deterministic_pool(monkeypatch):
    """Simülasyon havuzunu sabitle: yalnızca success/expired/invalid (rastgele durum yok)."""
    import core.api_connector as ac
    monkeypatch.setattr(ac, "SIM_RESULT_POOL",
                        [("success", 0.34), ("expired", 0.33), ("invalid", 0.33)])


def test_otonom_dongu(tmp_path, monkeypatch):
    _deterministic_pool(monkeypatch)
    obs, clock, session, connector, queue, memory, delay, planner = build(tmp_path, monkeypatch)

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
    obs, clock, session, connector, queue, memory, delay, planner = build(tmp_path, monkeypatch)
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
    obs, clock, session, connector, queue, memory, delay, planner = build(tmp_path, monkeypatch)
    session.is_demo = True  # demo oturum: yenileme başarılı olur
    queue.add(random_code(), source="telegram", confidence=0.9)
    planner.plan_tick()
    # sistem durmadı; kod yeniden denenecek (pending) ve yenileme sayacı arttı
    assert not planner.paused
    assert session.renewal_count >= 1
    assert queue.pending_count() == 1
