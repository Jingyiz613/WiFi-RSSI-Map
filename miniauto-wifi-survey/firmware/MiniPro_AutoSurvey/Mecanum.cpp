#include "Mecanum.h"
#include "BoardDriver.h"

static float g_axleWidthM = 0.0f;
static float g_axleLengthM = 0.0f;
static float g_wheelDiameterM = 0.0f;
static float g_wheelCircumferenceM = 0.0f;
static float g_rotationArmM = 0.0f;
static bool g_mecanumInitialized = false;
static bool g_warnedNotInitialized = false;
static constexpr uint32_t kMotionStartTimeoutMs = 300U;

// Bench calibration: a 180 deg command rotates the chassis about 190 deg.
static constexpr float kRotationDistanceScale = 180.0f / 190.0f;

// =========================================================
// Inverse kinematics (channel order: [FL=0, BL=1, BR=2, FR=3])
//
// This matches the firmware sync example and chassis wiring:
//   Forward      = { +, +, +, + }
//   Right strafe = { +, -, +, - }
//   Left turn    = { -, -, +, + }
//
// Wheel edge linear speed:
//   v_FL = +Vx + Vy - k * Wz
//   v_BL = +Vx - Vy - k * Wz
//   v_BR = +Vx + Vy + k * Wz
//   v_FR = +Vx - Vy + k * Wz
// where k = (axle_width + axle_length) / 2.
// =========================================================
static bool mecanumReady() {
  if (g_mecanumInitialized) {
    return true;
  }

  if (!g_warnedNotInitialized) {
    Serial.println("Mecanum not initialized. Call mecanumInit() first.");
    g_warnedNotInitialized = true;
  }
  return false;
}

static float linearSpeedToRpm(float speed_mps) {
  return (speed_mps / g_wheelCircumferenceM) * 60.0f;
}

static void normalizeTranslationalVelocity(float& vx, float& vy) {
  float maxAbs = max(fabsf(vx), fabsf(vy));
  if (maxAbs == 0.0f) {
    return;
  }

  float vectorNorm = sqrtf(vx * vx + vy * vy);
  if (vectorNorm <= maxAbs) {
    return;
  }

  float scale = maxAbs / vectorNorm;
  vx *= scale;
  vy *= scale;
}

void mecanumInit(float axle_width_m, float axle_length_m, float wheel_diameter_m) {
  if (axle_width_m <= 0.0f || axle_length_m <= 0.0f || wheel_diameter_m <= 0.0f) {
    g_mecanumInitialized = false;
    Serial.println("Invalid mecanum geometry. All parameters must be > 0.");
    return;
  }

  g_axleWidthM = axle_width_m;
  g_axleLengthM = axle_length_m;
  g_wheelDiameterM = wheel_diameter_m;
  g_wheelCircumferenceM = PI * g_wheelDiameterM;
  g_rotationArmM = 0.5f * (g_axleWidthM + g_axleLengthM);
  g_mecanumInitialized = true;
  g_warnedNotInitialized = false;
}

static void calcWheelRpms(float vx, float vy, float wz, float rpms[4]) {
  normalizeTranslationalVelocity(vx, vy);

  float rot_speed = g_rotationArmM * wz;

  rpms[0] = linearSpeedToRpm(+vx + vy - rot_speed);   // FL
  rpms[1] = linearSpeedToRpm(+vx - vy - rot_speed);   // BL
  rpms[2] = linearSpeedToRpm(+vx + vy + rot_speed);   // BR
  rpms[3] = linearSpeedToRpm(+vx - vy + rot_speed);   // FR
}

// ---------------------------------------------------------
void mecanumSetVelocity(float vx, float vy, float wz) {
  if (!mecanumReady()) {
    return;
  }

  float rpms[4];
  calcWheelRpms(vx, vy, wz, rpms);
  motorSyncMove(rpms, 0.0f);  // dist_cm=0 → 纯速度环
}

// ---------------------------------------------------------
bool mecanumMoveDistance(float vx, float vy, float wz, float dist_m) {
  if (!mecanumReady()) {
    return false;
  }

  float rpms[4];
  calcWheelRpms(vx, vy, wz, rpms);

  if (dist_m <= 0.0f) {
    return motorSyncMove(rpms, 0.0f);
  }

  return motorSyncMove(rpms, fabsf(dist_m) * 100.0f);
}

// ---------------------------------------------------------
bool mecanumRotateAngle(float wz, float angle_rad) {
  if (!mecanumReady()) {
    return false;
  }

  float angleAbsRad = fabsf(angle_rad);
  if (wz == 0.0f || angleAbsRad == 0.0f) {
    mecanumStop(false);
    return true;
  }

  float arcM = g_rotationArmM * angleAbsRad * kRotationDistanceScale;
  if (arcM < 0.001f) {
    mecanumStop(false);
    return true;
  }

  float rpms[4];
  calcWheelRpms(0.0f, 0.0f, wz, rpms);
  return motorSyncMove(rpms, arcM * 100.0f);
}

// ---------------------------------------------------------
void mecanumStop(bool brake) {
  if (brake) {
    motorBrakeMask(0x0F);   // 同时刹车 4 路
    delay(30);
  }
  float zero[4] = {0.0f, 0.0f, 0.0f, 0.0f};
  invalidateMotorSyncSpeedCache();
  motorSyncMove(zero, 0.0f);
}

// ---------------------------------------------------------
bool mecanumWaitDone(uint32_t timeout_ms, uint32_t minimum_run_ms) {
  uint32_t start = millis();
  bool motionStarted = false;
  while (true) {
    bool motionDone = mecanumIsMotionDone();
    uint32_t elapsedMs = millis() - start;
    if (!motionDone) {
      motionStarted = true;
    }
    if (motionStarted && motionDone && elapsedMs >= minimum_run_ms) {
      return true;
    }
    if (!motionStarted && elapsedMs >= kMotionStartTimeoutMs) {
      return false;
    }
    if (timeout_ms > 0 && elapsedMs >= timeout_ms) return false;
    delay(50);
  }
}

bool mecanumIsMotionDone() {
  return (readReg8(REG_DIST_STATUS) & 0x0F) == 0x00;
}
