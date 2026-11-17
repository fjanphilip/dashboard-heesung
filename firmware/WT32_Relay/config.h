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
#define TOPIC_PREFIX "heesung"

// ID unik tiap board (huruf/angka/-/_, maks 23 karakter). Ubah lewat Serial:
//   set id ion-05
// Konvensi "ion-N" otomatis dipetakan dashboard ke titik ionizer nomor N.
// Tiap board WAJIB punya ID berbeda (ID juga dipakai sebagai MQTT client id).
#define DEVICE_ID "ion-01"

#define HEARTBEAT_INTERVAL_MS 15000UL
#define MQTT_RETRY_INTERVAL_MS 3000UL

// ---------- Relay ----------
// Modul relay 4 channel. Ubah pin bila wiring berbeda.
// Pin default: GPIO 32 (label CFG), 33 (label 485_EN), 17 (label TXD, pin 8),
// 4 (label IO4). Keempatnya output-capable dan tidak strapping.
// JANGAN pakai GPIO 25/26: tidak ada di header WT32-ETH01 (dan dipakai
// jalur data Ethernet RMII pada ESP32). GPIO 35/36/39 hanya input.
#define RELAY_COUNT 4
#define RELAY_PINS_LIST {32, 33, 17, 4}
#define RELAY_ACTIVE_LOW 1         // 1 = aktif LOW, 0 = aktif HIGH
