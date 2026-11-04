#include "SurveyAuto.h"

#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <Preferences.h>
#include <math.h>
#include <string.h>

#include "BoardDriver.h"
#include "BleRemoteControl.h"
#include "FactoryControl.h"
#include "Mecanum.h"

namespace {

constexpr int kMaxSteps = 160;
constexpr int kMaxSamples = 32;
constexpr uint8_t kRssiInvalid = 127;      // 127 表示该次没读到（Wi-Fi 未关联）
constexpr uint32_t kSamplePeriodMs = 100;  // 相邻两次 RSSI 采样的间隔
constexpr uint32_t kMoveStartGraceMs = 600;
constexpr uint16_t kBatteryWarnMv = 6500;
constexpr uint16_t kBatteryStopMv = 6000;

enum Phase : uint8_t { P_IDLE, P_PLANNED, P_RUNNING, P_DONE, P_ERROR, P_ABORTED };
enum StepState : uint8_t { S_MOVE_START, S_MOVE_WAIT, S_DWELL, S_SAMPLE, S_STORE };

struct StepPlan {
  char label[12];
  float dx;
  float dy;
  uint8_t sample;  // 1=到达后采样；0=只经过（绕行中间格）
};

struct StepResult {
  int8_t rssi[kMaxSamples];
  uint8_t n;
  uint8_t ok;
  uint16_t batt;
};

struct Meta {
  float speed;
  uint32_t dwell;
  uint8_t samples;
  uint8_t count;
  uint8_t done;
  uint8_t phase;
};

StepPlan gPlan[kMaxSteps];
StepResult gRes[kMaxSteps];

Meta gMeta = {0.08f, 3000, 10, 0, 0, P_IDLE};
int gReceived = 0;
volatile bool gAbortRequest = false;

StepState gStep = S_MOVE_START;
int gIndex = 0;
uint32_t gMark = 0;
uint32_t gNextSample = 0;
uint8_t gSampleIdx = 0;
bool gSawRunning = false;
uint8_t gMoveFails = 0;
uint16_t gLastBatt = 0;

bool isRunning() { return gMeta.phase == P_RUNNING; }

const char* phaseName(uint8_t p) {
  switch (p) {
    case P_IDLE: return "idle";
    case P_PLANNED: return "planned";
    case P_RUNNING: return "running";
    case P_DONE: return "done";
    case P_ERROR: return "error";
    case P_ABORTED: return "aborted";
    default: return "unknown";
  }
}

// ---------------------------------------------------------------- NVS

void saveMeta() {
  Preferences prefs;
  if (!prefs.begin("svy", false)) return;
  prefs.putBytes("meta", &gMeta, sizeof(gMeta));
  prefs.end();
}

void savePlanAll() {
  Preferences prefs;
  if (!prefs.begin("svy", false)) return;
  prefs.putBytes("meta", &gMeta, sizeof(gMeta));
  for (int i = 0; i < gMeta.count; ++i) {
    char key[8];
    snprintf(key, sizeof(key), "p%d", i);
    prefs.putBytes(key, &gPlan[i], sizeof(StepPlan));
  }
  prefs.end();
}

void saveResult(int i) {
  Preferences prefs;
  if (!prefs.begin("svy", false)) return;
  char key[8];
  snprintf(key, sizeof(key), "r%d", i);
  prefs.putBytes(key, &gRes[i], sizeof(StepResult));
  prefs.putBytes("meta", &gMeta, sizeof(gMeta));
  prefs.end();
}

void loadFromNvs() {
  Preferences prefs;
  if (!prefs.begin("svy", true)) return;
  Meta m{};
  size_t got = prefs.getBytes("meta", &m, sizeof(m));
  if (got == sizeof(m) && m.count > 0 && m.count <= kMaxSteps) {
    gMeta = m;
    for (int i = 0; i < gMeta.count; ++i) {
      char key[8];
      snprintf(key, sizeof(key), "p%d", i);
      prefs.getBytes(key, &gPlan[i], sizeof(StepPlan));
      snprintf(key, sizeof(key), "r%d", i);
      prefs.getBytes(key, &gRes[i], sizeof(StepResult));
    }
    if (gMeta.phase == P_RUNNING) {
      // 上次断电/复位时还在跑，标记为中止，已采到的点仍然可以导出。
      gMeta.phase = P_ABORTED;
    }
  }
  prefs.end();
}

void clearAll() {
  Preferences prefs;
  if (!prefs.begin("svy", false)) return;
  prefs.clear();
  prefs.end();
  memset(&gMeta, 0, sizeof(gMeta));
  gMeta.speed = 0.08f;
  gMeta.dwell = 3000;
  gMeta.samples = 10;
  gMeta.phase = P_IDLE;
  memset(gPlan, 0, sizeof(gPlan));
  memset(gRes, 0, sizeof(gRes));
  gReceived = 0;
  gIndex = 0;
  gStep = S_MOVE_START;
  gAbortRequest = false;
}

// ---------------------------------------------------------------- 采样

int8_t readRssi() {
  wifi_ap_record_t ap{};
  if (WiFi.status() == WL_CONNECTED && esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
    return static_cast<int8_t>(ap.rssi);
  }
  return static_cast<int8_t>(kRssiInvalid);
}

void stopMotion() {
  gSawRunning = false;
  gStep = S_MOVE_START;
}

void finish(Phase next) {
  mecanumStop(true);
  gMeta.phase = next;
  saveMeta();
  Serial.printf("SURVEY_STATE,%s,%d,%d,,%u\n", phaseName(next), gMeta.done, gMeta.count,
                static_cast<unsigned>(gLastBatt));
}

void printStatus() {
  Serial.printf("SURVEY_STATE,%s,%d,%d,%s,%u\n", phaseName(gMeta.phase), gMeta.done,
                gMeta.count,
                (gMeta.phase == P_RUNNING && gIndex < gMeta.count) ? gPlan[gIndex].label : "",
                static_cast<unsigned>(gLastBatt));
}

void printDump() {
  Serial.printf("SURVEY_RESULT,%d,%d,%u\n", gMeta.count, gMeta.done,
                static_cast<unsigned>(gMeta.samples));
  for (int i = 0; i < gMeta.count; ++i) {
    const StepPlan& p = gPlan[i];
    const StepResult& r = gRes[i];
    Serial.printf("SURVEY_R,%s,%.4f,%.4f,%u,%u,%u", p.label, p.dx, p.dy,
                  static_cast<unsigned>(r.n), static_cast<unsigned>(r.ok),
                  static_cast<unsigned>(r.batt));
    for (int s = 0; s < r.n && s < kMaxSamples; ++s) {
      Serial.printf(",%d", static_cast<int>(r.rssi[s]));
    }
    Serial.println();
  }
  Serial.println("SURVEY_DUMP_END");
}

// ---------------------------------------------------------------- 执行

void beginMove() {
  uint8_t flags = readReg8(REG_MOTOR_LOCK);
  if ((flags & 0xE0) != 0) {
    Serial.println("[SURVEY] motor board fault");
    finish(P_ERROR);
    return;
  }

  const StepPlan& p = gPlan[gIndex];
  const float dist = fabsf(p.dx) + fabsf(p.dy);
  if (dist < 0.001f) {
    if (!p.sample) {
      gMeta.done = static_cast<uint8_t>(gIndex + 1);
      ++gIndex;
      return;
    }
    gStep = S_DWELL;
    gMark = millis();
    return;
  }

  // 与 AutoMotion 保持同一套方向映射：x 向右为正，y 向前为正。
  const uint8_t direction = p.dx > 0 ? 2 : p.dx < 0 ? 6 : p.dy > 0 ? 0 : 4;
  float vx = 0, vy = 0;
  bleRemoteControlGetDirection(direction, vx, vy);

  if (!mecanumMoveDistance(vx * gMeta.speed, vy * gMeta.speed, 0, dist)) {
    Serial.println("[SURVEY] move rejected");
    finish(P_ERROR);
    return;
  }

  gSawRunning = false;
  gMoveFails = 0;
  gStep = S_MOVE_WAIT;
  gMark = millis();
}

void stepMachine() {
  if (gMeta.phase != P_RUNNING) return;
  if (gAbortRequest) {
    gAbortRequest = false;
    Serial.println("[SURVEY] aborted by host");
    finish(P_ABORTED);
    return;
  }
  if (gIndex >= gMeta.count) {
    finish(P_DONE);
    return;
  }

  const uint32_t now = millis();

  switch (gStep) {
    case S_MOVE_START:
      beginMove();
      break;

    case S_MOVE_WAIT: {
      const float dist = fabsf(gPlan[gIndex].dx) + fabsf(gPlan[gIndex].dy);
      const uint32_t timeoutMs =
          static_cast<uint32_t>(dist / (gMeta.speed > 0.01f ? gMeta.speed : 0.08f) * 2000.0f) +
          4000;
      if (now - gMark < 50) return;

      uint8_t status = readReg8(REG_DIST_STATUS);
      uint8_t flags = readReg8(REG_MOTOR_LOCK);
      if ((flags & 0xE0) != 0) {
        Serial.println("[SURVEY] motor board fault while moving");
        finish(P_ERROR);
        return;
      }
      if (status & 0x0F) gSawRunning = true;

      if (gSawRunning && (status & 0x0F) == 0 && now - gMark >= 300) {
        mecanumStop(true);
        gLastBatt = readBatteryMv();
        if (gPlan[gIndex].sample) {
          gRes[gIndex].ok = 1;
          gStep = S_DWELL;
          gMark = now;
        } else {
          // 绕行中间格：不停留、不采样，直接走下一步。
          gMeta.done = static_cast<uint8_t>(gIndex + 1);
          ++gIndex;
          gStep = S_MOVE_START;
        }
      } else if ((!gSawRunning && now - gMark > kMoveStartGraceMs) ||
                 (now - gMark > timeoutMs)) {
        ++gMoveFails;
        Serial.printf("[SURVEY] move %d failed (fails=%u)\n", gIndex,
                      static_cast<unsigned>(gMoveFails));
        mecanumStop(true);
        if (gMoveFails >= 2) {
          finish(P_ERROR);
        } else {
          // 允许原地重试一次。
          gStep = S_MOVE_START;
          gMark = now;
        }
      }
      break;
    }

    case S_DWELL:
      if (now - gMark >= gMeta.dwell) {
        gSampleIdx = 0;
        gNextSample = now;
        gStep = S_SAMPLE;
      }
      break;

    case S_SAMPLE:
      if (now >= gNextSample) {
        gRes[gIndex].rssi[gSampleIdx++] = readRssi();
        gNextSample += kSamplePeriodMs;
        if (gSampleIdx >= gMeta.samples) {
          gRes[gIndex].n = gSampleIdx;
          gStep = S_STORE;
        }
      }
      break;

    case S_STORE: {
      gRes[gIndex].batt = readBatteryMv();
      gLastBatt = gRes[gIndex].batt;
      gMeta.done = static_cast<uint8_t>(gIndex + 1);
      saveResult(gIndex);
      Serial.printf("SURVEY_STATE,running,%d,%d,%s,%u\n", gMeta.done, gMeta.count,
                    gPlan[gIndex].label, static_cast<unsigned>(gRes[gIndex].batt));

      if (gRes[gIndex].batt > 0 && gRes[gIndex].batt < kBatteryWarnMv) {
        Serial.printf("[SURVEY] low battery %u mV\n",
                      static_cast<unsigned>(gRes[gIndex].batt));
        if (gRes[gIndex].batt < kBatteryStopMv) {
          finish(P_ERROR);
          return;
        }
      }

      ++gIndex;
      gStep = S_MOVE_START;
      break;
    }
  }
}

// ---------------------------------------------------------------- 命令解析

bool parseBegin(const String& line) {
  float speed = 0;
  unsigned long dwell = 0;
  int samples = 0, count = 0;
  if (sscanf(line.c_str(), "SURVEY_BEGIN %f %lu %d %d", &speed, &dwell, &samples, &count) != 4) {
    Serial.println("[SURVEY] usage: SURVEY_BEGIN <speed_mps> <dwell_ms> <samples> <count>");
    return true;
  }
  if (isRunning()) {
    Serial.println("[SURVEY] busy");
    return true;
  }
  if (speed < 0.03f || speed > 0.12f || samples < 1 || samples > kMaxSamples || count < 1 ||
      count > kMaxSteps) {
    Serial.println("[SURVEY] bad parameters");
    return true;
  }
  gMeta.speed = speed;
  gMeta.dwell = static_cast<uint32_t>(dwell);
  gMeta.samples = static_cast<uint8_t>(samples);
  gMeta.count = static_cast<uint8_t>(count);
  gMeta.done = 0;
  gMeta.phase = P_IDLE;
  gReceived = 0;
  gIndex = 0;
  memset(gPlan, 0, sizeof(gPlan));
  memset(gRes, 0, sizeof(gRes));
  Serial.printf("SURVEY_ACK,BEGIN,%d\n", count);
  return true;
}

bool parseStep(const String& line) {
  char label[16] = {0};
  float dx = 0, dy = 0;
  int sample = 1;
  const int matched = sscanf(line.c_str(), "SURVEY_STEP %15s %f %f %d", label, &dx, &dy, &sample);
  if (matched < 3) {
    Serial.println("[SURVEY] usage: SURVEY_STEP <label> <dx_m> <dy_m> [sample]");
    return true;
  }
  if (gMeta.count == 0) {
    Serial.println("[SURVEY] send SURVEY_BEGIN first");
    return true;
  }
  if (gReceived >= gMeta.count) {
    Serial.println("[SURVEY] too many steps");
    return true;
  }
  const float sum = fabsf(dx) + fabsf(dy);
  const bool singleAxis = (dx == 0.0f) != (dy == 0.0f);
  if (sum > 1.10f || (sum > 0.0001f && (!singleAxis || sum < 0.05f))) {
    Serial.printf("[SURVEY] step %d out of range\n", gReceived);
    return true;
  }
  StepPlan& p = gPlan[gReceived];
  strncpy(p.label, label, sizeof(p.label) - 1);
  p.dx = dx;
  p.dy = dy;
  p.sample = (sample != 0) ? 1 : 0;
  ++gReceived;
  Serial.printf("SURVEY_ACK,STEP,%d\n", gReceived);
  return true;
}

bool parseEnd() {
  if (gMeta.count == 0 || gReceived != gMeta.count) {
    Serial.printf("[SURVEY] plan incomplete: got %d / %d\n", gReceived, gMeta.count);
    return true;
  }
  gMeta.phase = P_PLANNED;
  gMeta.done = 0;
  savePlanAll();
  Serial.printf("SURVEY_ACK,END,%d\n", gMeta.count);
  return true;
}

bool parseRun() {
  if (gMeta.phase == P_RUNNING) {
    Serial.println("[SURVEY] already running");
    return true;
  }
  if (gMeta.count == 0 || gReceived != gMeta.count) {
    Serial.println("[SURVEY] no complete plan");
    return true;
  }
  gLastBatt = readBatteryMv();
  if (gLastBatt > 0 && gLastBatt < kBatteryStopMv) {
    Serial.printf("[SURVEY] refuse to start: battery %u mV\n", static_cast<unsigned>(gLastBatt));
    return true;
  }

  factoryControlPrepareAuto();
  for (int i = 0; i < gMeta.count; ++i) {
    memset(&gRes[i], 0, sizeof(StepResult));
  }
  gMeta.done = 0;
  gMeta.phase = P_RUNNING;
  gIndex = 0;
  gMoveFails = 0;
  gSawRunning = false;
  gAbortRequest = false;
  gStep = S_MOVE_START;
  gMark = millis();
  saveMeta();
  Serial.printf("SURVEY_STATE,running,0,%d,%s,%u\n", gMeta.count, gPlan[0].label,
                static_cast<unsigned>(gLastBatt));
  return true;
}

}  // namespace

// ---------------------------------------------------------------- 对外接口

void surveyAutoSetup() {
  loadFromNvs();
  Serial.printf("[SURVEY] autonomous mode ready, plan=%d phase=%s\n", gMeta.count,
                phaseName(gMeta.phase));
  Serial.println(
      "[SURVEY] USB only: SURVEY_BEGIN / SURVEY_STEP / SURVEY_END / SURVEY_RUN / "
      "SURVEY_DUMP / SURVEY_ABORT / SURVEY_CLEAR");
}

void surveyAutoUpdate() { stepMachine(); }

bool surveyAutoBusy() { return gMeta.phase == P_RUNNING; }

void surveyAutoAbort() { gAbortRequest = true; }

bool surveyAutoHandleSerial(const String& line) {
  if (!line.startsWith("SURVEY_")) return false;

  if (line.startsWith("SURVEY_BEGIN")) return parseBegin(line);
  if (line.startsWith("SURVEY_STEP")) return parseStep(line);
  if (line == "SURVEY_END") return parseEnd();
  if (line == "SURVEY_RUN") return parseRun();
  if (line == "SURVEY_STATUS") {
    printStatus();
    return true;
  }
  if (line == "SURVEY_ABORT") {
    surveyAutoAbort();
    Serial.println("SURVEY_ACK,ABORT");
    return true;
  }
  if (line == "SURVEY_DUMP") {
    printDump();
    return true;
  }
  if (line == "SURVEY_CLEAR") {
    if (isRunning()) {
      Serial.println("[SURVEY] busy");
      return true;
    }
    clearAll();
    Serial.println("SURVEY_ACK,CLEAR");
    return true;
  }
  Serial.println("[SURVEY] unknown command");
  return true;
}
