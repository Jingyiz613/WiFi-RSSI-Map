#include "FactoryControl.h"
#include "AutoMotion.h"

#include <Arduino.h>
#include <math.h>
#include <string.h>
#include <Wire.h>

#include "ArmControl.h"
#include "BleRemoteControl.h"
#include "BoardDriver.h"
#include "LineFollower.h"
#include "Mecanum.h"
#include "Ultrasound.h"
#include "WonderLLM.h"

namespace {

constexpr int kExtendI2cSda = 26;
constexpr int kExtendI2cScl = 27;

constexpr float kAxleWidthM = 0.140f;
constexpr float kAxleLengthM = 0.145f;
constexpr float kWheelDiameterM = 0.065f;

constexpr float kAppMaxLinearSpeedMps = 0.6f;
constexpr float kAppMaxAngularSpeedRadS = 3.0f;
constexpr float kWonderMoveSpeedMps = 0.15f;
constexpr float kWonderStrafeDistanceCompensation = 1.2f;
constexpr float kWonderTurnSpeedRadS = 1.2f;

constexpr float kAvoidForwardSpeedMps = 0.12f;
constexpr float kAvoidTurnSpeedRadS = 1.05f;
constexpr uint16_t kDefaultAvoidDistanceMm = 300;
constexpr uint16_t kAvoidHysteresisMm = 100;
constexpr uint8_t kDistanceFilterSize = 5;
constexpr uint32_t kAvoidUpdateIntervalMs = 60;
constexpr uint32_t kDistanceColorUpdateIntervalMs = 250;

constexpr float kLineForwardSpeedMps = 0.12f;
constexpr float kLineTurnForwardMps = 0.08f;
constexpr float kLineTurnSpeedRadS = 0.35f;
constexpr float kLineSharpTurnSpeedRadS = 0.80f;
constexpr uint32_t kLineUpdateIntervalMs = 40;
constexpr uint32_t kVisionNudgeDurationMs = 1000;
constexpr uint32_t kVisionRetryDelayMs = 1500;
constexpr uint32_t kVisionResponseTimeoutMs = 8000;

constexpr uint8_t kClawLiftChannel = 0;
constexpr uint8_t kClawGripChannel = 1;
constexpr uint16_t kServoMinPulseUs = 500;
constexpr uint16_t kServoMaxPulseUs = 2500;
constexpr uint16_t kClawDefaultMoveTimeMs = 500;
constexpr uint16_t kModeArmLowerTimeMs = 600;
constexpr uint8_t kClawCloseAngleDeg = 95;
constexpr uint8_t kClawClampAngleDeg = 105;
constexpr uint8_t kClawOpenAngleDeg = 180;
constexpr uint8_t kClawLiftDownAngleDeg = ARM_HEIGHT_MIN_ANGLE;
constexpr uint8_t kClawLiftUpAngleDeg = ARM_HEIGHT_MAX_ANGLE;

constexpr int16_t kTransportPickupForwardCm = 10;
constexpr int16_t kTransportPlaceForwardCm = 15;
constexpr int16_t kTransportTurnAngleDeg = 90;

constexpr uint32_t kWonderRetryIntervalMs = 5000;
constexpr uint32_t kMotionPollIntervalMs = 50;
constexpr uint32_t kMotionMinimumRunMs = 500;
constexpr uint32_t kMotionStartTimeoutMs = 300;

enum RunningMode : uint8_t {
  RUNNING_MODE_IDLE = 0,
  RUNNING_MODE_AVOID = 1,
  RUNNING_MODE_LINE_PATROL = 2,
  RUNNING_MODE_SMART_LINE_PATROL = 3,
  RUNNING_MODE_TRANSPORT = 5,
};

enum WonderActionKind : uint8_t {
  WONDER_ACTION_NONE = 0,
  WONDER_ACTION_MOVE,
  WONDER_ACTION_TURN,
  WONDER_ACTION_BUZZER,
  WONDER_ACTION_CLAW,
  WONDER_ACTION_MODE_START,
  WONDER_ACTION_VISION_NUDGE,
  WONDER_ACTION_TRANSPORT,
};

enum AvoidMotionMode : uint8_t {
  AVOID_MOTION_NONE = 0,
  AVOID_MOTION_FORWARD,
  AVOID_MOTION_TURN,
};

struct WonderActionState {
  WonderActionKind kind;
  uint32_t startedMs;
  uint32_t timeoutMs;
  uint32_t nextUpdateMs;
  bool motionStarted;
  int16_t turnAngleDeg;
  uint8_t buzzerRemaining;
  bool buzzerOn;
  uint8_t targetMode;
  uint8_t transportStep;
  bool transportWaitingMotion;
};

static Ultrasound s_ultrasound;
static LineFollower s_lineFollower;
static WonderActionState s_wonderAction = {};
static bool s_wonderReady = false;
static uint32_t s_lastWonderRetryMs = 0;
static int16_t s_headingAngleDeg = 0;
static bool s_pollAppFirst = true;
static uint8_t s_runningMode = RUNNING_MODE_IDLE;
static uint16_t s_avoidDistanceMm = kDefaultAvoidDistanceMm;
static uint32_t s_lastAvoidUpdateMs = 0;
static uint32_t s_lastLineUpdateMs = 0;
static uint32_t s_lastDistanceColorUpdateMs = 0;
static int s_distanceFilter[kDistanceFilterSize + 1] = {0};
static uint8_t s_distanceFilterCount = 0;
static bool s_avoidTurning = false;
static AvoidMotionMode s_avoidMotionMode = AVOID_MOTION_NONE;
static int8_t s_lastLineTurnDirection = 0;
static bool s_modeLedEnabled = false;
static bool s_smartLineWaitingVision = false;
static uint32_t s_nextVisionRequestMs = 0;
static uint32_t s_visionResponseDeadlineMs = 0;

static bool deadlineReached(uint32_t nowMs, uint32_t deadlineMs) {
  return (int32_t)(nowMs - deadlineMs) >= 0;
}

static int16_t normalizeAngle(int32_t angle) {
  while (angle > 180) {
    angle -= 360;
  }
  while (angle < -180) {
    angle += 360;
  }
  return (int16_t)angle;
}

static uint32_t moveTimeoutMs(float distanceCm) {
  const float speedCmS = kWonderMoveSpeedMps * 100.0f;
  return (uint32_t)((fabsf(distanceCm) / speedCmS) * 1000.0f) + 2500U;
}

static uint32_t turnTimeoutMs(float angleDeg) {
  const float speedDegS = kWonderTurnSpeedRadS * 180.0f / PI;
  return (uint32_t)((fabsf(angleDeg) / speedDegS) * 1000.0f) + 2500U;
}

static void initializeWonderState() {
  WonderLLM_hiwonder.motion_target_distance = 0;
  WonderLLM_hiwonder.motion_strafe_distance = 0;
  WonderLLM_hiwonder.motion_target_angle = 0;
  WonderLLM_hiwonder.running_mode = RUNNING_MODE_IDLE;
  WonderLLM_hiwonder.avoid_distance = 0;
  WonderLLM_hiwonder.buzzer_count = 0;
  WonderLLM_hiwonder.stop_brake = false;
  WonderLLM_hiwonder.claw_channel = kClawGripChannel;
  WonderLLM_hiwonder.claw_action = ClawAction_none;
  WonderLLM_hiwonder.claw_angle = -1;
  WonderLLM_hiwonder.claw_pulse = 0;
  WonderLLM_hiwonder.claw_time_ms = kClawDefaultMoveTimeMs;
  WonderLLM_hiwonder.claw_has_angle = false;
  WonderLLM_hiwonder.claw_has_pulse = false;
  WonderLLM_hiwonder.claw_has_time = false;
  memset(WonderLLM_hiwonder.vision_direction, 0, sizeof(WonderLLM_hiwonder.vision_direction));
  WonderLLM_hiwonder.Frame_mode = Frame_NULL;
  memset(WonderLLM_hiwonder.rgb_left, 0, sizeof(WonderLLM_hiwonder.rgb_left));
  memset(WonderLLM_hiwonder.rgb_right, 0, sizeof(WonderLLM_hiwonder.rgb_right));
  memset(WonderLLM_hiwonder.json_data_raw, 0, sizeof(WonderLLM_hiwonder.json_data_raw));
}

static bool tryInitializeWonder() {
  // 先做一次快速探测，避免模块未连接时 WonderLLM_Init() 阻塞 3 秒。
  if (!Detect_WonderLLM()) {
    return false;
  }

  Serial.println(F("[WonderLLM] Initializing..."));
  if (!WonderLLM_Init()) {
    Serial.println(F("[WonderLLM] Initialization failed"));
    return false;
  }

  initializeWonderState();
  Serial.println(F("[WonderLLM] Ready"));
  return true;
}

static bool tryReadDistanceMm(uint16_t& distanceOut) {
  const uint16_t distance = s_ultrasound.GetDistance();
  if (distance == 0) {
    return false;
  }
  distanceOut = distance;
  return true;
}

static bool readFilteredDistanceMm(uint16_t& distanceOut) {
  uint16_t sample = 0;
  if (!tryReadDistanceMm(sample)) {
    return false;
  }

  s_distanceFilter[kDistanceFilterSize] = sample;
  int sum = 0;
  for (uint8_t i = 0; i < kDistanceFilterSize; i++) {
    s_distanceFilter[i] = s_distanceFilter[i + 1];
    sum += s_distanceFilter[i];
  }
  if (s_distanceFilterCount < kDistanceFilterSize) {
    s_distanceFilterCount++;
  }
  distanceOut = (uint16_t)(sum / s_distanceFilterCount);
  return true;
}

static void sendStatusText(const char* key, const char* value) {
  char params[80];
  snprintf(params, sizeof(params), "[[\"%s\",\"%s\"]]", key, value);
  WonderLLM_Send_Status(params);
}

static void sendStatusNumber(const char* key, int32_t value) {
  char valueText[16];
  snprintf(valueText, sizeof(valueText), "%ld", (long)value);
  sendStatusText(key, valueText);
}

static const char* runningModeName() {
  switch (s_runningMode) {
    case RUNNING_MODE_AVOID:
      return "avoid";
    case RUNNING_MODE_LINE_PATROL:
      return "line_patrol";
    case RUNNING_MODE_SMART_LINE_PATROL:
      return "smart_line_patrol";
    case RUNNING_MODE_TRANSPORT:
      return "transport";
    default:
      return "idle";
  }
}

static const char* clawActionName(ClawAction action) {
  switch (action) {
    case ClawAction_open:
      return "open";
    case ClawAction_close:
      return "close";
    case ClawAction_up:
      return "up";
    case ClawAction_down:
      return "down";
    default:
      return "none";
  }
}

static void clearWonderActionState() {
  s_wonderAction = {};
  s_wonderAction.kind = WONDER_ACTION_NONE;
}

static void finishWonderAction(bool stopOutput, bool succeeded) {
  const WonderActionKind completedKind = s_wonderAction.kind;
  const int16_t completedTurnAngle = s_wonderAction.turnAngleDeg;

  if (completedKind == WONDER_ACTION_NONE) {
    return;
  }

  if ((completedKind == WONDER_ACTION_MOVE || completedKind == WONDER_ACTION_TURN ||
       completedKind == WONDER_ACTION_VISION_NUDGE ||
       completedKind == WONDER_ACTION_TRANSPORT) && stopOutput) {
    mecanumStop(false);
  } else if (completedKind == WONDER_ACTION_BUZZER && stopOutput) {
    setBuzzer(0, 0);
  }

  if (completedKind == WONDER_ACTION_TURN && succeeded) {
    s_headingAngleDeg = normalizeAngle((int32_t)s_headingAngleDeg + completedTurnAngle);
  }

  clearWonderActionState();
  WonderLLM_Send_Action_Finish();
}

static void replaceUnexpectedWonderAction() {
  if (s_wonderAction.kind == WONDER_ACTION_NONE) {
    return;
  }

  if (s_wonderAction.kind == WONDER_ACTION_MOVE ||
      s_wonderAction.kind == WONDER_ACTION_TURN ||
      s_wonderAction.kind == WONDER_ACTION_VISION_NUDGE ||
      s_wonderAction.kind == WONDER_ACTION_TRANSPORT) {
    mecanumStop(false);
  } else if (s_wonderAction.kind == WONDER_ACTION_BUZZER) {
    setBuzzer(0, 0);
  }
  clearWonderActionState();
}

static void cancelVisionRequest(bool notifyFinish) {
  if (s_smartLineWaitingVision && notifyFinish) {
    WonderLLM_Send_Action_Finish();
  }
  s_smartLineWaitingVision = false;
  s_visionResponseDeadlineMs = 0;
  s_nextVisionRequestMs = 0;
}

static void resetRunningMode(bool stopMotion, bool notifyVisionFinish) {
  cancelVisionRequest(notifyVisionFinish);
  s_runningMode = RUNNING_MODE_IDLE;
  s_avoidTurning = false;
  s_avoidMotionMode = AVOID_MOTION_NONE;
  s_lastLineTurnDirection = 0;
  s_modeLedEnabled = false;
  s_distanceFilterCount = 0;
  memset(s_distanceFilter, 0, sizeof(s_distanceFilter));
  if (stopMotion) {
    mecanumStop(false);
  }
}

static void prepareWonderMotionControl() {
  replaceUnexpectedWonderAction();
  resetRunningMode(true, true);
  bleRemoteControlReleaseMotion();
}

static void startWonderMove(int16_t distanceCm, int16_t strafeCm) {
  prepareWonderMotionControl();

  if (distanceCm == 0 && strafeCm == 0) {
    mecanumStop(false);
    WonderLLM_Send_Action_Finish();
    return;
  }

  float vx = 0.0f;
  float vy = 0.0f;
  float commandDistanceCm = 0.0f;

  if (distanceCm != 0) {
    vx = (distanceCm > 0) ? kWonderMoveSpeedMps : -kWonderMoveSpeedMps;
    commandDistanceCm = fabsf((float)distanceCm);
  } else {
    vy = (strafeCm > 0) ? kWonderMoveSpeedMps : -kWonderMoveSpeedMps;
    commandDistanceCm = fabsf((float)strafeCm) * kWonderStrafeDistanceCompensation;
  }

  if (!mecanumMoveDistance(vx, vy, 0.0f, commandDistanceCm / 100.0f)) {
    mecanumStop(false);
    Serial.println(F("[WonderLLM] Move command failed"));
    WonderLLM_Send_Action_Finish();
    return;
  }

  clearWonderActionState();
  s_wonderAction.kind = WONDER_ACTION_MOVE;
  s_wonderAction.startedMs = millis();
  s_wonderAction.timeoutMs = moveTimeoutMs(commandDistanceCm);
  s_wonderAction.nextUpdateMs = s_wonderAction.startedMs;
  Serial.println(F("[WonderLLM] Move started"));
}

static void startWonderTurn(int16_t angleDeg) {
  prepareWonderMotionControl();

  if (angleDeg == 0) {
    mecanumStop(false);
    WonderLLM_Send_Action_Finish();
    return;
  }

  const float wz = (angleDeg > 0) ? kWonderTurnSpeedRadS : -kWonderTurnSpeedRadS;
  const float angleRad = fabsf((float)angleDeg) * PI / 180.0f;
  if (!mecanumRotateAngle(wz, angleRad)) {
    mecanumStop(false);
    Serial.println(F("[WonderLLM] Turn command failed"));
    WonderLLM_Send_Action_Finish();
    return;
  }

  clearWonderActionState();
  s_wonderAction.kind = WONDER_ACTION_TURN;
  s_wonderAction.startedMs = millis();
  s_wonderAction.timeoutMs = turnTimeoutMs((float)angleDeg);
  s_wonderAction.nextUpdateMs = s_wonderAction.startedMs;
  s_wonderAction.turnAngleDeg = angleDeg;
  Serial.println(F("[WonderLLM] Turn started"));
}

static void startWonderBuzzer(uint8_t count) {
  const bool wasTransport = s_wonderAction.kind == WONDER_ACTION_TRANSPORT;
  replaceUnexpectedWonderAction();
  if (wasTransport) {
    resetRunningMode(false, false);
  }
  bleRemoteControlReleaseBuzzer();
  count = constrain(count, 0, 10);

  if (count == 0) {
    setBuzzer(0, 0);
    WonderLLM_Send_Action_Finish();
    return;
  }

  clearWonderActionState();
  s_wonderAction.kind = WONDER_ACTION_BUZZER;
  s_wonderAction.buzzerRemaining = count;
  s_wonderAction.buzzerOn = true;
  s_wonderAction.nextUpdateMs = millis() + 160U;
  setBuzzer(800, 50);
}

static bool issueWonderClaw(uint16_t& moveTimeMs) {
  const uint8_t channel = constrain(
    WonderLLM_hiwonder.claw_channel,
    kClawLiftChannel,
    kClawGripChannel
  );
  moveTimeMs = WonderLLM_hiwonder.claw_has_time
    ? WonderLLM_hiwonder.claw_time_ms
    : kClawDefaultMoveTimeMs;

  // 停止 APP 高度点动，防止其在下一轮继续改写 WonderLLM 的舵机命令。
  armControlSetHeightAngle(armControlGetEstimatedHeightAngle());
  armControlSetClawAngle(armControlGetEstimatedClawAngle());

  if (WonderLLM_hiwonder.claw_has_pulse) {
    const uint16_t pulseUs = constrain(
      WonderLLM_hiwonder.claw_pulse,
      kServoMinPulseUs,
      kServoMaxPulseUs
    );
    setPwmServo(channel, pulseUs, moveTimeMs);
    return true;
  }

  if (WonderLLM_hiwonder.claw_has_angle) {
    if (channel == kClawLiftChannel) {
      armControlSetHeightAngle(WonderLLM_hiwonder.claw_angle, moveTimeMs);
    } else {
      armControlSetClawAngle(WonderLLM_hiwonder.claw_angle, moveTimeMs);
    }
    return true;
  }

  switch (WonderLLM_hiwonder.claw_action) {
    case ClawAction_open:
      armControlSetClawAngle(kClawOpenAngleDeg, moveTimeMs);
      return true;
    case ClawAction_close:
      armControlSetClawAngle(kClawCloseAngleDeg, moveTimeMs);
      return true;
    case ClawAction_up:
      armControlSetHeightAngle(kClawLiftUpAngleDeg, moveTimeMs);
      return true;
    case ClawAction_down:
      armControlSetHeightAngle(kClawLiftDownAngleDeg, moveTimeMs);
      return true;
    default:
      Serial.println(F("[WonderLLM] Claw command ignored: no action, angle, or pulse"));
      return false;
  }
}

static void startWonderClaw() {
  const bool wasTransport = s_wonderAction.kind == WONDER_ACTION_TRANSPORT;
  replaceUnexpectedWonderAction();
  if (wasTransport) {
    resetRunningMode(false, false);
  }

  uint16_t moveTimeMs = 0;
  if (!issueWonderClaw(moveTimeMs) || moveTimeMs == 0) {
    WonderLLM_Send_Action_Finish();
    return;
  }

  clearWonderActionState();
  s_wonderAction.kind = WONDER_ACTION_CLAW;
  s_wonderAction.nextUpdateMs = millis() + moveTimeMs;
}

static void activateContinuousMode(uint8_t mode) {
  s_runningMode = mode;
  s_lastAvoidUpdateMs = 0;
  s_lastLineUpdateMs = 0;
  s_lastDistanceColorUpdateMs = 0;
  s_avoidTurning = false;
  s_avoidMotionMode = AVOID_MOTION_NONE;
  s_lastLineTurnDirection = 0;
  s_smartLineWaitingVision = false;
  s_nextVisionRequestMs = 0;
  s_visionResponseDeadlineMs = 0;
  s_modeLedEnabled = true;
  Serial.print(F("[WonderLLM] Mode active: "));
  Serial.println(runningModeName());
}

static bool startTransportMotion(float vx, float vy, float wz,
                                 float distanceOrAngle, bool isTurn) {
  const bool issued = isTurn
    ? mecanumRotateAngle(wz, fabsf(distanceOrAngle) * PI / 180.0f)
    : mecanumMoveDistance(vx, vy, 0.0f, fabsf(distanceOrAngle) / 100.0f);
  if (!issued) {
    return false;
  }

  s_wonderAction.transportWaitingMotion = true;
  s_wonderAction.motionStarted = false;
  s_wonderAction.startedMs = millis();
  s_wonderAction.timeoutMs = isTurn
    ? turnTimeoutMs(distanceOrAngle)
    : moveTimeoutMs(distanceOrAngle);
  s_wonderAction.nextUpdateMs = s_wonderAction.startedMs;
  s_wonderAction.turnAngleDeg = isTurn ? (int16_t)distanceOrAngle : 0;
  return true;
}

static void finishTransport(bool succeeded) {
  mecanumStop(false);
  s_runningMode = RUNNING_MODE_IDLE;
  clearWonderActionState();
  if (succeeded) {
    Serial.println(F("[WonderLLM] Transport finished"));
  } else {
    sendStatusText("action", "failed");
    Serial.println(F("[WonderLLM] Transport failed"));
  }
}

static void beginTransportStep() {
  s_wonderAction.transportWaitingMotion = false;
  switch (s_wonderAction.transportStep) {
    case 0:
      armControlSetHeightAngle(kClawLiftDownAngleDeg, 600);
      s_wonderAction.nextUpdateMs = millis() + 600U;
      break;
    case 1:
      armControlSetClawAngle(kClawOpenAngleDeg, 500);
      s_wonderAction.nextUpdateMs = millis() + 500U;
      break;
    case 2:
      if (!startTransportMotion(kWonderMoveSpeedMps, 0.0f, 0.0f,
                                kTransportPickupForwardCm, false)) {
        finishTransport(false);
      }
      break;
    case 3:
      armControlSetClawAngle(kClawClampAngleDeg, 500);
      s_wonderAction.nextUpdateMs = millis() + 500U;
      break;
    case 4:
      armControlSetHeightAngle(kClawLiftUpAngleDeg, 600);
      s_wonderAction.nextUpdateMs = millis() + 600U;
      break;
    case 5:
      if (!startTransportMotion(0.0f, 0.0f, kWonderTurnSpeedRadS,
                                kTransportTurnAngleDeg, true)) {
        finishTransport(false);
      }
      break;
    case 6:
      if (!startTransportMotion(kWonderMoveSpeedMps, 0.0f, 0.0f,
                                kTransportPlaceForwardCm, false)) {
        finishTransport(false);
      }
      break;
    case 7:
      armControlSetHeightAngle(kClawLiftDownAngleDeg, 600);
      s_wonderAction.nextUpdateMs = millis() + 600U;
      break;
    case 8:
      armControlSetClawAngle(kClawOpenAngleDeg, 500);
      s_wonderAction.nextUpdateMs = millis() + 500U;
      break;
    case 9:
      armControlSetHeightAngle(kClawLiftUpAngleDeg, 600);
      s_wonderAction.nextUpdateMs = millis() + 600U;
      break;
    case 10:
      if (!startTransportMotion(-kWonderMoveSpeedMps, 0.0f, 0.0f,
                                kTransportPlaceForwardCm, false)) {
        finishTransport(false);
      }
      break;
    case 11:
      if (!startTransportMotion(0.0f, 0.0f, -kWonderTurnSpeedRadS,
                                -kTransportTurnAngleDeg, true)) {
        finishTransport(false);
      }
      break;
    case 12:
      if (!startTransportMotion(-kWonderMoveSpeedMps, 0.0f, 0.0f,
                                kTransportPickupForwardCm, false)) {
        finishTransport(false);
      }
      break;
    default:
      finishTransport(true);
      break;
  }
}

static void startTransport() {
  clearWonderActionState();
  s_runningMode = RUNNING_MODE_TRANSPORT;
  s_modeLedEnabled = false;
  s_wonderAction.kind = WONDER_ACTION_TRANSPORT;
  s_wonderAction.transportStep = 0;
  beginTransportStep();
  Serial.println(F("[WonderLLM] Transport started"));
}

static void startRunningMode(uint8_t mode, uint16_t avoidDistanceCm) {
  prepareWonderMotionControl();
  s_avoidDistanceMm = avoidDistanceCm > 0
    ? (uint16_t)(avoidDistanceCm * 10U)
    : kDefaultAvoidDistanceMm;

  if (mode == RUNNING_MODE_TRANSPORT) {
    WonderLLM_Send_Action_Finish();
    startTransport();
    return;
  }
  if (mode != RUNNING_MODE_AVOID && mode != RUNNING_MODE_LINE_PATROL &&
      mode != RUNNING_MODE_SMART_LINE_PATROL) {
    sendStatusText("action", "failed");
    WonderLLM_Send_Action_Finish();
    return;
  }

  armControlSetHeightAngle(armControlGetEstimatedHeightAngle());
  armControlSetHeightAngle(kClawLiftDownAngleDeg, kModeArmLowerTimeMs);
  clearWonderActionState();
  s_wonderAction.kind = WONDER_ACTION_MODE_START;
  s_wonderAction.targetMode = mode;
  s_wonderAction.nextUpdateMs = millis() + kModeArmLowerTimeMs;
}

static void updateDistanceColor(uint16_t distanceMm) {
  if (!s_modeLedEnabled || distanceMm == 0) {
    return;
  }
  const uint32_t nowMs = millis();
  if (nowMs - s_lastDistanceColorUpdateMs < kDistanceColorUpdateIntervalMs) {
    return;
  }
  s_lastDistanceColorUpdateMs = nowMs;

  uint8_t value = 0;
  if (distanceMm <= 80) {
    s_ultrasound.Breathing(1, 0, 0, 1, 0, 0);
  } else if (distanceMm <= 180) {
    value = (uint8_t)map(distanceMm, 80, 180, 0, 255);
    s_ultrasound.Color(255 - value, 0, 0, 255 - value, 0, 0);
  } else if (distanceMm <= 320) {
    value = (uint8_t)map(distanceMm, 180, 320, 0, 255);
    s_ultrasound.Color(0, 0, value, 0, 0, value);
  } else if (distanceMm <= 500) {
    value = (uint8_t)map(distanceMm, 320, 500, 0, 255);
    s_ultrasound.Color(0, value, 255 - value, 0, value, 255 - value);
  } else {
    s_ultrasound.Color(0, 255, 0, 0, 255, 0);
  }
}

static void showModeErrorColor() {
  if (s_modeLedEnabled) {
    s_ultrasound.Color(225, 0, 0, 255, 0, 0);
  }
}

static void updateAvoidMode() {
  if (s_runningMode != RUNNING_MODE_AVOID) {
    return;
  }
  const uint32_t nowMs = millis();
  if (nowMs - s_lastAvoidUpdateMs < kAvoidUpdateIntervalMs) {
    return;
  }
  s_lastAvoidUpdateMs = nowMs;

  uint16_t distanceMm = 0;
  if (!readFilteredDistanceMm(distanceMm)) {
    mecanumStop(true);
    s_avoidMotionMode = AVOID_MOTION_NONE;
    showModeErrorColor();
    return;
  }
  updateDistanceColor(distanceMm);

  if (distanceMm < s_avoidDistanceMm) {
    s_avoidTurning = true;
  } else if (distanceMm >= (uint16_t)(s_avoidDistanceMm + kAvoidHysteresisMm)) {
    s_avoidTurning = false;
  }

  const AvoidMotionMode target = s_avoidTurning
    ? AVOID_MOTION_TURN
    : AVOID_MOTION_FORWARD;
  if (target == s_avoidMotionMode) {
    return;
  }
  s_avoidMotionMode = target;
  if (target == AVOID_MOTION_TURN) {
    mecanumSetVelocity(0.0f, 0.0f, kAvoidTurnSpeedRadS);
  } else {
    mecanumSetVelocity(kAvoidForwardSpeedMps, 0.0f, 0.0f);
  }
}

static void updateLinePatrolMode() {
  if (s_runningMode != RUNNING_MODE_LINE_PATROL &&
      s_runningMode != RUNNING_MODE_SMART_LINE_PATROL) {
    return;
  }
  if (s_wonderAction.kind == WONDER_ACTION_VISION_NUDGE) {
    return;
  }

  const uint32_t nowMs = millis();
  if (nowMs - s_lastLineUpdateMs < kLineUpdateIntervalMs) {
    return;
  }
  s_lastLineUpdateMs = nowMs;

  uint16_t distanceMm = 0;
  if (!tryReadDistanceMm(distanceMm)) {
    mecanumStop(true);
    showModeErrorColor();
    return;
  }
  if (distanceMm <= s_avoidDistanceMm) {
    mecanumStop(false);
    showModeErrorColor();
    return;
  }
  if (!s_lineFollower.update()) {
    mecanumStop(false);
    return;
  }

  const bool sensor0 = s_lineFollower.getSensor(0);
  const bool sensor1 = s_lineFollower.getSensor(1);
  const bool sensor2 = s_lineFollower.getSensor(2);
  const bool sensor3 = s_lineFollower.getSensor(3);
  const bool lineDetected = sensor0 || sensor1 || sensor2 || sensor3;

  if (s_smartLineWaitingVision) {
    if (deadlineReached(nowMs, s_visionResponseDeadlineMs)) {
      s_smartLineWaitingVision = false;
      s_visionResponseDeadlineMs = 0;
      s_nextVisionRequestMs = nowMs + kVisionRetryDelayMs;
      WonderLLM_Send_Action_Finish();
      Serial.println(F("[WonderLLM] Vision response timeout"));
    } else {
      mecanumStop(false);
      return;
    }
  }

  if (s_modeLedEnabled) {
    s_ultrasound.Color(0, 255, 0, 0, 255, 0);
  }
  if (!lineDetected) {
    mecanumStop(false);
    if (s_runningMode == RUNNING_MODE_SMART_LINE_PATROL &&
        s_wonderAction.kind == WONDER_ACTION_NONE &&
        deadlineReached(nowMs, s_nextVisionRequestMs)) {
      if (WonderLLM_Request_Vision()) {
        s_smartLineWaitingVision = true;
        s_visionResponseDeadlineMs = nowMs + kVisionResponseTimeoutMs;
      } else {
        s_nextVisionRequestMs = nowMs + kVisionRetryDelayMs;
      }
    }
    return;
  }

  if (sensor1 && sensor2) {
    mecanumSetVelocity(kLineForwardSpeedMps, 0.0f, 0.0f);
  } else if (sensor1 && !sensor2) {
    s_lastLineTurnDirection = 1;
    mecanumSetVelocity(kLineTurnForwardMps, 0.0f, kLineTurnSpeedRadS);
  } else if (!sensor1 && sensor2) {
    s_lastLineTurnDirection = -1;
    mecanumSetVelocity(kLineTurnForwardMps, 0.0f, -kLineTurnSpeedRadS);
  } else if (sensor0) {
    s_lastLineTurnDirection = 1;
    mecanumSetVelocity(0.0f, 0.0f, kLineSharpTurnSpeedRadS);
  } else if (sensor3) {
    s_lastLineTurnDirection = -1;
    mecanumSetVelocity(0.0f, 0.0f, -kLineSharpTurnSpeedRadS);
  } else if (s_lastLineTurnDirection > 0) {
    mecanumSetVelocity(0.0f, 0.0f, kLineSharpTurnSpeedRadS);
  } else if (s_lastLineTurnDirection < 0) {
    mecanumSetVelocity(0.0f, 0.0f, -kLineSharpTurnSpeedRadS);
  } else {
    mecanumStop(false);
  }
}

static void handleVisionDirection() {
  if (!s_smartLineWaitingVision ||
      s_runningMode != RUNNING_MODE_SMART_LINE_PATROL) {
    WonderLLM_Send_Action_Finish();
    return;
  }

  s_smartLineWaitingVision = false;
  s_visionResponseDeadlineMs = 0;
  if (strcmp(WonderLLM_hiwonder.vision_direction, "left") == 0) {
    mecanumSetVelocity(kLineForwardSpeedMps, 0.0f, -kLineSharpTurnSpeedRadS);
  } else if (strcmp(WonderLLM_hiwonder.vision_direction, "right") == 0) {
    mecanumSetVelocity(kLineForwardSpeedMps, 0.0f, kLineSharpTurnSpeedRadS);
  } else {
    mecanumStop(false);
    s_nextVisionRequestMs = millis() + kVisionRetryDelayMs;
    WonderLLM_Send_Action_Finish();
    return;
  }

  clearWonderActionState();
  s_wonderAction.kind = WONDER_ACTION_VISION_NUDGE;
  s_wonderAction.nextUpdateMs = millis() + kVisionNudgeDurationMs;
}

static void updateWonderMotion(uint32_t nowMs) {
  if (!deadlineReached(nowMs, s_wonderAction.nextUpdateMs)) {
    return;
  }
  s_wonderAction.nextUpdateMs = nowMs + kMotionPollIntervalMs;

  const bool motionDone = mecanumIsMotionDone();
  const uint32_t elapsedMs = nowMs - s_wonderAction.startedMs;
  if (!motionDone) {
    s_wonderAction.motionStarted = true;
  }

  if (s_wonderAction.motionStarted && motionDone && elapsedMs >= kMotionMinimumRunMs) {
    Serial.println(F("[WonderLLM] Motion finished"));
    finishWonderAction(true, true);
    return;
  }

  if (!s_wonderAction.motionStarted && elapsedMs >= kMotionStartTimeoutMs) {
    Serial.println(F("[WonderLLM] Motion did not start"));
    finishWonderAction(true, false);
    return;
  }

  if (elapsedMs >= s_wonderAction.timeoutMs) {
    Serial.println(F("[WonderLLM] Motion timeout"));
    finishWonderAction(true, false);
  }
}

static void updateWonderBuzzer(uint32_t nowMs) {
  if (!deadlineReached(nowMs, s_wonderAction.nextUpdateMs)) {
    return;
  }

  if (s_wonderAction.buzzerOn) {
    setBuzzer(0, 0);
    s_wonderAction.buzzerOn = false;
    if (s_wonderAction.buzzerRemaining > 0) {
      s_wonderAction.buzzerRemaining--;
    }
    if (s_wonderAction.buzzerRemaining == 0) {
      finishWonderAction(false, true);
      return;
    }
    s_wonderAction.nextUpdateMs = nowMs + 40U;
  } else {
    setBuzzer(800, 50);
    s_wonderAction.buzzerOn = true;
    s_wonderAction.nextUpdateMs = nowMs + 160U;
  }
}

static void updateTransport(uint32_t nowMs) {
  if (s_wonderAction.transportWaitingMotion) {
    if (!deadlineReached(nowMs, s_wonderAction.nextUpdateMs)) {
      return;
    }
    s_wonderAction.nextUpdateMs = nowMs + kMotionPollIntervalMs;

    const bool motionDone = mecanumIsMotionDone();
    const uint32_t elapsedMs = nowMs - s_wonderAction.startedMs;
    if (!motionDone) {
      s_wonderAction.motionStarted = true;
    }
    if (s_wonderAction.motionStarted && motionDone && elapsedMs >= kMotionMinimumRunMs) {
      mecanumStop(false);
      if (s_wonderAction.turnAngleDeg != 0) {
        s_headingAngleDeg = normalizeAngle(
          (int32_t)s_headingAngleDeg + s_wonderAction.turnAngleDeg
        );
      }
      s_wonderAction.transportStep++;
      beginTransportStep();
    } else if ((!s_wonderAction.motionStarted && elapsedMs >= kMotionStartTimeoutMs) ||
               elapsedMs >= s_wonderAction.timeoutMs) {
      finishTransport(false);
    }
    return;
  }

  if (deadlineReached(nowMs, s_wonderAction.nextUpdateMs)) {
    s_wonderAction.transportStep++;
    beginTransportStep();
  }
}

static void updateWonderAction() {
  const uint32_t nowMs = millis();
  switch (s_wonderAction.kind) {
    case WONDER_ACTION_MOVE:
    case WONDER_ACTION_TURN:
      updateWonderMotion(nowMs);
      break;
    case WONDER_ACTION_BUZZER:
      updateWonderBuzzer(nowMs);
      break;
    case WONDER_ACTION_CLAW:
      if (deadlineReached(nowMs, s_wonderAction.nextUpdateMs)) {
        finishWonderAction(false, true);
      }
      break;
    case WONDER_ACTION_MODE_START:
      if (deadlineReached(nowMs, s_wonderAction.nextUpdateMs)) {
        const uint8_t targetMode = s_wonderAction.targetMode;
        clearWonderActionState();
        activateContinuousMode(targetMode);
        WonderLLM_Send_Action_Finish();
      }
      break;
    case WONDER_ACTION_VISION_NUDGE:
      if (deadlineReached(nowMs, s_wonderAction.nextUpdateMs)) {
        mecanumStop(false);
        s_lastLineUpdateMs = 0;
        s_lastLineTurnDirection = 0;
        finishWonderAction(false, true);
      }
      break;
    case WONDER_ACTION_TRANSPORT:
      updateTransport(nowMs);
      break;
    default:
      break;
  }
}

static void handleAppEvents(uint8_t events) {
  if (events & BLE_CONTROL_EVENT_MOTION) autoMotionManualOverride();
  if ((events & BLE_CONTROL_EVENT_LED) != 0) {
    s_modeLedEnabled = false;
  }

  if ((events & BLE_CONTROL_EVENT_MOTION) != 0) {
    const WonderActionKind action = s_wonderAction.kind;
    if (action == WONDER_ACTION_MOVE || action == WONDER_ACTION_TURN ||
        action == WONDER_ACTION_MODE_START || action == WONDER_ACTION_VISION_NUDGE) {
      finishWonderAction(false, false);
    } else if (action == WONDER_ACTION_TRANSPORT) {
      clearWonderActionState();
    }
    resetRunningMode(false, true);
    Serial.println(F("[Control] APP took over motion"));
    return;
  }

  if ((events & BLE_CONTROL_EVENT_ARM) != 0 &&
      (s_wonderAction.kind == WONDER_ACTION_CLAW ||
       s_wonderAction.kind == WONDER_ACTION_MODE_START ||
       s_wonderAction.kind == WONDER_ACTION_TRANSPORT)) {
    const bool wasTransport = s_wonderAction.kind == WONDER_ACTION_TRANSPORT;
    if (wasTransport) {
      mecanumStop(false);
      clearWonderActionState();
      resetRunningMode(false, true);
    } else {
      finishWonderAction(false, false);
    }
    Serial.println(F("[Control] APP took over the arm"));
    return;
  }

  if ((events & BLE_CONTROL_EVENT_BUZZER) != 0 &&
      s_wonderAction.kind == WONDER_ACTION_BUZZER) {
    finishWonderAction(false, false);
    Serial.println(F("[Control] APP took over the buzzer"));
    return;
  }

}

static void handleWonderFrame() {
  char info[112];

  Serial.print(F("[WonderLLM RX] "));
  Serial.println(WonderLLM_hiwonder.json_data_raw);

  switch (WonderLLM_hiwonder.Frame_mode) {
    case Frame_move:
      snprintf(
        info,
        sizeof(info),
        "[WonderLLM] Move distance=%d, strafe=%d",
        WonderLLM_hiwonder.motion_target_distance,
        WonderLLM_hiwonder.motion_strafe_distance
      );
      Serial.println(info);
      startWonderMove(
        WonderLLM_hiwonder.motion_target_distance,
        WonderLLM_hiwonder.motion_strafe_distance
      );
      break;

    case Frame_turn:
      snprintf(info, sizeof(info), "[WonderLLM] Turn angle=%d", WonderLLM_hiwonder.motion_target_angle);
      Serial.println(info);
      startWonderTurn(WonderLLM_hiwonder.motion_target_angle);
      break;

    case Frame_stop:
      replaceUnexpectedWonderAction();
      resetRunningMode(false, true);
      bleRemoteControlReleaseMotion();
      mecanumStop(WonderLLM_hiwonder.stop_brake);
      setBuzzer(0, 0);
      armControlRelease();
      WonderLLM_Send_Action_Finish();
      break;

    case Frame_get_status_battery:
      sendStatusNumber("battery", (int32_t)readBatteryMv());
      break;

    case Frame_get_status_angle:
      sendStatusNumber("angle", s_headingAngleDeg);
      break;

    case Frame_get_status_distance: {
      uint16_t distanceMm = 0;
      if (tryReadDistanceMm(distanceMm)) {
        sendStatusNumber("distance", distanceMm / 10);
      } else {
        sendStatusText("distance", "unavailable");
      }
      break;
    }

    case Frame_get_status_running_mode:
      sendStatusText("running_mode", runningModeName());
      break;

    case Frame_set_mode:
    case Frame_set_mode_avoid:
      snprintf(info, sizeof(info), "[WonderLLM] Set mode=%d, distance=%u cm",
               WonderLLM_hiwonder.running_mode,
               WonderLLM_hiwonder.avoid_distance);
      Serial.println(info);
      startRunningMode((uint8_t)WonderLLM_hiwonder.running_mode,
                       WonderLLM_hiwonder.avoid_distance);
      break;

    case Frame_vision:
      handleVisionDirection();
      break;

    case Frame_set_led_color:
      bleRemoteControlReleaseLed();
      s_modeLedEnabled = false;
      s_ultrasound.Color(
        WonderLLM_hiwonder.rgb_left[0],
        WonderLLM_hiwonder.rgb_left[1],
        WonderLLM_hiwonder.rgb_left[2],
        WonderLLM_hiwonder.rgb_right[0],
        WonderLLM_hiwonder.rgb_right[1],
        WonderLLM_hiwonder.rgb_right[2]
      );
      WonderLLM_Send_Action_Finish();
      break;

    case Frame_set_buzzer:
      startWonderBuzzer((uint8_t)WonderLLM_hiwonder.buzzer_count);
      break;

    case Frame_set_claw:
      snprintf(
        info,
        sizeof(info),
        "[WonderLLM] Claw channel=%u, action=%s, angle=%d, pulse=%u",
        WonderLLM_hiwonder.claw_channel,
        clawActionName(WonderLLM_hiwonder.claw_action),
        WonderLLM_hiwonder.claw_angle,
        WonderLLM_hiwonder.claw_pulse
      );
      Serial.println(info);
      startWonderClaw();
      break;

    default:
      break;
  }

  memset(WonderLLM_hiwonder.json_data_raw, 0, sizeof(WonderLLM_hiwonder.json_data_raw));
}

static void updateWonderControl() {
  if (autoMotionOwnsControl()) return;
  updateWonderAction();
  updateAvoidMode();
  updateLinePatrolMode();

  if (!s_wonderReady) {
    const uint32_t nowMs = millis();
    if (nowMs - s_lastWonderRetryMs >= kWonderRetryIntervalMs) {
      s_lastWonderRetryMs = nowMs;
      s_wonderReady = tryInitializeWonder();
    }
    return;
  }

  WonderLLM_Info_Get(&WonderLLM_hiwonder);
  if (WonderLLM_hiwonder.Frame_mode != Frame_NULL) {
    handleWonderFrame();
  }
}

}  // namespace

void factoryControlSetup() {
  Serial.begin(115200);
  delay(100);
  Serial.println();
  Serial.println(F("MiniAutoPro factory control starting"));

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  Wire.setClock(100000);
  Wire.setTimeOut(20);
  Wire1.begin(kExtendI2cSda, kExtendI2cScl);
  Wire1.setClock(100000);
  Wire1.setTimeOut(20);

  mecanumInit(kAxleWidthM, kAxleLengthM, kWheelDiameterM);
  mecanumStop(false);
  bleRemoteControlInit(kAppMaxLinearSpeedMps, kAppMaxAngularSpeedRadS);
  s_ultrasound.begin();
  s_ultrasound.Color(0, 0, 200, 0, 0, 200);
  s_lineFollower.begin();

  Serial.print(F("Extend board ID=0x"));
  Serial.print(readDeviceId(), HEX);
  Serial.print(F(", firmware="));
  const uint16_t firmwareVersion = readFirmwareVersion();
  Serial.print(firmwareVersion >> 8);
  Serial.print('.');
  Serial.println(firmwareVersion & 0xFF);

  s_wonderReady = tryInitializeWonder();
  s_lastWonderRetryMs = millis();
  if (!s_wonderReady) {
    Serial.println(F("[WonderLLM] Not found; APP control remains available"));
  }
}

void factoryControlUpdate() {
  // 每轮交换先后顺序，避免同一调度周期内总是固定某一来源最后执行。
  if (s_pollAppFirst) {
    const uint8_t appEvents = bleRemoteControlUpdate();
    handleAppEvents(appEvents);
    updateWonderControl();
  } else {
    updateWonderControl();
    const uint8_t appEvents = bleRemoteControlUpdate();
    handleAppEvents(appEvents);
  }
  s_pollAppFirst = !s_pollAppFirst;

  armControlUpdate();
  delay(1);
}

void factoryControlPrepareAuto() {
  replaceUnexpectedWonderAction();
  resetRunningMode(true,true);
  bleRemoteControlReleaseMotion();
}
