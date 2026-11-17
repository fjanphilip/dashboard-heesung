# Heesung · Monitoring & Kontrol Relay (WT32-ETH01 + MQTT + Dashboard)

Sistem kontrol dan monitoring 4 channel relay melalui Ethernet menggunakan
**WT32-ETH01** dan **satu aplikasi Python** yang sekaligus menjadi:

- **Broker MQTT** (port `1883`) — WT32 mengirim/menerima data langsung ke sini
- **Dashboard web** (port `8000`) — monitoring & kontrol dari browser

Tidak perlu Mosquitto, Docker, atau instalasi broker terpisah. Cukup Python,
lalu jalankan aplikasi.

```
┌────────────────┐   Ethernet / MQTT :1883   ┌─────────────────────────────┐
│  WT32-ETH01    │ ────────────────────────► │  Aplikasi Python (1 proses) │
│  4x Relay      │ ◄──────────────────────── │  ┌──────────┐  ┌──────────┐ │
└────────────────┘                           │  │ Broker   │  │ Dashboard│ │
                                             │  │ MQTT     │  │ FastAPI  │ │
                                             │  └──────────┘  └────┬─────┘ │
                                             └─────────────────────┼───────┘
                                                                   │ HTTP/WS :8000
                                                                   ▼
                                                                Browser
```

## Struktur Project

```
Prototype-heesung/
├── start.bat / start.sh      # Launcher sekali-klik
├── firmware/
│   └── WT32_Relay/
│       ├── WT32_Relay.ino    # Sketch Arduino IDE
│       └── config.h          # Default awal IP/MQTT/pin relay (runtime via Serial)
└── dashboard/
    ├── app/
    │   ├── main.py           # FastAPI: REST + WebSocket + static
    │   ├── broker.py         # Broker MQTT 3.1.1 bawaan (pure Python)
    │   ├── mqtt_bridge.py    # Client MQTT dashboard & state relay
    │   ├── store.py          # Histori event (SQLite)
    │   ├── config.py
    │   └── static/           # UI (HTML/CSS/JS, tanpa CDN)
    ├── requirements.txt
    └── .env.example
```

---

## 1. Wiring

| Relay | Pin WT32-ETH01 | Keterangan |
|-------|----------------|------------|
| IN1   | GPIO 32        | Relay 1    |
| IN2   | GPIO 33        | Relay 2    |
| IN3   | GPIO 25        | Relay 3    |
| IN4   | GPIO 26        | Relay 4    |
| GND   | GND            | GND bersama |
| VCC   | 5V (atau supply eksternal 5V) | Sesuai modul relay |

> **Penting:** GPIO `0, 16, 18, 23` dipakai internal oleh PHY Ethernet
> (LAN8720), jangan dipakai untuk relay. Pin default bisa diubah di
> `firmware/WT32_Relay/config.h` (`RELAY_PINS_LIST`).
>
> Modul relay umumnya **aktif LOW** (default `RELAY_ACTIVE_LOW 1`). Kalau modul
> Anda aktif HIGH, ubah ke `0`.

Cukup colok kabel LAN RJ45 ke switch/router yang sama dengan PC server.

---

## 2. Firmware WT32-ETH01 (Arduino IDE)

### 2.1 Install board & library

1. **File → Preferences → Additional Boards Manager URLs**, tambahkan:
   ```
   https://espressif.github.io/arduino-esp32/package_esp32_index.json
   ```
2. **Tools → Board → Boards Manager** → cari **esp32** (Espressif) → Install.
3. **Tools → Manage Libraries** → cari **PubSubClient** (Nick O'Leary) →
   Install. Hanya library ini yang dibutuhkan.

### 2.2 Edit konfigurasi

Buka `firmware/WT32_Relay/config.h`. Nilai di sini adalah **default saat
pertama boot** — setelah itu bisa diubah lewat Serial tanpa upload ulang:

```c
#define MQTT_HOST "192.168.1.10"   // IP PC server (bukan 127.0.0.1!)
#define MQTT_PORT 1883
#define TOPIC_PREFIX "heesung"
#define RELAY_COUNT 4
```

`MQTT_HOST` **harus IP LAN PC** tempat aplikasi Python berjalan (lihat
`ipconfig`, contoh `10.128.45.184`).

### 2.3 Build & upload

1. Buka **`firmware/WT32_Relay/WT32_Relay.ino`** (nama folder = nama file `.ino`).
2. Board: **ESP32 Dev Module**, Upload Speed `921600`, Flash Size `4MB`.
3. USB-TTL: `TX→RX`, `RX→TX`, `GND→GND`, `3V3→3V3`.
4. Tahan **IO0 ke GND**, tekan EN/RST, lepas IO0 → mode bootloader.
5. Upload, lalu tekan EN/RST dan buka Serial Monitor `115200`.

Output yang benar:

```
=== WT32-ETH01 Relay Controller ===
[ETH] Start
[ETH] Link up
[ETH] IP: 192.168.1.50
[MQTT] Terhubung ke 192.168.1.10:1883
[MQTT] heesung/relay/1/state -> OFF
```

State relay disimpan di NVS (Preferences), tetap tersimpan setelah reboot.

### 2.4 Ubah IP tanpa upload ulang

Firmware menyimpan konfigurasi di NVS dan menyediakan perintah Serial. Setelah
firmware di-upload **sekali**, buka Serial Monitor (`115200`, akhir baris
`Newline`) dan ketik:

```
help                       daftar perintah
show                       lihat konfigurasi aktif
set server 10.128.45.184   ganti IP PC server — langsung dipakai
set port 1883              ganti port MQTT — langsung dipakai
set mode dhcp|static       mode IP board
set ip 192.168.1.50        IP board (mode static)
set gw 192.168.1.1         gateway
set mask 255.255.255.0     subnet mask
set dns 192.168.1.1        DNS
set prefix heesung         prefix topic
reboot                     restart untuk menerapkan mode/IP/prefix
```

- `set server` dan `set port` **langsung diterapkan** (MQTT reconnect otomatis).
- `set mode/ip/gw/mask/dns/prefix` disimpan ke NVS, berlaku setelah `reboot`.
- Semua nilai tersimpan permanen, tidak hilang saat listrik mati.

**Perlu subnet yang sama?** Untuk LAN pabrik biasa: ya. WT32 dan PC server harus
bisa saling menjangkau di jaringan yang sama (satu subnet). Kalau WT32 memakai
**DHCP**, ia otomatis mendapat IP di subnet yang sama — tidak perlu diubah.
Kalau memakai **IP statis**, sesuaikan `set ip`/`set gw`/`set mask` ke subnet
PC server lewat Serial, tanpa upload ulang.

> Mengubah IP PC server tidak perlu mengubah apa pun di dashboard: aplikasi
> listen di `0.0.0.0`, jadi otomatis mengikuti IP baru PC.

---

## 3. Menjalankan Server (PC pabrik)

Kebutuhan: **Python 3.10+**. Tidak perlu Docker/Mosquitto.

### Cara cepat (Windows)

Klik dua kali **`start.bat`** di folder project. Script akan:

1. Membuat virtualenv `.venv` (sekali saja),
2. Install dependensi (sekali saja, butuh internet),
3. Menjalankan broker MQTT + dashboard dan membuka browser.

### Cara manual

```bash
cd dashboard
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Buka **http://localhost:8000**. Dari PC lain: `http://<IP-PC-server>:8000`.

Konfigurasi opsional lewat `dashboard/.env` (lihat `.env.example`):

| Variabel | Default | Fungsi |
|----------|---------|--------|
| `BROKER_ENABLED` | `1` | `1` = broker MQTT bawaan aktif |
| `BROKER_BIND` | `0.0.0.0` | Alamat listen broker |
| `BROKER_PORT` | `1883` | Port MQTT untuk WT32 |
| `MQTT_HOST` | `127.0.0.1` | Alamat broker yang dipakai dashboard |
| `TOPIC_PREFIX` | `heesung` | Prefix semua topic |
| `RELAY_COUNT` | `4` | Jumlah relay |
| `DB_PATH` | `data/heesung.db` | Lokasi database histori |
| `OPEN_BROWSER` | `0` | `1` = buka browser otomatis |

> Jalankan **satu proses** (single worker) karena broker berjalan di dalam
> proses. Jangan pakai `--reload` atau `--workers`, karena broker akan mencoba
> bind port 1883 lebih dari sekali.

---

## 4. Topik MQTT

| Topic | Arah | Payload | Retain |
|-------|------|---------|--------|
| `heesung/relay/{n}/state` | device → dashboard | `ON` / `OFF` | ya |
| `heesung/relay/{n}/set` | dashboard → device | `ON` / `OFF` / `TOGGLE` | tidak |
| `heesung/device/status` | device → dashboard | `online` / `offline` (LWT) | ya |
| `heesung/device/heartbeat` | device → dashboard | JSON `{uptime_s, ip, mac, relays}` | tidak |

`{n}` = 1..4. Contoh heartbeat:

```json
{"uptime_s":3821,"ip":"192.168.1.50","mac":"AA:BB:CC:DD:EE:FF","relay_count":4,"relays":[true,false,false,true]}
```

Broker mendukung QoS 0/1, wildcard `+`/`#`, pesan retained, dan LWT. Retained
disimpan di memori: setelah server restart, WT32 akan mempublikasikan ulang
state saat reconnect.

---

## 5. REST API Dashboard

| Method | Endpoint | Body | Fungsi |
|--------|----------|------|--------|
| GET | `/api/state` | - | Snapshot state relay + device |
| POST | `/api/relay/{n}` | `{"state": true}` | Set relay ON/OFF |
| POST | `/api/relay/{n}/toggle` | - | Toggle relay |
| GET | `/api/history?hours=24&limit=500` | - | Histori event |
| GET | `/healthz` | - | Status server + broker |
| WS | `/ws` | - | Update realtime ke browser |

```bash
curl -X POST http://localhost:8000/api/relay/1 \
  -H "Content-Type: application/json" -d "{\"state\":true}"
```

---

## 6. Fitur Dashboard

- Status broker & device (LWT) realtime.
- Kartu relay dengan toggle ON/OFF dan waktu perubahan terakhir.
- Info device: IP, MAC, uptime, jumlah relay aktif.
- Timeline aktivitas 24 jam per relay.
- Log event tersimpan di SQLite (`dashboard/data/heesung.db`).
- Reconnect otomatis WebSocket dan MQTT.

---

## 7. Troubleshooting

| Gejala | Penyebab / Solusi |
|--------|-------------------|
| `address already in use` saat start | Port 1883/8000 dipakai proses lain (mis. Mosquitto). Tutup proses tersebut atau ubah `BROKER_PORT`/port uvicorn. |
| Device tidak dapat IP | Cek kabel LAN/switch, DHCP aktif atau atur IP statis via Serial (`set mode static`, `set ip`, `reboot`). |
| Device tidak connect MQTT (`rc=-2`) | Cek `set server` via Serial (IP LAN PC server), port 1883 terbuka di Windows Firewall, dan WT32 satu subnet dengan PC. |
| Dashboard "Broker" merah | Pastikan `BROKER_ENABLED=1` dan `MQTT_HOST=127.0.0.1`, lihat log console. |
| Relay tidak merespons | Cek `RELAY_ACTIVE_LOW`, wiring IN, dan VCC modul relay. |
| Dashboard tidak update | Cek `/healthz`, lihat log console server. |

**Windows Firewall:** saat pertama dijalankan, izinkan akses Python untuk
jaringan Private. Minimal port **1883** (WT32) dan **8000** (browser PC lain)
harus bisa diakses.

---

## 8. Catatan Keamanan

Broker saat ini menerima koneksi anonymous dan dashboard tanpa login —
cocok untuk LAN internal pabrik. Untuk produksi:

1. Tambahkan autentikasi MQTT (username/password) pada `broker.py` dan
   `config.h`/`mqtt_bridge.py`.
2. Batasi akses port 1883/8000 ke VLAN internal lewat firewall.
3. Tambahkan login + HTTPS pada dashboard bila diakses dari luar LAN.
