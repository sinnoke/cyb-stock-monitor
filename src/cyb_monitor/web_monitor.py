from __future__ import annotations

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import parse_qs, urlparse

from .status import read_status


def run_web_monitor(host: str, port: int, db_path: Path, status_dir: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(render_index())
                return
            if parsed.path == "/api/status":
                self._send_json(build_status(db_path, status_dir))
                return
            if parsed.path == "/api/recent-bars":
                query = parse_qs(parsed.query)
                limit = int(query.get("limit", ["20"])[0])
                self._send_json({"rows": load_recent_bars(db_path, limit)})
                return
            self.send_error(404)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_html(self, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"web monitor running at http://{host}:{port}", flush=True)
    server.serve_forever()


def build_status(db_path: Path, status_dir: Path) -> dict[str, Any]:
    return {
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db": load_db_stats(db_path),
        "processes": {
            "realtime": read_status(status_dir / "realtime.json"),
            "storage": read_status(status_dir / "storage.json"),
            "cache_analysis": read_status(status_dir / "cache_analysis.json"),
            "alerts": read_status(status_dir / "alerts.json"),
        },
    }


def load_db_stats(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"state": "missing", "path": str(db_path)}
    try:
        with sqlite3.connect(db_path) as conn:
            row_count = conn.execute("SELECT COUNT(*) FROM minute_bars").fetchone()[0]
            code_count = conn.execute("SELECT COUNT(DISTINCT code) FROM minute_bars").fetchone()[0]
            latest_time = conn.execute("SELECT MAX(time) FROM minute_bars").fetchone()[0]
            earliest_time = conn.execute("SELECT MIN(time) FROM minute_bars").fetchone()[0]
            latest_updated = conn.execute("SELECT MAX(updated_at) FROM minute_bars").fetchone()[0]
        return {
            "state": "ok",
            "path": str(db_path),
            "row_count": row_count,
            "code_count": code_count,
            "earliest_time": earliest_time,
            "latest_time": latest_time,
            "latest_updated_at": latest_updated,
        }
    except sqlite3.Error as exc:
        return {"state": "error", "path": str(db_path), "error": str(exc)}


def load_recent_bars(db_path: Path, limit: int) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    limit = max(1, min(limit, 200))
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT code, name, time, open, high, low, close, volume, amount, updated_at
            FROM minute_bars
            ORDER BY time DESC, updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "code": row[0],
            "name": row[1],
            "time": row[2],
            "open": row[3],
            "high": row[4],
            "low": row[5],
            "close": row[6],
            "volume": row[7],
            "amount": row[8],
            "updated_at": row[9],
        }
        for row in rows
    ]


def render_index() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CYB Stock Monitor</title>
  <style>
    :root { color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f6f7f9; color: #1f2933; }
    header { background: #12263a; color: white; padding: 18px 28px; }
    h1 { margin: 0; font-size: 22px; font-weight: 650; }
    main { padding: 22px 28px 32px; max-width: 1280px; margin: 0 auto; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
    .panel { background: white; border: 1px solid #d9e2ec; border-radius: 8px; padding: 14px; }
    .panel h2 { margin: 0 0 12px; font-size: 15px; }
    .metric { font-size: 26px; font-weight: 700; margin: 4px 0; }
    .muted { color: #627d98; font-size: 12px; }
    .status { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 650; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: #bcccdc; }
    .ok .dot { background: #2f9e44; }
    .warn .dot { background: #f08c00; }
    .bad .dot { background: #c92a2a; }
    pre { white-space: pre-wrap; word-break: break-word; font-size: 12px; background: #f8fafc; padding: 10px; border-radius: 6px; max-height: 260px; overflow: auto; }
    table { width: 100%; border-collapse: collapse; background: white; border: 1px solid #d9e2ec; border-radius: 8px; overflow: hidden; }
    th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid #edf2f7; font-size: 13px; }
    th { background: #edf2f7; color: #334e68; }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } main { padding: 16px; } }
  </style>
</head>
<body>
  <header><h1>CYB Stock Monitor</h1><div class="muted" id="clock"></div></header>
  <main>
    <section class="grid">
      <div class="panel"><h2>数据库股票数</h2><div class="metric" id="codeCount">-</div><div class="muted" id="dbPath"></div></div>
      <div class="panel"><h2>分钟K线行数</h2><div class="metric" id="rowCount">-</div><div class="muted" id="timeRange"></div></div>
      <div class="panel"><h2>订阅进程</h2><div id="realtimeStatus"></div><pre id="realtimeJson"></pre></div>
      <div class="panel"><h2>报警进程</h2><div id="alertStatus"></div><pre id="alertJson"></pre></div>
    </section>
    <section class="grid" style="grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 14px;">
      <div class="panel"><h2>存储状态</h2><div id="storageStatus"></div><pre id="storageJson"></pre></div>
      <div class="panel"><h2>缓存分析状态</h2><div id="cacheStatus"></div><pre id="cacheJson"></pre></div>
    </section>
    <section style="margin-top: 14px;">
      <h2 style="font-size: 16px;">最近写入的分钟K线</h2>
      <table><thead><tr><th>代码</th><th>名称</th><th>时间</th><th>开</th><th>高</th><th>低</th><th>收</th><th>量</th><th>更新时间</th></tr></thead><tbody id="recentRows"></tbody></table>
    </section>
  </main>
  <script>
    function cls(state) {
      if (state === 'running' || state === 'ok' || state === 'initialized') return 'status ok';
      if (state === 'missing' || state === 'starting') return 'status warn';
      return 'status bad';
    }
    function statusHtml(obj) {
      const state = obj && obj.state ? obj.state : 'unknown';
      return `<span class="${cls(state)}"><span class="dot"></span>${state}</span>`;
    }
    async function refresh() {
      const res = await fetch('/api/status');
      const data = await res.json();
      document.getElementById('clock').textContent = `刷新时间 ${data.now}`;
      const db = data.db || {};
      document.getElementById('codeCount').textContent = db.code_count ?? '-';
      document.getElementById('rowCount').textContent = db.row_count ?? '-';
      document.getElementById('dbPath').textContent = db.path || '';
      document.getElementById('timeRange').textContent = `${db.earliest_time || '-'} 到 ${db.latest_time || '-'}`;
      const p = data.processes || {};
      for (const [key, statusId, jsonId] of [
        ['realtime', 'realtimeStatus', 'realtimeJson'],
        ['alerts', 'alertStatus', 'alertJson'],
        ['storage', 'storageStatus', 'storageJson'],
        ['cache_analysis', 'cacheStatus', 'cacheJson'],
      ]) {
        document.getElementById(statusId).innerHTML = statusHtml(p[key]);
        document.getElementById(jsonId).textContent = JSON.stringify(p[key], null, 2);
      }
      const recent = await (await fetch('/api/recent-bars?limit=30')).json();
      document.getElementById('recentRows').innerHTML = (recent.rows || []).map(row => `
        <tr><td>${row.code}</td><td>${row.name}</td><td>${row.time}</td><td>${row.open}</td><td>${row.high}</td><td>${row.low}</td><td>${row.close}</td><td>${row.volume}</td><td>${row.updated_at}</td></tr>
      `).join('');
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>"""
