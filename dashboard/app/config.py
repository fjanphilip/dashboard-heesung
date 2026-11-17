from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    broker_enabled: bool
    broker_bind: str
    broker_port: int
    broker_max_connections: int
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_client_id: str
    topic_prefix: str
    relay_count: int
    db_path: str
    open_browser: bool


def load_settings() -> Settings:
    _load_dotenv()

    db_path = os.getenv("DB_PATH", str(BASE_DIR / "data" / "heesung.db"))
    if not os.path.isabs(db_path):
        db_path = str(BASE_DIR / db_path)

    return Settings(
        broker_enabled=_env_bool("BROKER_ENABLED", True),
        broker_bind=os.getenv("BROKER_BIND", "0.0.0.0"),
        broker_port=int(os.getenv("BROKER_PORT", "1883")),
        broker_max_connections=int(os.getenv("BROKER_MAX_CONNECTIONS", "50")),
        mqtt_host=os.getenv("MQTT_HOST", "127.0.0.1"),
        mqtt_port=int(os.getenv("MQTT_PORT", os.getenv("BROKER_PORT", "1883"))),
        mqtt_username=os.getenv("MQTT_USERNAME", ""),
        mqtt_password=os.getenv("MQTT_PASSWORD", ""),
        mqtt_client_id=os.getenv("MQTT_CLIENT_ID", "heesung-dashboard"),
        topic_prefix=os.getenv("TOPIC_PREFIX", "heesung").strip("/"),
        relay_count=int(os.getenv("RELAY_COUNT", "4")),
        db_path=db_path,
        open_browser=_env_bool("OPEN_BROWSER", False),
    )
