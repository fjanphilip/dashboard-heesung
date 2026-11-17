// ============================================================
//  WT32-ETH01 Relay Controller — MQTT
//  Arduino IDE sketch
//
//  Board      : ESP32 Dev Module
//  Library    : PubSubClient (Nick O'Leary)
//  Konfigurasi: config.h (default awal) + Serial (runtime, NVS)
// ============================================================

#include <WiFi.h>
#include <ETH.h>
#include <PubSubClient.h>
#include <Preferences.h>

#if ESP_ARDUINO_VERSION_MAJOR >= 3
#include <NetworkClient.h>
#endif

#include "config.h"

// ------------------------------------------------------------
// WT32-ETH01: ESP32 + LAN8720 PHY
// PHY addr 1, power GPIO16, MDC GPIO23, MDIO GPIO18,
// clock mode GPIO0 input.
// ------------------------------------------------------------
#define WT_ETH_PHY_ADDR 1
#define WT_ETH_POWER 16
#define WT_ETH_MDC 23
#define WT_ETH_MDIO 18

static const uint8_t RELAY_PINS[RELAY_COUNT] = RELAY_PINS_LIST;
static const int RELAY_ON_LEVEL = RELAY_ACTIVE_LOW ? LOW : HIGH;
static const int RELAY_OFF_LEVEL = RELAY_ACTIVE_LOW ? HIGH : LOW;

struct NetConfig {
  bool staticIp;
  IPAddress ip;
  IPAddress gateway;
  IPAddress subnet;
  IPAddress dns;
  char mqttHost[64];
  uint16_t mqttPort;
  char topicPrefix[32];
  char deviceId[24];
};

static NetConfig cfg;
static bool relayState[RELAY_COUNT];
static bool ethUp = false;
static bool mqttWasConnected = false;
static unsigned long lastHeartbeat = 0;
static unsigned long lastMqttAttempt = 0;

static Preferences prefsRelay;
static Preferences prefsNet;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
static NetworkClient ethClient;
#else
static EthernetClient ethClient;
#endif
static PubSubClient mqtt(ethClient);

static char topicBuf[96];
static char cmdBuf[96];
static uint8_t cmdLen = 0;

// ------------------------------------------------------------
// Konfigurasi runtime (NVS)
// ------------------------------------------------------------
static void printConfig() {
  Serial.println("[CFG] Konfigurasi saat ini:");
  Serial.printf("[CFG]   Mode IP      : %s\n", cfg.staticIp ? "STATIC" : "DHCP");
  Serial.printf("[CFG]   IP static    : %s\n", cfg.ip.toString().c_str());
  Serial.printf("[CFG]   Gateway      : %s\n", cfg.gateway.toString().c_str());
  Serial.printf("[CFG]   Subnet mask  : %s\n", cfg.subnet.toString().c_str());
  Serial.printf("[CFG]   DNS          : %s\n", cfg.dns.toString().c_str());
  Serial.printf("[CFG]   Server MQTT  : %s:%u\n", cfg.mqttHost, cfg.mqttPort);
  Serial.printf("[CFG]   Topic prefix : %s\n", cfg.topicPrefix);
  Serial.printf("[CFG]   Device ID    : %s\n", cfg.deviceId);
  Serial.printf("[CFG]   IP sekarang  : %s\n", ETH.localIP().toString().c_str());
}

static void printHelp() {
  Serial.println("[CFG] Perintah Serial:");
  Serial.println("[CFG]   show                 tampilkan konfigurasi");
  Serial.println("[CFG]   set server <ip>      alamat server MQTT (langsung dipakai)");
  Serial.println("[CFG]   set port <1-65535>   port MQTT (langsung dipakai)");
  Serial.println("[CFG]   set mode dhcp|static");
  Serial.println("[CFG]   set ip <a.b.c.d>");
  Serial.println("[CFG]   set gw <a.b.c.d>");
  Serial.println("[CFG]   set mask <a.b.c.d>");
  Serial.println("[CFG]   set dns <a.b.c.d>");
  Serial.println("[CFG]   set prefix <teks>");
  Serial.println("[CFG]   set id <teks>        ID unik board, mis. ion-05");
  Serial.println("[CFG]   reboot               restart board");
  Serial.println("[CFG] Catatan: set mode/ip/gw/mask/dns/prefix/id butuh reboot.");
}

static void saveNetConfig() {
  prefsNet.putBool("static", cfg.staticIp);
  prefsNet.putString("ip", cfg.ip.toString());
  prefsNet.putString("gw", cfg.gateway.toString());
  prefsNet.putString("mask", cfg.subnet.toString());
  prefsNet.putString("dns", cfg.dns.toString());
  prefsNet.putString("server", cfg.mqttHost);
  prefsNet.putUShort("port", cfg.mqttPort);
  prefsNet.putString("prefix", cfg.topicPrefix);
  prefsNet.putString("id", cfg.deviceId);
}

// "<prefix>/<deviceId>/<suffix>" mis. heesung/ion-05/status
static void deviceTopic(const char *suffix, char *out, size_t len) {
  snprintf(out, len, "%s/%s/%s", cfg.topicPrefix, cfg.deviceId, suffix);
}

static bool validDeviceId(const char *id) {
  size_t n = strlen(id);
  if (n == 0 || n >= sizeof(cfg.deviceId)) return false;
  for (size_t i = 0; i < n; i++) {
    char c = id[i];
    bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '-' || c == '_';
    if (!ok) return false;
  }
  return true;
}

static void loadConfig() {
  prefsNet.begin("net", false);

  cfg.staticIp = prefsNet.getBool("static", USE_STATIC_IP);

  String value = prefsNet.getString("ip", DEFAULT_STATIC_IP);
  if (!cfg.ip.fromString(value)) cfg.ip.fromString(DEFAULT_STATIC_IP);

  value = prefsNet.getString("gw", DEFAULT_STATIC_GATEWAY);
  if (!cfg.gateway.fromString(value)) cfg.gateway.fromString(DEFAULT_STATIC_GATEWAY);

  value = prefsNet.getString("mask", DEFAULT_STATIC_SUBNET);
  if (!cfg.subnet.fromString(value)) cfg.subnet.fromString(DEFAULT_STATIC_SUBNET);

  value = prefsNet.getString("dns", DEFAULT_STATIC_DNS);
  if (!cfg.dns.fromString(value)) cfg.dns.fromString(DEFAULT_STATIC_DNS);

  value = prefsNet.getString("server", MQTT_HOST);
  value.toCharArray(cfg.mqttHost, sizeof(cfg.mqttHost));

  cfg.mqttPort = prefsNet.getUShort("port", MQTT_PORT);
  if (cfg.mqttPort == 0) cfg.mqttPort = MQTT_PORT;

  value = prefsNet.getString("prefix", TOPIC_PREFIX);
  value.toCharArray(cfg.topicPrefix, sizeof(cfg.topicPrefix));

  value = prefsNet.getString("id", DEVICE_ID);
  value.toCharArray(cfg.deviceId, sizeof(cfg.deviceId));
  if (!validDeviceId(cfg.deviceId)) strlcpy(cfg.deviceId, DEVICE_ID, sizeof(cfg.deviceId));
}

// ------------------------------------------------------------
// Serial CLI
// ------------------------------------------------------------
static void applyMqttServerChange() {
  if (mqtt.connected()) {
    char statusTopic[96];
    deviceTopic("status", statusTopic, sizeof(statusTopic));
    mqtt.publish(statusTopic, "offline", true);
    mqtt.disconnect();
  }
  mqtt.setServer(cfg.mqttHost, cfg.mqttPort);
  lastMqttAttempt = 0;
}

static void handleSet(char *key, char *value) {
  if (key == nullptr || value == nullptr) {
    Serial.println("[CFG] Format: set <key> <value>");
    return;
  }

  if (!strcasecmp(key, "server")) {
    if (strlen(value) >= sizeof(cfg.mqttHost)) {
      Serial.println("[CFG] Alamat server terlalu panjang");
      return;
    }
    strlcpy(cfg.mqttHost, value, sizeof(cfg.mqttHost));
    prefsNet.putString("server", cfg.mqttHost);
    applyMqttServerChange();
    Serial.printf("[CFG] Server MQTT = %s (diterapkan)\n", cfg.mqttHost);
    return;
  }

  if (!strcasecmp(key, "port")) {
    long port = atol(value);
    if (port < 1 || port > 65535) {
      Serial.println("[CFG] Port tidak valid (1-65535)");
      return;
    }
    cfg.mqttPort = (uint16_t)port;
    prefsNet.putUShort("port", cfg.mqttPort);
    applyMqttServerChange();
    Serial.printf("[CFG] Port MQTT = %u (diterapkan)\n", cfg.mqttPort);
    return;
  }

  if (!strcasecmp(key, "mode")) {
    if (!strcasecmp(value, "dhcp")) {
      cfg.staticIp = false;
    } else if (!strcasecmp(value, "static")) {
      cfg.staticIp = true;
    } else {
      Serial.println("[CFG] Mode: dhcp|static");
      return;
    }
    saveNetConfig();
    Serial.println("[CFG] Mode disimpan. Ketik 'reboot' untuk menerapkan.");
    return;
  }

  if (!strcasecmp(key, "ip") || !strcasecmp(key, "gw") ||
      !strcasecmp(key, "mask") || !strcasecmp(key, "dns")) {
    IPAddress parsed;
    if (!parsed.fromString(value)) {
      Serial.println("[CFG] Format IP tidak valid");
      return;
    }
    if (!strcasecmp(key, "ip")) cfg.ip = parsed;
    else if (!strcasecmp(key, "gw")) cfg.gateway = parsed;
    else if (!strcasecmp(key, "mask")) cfg.subnet = parsed;
    else cfg.dns = parsed;

    saveNetConfig();
    Serial.println("[CFG] Disimpan. Ketik 'reboot' untuk menerapkan.");
    return;
  }

  if (!strcasecmp(key, "prefix")) {
    if (strlen(value) >= sizeof(cfg.topicPrefix)) {
      Serial.println("[CFG] Prefix terlalu panjang (maks 31 karakter)");
      return;
    }
    strlcpy(cfg.topicPrefix, value, sizeof(cfg.topicPrefix));
    prefsNet.putString("prefix", cfg.topicPrefix);
    Serial.println("[CFG] Prefix disimpan. Ketik 'reboot' untuk menerapkan.");
    return;
  }

  if (!strcasecmp(key, "id")) {
    if (!validDeviceId(value)) {
      Serial.println("[CFG] ID tidak valid (huruf/angka/-/_, maks 23 karakter)");
      return;
    }
    strlcpy(cfg.deviceId, value, sizeof(cfg.deviceId));
    prefsNet.putString("id", cfg.deviceId);
    Serial.println("[CFG] ID disimpan. Ketik 'reboot' untuk menerapkan.");
    return;
  }

  Serial.println("[CFG] Key tidak dikenal. Ketik 'help'.");
}

static void handleCommand(char *line) {
  char *command = strtok(line, " ");
  if (command == nullptr) return;

  if (!strcasecmp(command, "help")) {
    printHelp();
  } else if (!strcasecmp(command, "show")) {
    printConfig();
  } else if (!strcasecmp(command, "reboot")) {
    Serial.println("[CFG] Reboot...");
    delay(200);
    ESP.restart();
  } else if (!strcasecmp(command, "set")) {
    char *key = strtok(nullptr, " ");
    char *value = strtok(nullptr, " ");
    handleSet(key, value);
  } else {
    Serial.println("[CFG] Perintah tidak dikenal. Ketik 'help'.");
  }
}

static void pollSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      cmdBuf[cmdLen] = '\0';
      if (cmdLen > 0) handleCommand(cmdBuf);
      cmdLen = 0;
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    }
  }
}

// ------------------------------------------------------------
// Relay & topic helpers
// ------------------------------------------------------------
static void relayKey(uint8_t idx, char *out, size_t len) {
  snprintf(out, len, "r%u", (unsigned)(idx + 1));
}

static void applyRelay(uint8_t idx) {
  digitalWrite(RELAY_PINS[idx], relayState[idx] ? RELAY_ON_LEVEL : RELAY_OFF_LEVEL);
}

static void relayTopic(uint8_t idx, const char *suffix, char *out, size_t len) {
  snprintf(out, len, "%s/%s/relay/%u/%s", cfg.topicPrefix, cfg.deviceId,
           (unsigned)(idx + 1), suffix);
}

static void publishRelayState(uint8_t idx) {
  relayTopic(idx, "state", topicBuf, sizeof(topicBuf));
  mqtt.publish(topicBuf, relayState[idx] ? "ON" : "OFF", true);
  Serial.printf("[MQTT] %s -> %s\n", topicBuf, relayState[idx] ? "ON" : "OFF");
}

static void publishHeartbeat() {
  char relays[32];
  int pos = 0;
  for (uint8_t i = 0; i < RELAY_COUNT && pos < (int)sizeof(relays) - 4; i++) {
    pos += snprintf(relays + pos, sizeof(relays) - pos, "%s%d",
                    i == 0 ? "" : ",", relayState[i] ? 1 : 0);
  }

  char payload[192];
  snprintf(payload, sizeof(payload),
           "{\"id\":\"%s\",\"uptime_s\":%lu,\"ip\":\"%s\",\"mac\":\"%s\","
           "\"relay_count\":%u,\"relays\":[%s]}",
           cfg.deviceId, millis() / 1000UL, ETH.localIP().toString().c_str(),
           ETH.macAddress().c_str(), (unsigned)RELAY_COUNT, relays);

  deviceTopic("heartbeat", topicBuf, sizeof(topicBuf));
  mqtt.publish(topicBuf, payload);
}

static void setRelay(uint8_t idx, bool on) {
  if (idx >= RELAY_COUNT) return;

  relayState[idx] = on;
  applyRelay(idx);

  char key[4];
  relayKey(idx, key, sizeof(key));
  prefsRelay.putBool(key, on);

  if (mqtt.connected()) {
    publishRelayState(idx);
  }
}

// ------------------------------------------------------------
// MQTT
// ------------------------------------------------------------
static void onMqttMessage(char *topic, byte *payload, unsigned int len) {
  char expected[96];

  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    relayTopic(i, "set", expected, sizeof(expected));
    if (strcmp(topic, expected) != 0) continue;

    char value[16];
    unsigned int copyLen = len < sizeof(value) - 1 ? len : sizeof(value) - 1;
    memcpy(value, payload, copyLen);
    value[copyLen] = '\0';

    String command(value);
    command.trim();
    command.toUpperCase();

    if (command == "ON" || command == "1" || command == "TRUE") {
      setRelay(i, true);
    } else if (command == "OFF" || command == "0" || command == "FALSE") {
      setRelay(i, false);
    } else if (command == "TOGGLE") {
      setRelay(i, !relayState[i]);
    } else {
      Serial.printf("[MQTT] Perintah tidak dikenal: %s\n", value);
    }
    return;
  }
}

static void onMqttConnect() {
  Serial.printf("[MQTT] Terhubung ke %s:%u\n", cfg.mqttHost, cfg.mqttPort);

  char statusTopic[96];
  deviceTopic("status", statusTopic, sizeof(statusTopic));
  mqtt.publish(statusTopic, "online", true);

  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    publishRelayState(i);

    relayTopic(i, "set", topicBuf, sizeof(topicBuf));
    mqtt.subscribe(topicBuf);
    Serial.printf("[MQTT] Subscribe %s\n", topicBuf);
  }

  publishHeartbeat();
}

static void connectMqtt() {
  char statusTopic[96];
  deviceTopic("status", statusTopic, sizeof(statusTopic));

  // Client id harus unik per board, kalau tidak broker memutus board lain.
  char clientId[40];
  snprintf(clientId, sizeof(clientId), "wt32-%s", cfg.deviceId);

  bool ok;
  if (strlen(MQTT_USER) > 0) {
    ok = mqtt.connect(clientId, MQTT_USER, MQTT_PASS,
                      statusTopic, 0, true, "offline");
  } else {
    ok = mqtt.connect(clientId, nullptr, nullptr,
                      statusTopic, 0, true, "offline");
  }

  if (ok) {
    onMqttConnect();
  } else {
    Serial.printf("[MQTT] Gagal connect, rc=%d\n", mqtt.state());
  }
}

// ------------------------------------------------------------
// Ethernet
// ------------------------------------------------------------
static void onNetworkEvent(WiFiEvent_t event) {
  switch (event) {
    case ARDUINO_EVENT_ETH_START:
      Serial.println("[ETH] Start");
      ETH.setHostname(cfg.deviceId);
      break;
    case ARDUINO_EVENT_ETH_CONNECTED:
      Serial.println("[ETH] Link up");
      break;
    case ARDUINO_EVENT_ETH_GOT_IP:
      ethUp = true;
      Serial.printf("[ETH] IP: %s\n", ETH.localIP().toString().c_str());
      break;
    case ARDUINO_EVENT_ETH_DISCONNECTED:
      ethUp = false;
      Serial.println("[ETH] Link down");
      break;
    default:
      break;
  }
}

// ------------------------------------------------------------
// Setup / Loop
// ------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== WT32-ETH01 Relay Controller ===");

  loadConfig();
  printConfig();

  prefsRelay.begin("relays", false);
  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    pinMode(RELAY_PINS[i], OUTPUT);

    char key[4];
    relayKey(i, key, sizeof(key));
    relayState[i] = prefsRelay.getBool(key, false);
    applyRelay(i);
  }

  WiFi.onEvent(onNetworkEvent);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  // Core ESP32 3.x: begin(type, phy_addr, mdc, mdio, power, clk_mode)
  ETH.begin(ETH_PHY_LAN8720, WT_ETH_PHY_ADDR, WT_ETH_MDC, WT_ETH_MDIO,
            WT_ETH_POWER, ETH_CLOCK_GPIO0_IN);
#else
  // Core ESP32 2.x: begin(phy_addr, power, mdc, mdio, type, clk_mode)
  ETH.begin(WT_ETH_PHY_ADDR, WT_ETH_POWER, WT_ETH_MDC, WT_ETH_MDIO,
            ETH_PHY_LAN8720, ETH_CLOCK_GPIO0_IN);
#endif

  if (cfg.staticIp) {
    ETH.config(cfg.ip, cfg.gateway, cfg.subnet, cfg.dns);
    Serial.println("[ETH] Mode IP statis");
  } else {
    Serial.println("[ETH] Mode IP DHCP");
  }

  mqtt.setServer(cfg.mqttHost, cfg.mqttPort);
  mqtt.setCallback(onMqttMessage);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(30);
  mqtt.setSocketTimeout(5);

  Serial.println("[CFG] Ketik 'help' untuk mengubah IP/server tanpa upload ulang.");
}

void loop() {
  pollSerial();

  if (ethUp) {
    if (!mqtt.connected()) {
      if (mqttWasConnected) {
        Serial.println("[MQTT] Koneksi terputus");
        mqttWasConnected = false;
      }
      if (millis() - lastMqttAttempt >= MQTT_RETRY_INTERVAL_MS) {
        lastMqttAttempt = millis();
        connectMqtt();
      }
    } else {
      if (!mqttWasConnected) {
        mqttWasConnected = true;
        lastHeartbeat = millis();
      }
      mqtt.loop();

      if (millis() - lastHeartbeat >= HEARTBEAT_INTERVAL_MS) {
        lastHeartbeat = millis();
        publishHeartbeat();
      }
    }
  }

  delay(5);
}
