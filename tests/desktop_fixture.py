"""Disposable engine for UI integration tests. Never touches the user's Ken."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desktop.runtime import initialize
from desktop.services import DesktopServices
from engine import Engine
from web import WebApp
from test_engine import FakeBrain

async def main():
    home = Path(sys.argv[1])
    initialize(home)
    (home / 'jobs.json').write_text('[]')
    engine = Engine(home, FakeBrain())
    engine.configure_home(home / 'work', lambda: 'You are Ken')
    engine.start()
    services = DesktopServices(engine)
    server = WebApp(engine, home, services=services, transcribe=lambda path: "I run a small agency.")
    await server.start('127.0.0.1', 0)
    print(json.dumps({'port': server.runner.addresses[0][1]}), flush=True)
    await asyncio.Event().wait()

asyncio.run(main())
