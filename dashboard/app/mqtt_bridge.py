from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .config import Settings
from .store import Store

log = logging.getLogger("mqtt")


class MqttBridge:
    """Subscribe to the relay topics, keep in-memory state, publish commands."""

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

        self._device: dict[str, Any] = {
            "online": False,
            "ip": None,
            "mac": None,
            "uptime_s": None,
            "last_seen": None,
        }
        self._relays: dict[int, dict[str, Any]] = {
            i: {"state": False, "updated_at": None}
            for i in range(1, settings.relay_count + 1)
        }

        prefix = settings.topic_prefix
        self._set_topics = {
            f"{prefix}/relay/{i}/set": i for i in self._relays
        }
        self._state_topics = {
            f"{prefix}/relay/{i}/state": i for i in self._relays
        }
        self._status_topic = f"{prefix}/device/status"
        self._heartbeat_topic = f"{prefix}/device/heartbeat"

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

        prefix = self.settings.topic_prefix
        client.subscribe(f"{prefix}/relay/+/state", qos=1)
        client.subscribe(self._status_topic, qos=1)
        client.subscribe(self._heartbeat_topic, qos=1)
        log.info("MQTT terhubung, subscribe topic %s/*", prefix)
        self.emit({"type": "broker", "connected": True})

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        log.warning("MQTT terputus: %s", reason_code)
        self.emit({"type": "broker", "connected": False})

    def _on_message(self, client, userdata, msg) -> None:
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="replace").strip()

        if topic in self._state_topics:
            self._handle_relay_state(self._state_topics[topic], payload)
        elif topic == self._status_topic:
            self._handle_device_status(payload)
        elif topic == self._heartbeat_topic:
            self._handle_heartbeat(payload)

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------
    def _handle_relay_state(self, relay: int, payload: str) -> None:
        state = payload.upper() in ("ON", "1", "TRUE")
        now = int(time.time())

        with self._lock:
            was = self._relays[relay]["state"]
            known = self._relays[relay]["updated_at"] is not None
            self._relays[relay] = {"state": state, "updated_at": now}

        # Retained message dikirim ulang tiap dashboard reconnect.
        # Hanya catat ke histori jika benar-benar berubah.
        if known and was == state:
            return

        event = self.store.record(relay, state, now)
        log.info("Relay %d -> %s", relay, "ON" if state else "OFF")
        self.emit({"type": "relay", "event": event})

    def _handle_device_status(self, payload: str) -> None:
        online = payload.upper() == "ONLINE"
        with self._lock:
            self._device["online"] = online
            if online:
                self._device["last_seen"] = int(time.time())
            device = dict(self._device)

        self.emit({"type": "device", "device": device})

    def _handle_heartbeat(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            log.warning("Heartbeat bukan JSON valid: %s", payload)
            return

        with self._lock:
            self._device["online"] = True
            self._device["last_seen"] = int(time.time())
            self._device["ip"] = data.get("ip", self._device["ip"])
            self._device["mac"] = data.get("mac", self._device["mac"])
            self._device["uptime_s"] = data.get("uptime_s")
            device = dict(self._device)

        self.emit({"type": "device", "device": device})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            relays = [
                {"id": rid, **info}
                for rid, info in sorted(self._relays.items())
            ]
            device = dict(self._device)

        return {
            "type": "snapshot",
            "relays": relays,
            "device": device,
            "broker_connected": self._client.is_connected(),
            "relay_count": self.settings.relay_count,
        }

    def relay_state(self, relay: int) -> bool | None:
        with self._lock:
            info = self._relays.get(relay)
            return None if info is None else bool(info["state"])

    def set_relay(self, relay: int, state: bool) -> bool:
        topic = f"{self.settings.topic_prefix}/relay/{relay}/set"
        if topic not in self._set_topics:
            return False
        info = self._client.publish(
            topic, "ON" if state else "OFF", qos=0, retain=False
        )
        return info.rc == mqtt.MQTT_ERR_SUCCESS
