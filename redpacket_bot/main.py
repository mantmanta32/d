# -*- coding: utf-8 -*-
"""
main.py — RedPacketBot: OTONOM AVCININ TEK GİRİŞ NOKTASI
========================================================
Tüm sistemi başlatır/durdurur:

    python3 main.py                  # otonom döngü (simülasyon modu)
    python3 main.py --live           # CANLI mod (gerçek istekler! dikkatli kullan)
    python3 main.py import-har data/session.har
    python3 main.py status
    python3 main.py resume           # acil duruştan sonra planlamayı devam ettir
    python3 main.py --once           # tek tarama + tek plan turu (test)

Bağımlılık: yalnızca Python 3.10+ standart kütüphanesi.
İsteğe bağlı: `pip install cryptography` → güçlü oturum şifrelemesi.

GÜVENLİK: varsayılan mod SİMÜLASYON'dur. Gerçek istek yalnızca --live ile
ve oturum kaynağı (HAR) hazırken yapılır. Ölü oturumla istek asla atılmaz.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from utils.helpers import VirtualClock, ensure_dirs, load_json, save_json  # noqa: E402

CFG_PATH = BASE / "config" / "settings.json"
DATA_DIR = BASE / "data"


# ---------------------------------------------------------------------------
def load_cfg() -> dict:
    cfg = load_json(str(CFG_PATH), {})
    if not cfg:
        sys.exit(f"config/settings.json bulunamadı veya bozuk: {CFG_PATH}")
    # veri dizinini kök olarak ayarla (göreli yollar settings'e göre çözülür)
    os.chdir(BASE)
    ensure_dirs(str(DATA_DIR))
    return cfg


# ---------------------------------------------------------------------------
class RedPacketBot:
    """Tüm bileşenleri birbirine bağlayan orkestratör."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        g = cfg.get("general", {})
        self.mode = g.get("mode", "simulation")
        self.loop_interval = float(g.get("loop_interval_seconds", 10))
        self.clock = VirtualClock(scale=float(g.get("sim_speed", 1)))

        from observer.logger import ObserverLogger
        from observer.notifier import Notifier
        from observer.dashboard import Dashboard
        from core.session_manager import SessionManager
        from core.api_connector import ApiConnector
        from core.queue_engine import QueueEngine
        from brain.memory import Memory
        from brain.adaptive_delay import AdaptiveDelay
        from brain.planner import Planner

        self.obs = ObserverLogger(cfg)
        self.notifier = Notifier(cfg, self.obs)
        self.dashboard = Dashboard(cfg, self.obs, self.clock)
        self.session = SessionManager(cfg, self.obs)
        self.connector = ApiConnector(cfg, self.session, self.obs)
        self.queue = QueueEngine(cfg, self.obs)
        self.memory = Memory("data/memory.db", clock=self.clock)
        self.delay = AdaptiveDelay(cfg, self.obs)
        self.planner = Planner(cfg, self.queue, self.connector, self.session,
                               self.delay, self.memory, self.obs, self.clock)
        self._scanners = self._build_scanners()
        self._running = False
        self._new_codes_this_tick = 0
        self._stats = {"scans": 0, "plans": 0, "codes_found": 0}
        self._lock = threading.Lock()

        # oturum yenileme kancası: planner beynine bağla (otonom yenileme)
        def renew_hook():
            # 1. aday: kayıtlı HAR tazelemesi
            return None
        self.session._renew_hook = renew_hook

        self.obs.info("main", f"RedPacketBot hazır — mod: {self.mode.upper()}, "
                              f"simülasyon hızı: x{self.clock.scale}")

    # ------------------------------------------------------------------
    def _build_scanners(self) -> list:
        from scanner.binance_square import BinanceSquareScanner
        from scanner.telegram_channel import TelegramChannelScanner
        from scanner.forums import ForumScanner
        from scanner.aggregators import AggregatorScanner
        scanners = [
            BinanceSquareScanner(self.cfg, self.queue, self.obs),
            TelegramChannelScanner(self.cfg, self.queue, self.obs),
            ForumScanner(self.cfg, self.queue, self.obs),
            AggregatorScanner(self.cfg, self.queue, self.obs),
        ]
        # simülasyonda demo kaynağı devrede: gerçek ağ olmadan otonomi izlenir
        if self.mode == "simulation":
            from scanner.demo import DemoScanner
            scanners.append(DemoScanner(self.cfg, self.queue, self.obs))
        return scanners

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._running = True
        self.obs.info("main", "Başlatılıyor…")
        if not self.session.load():
            self.obs.error("main", "Oturum yüklenemedi — durduruluyor. "
                          "Çözüm: `python3 main.py import-har <dosya.har>`")
            self._running = False
            return
        self.dashboard.status_fn = self.build_status
        self.dashboard.start()
        self._bind_events()

        # otonom döngü: tarayıcılar → planlayıcı → panel (ayrı iş parçacıkları)
        threads = [
            threading.Thread(target=self._scan_loop, daemon=True, name="scanner"),
            threading.Thread(target=self._plan_loop, daemon=True, name="planner"),
        ]
        for t in threads:
            t.start()
        self.obs.info("main", "Otonom döngü başladı. Ctrl+C ile durdur.")
        try:
            while self._running:
                time.sleep(1.0)
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        self._running = False
        self.dashboard.stop()
        self.memory.close()
        self.obs.info("main", "Sistem durduruldu. Veriler korundu: data/")

    # ------------------------------------------------------------------
    def _scan_loop(self) -> None:
        while self._running:
            self._new_codes_this_tick = 0
            for sc in self._scanners:
                if not self._running:
                    return
                added = sc.tick()
                self._new_codes_this_tick += len(added)
                self._stats["codes_found"] += len(added)
            self._stats["scans"] += 1
            time.sleep(1.0)

    def _plan_loop(self) -> None:
        while self._running:
            report = self.planner.plan_tick()
            self._stats["plans"] += 1
            status = self.build_status()
            self.dashboard.render_terminal(status)
            time.sleep(self.loop_interval)

    # ------------------------------------------------------------------
    def _bind_events(self) -> None:
        """Observer olay akışını bildirimciye bağla."""
        orig = self.obs._emit

        def wrapped(level, *args, **kw):
            orig(level, *args, **kw)
            component = args[0] if len(args) > 1 else "main"
            msg = args[-1]
            try:
                self.notifier.on_event({"level": level, "component": component,
                                        "message": msg})
            except Exception:
                pass
        self.obs._emit = wrapped

    # ------------------------------------------------------------------
    def build_status(self) -> dict:
        with self._lock:
            return {
                "system": {
                    "mode": self.mode,
                    "plans_executed": self.planner.plans_executed,
                    "plans": self._stats["plans"],
                    "scans": self._stats["scans"],
                    "codes_found": self._stats["codes_found"],
                    "new_codes_this_tick": self._new_codes_this_tick,
                    "paused": self.planner.paused,
                    "pause_reason": self.planner.pause_reason,
                    "sources": {sc.name: sc.snapshot() for sc in self._scanners},
                },
                "queue": self.queue.snapshot(),
                "session": self.session.snapshot(),
                "memory": {
                    "success_rate": round(self.memory.success_rate(), 1),
                    "totals": self.memory.totals(),
                },
                "delay": self.delay.snapshot(),
                "insights": self.planner.insights(),
                "events": self.obs.recent(60),
            }

    def print_status(self) -> None:
        print(json.dumps(self.build_status(), ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
def cmd_import_har(cfg: dict, path: str) -> None:
    from core.session_manager import SessionManager
    from observer.logger import ObserverLogger
    obs = ObserverLogger(cfg)
    sm = SessionManager(cfg, obs)
    if sm.import_har(path):
        print(f"✓ Oturum HAR'dan alındı ve şifrelendi → {sm.store_path}")
        print(f"  Çerezler: {len(sm.session.all_cookies())}, "
              f"csrf: {'var' if sm.csrf_token() else 'yok'}")
    else:
        sys.exit(1)


def cmd_status(cfg: dict) -> None:
    bot = RedPacketBot(cfg)
    # oturum verisi için sadece yükle, döngü başlatma
    if not bot.session.load():
        sys.exit("Oturum yüklenemedi (data/session.enc veya HAR yok).")
    bot.build_status()
    bot.print_status()


def main() -> int:
    parser = argparse.ArgumentParser(prog="redpacketbot", description=__doc__)
    parser.add_argument("--live", action="store_true", help="CANLI mod (gerçek istekler)")
    parser.add_argument("--once", action="store_true", help="tek tarama + tek plan turu")
    parser.add_argument("--no-web", action="store_true", help="web panelini açma")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", help="anlık durum özeti (JSON)")
    sub.add_parser("resume", help="acil duruştan sonra planlamayı sürdür")
    sub.add_parser("import-har", help="HAR dosyasını oturum olarak içe aktar")
    args, rest = parser.parse_known_args()

    cfg = load_cfg()

    if args.live:
        cfg.setdefault("general", {})["mode"] = "live"
    if args.no_web:
        cfg.setdefault("observer", {})["web_dashboard"] = False
    if args.cmd == "import-har":
        path = rest[0] if rest else cfg.get("session", {}).get("har_path", "data/session.har")
        cmd_import_har(cfg, path)
        return 0
    if args.cmd == "status":
        cmd_status(cfg)
        return 0

    bot = RedPacketBot(cfg)
    if args.cmd == "resume":
        bot.planner.resume()
        bot.print_status()
        return 0
    if args.once:
        bot.obs.info("main", "TEK TUR modu")
        bot.session.load()
        for sc in bot._scanners:
            sc.tick(force=True)
        bot.planner.plan_tick()
        bot.print_status()
        bot.memory.close()
        return 0

    bot.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
