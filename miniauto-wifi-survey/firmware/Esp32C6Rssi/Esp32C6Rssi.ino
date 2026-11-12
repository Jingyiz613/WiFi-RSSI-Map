/*
 * ESP32-C6 Wi-Fi RSSI 采集固件
 *
 * 用途：把 ESP32-C6 开发板当作移动的 RSSI 探针，放在网格点上，
 *       通过 HTTP 读取当前关联 AP 的 RSSI。
 *
 * 端点：GET http://<ip>:62587/espectre/v1/wifi
 * 串口：115200，发送 STATUS 后回车可打印当前状态
 *
 * 依赖：esp32 core 3.x（Arduino core 2.x 不支持 C6）
 *
 * Wi-Fi 配置：
 *   将 secrets.example.h 复制为同目录下的 secrets.h 并填写；
 *   secrets.h 已加入 .gitignore，不应提交真实凭据。
 *   未提供 secrets.h 时使用下方演示配置，仅用于本地联调。
 */

#include <WiFi.h>
#include <WebServer.h>

#if __has_include("secrets.h")
#include "secrets.h"
#endif

#ifndef WIFI_SSID
#define WIFI_SSID "miniauto"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS "miniauto123"
#endif

static const char *kSsid = WIFI_SSID;
static const char *kPass = WIFI_PASS;
static const uint16_t kHttpPort = 62587;

WebServer server(kHttpPort);

static String ipStr() {
  return WiFi.localIP().toString();
}

static void handleWifi() {
  String json = "{";
  json += "\"firmware\":\"Esp32C6-RSSI-1.0\",";
  json += "\"connected\":";
  json += (WiFi.status() == WL_CONNECTED) ? "true" : "false";
  json += ",\"uptime_ms\":";
  json += String((unsigned long)millis());
  json += ",\"ssid\":\"";
  json += WiFi.SSID();
  json += "\",\"bssid\":\"";
  json += WiFi.BSSIDstr();
  json += "\",\"channel\":";
  json += String(WiFi.channel());
  json += ",\"band\":\"2.4GHz\",\"rssi_dbm\":";
  json += String(WiFi.RSSI());
  json += ",\"ip\":\"";
  json += ipStr();
  json += "\"}";
  server.send(200, "application/json", json);
}

static void handleRoot() {
  server.send(200, "text/plain", "Esp32C6-RSSI ready\n");
}

static void printStatus() {
  Serial.print("{\"firmware\":\"Esp32C6-RSSI-1.0\",\"connected\":");
  Serial.print((WiFi.status() == WL_CONNECTED) ? "true" : "false");
  Serial.print(",\"uptime_ms\":");
  Serial.print((unsigned long)millis());
  Serial.print(",\"ssid\":\"");
  Serial.print(WiFi.SSID());
  Serial.print("\",\"bssid\":\"");
  Serial.print(WiFi.BSSIDstr());
  Serial.print("\",\"channel\":");
  Serial.print(WiFi.channel());
  Serial.print(",\"rssi_dbm\":");
  Serial.print(WiFi.RSSI());
  Serial.print(",\"ip\":\"");
  Serial.print(ipStr());
  Serial.println("\"}");
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("Esp32C6-RSSI starting");

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(kSsid, kPass);

  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 15000) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[WiFi] connected, ip=");
    Serial.println(ipStr());
  } else {
    Serial.println("[WiFi] not connected yet, will keep retrying");
  }

  server.on("/", handleRoot);
  server.on("/espectre/v1/wifi", handleWifi);
  server.begin();
  Serial.print("[HTTP] port ");
  Serial.println(kHttpPort);
}

void loop() {
  server.handleClient();

  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.equalsIgnoreCase("STATUS")) {
      printStatus();
    } else if (line.length() > 0) {
      Serial.println("[CMD] unknown, use STATUS");
    }
  }

  static unsigned long lastCheck = 0;
  if (millis() - lastCheck > 10000) {
    lastCheck = millis();
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("[WiFi] reconnecting...");
      WiFi.reconnect();
    }
  }
}
