# -*- coding: utf-8 -*-
"""
observer/dashboard.py — Gerçek zamanlı kontrol paneli
-----------------------------------------------------
İki yüz:
1) Terminal panosu — her N saniyede ekranı temizleyip canlı özet basar
   (kod bulunamadıysa bayrakla tek satır, kod bulunduysa tam pano).
2) Web panosu — stdlib http.server ile, harici bağımlılık YOK:
   http://0.0.0.0:8899  (canlı önizlemede otomatik görünür)

Web panosu JSON API + otomatik yenilenen minimal HTML sunar.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

from utils.helpers import now_iso

_PAGE = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8">
<title>RedPacketBot — Panel</title>
<style>
 body{font-family:ui-monospace,Menlo,Consolas,monospace;background:#0d1117;color:#e6edf3;margin:0;padding:24px}
 h1{font-size:18px;color:#58a6ff}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
 .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px}
 .card h2{font-size:12px;color:#8b949e;margin:0 0 8px;text-transform:uppercase;letter-spacing:.5px}
 .big{font-size:26px;font-weight:700}
 .ok{color:#3fb950}.warn{color:#d29922}.bad{color:#f85149}.info{color:#58a6ff}
 table{width:100%;border-collapse:collapse;font-size:12px}
 td,th{padding:4px 8px;border-bottom:1px solid #21262d;text-align:left}
 .ev{font-size:12px;margin:2px 0}
 .bar{height:8px;background:#21262d;border-radius:4px;overflow:hidden;margin-top:6px}
 .bar>div{height:100%;background:#3fb950}
</style></head><body>
<h1>🟥 RedPacketBot — Otonom Avcı Paneli</h1>
<div class="grid" id="cards"></div>
<h1>Olay Akışı (son 60)</h1>
<div id="events"></div>
<script>
function lvl(l){return l==='SUCCESS'?'ok':(l==='WARNING'?'warn':(l==='CRITICAL'||l==='ERROR'?'bad':'info'));}
async function load(){
 try{
  const d=await fetch('/api/status').then(r=>r.json());
  const s=d.system, q=d.queue, sess=d.session, mem=d.memory, dd=d.delay, df=d.defense||{};
  const cards=[
   ['Durum',s.mode==='simulation'?'SİMÜLASYON':'CANLI', s.mode==='simulation'?'warn':'ok'],
   ['Plan turları',s.plans_executed,'info'],
   ['Başarı oranı',mem.success_rate+'%',mem.success_rate>20?'ok':'warn'],
   ['Kuyruk (bekleyen/toplam)',q.pending+' / '+q.total,'info'],
   ['Oturum',sess.state+' — '+Math.round(sess.remaining_seconds/60)+'dk kaldı',
      sess.state==='healthy'?'ok':'warn'],
   ['🛡️ CAN',df.lives_left+' / '+df.max_lives,
      df.emergency_stop?'bad':(df.lives_left<=df.reserve_lives+1?'warn':'ok')],
   ['Gecikme (akıllı)',dd.current_delay+'s',dd.emergency_stop?'bad':'info'],
   ['Yenileme sayısı',sess.renewal_count,'info'],
   ['Rate-limit vuruşu',dd.rate_limit_strikes,dd.rate_limit_strikes>2?'bad':'info'],
   ['Kaynaklar',Object.keys(s.sources||{}).join(', ')||'-','info'],
  ];
  document.getElementById('cards').innerHTML=cards.map(([t,v,c])=>
   `<div class="card"><h2>${t}</h2><div class="${c}">${v}</div></div>`).join('');
  const evs=d.events||[];
  document.getElementById('events').innerHTML=evs.map(e=>
   `<div class="ev ${lvl(e.level)}">[${e.ts.slice(11,19)}] ${e.level} ${e.component}: ${e.message}</div>`).join('');
 }catch(e){document.getElementById('events').textContent='Panel verisi alınamadı: '+e;}
}
load();setInterval(load,3000);
</script></body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    # NOT: sınıf niteliğine atanan bu fonksiyona do_GET içinde ÖRNEK üzerinden
    # değil SINIF üzerinden erişilir — yoksa method binding self'i ilk argüman
    # yapar ve imza bozulur.
    status_fn: Callable[[], dict] = staticmethod(lambda: {})

    def log_message(self, *a):  # istek gürültüsünü bastır
        pass

    def do_GET(self):  # noqa: N802
        try:
            if self.path.startswith("/api/status"):
                body = json.dumps(_Handler.status_fn(), ensure_ascii=False).encode()
                ctype = "application/json; charset=utf-8"
            else:
                body = _PAGE.encode()
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:  # sessiz boş yanıt yerine 500 + hata
            try:
                body = json.dumps({"error": str(e)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                pass


class Dashboard:
    def __init__(self, cfg: dict, logger: Any, clock: Any):
        self.cfg = cfg
        self.log = logger
        self.clock = clock
        o = cfg.get("observer", {})
        self.terminal_enabled = bool(o.get("terminal_dashboard", True))
        self.web_enabled = bool(o.get("web_dashboard", True))
        self.host = o.get("web_host", "0.0.0.0")
        self.port = int(o.get("web_port", 8899))
        self.status_fn: Optional[Callable[[], dict]] = None
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._last_terminal: dict = {}

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.web_enabled:
            try:
                _Handler.status_fn = lambda: (self.status_fn or (lambda: {}))()
                self._server = ThreadingHTTPServer((self.host, self.port), _Handler)
                self._thread = threading.Thread(target=self._server.serve_forever,
                                                daemon=True)
                self._thread.start()
                self.log.info("dashboard", f"Web paneli: http://{self.host}:{self.port}")
            except Exception as e:
                self.log.warning("dashboard", f"Web paneli başlatılamadı: {e}")

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    # ------------------------------------------------------------------
    def render_terminal(self, status: dict) -> None:
        if not self.terminal_enabled:
            return
        s = status.get("system", {})
        q = status.get("queue", {})
        mem = status.get("memory", {})
        sess = status.get("session", {})
        dd = status.get("delay", {})
        df = status.get("defense", {})
        new_codes = s.get("new_codes_this_tick", 0)
        if new_codes:
            os.system("clear")
            print("=" * 62)
            print("  RedPacketBot — OTONOM AVI (canlı pano)   saat: " + now_iso()[11:19])
            print("=" * 62)
            print(f"  Mod        : {s.get('mode','?').upper()}")
            print(f"  Plan turu  : {s.get('plans_executed',0)}   "
                  f"Yeni kod (bu tur): {new_codes}")
            print(f"  Başarı     : %{mem.get('success_rate',0):.1f}   "
                  f"Oturum: {sess.get('state','?')} ({sess.get('remaining_seconds',0)//60}dk kaldı)")
            print(f"  🛡️ Can      : {df.get('lives_left','?')}/{df.get('max_lives','?')}   "
                  f"Gecikme: {dd.get('current_delay','?')}s   "
                  f"RL vuruşu: {dd.get('rate_limit_strikes',0)}   "
                  f"Kuyruk: {q.get('pending',0)}/{q.get('total',0)}")
            print("=" * 62)
        else:
            print(f"⏳ [{now_iso()[11:19]}] tur {s.get('plans_executed',0)} — bekleniyor… "
                  f"(kuyruk {q.get('pending',0)}/{q.get('total',0)}, "
                  f"oturum {sess.get('state','?')}, "
                  f"başarı %{mem.get('success_rate',0):.1f}, "
                  f"can {df.get('lives_left','?')}/{df.get('max_lives','?')})", flush=True)

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {"terminal": self.terminal_enabled, "web": self.web_enabled,
                "url": f"http://{self.host}:{self.port}" if self.web_enabled else None}
