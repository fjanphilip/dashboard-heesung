#pragma once

// ============================================================
//  Konfigurasi default WT32-ETH01 Relay Controller
//
//  Nilai di sini hanya dipakai SAAT PERTAMA KALI boot.
//  Setelah itu bisa diubah lewat Serial (tanpa upload ulang)
//  dan disimpan di NVS. Ketik "help" di Serial Monitor.
// ============================================================

// ---------- Jaringan ----------
// 0 = DHCP, 1 = IP statis (default awal)
#define USE_STATIC_IP 0

#define DEFAULT_STATIC_IP "192.168.1.50"
#define DEFAULT_STATIC_GATEWAY "192.168.1.1"
#define DEFAULT_STATIC_SUBNET "255.255.255.0"
#define DEFAULT_STATIC_DNS "192.168.1.1"

// ---------- MQTT ----------
#define MQTT_HOST "10.128.45.184"  // IP LAN laptop/PC server (bisa diubah via Serial)
#define MQTT_PORT 1883
#define MQTT_USER ""               // kosongkan jika broker tanpa auth
#define MQTT_PASS ""
#define MQTT_CLIENT_ID "wt32-relay-01"
#define TOPIC_PREFIX "heesung"

#define HEARTBEAT_INTERVAL_MS 15000UL
#define MQTT_RETRY_INTERVAL_MS 3000UL

// ---------- Relay ----------
// Modul relay 4 channel. Ubah pin bila wiring berbeda.
// Pin default: GPIO 32, 33, 25, 26 (tidak bentrok dengan PHY Ethernet
// yang memakai GPIO 0/16/18/23).
#define RELAY_COUNT 4
#define RELAY_PINS_LIST {32, 33, 25, 26}
#define RELAY_ACTIVE_LOW 1         // 1 = aktif LOW, 0 = aktif HIGH
