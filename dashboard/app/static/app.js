"use strict";

const DAY_SECONDS = 24 * 3600;

const state = {
  relayCount: 4,
  relays: [],
  device: {},
  broker: false,
  events: [],
  ws: null,
  backoff: 1000,
  wsOpen: false,
  uiBuilt: false,
};

const el = (id) => document.getElementById(id);

const fmtTime = new Intl.DateTimeFormat("id-ID", {
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const fmtDateTime = new Intl.DateTimeFormat("id-ID", {
  day: "2-digit", month: "short",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

/* ---------------- helpers ---------------- */

function fmtDuration(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds));
  if (s < 60) return `${s}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h === 0) return `${m}m`;
  return m ? `${h}j ${m}m` : `${h}j`;
}

async function getJSON(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function setBadge(id, ok) {
  const node = el(id);
  node.classList.toggle("ok", !!ok);
  node.classList.toggle("bad", !ok);
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = el("toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3200);
}

/* ---------------- UI build ---------------- */

function buildUI() {
  const grid = el("relayGrid");
  grid.innerHTML = "";
  for (let id = 1; id <= state.relayCount; id++) {
    const card = document.createElement("article");
    card.className = "relay-card";
    card.dataset.id = String(id);
    card.innerHTML =
      `<div class="relay-top">` +
        `<span class="relay-name">Relay ${id}</span>` +
        `<span class="state-pill">OFF</span>` +
      `</div>` +
      `<div class="relay-meta">Belum ada data</div>` +
      `<button class="toggle" type="button" role="switch" aria-checked="false" ` +
        `aria-label="Toggle Relay ${id}"><span></span></button>`;
    card.querySelector(".toggle").addEventListener("click", () => toggleRelay(id));
    grid.appendChild(card);
  }

  const list = el("timelineList");
  list.innerHTML = "";
  for (let id = 1; id <= state.relayCount; id++) {
    const row = document.createElement("div");
    row.className = "timeline-row";
    row.innerHTML =
      `<div class="timeline-label">Relay ${id}</div>` +
      `<canvas data-id="${id}"></canvas>` +
      `<div class="timeline-total" data-id="${id}">—</div>`;
    list.appendChild(row);
  }

  state.uiBuilt = true;
}

/* ---------------- rendering ---------------- */

function renderStats() {
  const d = state.device || {};
  el("statIp").textContent = d.ip || "—";
  el("statMac").textContent = d.mac || "—";
  el("statUptime").textContent = d.uptime_s != null ? fmtUptime(d.uptime_s) : "—";
  setBadge("brokerBadge", state.broker);
  setBadge("deviceBadge", d.online);

  const active = state.relays.filter((r) => r.state).length;
  el("statActive").textContent = `${active} / ${state.relayCount}`;
}

function fmtUptime(seconds) {
  const s = Number(seconds) || 0;
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}h ${h}j`;
  if (h > 0) return `${h}j ${m}m`;
  return `${m}m ${Math.floor(s % 60)}s`;
}

function renderRelays() {
  const online = !!state.device.online;
  for (const relay of state.relays) {
    const card = document.querySelector(`.relay-card[data-id="${relay.id}"]`);
    if (!card) continue;
    card.classList.toggle("on", !!relay.state);
    card.classList.toggle("offline", !online);
    card.querySelector(".state-pill").textContent = relay.state ? "ON" : "OFF";
    card.querySelector(".relay-meta").textContent = relay.updated_at
      ? `Terakhir: ${fmtTime.format(relay.updated_at * 1000)}`
      : "Belum ada data";
    const btn = card.querySelector(".toggle");
    btn.setAttribute("aria-checked", String(!!relay.state));
    btn.disabled = !online;
  }
}

function renderAll() {
  renderStats();
  renderRelays();
  drawTimelines();
  renderLog();
}

function renderLog() {
  const body = el("logBody");
  const rows = state.events.slice(0, 60);
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="3" class="empty">Belum ada event</td></tr>`;
    return;
  }
  body.innerHTML = rows
    .map(
      (e) =>
        `<tr><td>${fmtDateTime.format(e.ts * 1000)}</td>` +
        `<td>Relay ${e.relay}</td>` +
        `<td><span class="tag ${e.state ? "on" : "off"}">${e.state ? "ON" : "OFF"}</span></td></tr>`
    )
    .join("");
}

/* ---------------- timeline ---------------- */

function buildSegments(relayId, start, end) {
  const events = state.events
    .filter((e) => e.relay === relayId)
    .sort((a, b) => a.ts - b.ts);

  let on = false;
  let cursor = start;
  const segments = [];

  for (const event of events) {
    if (event.ts < start) {
      on = !!event.state;
      continue;
    }
    if (event.ts >= end) break;
    if (!!event.state === on) continue;
    if (on) segments.push([cursor, event.ts]);
    on = !!event.state;
    cursor = event.ts;
  }
  if (on) segments.push([cursor, end]);
  return segments;
}

function drawTimelines() {
  const end = Date.now() / 1000;
  const start = end - DAY_SECONDS;
  const canvases = document.querySelectorAll("#timelineList canvas");

  canvases.forEach((canvas) => {
    const relayId = Number(canvas.dataset.id);
    const segments = buildSegments(relayId, start, end);
    drawTimelineCanvas(canvas, segments, start, end);

    const total = segments.reduce((acc, [s, e]) => acc + (e - s), 0);
    const label = document.querySelector(`.timeline-total[data-id="${relayId}"]`);
    if (label) label.textContent = `ON ${fmtDuration(total)}`;
  });
}

function drawTimelineCanvas(canvas, segments, start, end) {
  const rect = canvas.getBoundingClientRect();
  if (rect.width === 0) return;

  const dpr = window.devicePixelRatio || 1;
  const width = Math.round(rect.width);
  const height = 30;

  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  // grid tiap 6 jam
  ctx.strokeStyle = "rgba(126, 147, 168, 0.13)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const x = Math.round((width / 4) * i) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }

  // base track
  ctx.fillStyle = "rgba(126, 147, 168, 0.10)";
  ctx.fillRect(0, 8, width, height - 16);

  // segmen ON
  ctx.fillStyle = "rgba(34, 197, 94, 0.85)";
  const span = end - start;
  for (const [segStart, segEnd] of segments) {
    const x = ((segStart - start) / span) * width;
    const w = Math.max(1.5, ((segEnd - segStart) / span) * width);
    ctx.fillRect(x, 8, w, height - 16);
  }
}

/* ---------------- data application ---------------- */

function applySnapshot(snapshot) {
  const count = snapshot.relay_count || state.relayCount;
  if (!state.uiBuilt || count !== state.relayCount) {
    state.relayCount = count;
    buildUI();
  }
  state.relays = snapshot.relays || [];
  state.device = snapshot.device || {};
  state.broker = !!snapshot.broker_connected;
  state.lastUpdate = Date.now();
}

function mergeEvents(events) {
  const known = new Set(state.events.map((e) => e.id));
  for (const event of events) {
    if (!known.has(event.id)) {
      state.events.push(event);
      known.add(event.id);
    }
  }
  state.events.sort((a, b) => b.ts - a.ts);
  if (state.events.length > 1000) state.events.length = 1000;
}

function handleMessage(msg) {
  if (msg.type === "snapshot") {
    applySnapshot(msg);
    renderAll();
    return;
  }

  if (msg.type === "relay") {
    const event = msg.event;
    const relay = state.relays.find((r) => r.id === event.relay);
    if (relay) {
      relay.state = event.state;
      relay.updated_at = event.ts;
    }
    mergeEvents([event]);
    renderStats();
    renderRelays();
    drawTimelines();
    renderLog();
    return;
  }

  if (msg.type === "device") {
    state.device = Object.assign({}, state.device, msg.device);
    renderStats();
    renderRelays();
    return;
  }

  if (msg.type === "broker") {
    state.broker = !!msg.connected;
    renderStats();
  }
}

/* ---------------- interaction ---------------- */

async function toggleRelay(relayId) {
  const relay = state.relays.find((r) => r.id === relayId);
  if (!relay || !state.device.online) return;

  const card = document.querySelector(`.relay-card[data-id="${relayId}"]`);
  card?.classList.add("pending");

  try {
    const res = await fetch(`/api/relay/${relayId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state: !relay.state }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
  } catch (err) {
    toast(`Gagal mengirim perintah: ${err.message}`, true);
  } finally {
    card?.classList.remove("pending");
  }
}

/* ---------------- websocket ---------------- */

function setWsStatus(open) {
  state.wsOpen = open;
  const node = el("wsStatus");
  node.textContent = open ? "Live" : "Terputus — mencoba ulang…";
  node.style.color = open ? "var(--accent)" : "var(--muted)";
}

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;

  ws.onopen = () => {
    state.backoff = 1000;
    setWsStatus(true);
  };

  ws.onmessage = (event) => {
    try {
      handleMessage(JSON.parse(event.data));
      el("lastSync").textContent = `Diperbarui ${fmtTime.format(Date.now())}`;
    } catch (err) {
      console.warn("Pesan tidak valid", err);
    }
  };

  ws.onclose = () => {
    setWsStatus(false);
    setTimeout(connectWebSocket, state.backoff);
    state.backoff = Math.min(state.backoff * 1.7, 10000);
  };

  ws.onerror = () => ws.close();
}

/* ---------------- boot ---------------- */

async function refreshHistory() {
  try {
    const data = await getJSON("/api/history?hours=24&limit=2000");
    state.events = data.events || [];
    state.events.sort((a, b) => b.ts - a.ts);
    drawTimelines();
    renderLog();
  } catch (err) {
    toast("Gagal memuat histori", true);
  }
}

async function boot() {
  try {
    const [snapshot, history] = await Promise.all([
      getJSON("/api/state"),
      getJSON("/api/history?hours=24&limit=2000"),
    ]);
    applySnapshot(snapshot);
    mergeEvents(history.events || []);
    renderAll();
  } catch (err) {
    toast("Gagal memuat data awal", true);
    buildUI();
  }

  connectWebSocket();
  el("lastSync").textContent = `Diperbarui ${fmtTime.format(Date.now())}`;

  setInterval(() => {
    el("clock").textContent = fmtTime.format(Date.now());
  }, 1000);

  setInterval(() => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send("ping");
    }
  }, 25000);

  el("refreshBtn").addEventListener("click", refreshHistory);

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(drawTimelines, 150);
  });
}

boot();
