#include "AutoMotion.h"
#include "FactoryControl.h"
#include "Mecanum.h"
#include "BoardDriver.h"
#include "BleRemoteControl.h"
#include "SurveyAuto.h"
#include <WiFi.h>
#include <Wire.h>
#include <esp_random.h>
#include <cJSON.h>
#include <math.h>

namespace {
enum Phase { IDLE, READY, QUEUED, MOVING, DONE, STOPPED, ERROR, MANUAL };
const char* names[] = {"idle","ready","queued","moving","done","stopped","error","manual"};
portMUX_TYPE lock = portMUX_INITIALIZER_UNLOCKED;
Phase phase = IDLE;
bool owned = false, armPending = false, stopPending = false;
float dx = 0, dy = 0, speed = .08f;
uint32_t commandId = 0, heartbeat = 0, began = 0, timeoutMs = 0, lastPoll = 0;
bool sawRunning = false;
const char* reason = "boot_idle";
char key[33] = {};

void finish(Phase next, const char* why, bool stopMotor = true) {
  if (stopMotor) mecanumStop(true);
  portENTER_CRITICAL(&lock);
  phase = next; owned = false; armPending = false; reason = why;
  portEXIT_CRITICAL(&lock);
}

esp_err_t reply(httpd_req_t* req, bool accepted) {
  Phase p; uint32_t id; const char* why;
  portENTER_CRITICAL(&lock);
  p = phase; id = commandId; why = reason;
  portEXIT_CRITICAL(&lock);
  char body[180];
  snprintf(body,sizeof(body),"{\"auto_version\":1,\"state\":\"%s\",\"command_id\":%lu,\"reason\":\"%s\"}",
           names[p],static_cast<unsigned long>(id),why);
  httpd_resp_set_type(req,"application/json");
  httpd_resp_set_hdr(req,"Cache-Control","no-store");
  if (!accepted) httpd_resp_set_status(req,"409 Conflict");
  return httpd_resp_send(req,body,HTTPD_RESP_USE_STRLEN);
}

esp_err_t command(httpd_req_t* req) {
  char supplied[33] = {};
  if (httpd_req_get_hdr_value_str(req,"X-Survey-Key",supplied,sizeof(supplied)) != ESP_OK ||
      strcmp(supplied,key) != 0) {
    httpd_resp_set_status(req,"403 Forbidden");
    return httpd_resp_send(req,"Control key required",HTTPD_RESP_USE_STRLEN);
  }
  if (req->content_len <= 0 || req->content_len >= 256)
    return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Invalid body size");
  char body[256] = {}; int used = 0;
  while (used < req->content_len) {
    int n = httpd_req_recv(req,body+used,req->content_len-used);
    if (n <= 0) return ESP_FAIL;
    used += n;
  }
  cJSON* doc = cJSON_Parse(body);
  const cJSON* op = cJSON_GetObjectItemCaseSensitive(doc,"op");
  if (!cJSON_IsString(op)) {
    cJSON_Delete(doc);
    return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Missing op");
  }
  const cJSON* x = cJSON_GetObjectItemCaseSensitive(doc,"dx");
  const cJSON* y = cJSON_GetObjectItemCaseSensitive(doc,"dy");
  const cJSON* s = cJSON_GetObjectItemCaseSensitive(doc,"speed");
  const cJSON* id = cJSON_GetObjectItemCaseSensitive(doc,"id");
  bool moveValid = cJSON_IsNumber(x) && cJSON_IsNumber(y) && cJSON_IsNumber(s) && cJSON_IsNumber(id);
  if (moveValid) {
    moveValid = isfinite(x->valuedouble) && isfinite(y->valuedouble) && isfinite(s->valuedouble) &&
      isfinite(id->valuedouble) && id->valuedouble >= 1 && id->valuedouble <= 2147483647 &&
      floor(id->valuedouble) == id->valuedouble && s->valuedouble >= .03 && s->valuedouble <= .12 &&
      ((x->valuedouble == 0) != (y->valuedouble == 0)) &&
      fabs(x->valuedouble)+fabs(y->valuedouble) >= .05 &&
      fabs(x->valuedouble)+fabs(y->valuedouble) <= 1.10;
  }
  bool ok = false;
  portENTER_CRITICAL(&lock);
  if (strcmp(op->valuestring,"stop") == 0) { stopPending = true; ok = true; }
  else if (strcmp(op->valuestring,"arm") == 0 && !owned && !stopPending && !surveyAutoBusy()) {
    owned = true; armPending = true; phase = READY; reason = "armed";
    heartbeat = millis(); ok = true;
  } else if (strcmp(op->valuestring,"heartbeat") == 0) {
    if (owned) heartbeat = millis();
    ok = true;
  } else if (strcmp(op->valuestring,"move") == 0 && moveValid && owned && !armPending &&
             !stopPending && (phase == READY || phase == DONE) && id->valuedouble > commandId) {
    dx=x->valuedouble; dy=y->valuedouble; speed=s->valuedouble;
    commandId=static_cast<uint32_t>(id->valuedouble); phase=QUEUED; reason="queued";
    heartbeat=millis(); ok=true;
  }
  portEXIT_CRITICAL(&lock);
  cJSON_Delete(doc);
  return reply(req,ok);
}

bool boardStatus(uint8_t reg, uint8_t& result) {
  Wire.beginTransmission(EXTEND_I2C_ADDR); Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(static_cast<uint8_t>(EXTEND_I2C_ADDR),static_cast<uint8_t>(1)) != 1) return false;
  result=Wire.read(); return true;
}
}  // namespace

bool autoMotionOwnsControl() {
  portENTER_CRITICAL(&lock); bool result=owned; portEXIT_CRITICAL(&lock); return result;
}

void autoMotionManualOverride() {
  // Called after APP motion has been applied: release ownership without overwriting it.
  if (autoMotionOwnsControl()) finish(MANUAL,"app_takeover",false);
}

void autoMotionPrintKey() {
  Serial.printf("{\"survey_key\":\"%s\"}\n",key);
}

void autoMotionRegister(httpd_handle_t server) {
  for (int i=0;i<4;++i) snprintf(key+i*8,9,"%08lx",static_cast<unsigned long>(esp_random()));
  httpd_uri_t uri{}; uri.uri="/survey/command"; uri.method=HTTP_POST; uri.handler=command;
  httpd_register_uri_handler(server,&uri);
  Serial.println("[AUTO] Idle. USB SURVEY_KEY obtains per-boot control key; never starts by itself.");
}

void autoMotionUpdate() {
  Phase p; bool arm,stop,active; float x,y,v; uint32_t lastBeat;
  portENTER_CRITICAL(&lock);
  p=phase; arm=armPending; armPending=false; stop=stopPending; stopPending=false;
  active=owned; lastBeat=heartbeat; x=dx; y=dy; v=speed;
  portEXIT_CRITICAL(&lock);
  if (stop) { finish(STOPPED,"operator_stop"); return; }
  if (!active) return;
  if (WiFi.status()!=WL_CONNECTED || millis()-lastBeat>2000) {
    finish(ERROR,"connection_or_heartbeat_lost"); return;
  }
  if (arm) { factoryControlPrepareAuto(); return; }
  if (p==QUEUED) {
    uint8_t flags=0;
    if (!boardStatus(REG_MOTOR_LOCK,flags) || (flags & 0xe0)) {
      finish(ERROR,"motor_board_fault"); return;
    }
    // Use the tested APP mapping, which is rotated relative to driver comments.
    const uint8_t direction = x>0 ? 2 : x<0 ? 6 : y>0 ? 0 : 4;
    float vx,vy;
    bleRemoteControlGetDirection(direction,vx,vy);
    if (!mecanumMoveDistance(vx*v,vy*v,0,fabsf(x)+fabsf(y))) {
      finish(ERROR,"move_rejected"); return;
    }
    began=millis(); sawRunning=false; lastPoll=0;
    timeoutMs=static_cast<uint32_t>((fabsf(x)+fabsf(y))/v*2000)+3000;
    portENTER_CRITICAL(&lock); phase=MOVING; reason="encoder_move"; portEXIT_CRITICAL(&lock);
  } else if (p==MOVING && millis()-lastPoll>=50) {
    lastPoll=millis(); uint8_t status=0,flags=0;
    if (!boardStatus(REG_DIST_STATUS,status) || !boardStatus(REG_MOTOR_LOCK,flags) || (flags&0xe0)) {
      finish(ERROR,"feedback_unavailable_or_fault"); return;
    }
    if (status&0x0f) sawRunning=true;
    if (sawRunning && !(status&0x0f) && millis()-began>=300) {
      mecanumStop(true);
      portENTER_CRITICAL(&lock); phase=DONE; reason="encoder_target_done"; portEXIT_CRITICAL(&lock);
    } else if ((!sawRunning && millis()-began>500) || millis()-began>timeoutMs) {
      finish(ERROR,"move_start_or_completion_timeout");
    }
  }
}
