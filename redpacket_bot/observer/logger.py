# -*- coding: utf-8 -*-
"""
observer/logger.py — Yapılandırılabilir günlükçü + olay akışı
--------------------------------------------------------------
- Konsola: renkli, seviyeli satırlar (zaman damgalı).
- Dosyaya: data/logs/redpacketbot.log (dönen dosya, 1 MB × 5).
- Olay akışı: data/events.jsonl — observer'ın diğer parçaları ve web paneli
  bu akıştan beslenir. Hassas alanlar maskelenir (redact).

Seviyeler: DEBUG < INFO < SUCCESS < WARNING < ERROR < CRITICAL
"""
from __future__ import annotations

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Any

from utils.helpers import ensure_dirs, now_iso, redact

LEVELS = {"DEBUG": 10, "INFO": 20, "SUCCESS": 25, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

_COLORS = {
    "DEBUG": "\033[90m", "INFO": "\033[36m", "SUCCESS": "\033[32m",
    "WARNING": "\033[33m", "ERROR": "\033[31m", "CRITICAL": "\033[1;31m",
}
_RESET = "\033[0m"


class LevelFilter(logging.Filter):
    def __init__(self, name: str = ""):
        super().__init__(name)
        self.level = logging.INFO

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= self.level


class ObserverLogger:
    """Uygulamanın tek günlükçüsü — her katman bunu kullanır."""

    def __init__(self, cfg: dict):
        o = cfg.get("observer", {})
        self.log_dir = o.get("log_dir", "data/logs")
        self.events_file = o.get("events_file", "data/events.jsonl")
        self.level_name = o.get("log_level", "INFO").upper()
        ensure_dirs(self.log_dir, os.path.dirname(self.events_file) or ".")

        self.logger = logging.getLogger("redpacketbot")
        self.logger.setLevel(LEVELS.get(self.level_name, logging.INFO))
        self.logger.propagate = False
        logging.addLevelName(LEVELS["SUCCESS"], "SUCCESS")  # özel seviyeye ad ver
        self._init_handlers()

        self.event_bus: list = []   # dashboard'ın bağlanacağı bellek içi tampon
        self._event_count = 0
        self._recent_events: list[dict] = []
        self._MAX_RECENT = 100

    def _init_handlers(self) -> None:
        self.logger.handlers.clear()
        # konsol
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(LEVELS.get(self.level_name, logging.INFO))
        console.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
                                               "%H:%M:%S"))
        self.logger.addHandler(console)
        # dosya (dönen)
        fh = RotatingFileHandler(os.path.join(self.log_dir, "redpacketbot.log"),
                                 maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
        self.logger.addHandler(fh)

    # ------------------------------------------------------------------
    def _emit(self, level: str, *args: Any, **extra: Any) -> None:
        """Seviyeli olay basar.

        İki çağrı biçimi desteklenir (büyüyen sistemde her ikisi de kullanılır):
            log.info("mesaj")                    → component="main"
            log.info("planner", "mesaj")         → component="planner"
        """
        if len(args) == 1:
            component, msg = "main", args[0]
        else:
            component, msg = args[0], args[1]
        lvl = LEVELS.get(level, logging.INFO)
        self.logger.log(lvl, "[%s] %s", component, msg)
        event = {
            "ts": now_iso(),
            "level": level,
            "component": component,
            "message": msg,
            **extra,
        }
        safe = redact(event)
        with open(self.events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(safe, ensure_ascii=False) + "\n")
        self._recent_events.append(safe)
        if len(self._recent_events) > self._MAX_RECENT:
            self._recent_events = self._recent_events[-self._MAX_RECENT:]
        self.event_bus.append(safe)
        self._event_count += 1

    def event(self, level: str, component: str, msg: str, **kw: Any) -> None:
        """Olay akışına yapılandırılmış olay bırakır (planner/observer kullanır)."""
        self._emit(level, component, msg, **kw)

    def debug(self, *args: Any, **kw: Any): self._emit("DEBUG", *args, **kw)
    def info(self, *args: Any, **kw: Any): self._emit("INFO", *args, **kw)
    def success(self, *args: Any, **kw: Any): self._emit("SUCCESS", *args, **kw)
    def warning(self, *args: Any, **kw: Any): self._emit("WARNING", *args, **kw)
    def error(self, *args: Any, **kw: Any): self._emit("ERROR", *args, **kw)
    def critical(self, *args: Any, **kw: Any): self._emit("CRITICAL", *args, **kw)

    # ------------------------------------------------------------------
    def recent(self, limit: int = 50) -> list[dict]:
        return self._recent_events[-limit:]

    def event_count(self) -> int:
        return self._event_count

    def status(self) -> dict:
        return {"events": self._event_count, "level": self.level_name}
