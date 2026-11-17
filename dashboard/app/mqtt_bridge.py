from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .config import Settings
from .store import Store

log = logging.getLogger("mqtt")

DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,23}$")

# Topic format lama (firmware versi satu-device) memakai segmen ini di posisi
# device id; abaikan agar tidak muncul sebagai device palsu.
LEGACY_SEGMENTS = {"device", "relay"}


def _new_device(device_id: str) -> dict[str, Any]:
    return {
        "id": device_id,
        "online": False,
        "ip": None,
        "mac": None,
        "uptime_s": None,
        "last_seen": None,
        "relays": {},  # relay id -> {"state": bool, "updated_at": int}
    }


class MqttBridge:
    """Auto-discover WT32 devices lewat MQTT, simpan state, kirim perintah.

    Topic: {prefix}/{device}/status | heartbeat | relay/{n}/state | relay/{n}/set
    """

    def __init__(
        self,
        settings: Settings,
        store: Store,
        emit: Callable[[dict[str, Any]], None],
    ) -> None:
        self.settings = settings
        self.store = store
        self.emit = emit
        self._lock = threading.Lock()
        self._devices: dict[str, dict[str, Any]] = {}
        self._prefix = settings.topic_prefix

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{settings.mqtt_client_id}-{uuid.uuid4().hex[:6]}",
            protocol=mqtt.MQTTv311,
        )
        if settings.mqtt_username:
            self._client.username_pw_set(
                settings.mqtt_username, settings.mqtt_password
            )
        self._client.reconnect_delay_set(min_delay=1, max_delay=15)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        log.info(
            "Menghubungkan MQTT ke %s:%s",
            self.settings.mqtt_host,
            self.settings.mqtt_port,
        )
        self._client.connect_async(
            self.settings.mqtt_host, self.settings.mqtt_port, keepalive=30
        )
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    # ------------------------------------------------------------------
    # Paho callbacks (dipanggil dari thread MQTT)
    # ------------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if getattr(reason_code, "is_failure", False):
            log.error("MQTT connect gagal: %s", reason_code)
            return

        p = self._prefix
        client.subscribe(f"{p}/+/status", qos=1)
        client.subscribe(f"{p}/+/heartbeat", qos=1)
        client.subscribe(f"{p}/+/relay/+/state", qos=1)
        log.info("MQTT terhubung, subscribe %s/+/*", p)
        self.emit({"type": "broker", "connected": True})

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        log.warning("MQTT terputus: %s", reason_code)
        self.emit({"type": "broker", "connected": False})

    def _on_message(self, client, userdata, msg) -> None:
        parts = msg.topic.split("/")
        base = self._prefix.split("/")
        if parts[: len(base)] != base:
            return
        rest = parts[len(base):]
        if len(rest) < 2:
            return

        device_id = rest[0]
        if device_id in LEGACY_SEGMENTS or not DEVICE_ID_RE.match(device_id):
            return

        payload = msg.payload.decode("utf-8", errors="replace").strip()
        kind = rest[1:]

        if kind == ["status"]:
            self._handle_status(device_id, payload)
        elif kind == ["heartbeat"]:
            self._handle_heartbeat(device_id, payload)
        elif len(kind) == 3 and kind[0] == "relay" and kind[2] == "state":
            if kind[1].isdigit():
                self._handle_relay_state(device_id, int(kind[1]), payload)

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------
    def _device(self, device_id: str) -> dict[str, Any]:
        """Ambil/buat device. Panggil hanya saat memegang self._lock."""
        device = self._devices.get(device_id)
        if device is None:
            device = self._devices[device_id] = _new_device(device_id)
            log.info("Device baru terdeteksi: %s", device_id)
        return device

    @staticmethod
    def _public(device: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in device.items() if k != "relays"}
        out["relays"] = [
            {"id": rid, **info} for rid, info in sorted(device["relays"].items())
        ]
        return out

    def _handle_relay_state(self, device_id: str, relay: int, payload: str) -> None:
        if relay < 1 or relay > self.settings.relay_count:
            return

        state = payload.upper() in ("ON", "1", "TRUE")
        now = int(time.time())

        with self._lock:
            device = self._device(device_id)
            previous = device["relays"].get(relay)
            device["relays"][relay] = {"state": state, "updated_at": now}

        # Retained message dikirim ulang tiap reconnect; catat hanya jika berubah.
        if previous is not None and previous["state"] == state:
            return

        event = self.store.record(device_id, relay, state, now)
        log.info("%s relay %d -> %s", device_id, relay, "ON" if state else "OFF")
        self.emit({"type": "relay", "device": device_id, "event": event})

    def _handle_status(self, device_id: str, payload: str) -> None:
        online = payload.upper() == "ONLINE"
        with self._lock:
            device = self._device(device_id)
            device["online"] = online
            if online:
                device["last_seen"] = int(time.time())
            public = self._public(device)

        self.emit({"type": "device", "device": public})

    def _handle_heartbeat(self, device_id: str, payload: str) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            log.warning("Heartbeat %s bukan JSON valid: %s", device_id, payload)
            return

        with self._lock:
            device = self._device(device_id)
            device["online"] = True
            device["last_seen"] = int(time.time())
            device["ip"] = data.get("ip", device["ip"])
            device["mac"] = data.get("mac", device["mac"])
            device["uptime_s"] = data.get("uptime_s")
            public = self._public(device)

        self.emit({"type": "device", "device": public})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            devices = [self._public(d) for _, d in sorted(self._devices.items())]

        return {
            "type": "snapshot",
            "devices": devices,
            "broker_connected": self._client.is_connected(),
            "relay_count": self.settings.relay_count,
        }

    def device_exists(self, device_id: str) -> bool:
        with self._lock:
            return device_id in self._devices

    def relay_state(self, device_id: str, relay: int) -> bool | None:
        with self._lock:
            device = self._devices.get(device_id)
            info = device["relays"].get(relay) if device else None
            return None if info is None else bool(info["state"])

    def set_relay(self, device_id: str, relay: int, state: bool) -> bool:
        if not DEVICE_ID_RE.match(device_id):
            return False
        topic = f"{self._prefix}/{device_id}/relay/{relay}/set"
        info = self._client.publish(
            topic, "ON" if state else "OFF", qos=0, retain=False
        )
        return info.rc == mqtt.MQTT_ERR_SUCCESS
