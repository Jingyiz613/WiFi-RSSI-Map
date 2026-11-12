#include "WifiSurvey.h"
#include "AutoMotion.h"
#include "SurveyAuto.h"
#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_http_server.h>
#include <cJSON.h>

namespace {
httpd_handle_t server = nullptr;
String serialLine;
bool discardLine = false;
uint32_t lastStatus = 0;

// A current associated-AP snapshot, not an independent packet sample or CSI.
char* statusJson() {
  wifi_ap_record_t ap{};
  const bool connected = WiFi.status() == WL_CONNECTED &&
                         esp_wifi_sta_get_ap_info(&ap) == ESP_OK;
  cJSON* doc = cJSON_CreateObject();
  if (!doc) return nullptr;
  cJSON_AddStringToObject(doc, "firmware", "MiniAutoPro-WiFiRSSI-1.0");
  cJSON_AddBoolToObject(doc, "connected", connected);
  cJSON_AddNumberToObject(doc, "uptime_ms", millis());
  if (connected) {
    char bssid[18];
    snprintf(bssid, sizeof(bssid), "%02X:%02X:%02X:%02X:%02X:%02X",
             ap.bssid[0], ap.bssid[1], ap.bssid[2],
             ap.bssid[3], ap.bssid[4], ap.bssid[5]);
    cJSON_AddStringToObject(doc, "ssid", reinterpret_cast<char*>(ap.ssid));
    cJSON_AddStringToObject(doc, "bssid", bssid);
    cJSON_AddNumberToObject(doc, "channel", ap.primary);
    cJSON_AddStringToObject(doc, "band", "2.4GHz");
    cJSON_AddNumberToObject(doc, "rssi_dbm", ap.rssi);
    cJSON_AddStringToObject(doc, "ip", WiFi.localIP().toString().c_str());
  } else {
    cJSON_AddNullToObject(doc, "rssi_dbm");
  }
  char* text = cJSON_PrintUnformatted(doc);
  cJSON_Delete(doc);
  return text;
}

void printStatus() {
  char* text = statusJson();
  if (text) { Serial.println(text); cJSON_free(text); }
}

esp_err_t serveWifi(httpd_req_t* request) {
  char* text = statusJson();
  if (!text) return httpd_resp_send_err(request, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
  httpd_resp_set_type(request, "application/json");
  httpd_resp_set_hdr(request, "Access-Control-Allow-Origin", "https://test.espectre.dev");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  const esp_err_t result = httpd_resp_send(request, text, HTTPD_RESP_USE_STRLEN);
  cJSON_free(text);
  return result;
}

void handleCommand(const String& line) {
  // SURVEY_KEY must be checked before the autonomous module, which owns the
  // SURVEY_* prefix.
  if (line == "SURVEY_KEY") { autoMotionPrintKey(); return; }
  if (surveyAutoHandleSerial(line)) { return; }
  if (line == "STATUS") { printStatus(); return; }
  cJSON* doc = cJSON_Parse(line.c_str());
  if (!doc) { Serial.println("[RSSI] Invalid command"); return; }
  const cJSON* command = cJSON_GetObjectItemCaseSensitive(doc, "cmd");
  const cJSON* ssid = cJSON_GetObjectItemCaseSensitive(doc, "ssid");
  const cJSON* password = cJSON_GetObjectItemCaseSensitive(doc, "password");
  if (cJSON_IsString(command) && strcmp(command->valuestring, "wifi") == 0 &&
      cJSON_IsString(ssid) && cJSON_IsString(password) &&
      strlen(ssid->valuestring) > 0 && strlen(ssid->valuestring) <= 32 &&
      strlen(password->valuestring) <= 63) {
    // Credentials travel only over USB and are stored in the board's Wi-Fi NVS.
    WiFi.begin(ssid->valuestring, password->valuestring);
    Serial.println("[RSSI] Connecting; credentials are not echoed");
  } else {
    Serial.println("[RSSI] Expected wifi command, SSID 1..32 bytes, password 0..63 bytes");
  }
  cJSON_Delete(doc);
}
}  // namespace

void wifiSurveySetup() {
  serialLine.reserve(512);
  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
    if (event == ARDUINO_EVENT_WIFI_STA_CONNECTED) {
      Serial.println("[WIFI-DIAG] Associated with AP");
    } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
      Serial.println("[WIFI-DIAG] Obtained IP");
    } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
      Serial.printf("[WIFI-DIAG] Disconnected reason=%u\n",
                    static_cast<unsigned>(info.wifi_sta_disconnected.reason));
    }
  });
  WiFi.setHostname("minipro-rssi");
  WiFi.persistent(true);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin();  // Reuse provisioned credentials; do not wait for association.
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 62587;
  config.max_open_sockets = 3;
  config.lru_purge_enable = true;
  config.recv_wait_timeout = 1;
  config.send_wait_timeout = 1;
  if (httpd_start(&server, &config) == ESP_OK) {
    httpd_uri_t resource{};
    resource.uri = "/espectre/v1/wifi";
    resource.method = HTTP_GET;
    resource.handler = serveWifi;
    httpd_register_uri_handler(server, &resource);
    autoMotionRegister(server);
    Serial.println("[RSSI] HTTP port 62587 ready; USB STATUS prints current AP RSSI");
  } else {
    Serial.println("[RSSI] HTTP start failed");
  }
}

void wifiSurveyUpdate() {
  // Bounded work per control-loop iteration, including malformed serial input.
  for (unsigned budget = 0; budget < 64 && Serial.available(); ++budget) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\n') {
      if (!discardLine && serialLine.length()) handleCommand(serialLine);
      serialLine = "";
      discardLine = false;
    } else if (c != '\r' && !discardLine) {
      if (serialLine.length() < 511) serialLine += c;
      else { serialLine = ""; discardLine = true; }
    }
  }
  if (millis() - lastStatus >= 5000) {
    lastStatus = millis();
    printStatus();
  }
}
