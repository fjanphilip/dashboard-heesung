from __future__ import annotations

import asyncio
import logging
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .broker import EmbeddedBroker
from .config import load_settings
from .mqtt_bridge import MqttBridge
from .store import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("dashboard")

STATIC_DIR = Path(__file__).resolve().parent / "static"

settings = load_settings()
store = Store(settings.db_path)

_main_loop: asyncio.AbstractEventLoop | None = None
bridge: MqttBridge | None = None
broker: EmbeddedBroker | None = None


class Hub:
    """Broadcast state updates to semua browser yang terhubung via WebSocket."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._send_lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            dead: list[WebSocket] = []
            for ws in list(self._clients):
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)


hub = Hub()


def emit_from_mqtt(payload: dict[str, Any]) -> None:
    """Dipanggil dari thread paho; jadwalkan broadcast di event loop."""
    loop = _main_loop
    if loop is None or loop.is_closed():
        return
    asyncio.run_coroutine_threadsafe(hub.broadcast(payload), loop)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _main_loop, bridge, broker
    _main_loop = asyncio.get_running_loop()

    if settings.broker_enabled:
        broker = EmbeddedBroker(settings)
        await broker.start()

    bridge = MqttBridge(settings, store, emit_from_mqtt)
    bridge.start()
    log.info(
        "Dashboard siap di http://0.0.0.0:8000 (broker MQTT %s)",
        "bawaan aktif" if settings.broker_enabled else "eksternal",
    )

    if settings.open_browser:
        opener = threading.Timer(
            1.5, webbrowser.open, args=("http://localhost:8000",)
        )
        opener.daemon = True
        opener.start()

    try:
        yield
    finally:
        bridge.stop()
        if broker is not None:
            await broker.stop()
        store.close()


app = FastAPI(title="Heesung Relay Dashboard", lifespan=lifespan)


class RelayCommand(BaseModel):
    state: bool


def _require_bridge() -> MqttBridge:
    if bridge is None:
        raise HTTPException(status_code=503, detail="MQTT bridge belum siap")
    return bridge


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    return _require_bridge().snapshot()


@app.post("/api/relay/{relay_id}")
def set_relay(relay_id: int, command: RelayCommand) -> dict[str, Any]:
    target = _require_bridge()
    if relay_id not in range(1, settings.relay_count + 1):
        raise HTTPException(status_code=404, detail="Relay tidak ditemukan")

    if not target.set_relay(relay_id, command.state):
        raise HTTPException(status_code=503, detail="Broker MQTT tidak terhubung")

    return {"ok": True, "relay": relay_id, "requested": command.state}


@app.post("/api/relay/{relay_id}/toggle")
def toggle_relay(relay_id: int) -> dict[str, Any]:
    target = _require_bridge()
    current = target.relay_state(relay_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Relay tidak ditemukan")

    if not target.set_relay(relay_id, not current):
        raise HTTPException(status_code=503, detail="Broker MQTT tidak terhubung")

    return {"ok": True, "relay": relay_id, "requested": not current}


@app.get("/api/history")
def get_history(
    relay: int | None = None,
    hours: float = 24,
    limit: int = 500,
) -> dict[str, Any]:
    if relay is not None and relay not in range(1, settings.relay_count + 1):
        raise HTTPException(status_code=404, detail="Relay tidak ditemukan")

    limit = max(1, min(limit, 5000))
    since = int(time.time() - hours * 3600) if hours > 0 else None

    return {
        "events": store.history(relay=relay, since=since, limit=limit),
        "hours": hours,
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    target = _require_bridge()
    await hub.connect(ws)

    try:
        await ws.send_json(target.snapshot())
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.disconnect(ws)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "broker_connected": bool(bridge and bridge.snapshot()["broker_connected"]),
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
