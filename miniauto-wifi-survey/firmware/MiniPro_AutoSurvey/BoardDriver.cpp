#include "BoardDriver.h"
#include <Wire.h>

static bool isValidMotorChannel(uint8_t channel) {
  return channel < 4;
}

static bool isValidMotorMask(uint8_t motorMask) {
  return motorMask == 0xFF || (motorMask > 0 && motorMask <= 0x0F);
}

static int16_t floatToX10(float value) {
  return (int16_t)(value * 10.0f);
}

static bool readRegisterBlock(uint8_t regAddr, uint8_t* data, uint8_t length) {
  if (data == nullptr || length == 0) {
    return false;
  }

  Wire.beginTransmission(EXTEND_I2C_ADDR);
  Wire.write(regAddr);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  uint8_t received = Wire.requestFrom((uint8_t)EXTEND_I2C_ADDR, length);
  if (received != length || Wire.available() < length) {
    while (Wire.available()) {
      Wire.read();
    }
    return false;
  }

  for (uint8_t i = 0; i < length; ++i) {
    data[i] = Wire.read();
  }
  return true;
}

static bool writeRegisterBlock(uint8_t regAddr, const uint8_t* data, uint8_t length) {
  if (data == nullptr || length == 0) {
    return false;
  }

  Wire.beginTransmission(EXTEND_I2C_ADDR);
  Wire.write(regAddr);
  Wire.write(data, length);
  return Wire.endTransmission() == 0;
}

// Cache chassis speed commands so unchanged targets are not sent repeatedly.
static bool g_motorPwmCacheValid[4] = {false, false, false, false};
static int16_t g_motorPwmCache[4] = {0, 0, 0, 0};
static bool g_motorRpmCacheValid[4] = {false, false, false, false};
static int16_t g_motorRpmCache[4] = {0, 0, 0, 0};
static bool g_syncSpeedCacheValid = false;
static int16_t g_syncRpm10Cache[4] = {0, 0, 0, 0};
static constexpr uint8_t kSyncCommandWriteAttempts = 3;
static constexpr uint32_t kSyncCommandRetryDelayMs = 2;

static void invalidateSingleMotorSpeedCache() {
  for (int i = 0; i < 4; i++) {
    g_motorPwmCacheValid[i] = false;
    g_motorRpmCacheValid[i] = false;
  }
}

static void invalidateMotorChannelSpeedCache(uint8_t channel) {
  if (!isValidMotorChannel(channel)) {
    return;
  }
  g_motorPwmCacheValid[channel] = false;
  g_motorRpmCacheValid[channel] = false;
}

static void invalidateSyncSpeedCache() {
  g_syncSpeedCacheValid = false;
}

static void invalidateAllMotorSpeedCache() {
  invalidateSingleMotorSpeedCache();
  invalidateSyncSpeedCache();
}

void invalidateMotorSyncSpeedCache() {
  invalidateSyncSpeedCache();
}

// =========================================================
// I2C 基础读写工具函数 (小端模式)
// =========================================================
void writeReg8(uint8_t regAddr, uint8_t value) {
  Wire.beginTransmission(EXTEND_I2C_ADDR);
  Wire.write(regAddr);
  Wire.write(value);
  Wire.endTransmission();
}

void writeReg16(uint8_t regAddr, int16_t value) {
  Wire.beginTransmission(EXTEND_I2C_ADDR);
  Wire.write(regAddr);
  Wire.write((uint8_t)(value & 0xFF));
  Wire.write((uint8_t)((value >> 8) & 0xFF));
  Wire.endTransmission();
}

void writeReg32(uint8_t regAddr, int32_t value) {
  Wire.beginTransmission(EXTEND_I2C_ADDR);
  Wire.write(regAddr);
  Wire.write((uint8_t)(value & 0xFF));
  Wire.write((uint8_t)((value >> 8) & 0xFF));
  Wire.write((uint8_t)((value >> 16) & 0xFF));
  Wire.write((uint8_t)((value >> 24) & 0xFF));
  Wire.endTransmission();
}

void writeRegFloat(uint8_t regAddr, float value) {
  union {
    float floatValue;
    uint32_t rawValue;
  } converter;
  converter.floatValue = value;
  writeReg32(regAddr, (int32_t)converter.rawValue);
}

uint8_t readReg8(uint8_t regAddr) {
  Wire.beginTransmission(EXTEND_I2C_ADDR);
  Wire.write(regAddr);
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)EXTEND_I2C_ADDR, (uint8_t)1);
  if (Wire.available()) {
    return Wire.read();
  }
  return 0;
}

uint16_t readReg16(uint8_t regAddr) {
  Wire.beginTransmission(EXTEND_I2C_ADDR);
  Wire.write(regAddr);
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)EXTEND_I2C_ADDR, (uint8_t)2);
  if (Wire.available() >= 2) {
    uint8_t lo = Wire.read();
    uint8_t hi = Wire.read();
    return (lo | (hi << 8));
  }
  return 0;
}

int32_t readReg32(uint8_t regAddr) {
  Wire.beginTransmission(EXTEND_I2C_ADDR);
  Wire.write(regAddr);
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)EXTEND_I2C_ADDR, (uint8_t)4);
  if (Wire.available() >= 4) {
    uint32_t b0 = Wire.read();
    uint32_t b1 = Wire.read();
    uint32_t b2 = Wire.read();
    uint32_t b3 = Wire.read();
    return (int32_t)(b0 | (b1 << 8) | (b2 << 16) | (b3 << 24));
  }
  return 0;
}

void unlockProtectedMotorConfig() {
  // The key must be a standalone write immediately before the protected command.
  writeReg8(REG_SAFETY_KEY, SAFETY_KEY_VALUE);
}

// =========================================================
// 系统信息与状态函数
// =========================================================

uint16_t readDeviceId() {
  return readReg16(REG_DEVICE_ID_L);
}

uint16_t readFirmwareVersion() {
  return readReg16(REG_FW_VER_L);
}

uint8_t readBoardStatus() {
  return readReg8(REG_MOTOR_LOCK);
}

uint8_t getMotorLockMask() {
  return readBoardStatus() & 0x0F;
}

bool boardHasI2cError() {
  return (readBoardStatus() & BOARD_STATUS_I2C_ERROR) != 0;
}

bool boardHasRejectedCommand() {
  return (readBoardStatus() & BOARD_STATUS_COMMAND_REJECTED) != 0;
}

bool boardHasFeedbackDirectionError() {
  return (readBoardStatus() & BOARD_STATUS_FEEDBACK_DIR_ERROR) != 0;
}

// =========================================================
// 编码电机高级控制函数
// =========================================================

// 设置电机机械配置
void setMotorConfig(uint8_t channel, uint8_t polePairs, float gearRatio, float wheelDia, uint32_t ppr) {
  if (!isValidMotorChannel(channel)) {
    return;
  }

  writeReg8(REG_CFG_MOTOR_SEL, channel);
  writeReg8(REG_CFG_POLE_PAIRS, polePairs);
  writeRegFloat(REG_CFG_GEAR_RATIO, gearRatio);
  writeRegFloat(REG_CFG_WHEEL_DIA, wheelDia);
  writeReg32(REG_CFG_PPR, ppr);
  unlockProtectedMotorConfig();
  writeReg8(REG_CFG_CFG_APPLY, 1);
}

// 设置电机 PID
void setMotorPID(uint8_t channel, float kp, float ki, float kd) {
  if (!isValidMotorChannel(channel)) {
    return;
  }

  writeReg8(REG_CFG_MOTOR_SEL, channel);
  writeRegFloat(REG_CFG_PID_KP_B0, kp);
  writeRegFloat(REG_CFG_PID_KI_B0, ki);
  writeRegFloat(REG_CFG_PID_KD_B0, kd);
  unlockProtectedMotorConfig();
  writeReg8(REG_CFG_PID_APPLY, 1);
}

// 设置电机 PWM 开环速度
// channel: 0-3, pwm: 目标 PWM (正/负代表方向)
void setMotorSpeedPwm(uint8_t channel, int16_t pwm) {
  if (!isValidMotorChannel(channel)) {
    return;
  }

  if (g_motorPwmCacheValid[channel] && g_motorPwmCache[channel] == pwm) {
    return;
  }

  writeReg16(REG_MOTOR1_PWM_L + channel * 2, pwm);
  g_motorPwmCacheValid[channel] = true;
  g_motorPwmCache[channel] = pwm;
  g_motorRpmCacheValid[channel] = false;
  invalidateSyncSpeedCache();
}

// 设置电机 RPM 速度环
// channel: 0-3, rpm: 目标转速 RPM (正/负代表方向)
void setMotorSpeedRpm(uint8_t channel, float rpm) {
  if (!isValidMotorChannel(channel)) {
    return;
  }

  int16_t rpm10 = floatToX10(rpm);
  if (g_motorRpmCacheValid[channel] && g_motorRpmCache[channel] == rpm10) {
    return;
  }

  writeReg16(REG_MOTOR1_RPM_L + channel * 2, rpm10);
  g_motorRpmCacheValid[channel] = true;
  g_motorRpmCache[channel] = rpm10;
  g_motorPwmCacheValid[channel] = false;
  invalidateSyncSpeedCache();
}

// 读取电机实测 RPM
float getMotorSpeedRpm(uint8_t channel) {
  if (!isValidMotorChannel(channel)) {
    return 0.0f;
  }

  int16_t rpm10 = (int16_t)readReg16(REG_MOTOR1_ACTUAL_RPM_L + channel * 2);
  return (float)rpm10 / 10.0f;
}

// 设置电机位置锁定掩码
// lockMask: bit0~3 分别对应 Motor1~Motor4
void setMotorLockMask(uint8_t lockMask) {
  lockMask &= 0x0F;
  if (lockMask != 0) {
    unlockProtectedMotorConfig();
  }
  writeReg8(REG_MOTOR_LOCK, lockMask);
  invalidateAllMotorSpeedCache();
}

// 设置单路电机位置锁定
void setMotorLock(uint8_t channel, bool enable) {
  if (!isValidMotorChannel(channel)) {
    return;
  }

  uint8_t lockMask = getMotorLockMask();
  if (enable) {
    lockMask |= (uint8_t)(1 << channel);
  } else {
    lockMask &= (uint8_t)~(1 << channel);
  }
  setMotorLockMask(lockMask);
}

// 设置编码器极性与输出方向极性
// encoderPolarity/outputDirection 通常取 1 或 -1，具体含义以固件配置为准
void setMotorDirection(uint8_t channel, int8_t encoderPolarity, int8_t outputDirection) {
  if (!isValidMotorChannel(channel)) {
    return;
  }

  uint8_t directionBlock[8];
  if (!readRegisterBlock(REG_ENCODER_POL1, directionBlock, sizeof(directionBlock))) {
    return;
  }

  directionBlock[channel] = (uint8_t)encoderPolarity;
  directionBlock[4 + channel] = (uint8_t)outputDirection;
  unlockProtectedMotorConfig();
  if (writeRegisterBlock(REG_ENCODER_POL1, directionBlock, sizeof(directionBlock))) {
    invalidateMotorChannelSpeedCache(channel);
    invalidateSyncSpeedCache();
  }
}

bool setMotorDirectionBlock(const int8_t encoderPolarities[4], const int8_t outputDirections[4]) {
  if (encoderPolarities == nullptr || outputDirections == nullptr) {
    return false;
  }

  uint8_t directionBlock[8];
  for (uint8_t i = 0; i < 4; ++i) {
    directionBlock[i] = (uint8_t)encoderPolarities[i];
    directionBlock[4 + i] = (uint8_t)outputDirections[i];
  }

  unlockProtectedMotorConfig();
  if (!writeRegisterBlock(REG_ENCODER_POL1, directionBlock, sizeof(directionBlock))) {
    return false;
  }

  invalidateAllMotorSpeedCache();
  return true;
}

// 主动刹车单路电机
void motorBrake(uint8_t channel) {
  if (!isValidMotorChannel(channel)) {
    return;
  }

  motorBrakeMask((uint8_t)(1 << channel));
}

// 主动刹车多路电机
void motorBrakeMask(uint8_t motorMask) {
  writeReg8(REG_MOTOR_BRAKE, motorMask & 0x0F);
  invalidateAllMotorSpeedCache();
}

static bool startMotorCalibration(uint8_t motorMask) {
  if (!isValidMotorMask(motorMask)) {
    return false;
  }

  const uint8_t clearCommand = 0;
  if (!writeRegisterBlock(REG_MOTOR_CALIB, &clearCommand, 1)) {
    return false;
  }
  delay(2);

  const uint8_t safetyKey = SAFETY_KEY_VALUE;
  if (!writeRegisterBlock(REG_SAFETY_KEY, &safetyKey, 1) ||
      !writeRegisterBlock(REG_MOTOR_CALIB, &motorMask, 1)) {
    return false;
  }

  invalidateAllMotorSpeedCache();
  return true;
}

// 自动校准单路电机（异步启动）
void motorAutoCalibrate(uint8_t channel) {
  if (isValidMotorChannel(channel)) {
    startMotorCalibration((uint8_t)(1 << channel));
  }
}

// 自动校准全部电机（异步启动）
void motorAutoCalibrateAll() {
  startMotorCalibration(0xFF);
}

// 距离控制行进
// channel: 0-3, dist_cm: 目标距离(负=反转), rpm: 巡航转速
void moveMotorDistance(uint8_t channel, float dist_cm, float rpm) {
  if (!isValidMotorChannel(channel)) {
    return;
  }

  int32_t dist10  = (int32_t)(dist_cm * 10);
  uint16_t rpm10  = (uint16_t)(rpm * 10);
  writeReg8(REG_DIST_EXEC, 0);
  writeReg32(REG_DIST1_CM_B0 + channel * 4, dist10);
  writeReg16(REG_DIST1_RPM_L + channel * 2, rpm10);
  writeReg8(REG_DIST_EXEC, (1 << channel));
  invalidateAllMotorSpeedCache();
}

// 查询单路距离控制是否完成
bool motorDistanceDone(uint8_t channel) {
  if (!isValidMotorChannel(channel)) {
    return true;
  }

  uint8_t status = readReg8(REG_DIST_STATUS) & MOTOR_STATUS_DISTANCE_MASK;
  return (status & (1 << channel)) == 0;
}

MotorCalibrationStatus getMotorCalibrationStatus() {
  // A queued command must be checked first; firmware atomically changes it to RUNNING.
  if (readReg8(REG_MOTOR_CALIB) != 0) {
    return MOTOR_CALIB_PENDING;
  }

  uint8_t status = readReg8(REG_DIST_STATUS);
  if ((status & MOTOR_STATUS_CALIBRATION_RUNNING) != 0) {
    return MOTOR_CALIB_RUNNING;
  }
  if ((status & MOTOR_STATUS_CALIBRATION_FAILED) != 0) {
    return MOTOR_CALIB_FAILED;
  }
  if ((status & MOTOR_STATUS_CALIBRATION_SUCCESS) != 0) {
    return MOTOR_CALIB_SUCCESS;
  }
  return MOTOR_CALIB_IDLE;
}

bool motorCalibrationBusy() {
  MotorCalibrationStatus status = getMotorCalibrationStatus();
  return status == MOTOR_CALIB_PENDING || status == MOTOR_CALIB_RUNNING;
}

bool motorCalibrationSucceeded() {
  return getMotorCalibrationStatus() == MOTOR_CALIB_SUCCESS;
}

bool motorCalibrationFailed() {
  return getMotorCalibrationStatus() == MOTOR_CALIB_FAILED;
}

bool calibrateMotors(uint8_t motorMask, uint32_t timeoutMs) {
  if (!startMotorCalibration(motorMask)) {
    return false;
  }

  uint32_t startMs = millis();
  while (true) {
    MotorCalibrationStatus status = getMotorCalibrationStatus();
    if (status == MOTOR_CALIB_SUCCESS) {
      return true;
    }
    if (status == MOTOR_CALIB_FAILED || status == MOTOR_CALIB_IDLE) {
      return false;
    }
    if ((uint32_t)(millis() - startMs) >= timeoutMs) {
      return false;
    }
    delay(100);
  }
}

// 同步运动控制 (麦克纳姆轮等多路同步场景)
// rpms[4]: 4路目标RPM(正/负代表方向, 0=不参与), dist_cm: 运行距离(0=纯速度环)
bool motorSyncMove(const float rpms[4], float dist_cm) {
  if (rpms == nullptr || dist_cm < 0.0f || dist_cm > 6553.5f) {
    return false;
  }

  int16_t rpm10s[4];
  for (int i = 0; i < 4; i++) {
    rpm10s[i] = floatToX10(rpms[i]);
  }
  uint16_t dist10 = (uint16_t)(dist_cm * 10.0f);
  bool isSpeedLoop = (dist10 == 0);

  if (isSpeedLoop && g_syncSpeedCacheValid) {
    bool speedUnchanged = true;
    for (int i = 0; i < 4; i++) {
      if (g_syncRpm10Cache[i] != rpm10s[i]) {
        speedUnchanged = false;
        break;
      }
    }
    if (speedUnchanged) {
      return true;
    }
  }

  writeReg8(REG_SYNC_EXEC, 0);

  uint8_t transmissionResult = 0;
  for (uint8_t attempt = 0; attempt < kSyncCommandWriteAttempts; ++attempt) {
    Wire.beginTransmission(EXTEND_I2C_ADDR);
    Wire.write(REG_SYNC_RPM1_L);
    for (int i = 0; i < 4; i++) {
      int16_t rpm10 = rpm10s[i];
      Wire.write((uint8_t)(rpm10 & 0xFF));
      Wire.write((uint8_t)((rpm10 >> 8) & 0xFF));
    }
    Wire.write((uint8_t)(dist10 & 0xFF));
    Wire.write((uint8_t)((dist10 >> 8) & 0xFF));
    Wire.write(0x01); // REG_SYNC_EXEC: 触发同步运动
    transmissionResult = Wire.endTransmission();
    if (transmissionResult == 0) {
      break;
    }
    if (attempt + 1 < kSyncCommandWriteAttempts) {
      delay(kSyncCommandRetryDelayMs);
    }
  }

  if (transmissionResult != 0) {
    Serial.print(F("[motor] sync command write failed, i2c="));
    Serial.println(transmissionResult);
    invalidateSyncSpeedCache();
    if (!isSpeedLoop) {
      invalidateSingleMotorSpeedCache();
    }
    return false;
  }

  if (isSpeedLoop) {
    for (int i = 0; i < 4; i++) {
      g_syncRpm10Cache[i] = rpm10s[i];
    }
    g_syncSpeedCacheValid = true;
    invalidateSingleMotorSpeedCache();
  } else {
    invalidateAllMotorSpeedCache();
  }
  return true;
}

// 读取编码器计数
int32_t encoderGetCount(uint8_t channel) {
  if (!isValidMotorChannel(channel)) {
    return 0;
  }

  return readReg32(REG_ENC1_B0 + channel * 4);
}

// 复位单路编码器
void encoderReset(uint8_t channel) {
  if (!isValidMotorChannel(channel)) {
    return;
  }

  encoderResetMask((uint8_t)(1 << channel));
}

// 复位多路编码器
void encoderResetMask(uint8_t motorMask) {
  writeReg8(REG_ENC_RESET, motorMask & 0x0F);
}

// 读取某通道某方向的自动校准结果
void readMotorCalibration(uint8_t channel, bool positiveDir, uint16_t* minStart, uint16_t* minRun, float* feedForward) {
  if (!isValidMotorChannel(channel)) {
    if (minStart) *minStart = 0;
    if (minRun) *minRun = 0;
    if (feedForward) *feedForward = 0.0f;
    return;
  }

  writeReg8(REG_CFG_MOTOR_SEL, channel);
  writeReg8(REG_CFG_CAL_DIR, positiveDir ? 1 : 0);
  delay(5);

  if (minStart) {
    *minStart = readReg16(REG_CFG_MIN_START_L);
  }
  if (minRun) {
    *minRun = readReg16(REG_CFG_MIN_RUN_L);
  }
  if (feedForward) {
    *feedForward = (float)readReg16(REG_CFG_FF_X10_L) / 10.0f;
  }
}

// 文档 API 命名兼容封装
void motor_set_config(uint8_t channel, uint8_t polePairs, float gearRatio, float wheelDia, uint32_t ppr) {
  setMotorConfig(channel, polePairs, gearRatio, wheelDia, ppr);
}

void motor_set_pid(uint8_t channel, float kp, float ki, float kd) {
  setMotorPID(channel, kp, ki, kd);
}

void motor_set_speed(uint8_t channel, int16_t pwm) {
  setMotorSpeedPwm(channel, pwm);
}

void motor_set_speed_rpm(uint8_t channel, float rpm) {
  setMotorSpeedRpm(channel, rpm);
}

float motor_get_speed_rpm(uint8_t channel) {
  return getMotorSpeedRpm(channel);
}

void motor_set_lock_mask(uint8_t lockMask) {
  setMotorLockMask(lockMask);
}

void motor_set_lock(uint8_t channel, bool enable) {
  setMotorLock(channel, enable);
}

void motor_set_direction(uint8_t channel, int8_t encoderPolarity, int8_t outputDirection) {
  setMotorDirection(channel, encoderPolarity, outputDirection);
}

bool motor_set_direction_block(const int8_t encoderPolarities[4], const int8_t outputDirections[4]) {
  return setMotorDirectionBlock(encoderPolarities, outputDirections);
}

void motor_brake(uint8_t channel) {
  motorBrake(channel);
}

void motor_brake_mask(uint8_t motorMask) {
  motorBrakeMask(motorMask);
}

void motor_auto_calibrate(uint8_t channel) {
  motorAutoCalibrate(channel);
}

void motor_auto_calibrate_all() {
  motorAutoCalibrateAll();
}

void motor_set_distance_cm(uint8_t channel, float dist_cm, float rpm) {
  moveMotorDistance(channel, dist_cm, rpm);
}

bool motor_distance_done(uint8_t channel) {
  return motorDistanceDone(channel);
}

MotorCalibrationStatus motor_calibration_status() {
  return getMotorCalibrationStatus();
}

bool motor_calibration_busy() {
  return motorCalibrationBusy();
}

bool motor_calibration_succeeded() {
  return motorCalibrationSucceeded();
}

bool motor_calibration_failed() {
  return motorCalibrationFailed();
}

bool motor_calibrate(uint8_t motorMask, uint32_t timeout_ms) {
  return calibrateMotors(motorMask, timeout_ms);
}

void motor_set_sync_motion(const float rpms[4], float dist_cm) {
  motorSyncMove(rpms, dist_cm);
}

int32_t encoder_get_count(uint8_t channel) {
  return encoderGetCount(channel);
}

void encoder_reset(uint8_t channel) {
  encoderReset(channel);
}

void encoder_reset_mask(uint8_t motorMask) {
  encoderResetMask(motorMask);
}

// =========================================================
// PWM 舵机控制函数
// =========================================================

// PWM 舵机控制 (各通道独立运行时间)
// channel: 0-3, pulse_us: 500-2500 (0=卸力), time_ms: 运行时间(0=立即到达)
void setPwmServo(uint8_t channel, uint16_t pulse_us, uint16_t time_ms) {
  if (!isValidMotorChannel(channel)) {
    return;
  }

  writeReg16(REG_SERVO1_TIME_L + channel * 2, time_ms);
  writeReg16(REG_SERVO1_L      + channel * 2, pulse_us);
}

void setPwmServoPulse(uint8_t channel, uint16_t pulse_us, uint16_t time_ms) {
  setPwmServo(channel, pulse_us, time_ms);
}

uint16_t getPwmServoPulse(uint8_t channel) {
  if (!isValidMotorChannel(channel)) {
    return 0;
  }

  return readReg16(REG_SERVO1_L + channel * 2);
}

void setPwmServoOffset(uint8_t channel, int16_t offset_us) {
  if (!isValidMotorChannel(channel)) {
    return;
  }

  writeReg16(REG_SERVO1_OFFSET_L + channel * 2, offset_us);
}

int16_t getPwmServoOffset(uint8_t channel) {
  if (!isValidMotorChannel(channel)) {
    return 0;
  }

  return (int16_t)readReg16(REG_SERVO1_OFFSET_L + channel * 2);
}

void pwm_servo_set_pulse(uint8_t channel, uint16_t pulse_us, uint16_t time_ms) {
  setPwmServoPulse(channel, pulse_us, time_ms);
}

uint16_t pwm_servo_get_pulse(uint8_t channel) {
  return getPwmServoPulse(channel);
}

void pwm_servo_set_offset(uint8_t channel, int16_t offset_us) {
  setPwmServoOffset(channel, offset_us);
}

int16_t pwm_servo_get_offset(uint8_t channel) {
  return getPwmServoOffset(channel);
}

// =========================================================
// 低压报警、电池与蜂鸣器函数
// =========================================================

void setLowVoltageAlarmEnabled(bool enable) {
  writeReg8(REG_LOW_VOLTAGE_CTRL, enable ? 1 : 0);
}

bool isLowVoltageAlarmEnabled() {
  return (readReg8(REG_LOW_VOLTAGE_CTRL) & 0x01) != 0;
}

bool isLowVoltageAlarmActive() {
  return (readReg8(REG_LOW_VOLTAGE_CTRL) & 0x80) != 0;
}

bool setLowVoltageThresholdMv(uint16_t threshold_mv) {
  if (threshold_mv < LOW_VOLTAGE_MIN_MV || threshold_mv > LOW_VOLTAGE_MAX_MV) {
    return false;
  }

  uint8_t thresholdBytes[2] = {
    (uint8_t)(threshold_mv & 0xFF),
    (uint8_t)(threshold_mv >> 8),
  };
  return writeRegisterBlock(REG_LOW_VOLTAGE_MV_L, thresholdBytes, sizeof(thresholdBytes));
}

uint16_t readLowVoltageThresholdMv() {
  return readReg16(REG_LOW_VOLTAGE_MV_L);
}

uint16_t readBatteryMv() {
  return readReg16(REG_BATTERY_MV_L);
}

void setBuzzer(uint16_t freq_hz, uint8_t duty_percent) {
  if (duty_percent > 99) {
    duty_percent = 99;
  }

  Wire.beginTransmission(EXTEND_I2C_ADDR);
  Wire.write(REG_BUZZER_FREQ_L);
  Wire.write((uint8_t)(freq_hz & 0xFF));
  Wire.write((uint8_t)((freq_hz >> 8) & 0xFF));
  Wire.write(duty_percent);
  Wire.endTransmission();
}

void low_voltage_alarm_set_enabled(bool enable) {
  setLowVoltageAlarmEnabled(enable);
}

bool low_voltage_alarm_is_enabled() {
  return isLowVoltageAlarmEnabled();
}

bool low_voltage_alarm_is_active() {
  return isLowVoltageAlarmActive();
}

bool low_voltage_alarm_set_threshold_mv(uint16_t threshold_mv) {
  return setLowVoltageThresholdMv(threshold_mv);
}

uint16_t low_voltage_alarm_get_threshold_mv() {
  return readLowVoltageThresholdMv();
}

uint16_t battery_get_mv() {
  return readBatteryMv();
}

void buzzer_set(uint16_t freq_hz, uint8_t duty_percent) {
  setBuzzer(freq_hz, duty_percent);
}

// =========================================================
// 串口总线舵机透传函数
// =========================================================

// 总线舵机透传发送
// exec_mode: 1=发送并等待回复(默认), 2=发送不等待回复(广播写命令)
void busServoSend(const uint8_t* packet, uint8_t len, uint8_t exec_mode) {
  if (packet == nullptr || len == 0 || len > BUS_SERVO_TX_MAX_LEN ||
      (exec_mode != 1 && exec_mode != 2)) {
    return;
  }

  Wire.beginTransmission(EXTEND_I2C_ADDR);
  Wire.write(REG_BUS_TX_LEN);
  Wire.write(len);
  Wire.endTransmission();

  Wire.beginTransmission(EXTEND_I2C_ADDR);
  Wire.write(REG_BUS_TX_BUF);
  for (uint8_t i = 0; i < len; i++) {
    Wire.write(packet[i]);
  }
  Wire.endTransmission();
  writeReg8(REG_BUS_EXEC, exec_mode);
}

// 读取总线舵机回复数据
// 返回接收到的字节数，数据写入 rxBuf
uint8_t busServoRecv(uint8_t* rxBuf, uint8_t maxLen, uint16_t timeout_ms) {
  if (rxBuf == nullptr || maxLen == 0) {
    return 0;
  }

  uint32_t start = millis();
  uint8_t rxLen = 0;
  while (millis() - start < timeout_ms) {
    Wire.beginTransmission(EXTEND_I2C_ADDR);
    Wire.write(REG_BUS_RX_LEN);
    Wire.endTransmission(false);
    Wire.requestFrom((uint8_t)EXTEND_I2C_ADDR, (uint8_t)1);
    if (Wire.available()) {
      rxLen = Wire.read();
    }
    if (rxLen > 0) break;
    delay(5);
  }
  if (rxLen == 0) return 0;
  if (rxLen > BUS_SERVO_RX_MAX_LEN) rxLen = BUS_SERVO_RX_MAX_LEN;
  if (rxLen > maxLen) rxLen = maxLen;

  Wire.beginTransmission(EXTEND_I2C_ADDR);
  Wire.write(REG_BUS_RX_BUF);
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)EXTEND_I2C_ADDR, rxLen);
  uint8_t cnt = 0;
  while (Wire.available() && cnt < rxLen) {
    rxBuf[cnt++] = Wire.read();
  }
  return cnt;
}

// 打印十六进制数据包
void printHexPacket(const char* prefix, const uint8_t* buf, uint8_t len) {
  Serial.print(prefix);
  for (uint8_t i = 0; i < len; i++) {
    Serial.printf(" %02X", buf[i]);
  }
  Serial.println();
}

// =========================================================
// 旧协议 (帧头 0x55 0x55) 总线舵机组包函数
// 帧格式: [0x55][0x55][ID][Len][Cmd][Params...][CheckSum]
// 总长度 = Len + 3
// CheckSum = ~(ID + Len + Cmd + Params) 低8位
// =========================================================

uint8_t oldProtocolCheckSum(const uint8_t* buf) {
  uint16_t temp = 0;
  for (uint8_t i = 2; i < buf[3] + 2; i++) {
    temp += buf[i];
  }
  return (uint8_t)(~temp);
}

// 旧协议: 设置舵机在指定时间转到指定角度 (Cmd=1)
// position: 0~1000, time_ms: 运行时间(ms)
void oldBusServoMoveTimeWrite(uint8_t id, uint16_t position, uint16_t time_ms) {
  uint8_t buf[10];
  buf[0] = 0x55; buf[1] = 0x55;
  buf[2] = id;   buf[3] = 7;   buf[4] = 1;
  buf[5] = (uint8_t)(position & 0xFF);
  buf[6] = (uint8_t)(position >> 8);
  buf[7] = (uint8_t)(time_ms & 0xFF);
  buf[8] = (uint8_t)(time_ms >> 8);
  buf[9] = oldProtocolCheckSum(buf);
  busServoSend(buf, 10);
}

// 旧协议: 读取舵机设置的角度和时间参数 (Cmd=2)
void oldBusServoMoveTimeRead(uint8_t id) {
  uint8_t buf[6];
  buf[0] = 0x55; buf[1] = 0x55;
  buf[2] = id;   buf[3] = 3;   buf[4] = 2;
  buf[5] = oldProtocolCheckSum(buf);
  busServoSend(buf, 6);
}

// 旧协议: 预置舵机在指定时间转到指定角度 (Cmd=7)
void oldBusServoMoveTimeWaitWrite(uint8_t id, uint16_t position, uint16_t time_ms) {
  uint8_t buf[10];
  buf[0] = 0x55; buf[1] = 0x55;
  buf[2] = id;   buf[3] = 7;   buf[4] = 7;
  buf[5] = (uint8_t)(position & 0xFF);
  buf[6] = (uint8_t)(position >> 8);
  buf[7] = (uint8_t)(time_ms & 0xFF);
  buf[8] = (uint8_t)(time_ms >> 8);
  buf[9] = oldProtocolCheckSum(buf);
  busServoSend(buf, 10);
}

// 旧协议: 触发预置命令执行 (Cmd=11)
void oldBusServoMoveStart(uint8_t id) {
  uint8_t buf[6];
  buf[0] = 0x55; buf[1] = 0x55;
  buf[2] = id;   buf[3] = 3;   buf[4] = 11;
  buf[5] = oldProtocolCheckSum(buf);
  busServoSend(buf, 6, (id == 254) ? 2 : 1);
}

// 旧协议: 立即停止 (Cmd=12)
void oldBusServoMoveStop(uint8_t id) {
  uint8_t buf[6];
  buf[0] = 0x55; buf[1] = 0x55;
  buf[2] = id;   buf[3] = 3;   buf[4] = 12;
  buf[5] = oldProtocolCheckSum(buf);
  busServoSend(buf, 6);
}

// 旧协议: 读取舵机ID (Cmd=14)
// 注意: 广播(id=254)时总线上只能有一个舵机才能正常回复, 否则会冲突
void oldBusServoIDRead(uint8_t id) {
  uint8_t buf[6];
  buf[0] = 0x55; buf[1] = 0x55;
  buf[2] = id;   buf[3] = 3;   buf[4] = 14;
  buf[5] = oldProtocolCheckSum(buf);
  busServoSend(buf, 6);
}

// 旧协议: 读取当前位置 (Cmd=28)
void oldBusServoPosRead(uint8_t id) {
  uint8_t buf[6];
  buf[0] = 0x55; buf[1] = 0x55;
  buf[2] = id;   buf[3] = 3;   buf[4] = 28;
  buf[5] = oldProtocolCheckSum(buf);
  busServoSend(buf, 6);
}

// 旧协议: 读取当前电压 (Cmd=27)
void oldBusServoVinRead(uint8_t id) {
  uint8_t buf[6];
  buf[0] = 0x55; buf[1] = 0x55;
  buf[2] = id;   buf[3] = 3;   buf[4] = 27;
  buf[5] = oldProtocolCheckSum(buf);
  busServoSend(buf, 6);
}

// 旧协议: 读取当前温度 (Cmd=26)
void oldBusServoTempRead(uint8_t id) {
  uint8_t buf[6];
  buf[0] = 0x55; buf[1] = 0x55;
  buf[2] = id;   buf[3] = 3;   buf[4] = 26;
  buf[5] = oldProtocolCheckSum(buf);
  busServoSend(buf, 6);
}

// 旧协议: 设置舵机运行模式 (Cmd=29)
// mode: 0=舵机模式, 1=电机模式; speed: 电机模式速度 -1000~1000
void oldBusServoOrMotorModeWrite(uint8_t id, uint8_t mode, int16_t speed) {
  uint8_t buf[10];
  buf[0] = 0x55; buf[1] = 0x55;
  buf[2] = id;   buf[3] = 7;   buf[4] = 29;
  buf[5] = mode; buf[6] = 0;
  buf[7] = (uint8_t)(speed & 0xFF);
  buf[8] = (uint8_t)(speed >> 8);
  buf[9] = oldProtocolCheckSum(buf);
  busServoSend(buf, 10);
}

// 旧协议: 设置舵机使能/卸力 (Cmd=31)
// enable: 1=使能(锁定), 0=卸力(释放)
void oldBusServoLoadWrite(uint8_t id, uint8_t enable) {
  uint8_t buf[7];
  buf[0] = 0x55; buf[1] = 0x55;
  buf[2] = id;   buf[3] = 4;   buf[4] = 31;
  buf[5] = enable;
  buf[6] = oldProtocolCheckSum(buf);
  busServoSend(buf, 7);
}

// 旧协议: 解析回复数据包并打印
void oldProtocolParseReply(const uint8_t* buf, uint8_t len) {
  if (len < 6 || buf[0] != 0x55 || buf[1] != 0x55) {
    Serial.println("[Old] Invalid reply");
    return;
  }
  uint8_t id   = buf[2];
  uint8_t pLen = buf[3];
  uint8_t cmd  = buf[4];

  switch (cmd) {
    case 2:
      if (pLen >= 7) {
        int16_t pos  = buf[5] | (buf[6] << 8);
        int16_t time = buf[7] | (buf[8] << 8);
        Serial.printf("[Old] ID=%d MoveTimeRead: pos=%d, time=%d ms\n", id, pos, time);
      }
      break;
    case 14:
      if (pLen >= 4) Serial.printf("[Old] ID_READ reply: ID=%d\n", buf[5]);
      break;
    case 26:
      if (pLen >= 4) Serial.printf("[Old] ID=%d Temp: %d C\n", id, buf[5]);
      break;
    case 27:
      if (pLen >= 5) {
        int16_t vin = buf[5] | (buf[6] << 8);
        Serial.printf("[Old] ID=%d Vin: %d mV\n", id, vin);
      }
      break;
    case 28:
      if (pLen >= 5) {
        int16_t pos = buf[5] | (buf[6] << 8);
        Serial.printf("[Old] ID=%d Pos: %d\n", id, pos);
      }
      break;
    default:
      Serial.printf("[Old] ID=%d Cmd=%d reply, len=%d\n", id, cmd, pLen);
      break;
  }
}

// =========================================================
// 新协议 (帧头 0xFF 0xFF) 总线舵机组包函数
// 帧格式: [0xFF][0xFF][ID][Len][Cmd][Params...][CheckSum]
// 总长度 = Len + 4
// CheckSum = ~(ID + Len + Cmd + Params) 低8位
// =========================================================

static uint8_t g_lastReadAddr = 0;
static uint8_t g_lastReadLen  = 0;

uint8_t newProtocolCheckSum(const uint8_t* buf) {
  uint16_t temp = 0;
  for (uint8_t i = 2; i < buf[3] + 3; i++) {
    temp += buf[i];
  }
  return (uint8_t)(~temp);
}

// 新协议: 写数据 (Cmd=0x03)
void newBusServoWriteData(uint8_t id, uint8_t addr, const uint8_t* data, uint8_t dataLen) {
  uint8_t buf[48];
  buf[0] = 0xFF; buf[1] = 0xFF;
  buf[2] = id;
  buf[3] = dataLen + 3;
  buf[4] = 0x03;
  buf[5] = addr;
  for (uint8_t i = 0; i < dataLen; i++) {
    buf[6 + i] = data[i];
  }
  uint8_t totalLen = buf[3] + 4;
  buf[totalLen - 1] = newProtocolCheckSum(buf);
  busServoSend(buf, totalLen);
}

// 新协议: 写单字节
void newBusServoWriteByte(uint8_t id, uint8_t addr, uint8_t value) {
  newBusServoWriteData(id, addr, &value, 1);
}

// 新协议: 写双字节 (小端)
void newBusServoWriteWord(uint8_t id, uint8_t addr, int16_t value) {
  uint8_t data[2] = { (uint8_t)(value & 0xFF), (uint8_t)(value >> 8) };
  newBusServoWriteData(id, addr, data, 2);
}

// 新协议: 读数据 (Cmd=0x02)
void newBusServoReadData(uint8_t id, uint8_t addr, uint8_t readLen) {
  g_lastReadAddr = addr;
  g_lastReadLen  = readLen;
  uint8_t buf[8];
  buf[0] = 0xFF; buf[1] = 0xFF;
  buf[2] = id;   buf[3] = 4;   buf[4] = 0x02;
  buf[5] = addr; buf[6] = readLen;
  buf[7] = newProtocolCheckSum(buf);
  busServoSend(buf, 8);
}

// 新协议: 查询状态 (Cmd=0x01)
void newBusServoQueryStatus(uint8_t id) {
  uint8_t buf[6];
  buf[0] = 0xFF; buf[1] = 0xFF;
  buf[2] = id;   buf[3] = 2;   buf[4] = 0x01;
  buf[5] = newProtocolCheckSum(buf);
  busServoSend(buf, 6);
}

// 新协议: 异步写 (Cmd=0x04)
void newBusServoRegWrite(uint8_t id, uint8_t addr, const uint8_t* data, uint8_t dataLen) {
  uint8_t buf[48];
  buf[0] = 0xFF; buf[1] = 0xFF;
  buf[2] = id;
  buf[3] = dataLen + 3;
  buf[4] = 0x04;
  buf[5] = addr;
  for (uint8_t i = 0; i < dataLen; i++) {
    buf[6 + i] = data[i];
  }
  uint8_t totalLen = buf[3] + 4;
  buf[totalLen - 1] = newProtocolCheckSum(buf);
  busServoSend(buf, totalLen);
}

// 新协议: ACTION 触发异步写执行 (Cmd=0x05)
void newBusServoAction(uint8_t id) {
  uint8_t buf[6];
  buf[0] = 0xFF; buf[1] = 0xFF;
  buf[2] = id;   buf[3] = 2;   buf[4] = 0x05;
  buf[5] = newProtocolCheckSum(buf);
  busServoSend(buf, 6, (id == 254) ? 2 : 1);
}

// 新协议: 复位 (Cmd=0x06)
void newBusServoReset(uint8_t id) {
  uint8_t buf[6];
  buf[0] = 0xFF; buf[1] = 0xFF;
  buf[2] = id;   buf[3] = 2;   buf[4] = 0x06;
  buf[5] = newProtocolCheckSum(buf);
  busServoSend(buf, 6);
}

// 新协议: 便捷函数 - 设置目标位置 (位置模式, 适配 Feetech STS)
// position: 目标位置(step), duration_ms: 持续时间(ms)
void newBusServoSetPosition(uint8_t id, int16_t position, uint16_t duration_ms) {
  uint8_t data[6];
  data[0] = (uint8_t)(position & 0xFF);
  data[1] = (uint8_t)(position >> 8);
  data[2] = (uint8_t)(duration_ms & 0xFF);
  data[3] = (uint8_t)(duration_ms >> 8);
  data[4] = 0; // Speed L
  data[5] = 0; // Speed H
  newBusServoWriteData(id, 42, data, 6);
}

// 新协议: 便捷函数 - 设置输出使能
// enable: 1=使能(锁定), 0=卸力(释放)
void newBusServoSetEnable(uint8_t id, uint8_t enable) {
  newBusServoWriteByte(id, 40, enable);
}

// 新协议: 便捷函数 - 设置运行模式
// mode: 0=位置模式, 1=电机恒速, 2=PWM开环, 3=步进伺服
void newBusServoSetMode(uint8_t id, uint8_t mode) {
  newBusServoWriteByte(id, 33, mode);
}

// 新协议: 便捷函数 - 设置目标速度 (电机恒速模式)
void newBusServoSetSpeed(uint8_t id, int16_t speed) {
  newBusServoWriteWord(id, 46, speed);
}

// 新协议: 解析回复数据包并打印
// STS/SCS 舵机回复格式: [0xFF][0xFF][ID][Len][Error][Data...][Checksum]
void newProtocolParseReply(const uint8_t* buf, uint8_t len) {
  if (len < 6 || buf[0] != 0xFF || buf[1] != 0xFF) {
    Serial.println("[New] Invalid reply");
    return;
  }
  uint8_t id    = buf[2];
  uint8_t pLen  = buf[3];
  uint8_t error = buf[4];

  Serial.printf("[New] ID=%d, Error=0x%02X, Len=%d\n", id, error, pLen);
  if (error != 0x00) {
    Serial.printf("  -> Error: 0x%02X\n", error);
    return;
  }

  uint8_t dataLen = pLen - 2;
  if (dataLen > 0) {
    Serial.printf("  Data(%d):", dataLen);
    for (uint8_t i = 0; i < dataLen; i++) {
      Serial.printf(" %02X", buf[5 + i]);
    }
    Serial.println();

    uint8_t addr = g_lastReadAddr;
    if (addr == 56 && dataLen >= 2) {
      int16_t pos = buf[5] | (buf[6] << 8);
      Serial.printf("  -> Current Position: %d step\n", pos);
    } else if (addr == 58 && dataLen >= 2) {
      int16_t spd = buf[5] | (buf[6] << 8);
      Serial.printf("  -> Current Speed: %d step/s\n", spd);
    } else if (addr == 62 && dataLen >= 1) {
      Serial.printf("  -> Voltage: %.1f V\n", buf[5] * 0.1f);
    } else if (addr == 63 && dataLen >= 1) {
      Serial.printf("  -> Temperature: %d C\n", buf[5]);
    } else if (addr == 5 && dataLen >= 1) {
      Serial.printf("  -> Servo ID: %d\n", buf[5]);
    } else if (addr == 88 && dataLen >= 2) {
      int16_t cur = buf[5] | (buf[6] << 8);
      Serial.printf("  -> Current: %d mA\n", cur);
    }
  }
}

// =========================================================
// 通用: 发送并接收总线舵机回复 (自动识别协议类型并解析)
// =========================================================
void busServoSendAndPrint(const char* desc) {
  Serial.printf("--- %s ---\n", desc);
  delay(50);
  uint8_t rxBuf[BUS_SERVO_RX_MAX_LEN];
  uint8_t rxLen = busServoRecv(rxBuf, sizeof(rxBuf), 200);
  if (rxLen > 0) {
    printHexPacket("  RX:", rxBuf, rxLen);
    if (rxLen >= 6) {
      if (rxBuf[0] == 0x55 && rxBuf[1] == 0x55) {
        oldProtocolParseReply(rxBuf, rxLen);
      } else if (rxBuf[0] == 0xFF && rxBuf[1] == 0xFF) {
        newProtocolParseReply(rxBuf, rxLen);
      }
    }
  } else {
    Serial.println("  (No reply)");
  }
}
