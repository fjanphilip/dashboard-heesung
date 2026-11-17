"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const PENDING_TIMEOUT_MS = 5000;

const state = {
  ionizers: [],
  devices: new Map(), // device id -> {id, online, ip, mac, uptime_s, last_seen, relays: Map}
  broker: false,
  wsOpen: false,
  selected: null,
  filter: "all",
  query: "",
  pending: new Map(), // ionizer id -> {target, timer}
  history: [],
  ws: null,
  backoff: 1000,
};

const el = (id) => document.getElementById(id);
const fmtTime = new Intl.DateTimeFormat("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const fmtDateTime = new Intl.DateTimeFormat("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

/* ================= mapping ionizer <-> device ================= */

// Konvensi: ionizer N dilayani WT32 berID "ion-N" (angka bebas nol di depan:
// ion-5 dan ion-05 sama), relay 1. Field `device`/`relay` di ionizers.json
// menimpa konvensi ini.
const ionNumberOf = (deviceId) => {
  const m = /^ion-0*(\d+)$/i.exec(deviceId);
  return m ? Number(m[1]) : null;
};
const relayOf = (ion) => ion.relay || 1;

function deviceFor(ion) {
  if (ion.device) return state.devices.get(ion.device) || null;
  for (const device of state.devices.values()) {
    if (ionNumberOf(device.id) === ion.id) return device;
  }
  return null;
}

// ID yang dipakai ke API; sebelum terdeteksi, tampilkan ID yang diharapkan.
const deviceIdOf = (ion) =>
  deviceFor(ion)?.id || ion.device || `ion-${String(ion.id).padStart(2, "0")}`;

// key: on | off | offline | na
function statusOf(ion) {
  const device = deviceFor(ion);
  if (!device) return { key: "na", label: "Belum terdeteksi", device: null };
  if (!device.online) return { key: "offline", label: "Offline", device };
  const relay = device.relays.get(relayOf(ion));
  if (!relay) return { key: "na", label: "Menunggu data", device };
  return relay.state
    ? { key: "on", label: "ON", device, relay }
    : { key: "off", label: "OFF", device, relay };
}

const isControllable = (st) => st.key === "on" || st.key === "off";

/* ================= helpers ================= */

function ago(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 5) return "baru saja";
  if (s < 60) return `${s} detik lalu`;
  if (s < 3600) return `${Math.floor(s / 60)} menit lalu`;
  if (s < 86400) return `${Math.floor(s / 3600)} jam lalu`;
  return `${Math.floor(s / 86400)} hari lalu`;
}

function fmtUptime(seconds) {
  if (seconds == null) return "—";
  const s = Number(seconds) || 0;
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}h ${h}j`;
  if (h > 0) return `${h}j ${m}m`;
  return `${m}m ${Math.floor(s % 60)}s`;
}

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function svgEl(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = el("toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3500);
}

/* ================= denah (skema; sesuaikan bila layout berubah) ================= */

// [x, y, lebar, tinggi, jenis]  jenis: g = conveyor, s = conveyor bergaris, b = biru
const SHAPES = [
  [27, 95, 125, 38, "g"], [152, 98, 62, 34, "s"], [217, 98, 62, 34, "g"],
  [281, 98, 62, 34, "s"], [345, 98, 60, 34, "g"], [407, 98, 85, 34, "g"],
  [496, 98, 86, 34, "g"], [586, 98, 148, 34, "g"], [738, 98, 58, 34, "g"],
  [800, 98, 62, 34, "s"], [865, 98, 60, 34, "g"],
  [27, 143, 60, 34, "g"], [35, 178, 40, 66, "b"], [562, 142, 38, 100, "b"],
  [933, 98, 120, 34, "g"], [1060, 98, 62, 34, "s"], [1130, 98, 60, 34, "s"],
  [1197, 98, 122, 34, "s"], [1060, 140, 60, 50, "g"], [1130, 140, 60, 50, "g"],
  [1195, 140, 58, 44, "g"], [1262, 140, 58, 44, "g"],
  [1328, 90, 105, 42, "g"], [1437, 90, 100, 42, "g"], [1541, 90, 73, 42, "g"],
  [1417, 135, 36, 100, "b"], [1297, 197, 36, 48, "b"],
];
const ZONES = [[929, 92, 394, 44], [1055, 92, 70, 100], [1125, 92, 68, 100]];

function buildMap() {
  const svg = el("lineMap");
  svg.innerHTML = "";

  for (const [x, y, w, h, kind] of SHAPES) {
    svg.appendChild(svgEl("rect", { x, y, width: w, height: h, class: kind === "b" ? "blue" : "conv" }));
    if (kind === "s") {
      for (const dy of [h * 0.35, h * 0.65]) {
        svg.appendChild(svgEl("line", { x1: x + 2, x2: x + w - 2, y1: y + dy, y2: y + dy, class: "conv-line" }));
      }
    }
  }
  for (const [x, y, w, h] of ZONES) {
    svg.appendChild(svgEl("rect", { x, y, width: w, height: h, class: "zone" }));
  }

  for (const ion of state.ionizers) {
    const g = svgEl("g", { class: "pt na", "data-id": ion.id, transform: `translate(${ion.x} ${ion.y})` });
    g.appendChild(svgEl("circle", { class: "halo", r: 19 }));
    g.appendChild(svgEl("circle", { class: "body", r: 17 }));
    const label = svgEl("text", {});
    label.textContent = ion.id;
    g.appendChild(label);
    g.appendChild(svgEl("title", {}));
    g.addEventListener("mouseenter", () => highlight(ion.id, true));
    g.addEventListener("mouseleave", () => highlight(ion.id, false));
    g.addEventListener("click", () => select(ion.id));
    svg.appendChild(g);
  }
}

function highlight(id, on) {
  document.querySelector(`.pt[data-id="${id}"]`)?.classList.toggle("hl", on);
  document.querySelector(`#tableBody tr[data-id="${id}"]`)?.classList.toggle("hl", on);
}

/* ================= kontrol ON/OFF (segmented) ================= */

function buildSeg(ion, large) {
  const seg = make("div", large ? "seg big" : "seg");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", `Kontrol Ionizer ${ion.id}`);
  for (const v of ["on", "off"]) {
    const btn = make("button", "", v.toUpperCase());
    btn.type = "button";
    btn.dataset.v = v;
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      setRelay(ion, v === "on");
    });
    seg.appendChild(btn);
  }
  return seg;
}

function paintSeg(seg, ion, st) {
  const pend = state.pending.get(ion.id);
  for (const btn of seg.querySelectorAll("button")) {
    const isOn = btn.dataset.v === "on";
    btn.classList.toggle("active", st.key === (isOn ? "on" : "off"));
    btn.classList.toggle("busy", !!pend && pend.target === isOn);
    btn.disabled = !isControllable(st) || !!pend;
  }
}

async function setRelay(ion, on) {
  const st = statusOf(ion);
  if (!isControllable(st) || state.pending.has(ion.id)) return;
  if ((st.key === "on") === on) return;

  startPending(ion, on);
  try {
    const res = await fetch(`/api/devices/${encodeURIComponent(deviceIdOf(ion))}/relay/${relayOf(ion)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state: on }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
  } catch (err) {
    clearPending(ion.id);
    toast(`Ionizer ${ion.id}: gagal mengirim perintah (${err.message})`, true);
  }
}

// Status baru dianggap berhasil hanya setelah WT32 mengonfirmasi lewat MQTT.
function startPending(ion, target) {
  const timer = setTimeout(() => {
    if (!state.pending.has(ion.id)) return;
    clearPending(ion.id);
    toast(`Ionizer ${ion.id}: perangkat tidak merespons`, true);
  }, PENDING_TIMEOUT_MS);
  state.pending.set(ion.id, { target, timer });
  render();
}

function clearPending(id) {
  const pend = state.pending.get(id);
  if (!pend) return;
  clearTimeout(pend.timer);
  state.pending.delete(id);
  render();
}

/* ================= tabel ================= */

function buildTable() {
  const body = el("tableBody");
  body.innerHTML = "";

  for (const ion of state.ionizers) {
    const tr = make("tr");
    tr.dataset.id = ion.id;

    const tdNo = make("td", "", ion.id);

    const tdName = make("td");
    tdName.append(make("div", "nm", `Ionizer ${ion.id}`), make("div", "pr", ion.process));

    const tdType = make("td", "", ion.type);

    const tdDev = make("td");
    const dev = make("div", "dev");
    const id = make("div", "id");
    id.append(make("i", "dot"), make("span", "", deviceIdOf(ion)));
    dev.append(id, make("div", "ip", "—"));
    tdDev.appendChild(dev);

    const tdStatus = make("td", "status");
    tdStatus.appendChild(make("span", "pill na", "—"));

    const tdCtl = make("td", "c-ctl");
    tdCtl.appendChild(buildSeg(ion, false));

    tr.append(tdNo, tdName, tdType, tdDev, tdStatus, tdCtl);
    tr.addEventListener("click", () => select(ion.id));
    tr.addEventListener("mouseenter", () => highlight(ion.id, true));
    tr.addEventListener("mouseleave", () => highlight(ion.id, false));
    body.appendChild(tr);
  }
}

function matchesFilter(ion, st) {
  if (state.filter === "on" && st.key !== "on") return false;
  if (state.filter === "off" && st.key !== "off") return false;
  if (state.filter === "down" && (st.key === "on" || st.key === "off")) return false;
  const q = state.query.trim().toLowerCase();
  if (!q) return true;
  return `ionizer ${ion.id} ${ion.process} ${ion.type} ${deviceIdOf(ion)}`.toLowerCase().includes(q);
}

/* ================= render ================= */

function render() {
  const counts = { on: 0, off: 0, down: 0 };
  let visible = 0;

  for (const ion of state.ionizers) {
    const st = statusOf(ion);
    if (st.key === "on") counts.on++;
    else if (st.key === "off") counts.off++;
    else counts.down++;

    const pend = state.pending.has(ion.id);
    const pt = document.querySelector(`.pt[data-id="${ion.id}"]`);
    if (pt) {
      pt.setAttribute("class", `pt ${st.key}${pend ? " pend" : ""}${state.selected === ion.id ? " sel" : ""}`);
      pt.querySelector("title").textContent = `Ionizer ${ion.id} — ${ion.process} — ${st.label}`;
    }

    const row = document.querySelector(`#tableBody tr[data-id="${ion.id}"]`);
    if (!row) continue;
    const show = matchesFilter(ion, st);
    row.hidden = !show;
    if (show) visible++;
    row.classList.toggle("sel", state.selected === ion.id);

    row.querySelector(".dev .dot").className = `dot ${st.device ? (st.device.online ? "on" : "offline") : ""}`;
    row.querySelector(".dev .ip").textContent = st.device?.ip || (st.device ? "IP —" : "belum terdeteksi");
    const pill = row.querySelector(".pill");
    pill.className = `pill ${st.key}`;
    pill.textContent = st.label;
    paintSeg(row.querySelector(".seg"), ion, st);
  }

  el("cAll").textContent = state.ionizers.length;
  el("cOn").textContent = counts.on;
  el("cOff").textContent = counts.off;
  el("cDown").textContent = counts.down;
  el("emptyRow").hidden = visible > 0;

  for (const node of document.querySelectorAll("[data-filter]")) {
    if (node.classList.contains("chip")) node.classList.toggle("active", node.dataset.filter === state.filter);
  }

  const controllable = state.ionizers.filter((i) => isControllable(statusOf(i))).length;
  el("bulkOn").disabled = el("bulkOff").disabled = controllable === 0;

  setBadge("brokerBadge", state.broker);
  const known = new Set(state.ionizers.map((i) => deviceFor(i)?.id));
  const unknown = [...state.devices.keys()].filter((id) => !known.has(id));
  el("unknownInfo").textContent = unknown.length
    ? `Perangkat belum terpasang ke ionizer mana pun (ID harus ion-N): ${unknown.join(", ")}`
    : "";
  const online = [...state.devices.values()].filter((d) => d.online).length;
  el("deviceCount").textContent = `${online} dari ${state.devices.size} perangkat terdeteksi online`;

  renderPanel();
}

function setBadge(id, ok) {
  const node = el(id);
  node.classList.toggle("ok", !!ok);
  node.classList.toggle("bad", !ok);
}

/* ================= panel detail ================= */

const panelCache = { id: null, seg: null };

function renderPanel() {
  const panel = el("panel");
  const ion = state.ionizers.find((i) => i.id === state.selected);

  if (!ion) {
    panelCache.id = null;
    const blank = make("div", "blank");
    blank.append(make("b", "", "Pilih ionizer"), document.createTextNode("Klik titik di denah atau baris di tabel untuk melihat detail dan mengontrol."));
    panel.replaceChildren(blank);
    return;
  }

  const st = statusOf(ion);

  // Bangun kerangka sekali per ionizer yang dipilih; sesudahnya hanya update isi.
  if (panelCache.id !== ion.id) {
    panelCache.id = ion.id;
    panel.innerHTML =
      `<div class="ph"><div><h2></h2><p class="pp"></p></div><button class="close" type="button" aria-label="Tutup">✕</button></div>` +
      `<div class="hero"><div class="big"></div><div class="why"></div></div>` +
      `<div class="ctl-slot"></div><p class="ctl-note"></p>` +
      `<dl></dl><h3>Riwayat terakhir</h3><ul class="hist"></ul>`;
    panel.querySelector("h2").textContent = `Ionizer ${ion.id}`;
    panel.querySelector(".pp").textContent = `${ion.process} · ${ion.type}`;
    panel.querySelector(".close").addEventListener("click", () => select(null));
    panelCache.seg = buildSeg(ion, true);
    panel.querySelector(".ctl-slot").appendChild(panelCache.seg);
  }

  const hero = panel.querySelector(".hero");
  hero.className = `hero ${st.key}`;
  hero.querySelector(".big").textContent = st.label.toUpperCase();
  hero.querySelector(".why").textContent = {
    on: "Ionizer menyala", off: "Ionizer mati",
    offline: "WT32 tidak terhubung ke server",
    na: st.device ? "Menunggu status relay dari WT32" : `Belum ada WT32 berID ${deviceIdOf(ion)}`,
  }[st.key];

  paintSeg(panelCache.seg, ion, st);
  panel.querySelector(".ctl-note").textContent = isControllable(st)
    ? "Perintah dianggap berhasil setelah WT32 mengonfirmasi."
    : "Kontrol aktif saat WT32 online.";

  const d = st.device;
  const rows = [
    ["ID perangkat", deviceIdOf(ion)],
    ["Relay", `#${relayOf(ion)}`],
    ["Alamat IP", d?.ip || "—"],
    ["MAC", d?.mac || "—"],
    ["Uptime", d?.online ? fmtUptime(d.uptime_s) : "—"],
    ["Terakhir terlihat", ago(d?.last_seen)],
    ["Perubahan status", ago(st.relay?.updated_at)],
  ];
  const dl = panel.querySelector("dl");
  dl.replaceChildren(...rows.flatMap(([k, v]) => [make("dt", "", k), make("dd", "", v)]));

  const hist = panel.querySelector(".hist");
  if (!state.history.length) {
    hist.replaceChildren(make("li", "none", "Belum ada riwayat"));
  } else {
    hist.replaceChildren(...state.history.map((e) => {
      const li = make("li");
      li.append(make("span", "", fmtDateTime.format(e.ts * 1000)), make("span", `pill ${e.state ? "on" : "off"}`, e.state ? "ON" : "OFF"));
      return li;
    }));
  }
}

async function loadHistory() {
  const ion = state.ionizers.find((i) => i.id === state.selected);
  if (!ion) return;
  try {
    const res = await fetch(`/api/history?device=${encodeURIComponent(deviceIdOf(ion))}&hours=168&limit=100`);
    const data = await res.json();
    if (state.selected !== ion.id) return;
    state.history = (data.events || []).filter((e) => e.relay === relayOf(ion)).slice(0, 6);
    renderPanel();
  } catch {
    /* riwayat bersifat tambahan; abaikan bila gagal */
  }
}

function select(id) {
  state.selected = state.selected === id ? null : id;
  state.history = [];
  render();
  if (state.selected != null) loadHistory();
}

/* ================= bulk ================= */

function confirmDialog(title, text) {
  return new Promise((resolve) => {
    const dialog = el("confirm");
    el("confirmTitle").textContent = title;
    el("confirmText").textContent = text;
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    dialog.showModal();
  });
}

async function bulk(on) {
  const targets = state.ionizers.filter((i) => {
    const st = statusOf(i);
    return isControllable(st) && (st.key === "on") !== on && !state.pending.has(i.id);
  });
  if (!targets.length) {
    toast(`Semua ionizer yang online sudah ${on ? "ON" : "OFF"}`);
    return;
  }
  const word = on ? "menyalakan" : "mematikan";
  const ok = await confirmDialog(
    `${on ? "Nyalakan" : "Matikan"} ${targets.length} ionizer?`,
    `Perintah akan dikirim ke ${targets.length} WT32 yang online. Ionizer offline tidak ikut ${word}.`
  );
  if (ok) await Promise.all(targets.map((ion) => setRelay(ion, on)));
}

/* ================= data ================= */

function toDevice(raw) {
  return {
    id: raw.id,
    online: !!raw.online,
    ip: raw.ip || null,
    mac: raw.mac || null,
    uptime_s: raw.uptime_s ?? null,
    last_seen: raw.last_seen || null,
    relays: new Map((raw.relays || []).map((r) => [r.id, { state: !!r.state, updated_at: r.updated_at }])),
  };
}

function applySnapshot(snapshot) {
  state.devices = new Map((snapshot.devices || []).map((d) => [d.id, toDevice(d)]));
  state.broker = !!snapshot.broker_connected;
}

function ionizerFor(deviceId, relay) {
  return state.ionizers.find((i) => deviceFor(i)?.id === deviceId && relayOf(i) === relay);
}

function handleMessage(msg) {
  if (msg.type === "snapshot") {
    applySnapshot(msg);
  } else if (msg.type === "device") {
    state.devices.set(msg.device.id, toDevice(msg.device));
  } else if (msg.type === "relay") {
    const id = msg.device;
    if (!state.devices.has(id)) state.devices.set(id, toDevice({ id, online: true }));
    state.devices.get(id).relays.set(msg.event.relay, { state: msg.event.state, updated_at: msg.event.ts });

    const ion = ionizerFor(id, msg.event.relay);
    const pend = ion && state.pending.get(ion.id);
    if (pend && pend.target === msg.event.state) clearPending(ion.id);
    if (ion && ion.id === state.selected) loadHistory();
  } else if (msg.type === "broker") {
    state.broker = !!msg.connected;
  } else {
    return;
  }
  render();
}

/* ================= websocket ================= */

function setWs(open) {
  state.wsOpen = open;
  setBadge("wsBadge", open);
  el("wsText").textContent = open ? "Live" : "Terputus, mencoba ulang…";
}

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;

  ws.onopen = () => { state.backoff = 1000; setWs(true); };
  ws.onmessage = (event) => {
    try { handleMessage(JSON.parse(event.data)); }
    catch (err) { console.warn("Pesan tidak valid", err); }
  };
  ws.onclose = () => {
    setWs(false);
    setTimeout(connectWebSocket, state.backoff);
    state.backoff = Math.min(state.backoff * 1.7, 10000);
  };
  ws.onerror = () => ws.close();
}

/* ================= boot ================= */

async function getJSON(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function bindUI() {
  for (const node of document.querySelectorAll("[data-filter]")) {
    node.addEventListener("click", () => { state.filter = node.dataset.filter; render(); });
  }
  el("search").addEventListener("input", (e) => { state.query = e.target.value; render(); });
  el("bulkOn").addEventListener("click", () => bulk(true));
  el("bulkOff").addEventListener("click", () => bulk(false));
}

async function boot() {
  try {
    state.ionizers = (await getJSON("/static/ionizers.json")).ionizers || [];
  } catch {
    toast("Gagal memuat ionizers.json", true);
  }
  buildMap();
  buildTable();
  bindUI();

  try {
    applySnapshot(await getJSON("/api/state"));
  } catch {
    toast("Gagal memuat data awal", true);
  }
  render();
  connectWebSocket();

  setInterval(() => {
    el("clock").textContent = fmtTime.format(Date.now());
    renderPanel(); // menyegarkan "x detik lalu"
  }, 1000);

  setInterval(() => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send("ping");
  }, 25000);
}

boot();
