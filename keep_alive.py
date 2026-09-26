"""Videl Keep-Alive (Werkzeug) + Watchdog"""
from __future__ import annotations
import logging, threading, time, os
from datetime import datetime, timezone

log = logging.getLogger("videl.keepalive")
_last = time.time()
_start = time.time()

def beat():
    global _last
    _last = time.time()

def start(host=None, port=None):
    host = host or os.getenv("KEEP_ALIVE_HOST", "0.0.0.0")
    port = int(port or os.getenv("KEEP_ALIVE_PORT", "8080") or 8080)
    try:
        from flask import Flask, jsonify
        from werkzeug.serving import make_server
    except ImportError:
        log.warning("Flask not installed – keep-alive disabled. pip install Flask Werkzeug")
        return
    app = Flask("videl")
    @app.get("/")
    def root():
        age = int(time.time() - _last)
        return jsonify(bot="Videl", status="alive" if age < 120 else "stale",
                       uptime=int(time.time()-_start), heartbeat_age=age,
                       time=datetime.now(timezone.utc).isoformat())
    @app.get("/health")
    def health():
        return (jsonify(ok=True), 200) if time.time()-_last < 120 else (jsonify(ok=False), 503)
    srv = make_server(host, port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, name="keepalive", daemon=True).start()
    log.info("Keep-alive on %s:%s", host, port)

def watchdog_loop(interval=30, timeout=180):
    while True:
        time.sleep(interval)
        age = time.time() - _last
        if age > timeout:
            log.warning("Watchdog: heartbeat %.0fs old", age)
