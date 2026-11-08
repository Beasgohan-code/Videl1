"""Local-only real aiohttp app with mock Mongo and synthetic bot identity.

Never bind this fixture publicly or use production credentials or data.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.harness import load_all, no_botapi, reset_db, run

load_all()
run(reset_db())
no_botapi()
from aiohttp import web
import keep_alive

if __name__ == '__main__':
    web.run_app(keep_alive.make_app(), host='127.0.0.1', port=8088, access_log=None)
