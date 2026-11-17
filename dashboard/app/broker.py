"""Broker MQTT 3.1.1 minimal, murni Python (tanpa dependensi eksternal).

Mendukung:
- CONNECT / CONNACK, PING, DISCONNECT
- PUBLISH QoS 0, 1, 2 (inbound) dan QoS 0/1 (outbound)
- SUBSCRIBE / UNSUBSCRIBE dengan wildcard `+` dan `#`
- Pesan retained
- Last Will and Testament (LWT)
- Keepalive timeout
- Takeover client id yang sama

Dijalankan di event loop yang sama dengan dashboard (satu proses).
"""

from __future__ import annotations

import asyncio
import logging

from .config import Settings

log = logging.getLogger("broker")

MAX_PACKET_SIZE = 256 * 1024
PROTOCOL_NAME = b"MQTT"


# --------------------------------------------------------------------
# Packet helpers
# --------------------------------------------------------------------
def _encode_length(length: int) -> bytes:
    out = bytearray()
    while True:
        digit = length % 128
        length //= 128
        if length > 0:
            digit |= 0x80
        out.append(digit)
        if length == 0:
            return bytes(out)


def _packet(header: int, body: bytes = b"") -> bytes:
    return bytes([header]) + _encode_length(len(body)) + body


def _utf8(value: str) -> bytes:
    data = value.encode("utf-8")
    return len(data).to_bytes(2, "big") + data


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    header = (await reader.readexactly(1))[0]

    multiplier = 1
    length = 0
    for _ in range(4):
        byte = (await reader.readexactly(1))[0]
        length += (byte & 0x7F) * multiplier
        if not (byte & 0x80):
            break
        multiplier *= 128
    else:
        raise ValueError("remaining length tidak valid")

    if length > MAX_PACKET_SIZE:
        raise ValueError("paket MQTT terlalu besar")

    body = await reader.readexactly(length) if length else b""
    return header, body


def _topic_matches(topic_filter: str, topic: str) -> bool:
    filter_parts = topic_filter.split("/")
    topic_parts = topic.split("/")

    for index, part in enumerate(filter_parts):
        if part == "#":
            return True
        if index >= len(topic_parts):
            return False
        if part == "+":
            continue
        if part != topic_parts[index]:
            return False

    return len(filter_parts) == len(topic_parts)


class _Cursor:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def u8(self) -> int:
        if self.remaining < 1:
            raise ValueError("paket terpotong")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        if self.remaining < 2:
            raise ValueError("paket terpotong")
        value = int.from_bytes(self.data[self.pos : self.pos + 2], "big")
        self.pos += 2
        return value

    def field(self) -> bytes:
        length = self.u16()
        if self.remaining < length:
            raise ValueError("field terpotong")
        value = self.data[self.pos : self.pos + length]
        self.pos += length
        return value

    def string(self) -> str:
        return self.field().decode("utf-8", "replace")

    def rest(self) -> bytes:
        value = self.data[self.pos :]
        self.pos = len(self.data)
        return value


def _parse_connect(
    body: bytes,
) -> tuple[str, int, tuple[str, bytes, bool] | None]:
    cursor = _Cursor(body)
    protocol = cursor.field()
    level = cursor.u8()

    if protocol != PROTOCOL_NAME or level not in (3, 4):
        raise ValueError("protokol MQTT tidak didukung")

    flags = cursor.u8()
    keepalive = cursor.u16()
    client_id = cursor.string()

    will: tuple[str, bytes, bool] | None = None
    if flags & 0x04:
        will_topic = cursor.string()
        will_payload = cursor.field()
        will = (will_topic, will_payload, bool(flags & 0x20))

    if flags & 0x80:
        cursor.field()
    if flags & 0x40:
        cursor.field()

    return client_id, keepalive, will


class _Session:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.client_id = ""
        self.keepalive = 0
        self.will: tuple[str, bytes, bool] | None = None
        self.subscriptions: dict[str, int] = {}
        self.packet_id = 0
        self.closed = False
        self.will_done = False

    async def send(self, data: bytes) -> None:
        if self.closed:
            return
        self.writer.write(data)
        try:
            await self.writer.drain()
        except (ConnectionError, RuntimeError):
            self.closed = True


class EmbeddedBroker:
    """Broker MQTT yang menyatu dengan aplikasi dashboard."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._server: asyncio.Server | None = None
        self._sessions: dict[str, _Session] = {}
        self._retained: dict[str, bytes] = {}
        self._stopping = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        try:
            self._server = await asyncio.start_server(
                self._handle_client,
                self.settings.broker_bind,
                self.settings.broker_port,
            )
        except OSError as exc:
            log.error(
                "Gagal membuka broker di %s:%s (%s). "
                "Port mungkin dipakai proses lain.",
                self.settings.broker_bind,
                self.settings.broker_port,
                exc,
            )
            raise
        log.info(
            "Broker MQTT aktif di %s:%s (siap menerima WT32)",
            self.settings.broker_bind,
            self.settings.broker_port,
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        for session in list(self._sessions.values()):
            await self._close_session(session)
        self._sessions.clear()
        log.info("Broker MQTT berhenti")

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------
    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        session = _Session(writer)
        peer = writer.get_extra_info("peername")
        graceful = False
        registered = False

        try:
            header, body = await asyncio.wait_for(_read_packet(reader), timeout=30)
            if header >> 4 != 1:
                return

            try:
                client_id, keepalive, will = _parse_connect(body)
            except ValueError:
                await session.send(_packet(0x20, b"\x00\x01"))
                return

            if not client_id:
                client_id = f"anon-{id(session):x}"

            if (
                len(self._sessions) >= self.settings.broker_max_connections
                and client_id not in self._sessions
            ):
                log.warning("Koneksi MQTT ditolak (penuh): %s", client_id)
                await session.send(_packet(0x20, b"\x00\x03"))
                return

            old = self._sessions.pop(client_id, None)
            if old is not None and old is not session:
                log.info("Client id %s terhubung ulang, sesi lama diputus", client_id)
                await self._close_session(old)
                if old.will and not old.will_done and not self._stopping:
                    old.will_done = True
                    await self._publish(*old.will)

            session.client_id = client_id
            session.keepalive = keepalive
            session.will = will
            self._sessions[client_id] = session
            registered = True

            await session.send(_packet(0x20, b"\x00\x00"))
            log.info("Klien MQTT terhubung: %s dari %s", client_id, peer)

            while not session.closed:
                timeout = session.keepalive * 1.5 if session.keepalive else None
                header, body = await asyncio.wait_for(_read_packet(reader), timeout)

                if header >> 4 == 14:  # DISCONNECT
                    graceful = True
                    break

                await self._handle_packet(session, header, body)

        except asyncio.TimeoutError:
            log.warning("Klien MQTT timeout (keepalive): %s", session.client_id or peer)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError) as exc:
            log.debug("Koneksi MQTT berakhir (%s): %s", peer, exc)
        finally:
            if registered:
                if self._sessions.get(session.client_id) is session:
                    self._sessions.pop(session.client_id, None)
                if (
                    session.will
                    and not graceful
                    and not session.will_done
                    and not self._stopping
                ):
                    session.will_done = True
                    await self._publish(*session.will)
                log.info("Klien MQTT terputus: %s", session.client_id)

            await self._close_session(session)

    async def _close_session(self, session: _Session) -> None:
        session.closed = True
        try:
            session.writer.close()
            await session.writer.wait_closed()
        except (ConnectionError, RuntimeError, OSError):
            pass

    # ------------------------------------------------------------------
    # Packet handlers
    # ------------------------------------------------------------------
    async def _handle_packet(self, session: _Session, header: int, body: bytes) -> None:
        packet_type = header >> 4

        if packet_type == 3:  # PUBLISH
            await self._handle_publish(session, header, body)
        elif packet_type == 8:  # SUBSCRIBE
            await self._handle_subscribe(session, body)
        elif packet_type == 10:  # UNSUBSCRIBE
            await self._handle_unsubscribe(session, body)
        elif packet_type == 12:  # PINGREQ
            await session.send(_packet(0xD0))
        elif packet_type == 6:  # PUBREL (QoS 2)
            packet_id = _Cursor(body).u16()
            await session.send(_packet(0x70, packet_id.to_bytes(2, "big")))
        elif packet_type in (4, 5, 7):  # PUBACK / PUBREC / PUBCOMP
            pass
        else:
            log.debug("Paket MQTT tipe %s diabaikan", packet_type)

    async def _handle_publish(self, session: _Session, header: int, body: bytes) -> None:
        qos = (header >> 1) & 0x03
        retain = bool(header & 0x01)

        cursor = _Cursor(body)
        topic = cursor.string()
        packet_id = cursor.u16() if qos > 0 else None
        payload = cursor.rest()

        if qos == 1 and packet_id is not None:
            await session.send(_packet(0x40, packet_id.to_bytes(2, "big")))
        elif qos == 2 and packet_id is not None:
            await session.send(_packet(0x50, packet_id.to_bytes(2, "big")))

        if topic:
            await self._publish(topic, payload, retain)

    async def _handle_subscribe(self, session: _Session, body: bytes) -> None:
        cursor = _Cursor(body)
        packet_id = cursor.u16()

        requested: list[tuple[str, int]] = []
        while cursor.remaining >= 2:
            topic_filter = cursor.string()
            qos = min(cursor.u8() & 0x03, 1)
            requested.append((topic_filter, qos))

        granted = bytearray()
        for topic_filter, qos in requested:
            session.subscriptions[topic_filter] = qos
            granted.append(qos)

        await session.send(_packet(0x90, packet_id.to_bytes(2, "big") + bytes(granted)))

        for topic_filter, qos in requested:
            for topic, payload in list(self._retained.items()):
                if _topic_matches(topic_filter, topic):
                    await self._deliver(session, topic, payload, qos, retain=True)

    async def _handle_unsubscribe(self, session: _Session, body: bytes) -> None:
        cursor = _Cursor(body)
        packet_id = cursor.u16()

        while cursor.remaining >= 2:
            session.subscriptions.pop(cursor.string(), None)

        await session.send(_packet(0xB0, packet_id.to_bytes(2, "big")))

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------
    async def _publish(self, topic: str, payload: bytes, retain: bool) -> None:
        if retain:
            if payload:
                self._retained[topic] = payload
            else:
                self._retained.pop(topic, None)

        for session in list(self._sessions.values()):
            if session.closed:
                continue

            matched_qos: int | None = None
            for topic_filter, qos in session.subscriptions.items():
                if _topic_matches(topic_filter, topic):
                    matched_qos = qos if matched_qos is None else max(matched_qos, qos)

            if matched_qos is None:
                continue

            try:
                await self._deliver(session, topic, payload, matched_qos)
            except (ConnectionError, RuntimeError, OSError):
                session.closed = True

    async def _deliver(
        self,
        session: _Session,
        topic: str,
        payload: bytes,
        qos: int,
        retain: bool = False,
    ) -> None:
        header = 0x30 | ((qos & 0x03) << 1) | (0x01 if retain else 0x00)
        body = bytearray(_utf8(topic))

        if qos > 0:
            session.packet_id = (session.packet_id % 65535) + 1
            body += session.packet_id.to_bytes(2, "big")

        body += payload
        await session.send(_packet(header, bytes(body)))
