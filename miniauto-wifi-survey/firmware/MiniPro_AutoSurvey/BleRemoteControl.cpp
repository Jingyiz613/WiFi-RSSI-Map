/**
 * @file BleRemoteControl.cpp
 * @brief miniAuto APP 遥控硬件控制层
 *
 * 本模块封装所有硬件动作：
 *   - 麦克纳姆轮运动（Mecanum）
 *   - 超声波模块（Ultrasound）
 *   - I2C PWM 舵机（setPwmServo）
 *   - 蜂鸣器低电量告警
 *   - 超声波避障循环
 *
 * 调用关系：
 *   FactoryControl.cpp → bleRemoteControlUpdate()
 *                      → bleRemoteUpdate()（接收 BLE NUS 帧）
 *                  → consume 命令 → 执行对应硬件动作
 */

#include "BleRemoteControl.h"

#include "ArmControl.h"
#include "BleRemote.h"
#include "Mecanum.h"
#include "BoardDriver.h"
#include "Ultrasound.h"

// =========================================================
// 超声波 + 3 点均值滤波
// =========================================================
#define FILTER_N 5
static Ultrasound s_ultrasound;
static int s_filterBuf[FILTER_N + 1] = {0};
static uint8_t s_filterCount = 0;

static int ultrasonicFilter() {
    s_filterBuf[FILTER_N] = (int)s_ultrasound.GetDistance();
    int sum = 0;
    for (int i = 0; i < FILTER_N; i++) {
        s_filterBuf[i] = s_filterBuf[i + 1];
        sum += s_filterBuf[i];
    }
    if (s_filterCount < FILTER_N) {
        s_filterCount++;
    }
    return sum / s_filterCount;
}

// =========================================================
// 速度参数
// =========================================================
static float s_maxLinearSpeedMps   = 0.5f;
static float s_maxAngularSpeedRadS = 3.0f;

static inline float speedToMps(uint8_t pct) {
    return (pct / 100.0f) * s_maxLinearSpeedMps;
}
static inline float speedToRadS(uint8_t pct) {
    return (pct / 100.0f) * s_maxAngularSpeedRadS;
}

// =========================================================
// 全局状态
// =========================================================
static uint8_t  s_speedUpdate = 50;   // 当前速度档位（0~100）
static uint8_t  s_avoidFlag   = 0;    // 避障模式开关
static bool     s_avoidTurning = false;
static bool     s_avoidLedEnabled = false;
enum AvoidMotionMode : uint8_t {
    AVOID_MOTION_NONE,
    AVOID_MOTION_FORWARD,
    AVOID_MOTION_TURN,
};
static AvoidMotionMode s_avoidMotionMode = AVOID_MOTION_NONE;
static uint8_t  s_rotFlag     = 0;    // 0=无旋转 1=左旋 2=右旋
static uint8_t  s_speedData   = 0;    // 当前是否有平移速度
static float    s_vx          = 0.0f;
static float    s_vy          = 0.0f;
static float    s_wz          = 0.0f;
static uint32_t s_lastVoltage = 8000; // mV，上次有效电压值
static uint8_t  s_buzCount    = 0;    // 蜂鸣计数（限制告警次数）
static uint32_t s_buzzerOffAt = 0;
static bool     s_buzzerCommandIssued = false;

static bool updateBatteryVoltage() {
    uint32_t raw = readBatteryMv();
    if (raw == 0) return false;

    int32_t diff = (int32_t)raw - (int32_t)s_lastVoltage;
    if (diff < 0) diff = -diff;
    if (diff >= 500) return false;    // 跳变过大，丢弃

    s_lastVoltage = raw;
    return true;
}

static void buzzerTone(uint16_t freq_hz, uint16_t duration_ms) {
    setBuzzer(freq_hz, 50);
    s_buzzerOffAt = millis() + duration_ms;
    s_buzzerCommandIssued = true;
}

static void buzzerUpdate() {
    if (s_buzzerOffAt != 0 && (int32_t)(millis() - s_buzzerOffAt) >= 0) {
        setBuzzer(0, 0);
        s_buzzerOffAt = 0;
        s_buzzerCommandIssued = true;
    }
}

// =========================================================
// 运动辅助
// =========================================================
struct MotionVector {
    float vxScale;
    float vyScale;
};

// APP state uses: 0=front, 2=right, 4=back, 6=left.
// The new chassis driver axes are rotated relative to the APP axes:
// APP front/back -> chassis -vy/+vy, APP left/right -> chassis -vx/+vx.
static const MotionVector kAppMotionMap[8] = {
    { 0.0f, -1.0f},   // 0 front
    { 1.0f, -1.0f},   // 1 front-right
    { 1.0f,  0.0f},   // 2 right
    { 1.0f,  1.0f},   // 3 back-right
    { 0.0f,  1.0f},   // 4 back
    {-1.0f,  1.0f},   // 5 back-left
    {-1.0f,  0.0f},   // 6 left
    {-1.0f, -1.0f},   // 7 front-left
};

static void driveAppMotion(uint8_t state, float lin_mps, float rot_radS) {
    const MotionVector& motion = kAppMotionMap[state];
    s_vx = lin_mps * motion.vxScale;
    s_vy = lin_mps * motion.vyScale;
    s_wz = rot_radS;
    mecanumSetVelocity(s_vx, s_vy, s_wz);
}

// =========================================================
// 电压监测（在 state=8 停止时调用）
// =========================================================
static void checkVoltage() {
    if (!updateBatteryVoltage()) return;

    if (s_lastVoltage <= 7000) {
        if (s_buzCount < 2 && s_buzzerOffAt == 0) {
            buzzerTone(800, 500);
            s_buzCount++;
        }
    } else {
        s_buzCount = 0;
        setBuzzer(0, 0);
        s_buzzerOffAt = 0;
        s_buzzerCommandIssued = true;
    }
}

// =========================================================
// CMD A — 运动状态处理
// =========================================================
static void handleMotionState(int state) {
    float lin = speedToMps(s_speedUpdate);
    float rot = speedToRadS(s_speedUpdate);

    switch (state) {
        // 8 方向平移
        case 0:
        case 1:
        case 2:
        case 3:
        case 4:
        case 5:
        case 6:
        case 7:
            s_speedData = s_speedUpdate;
            driveAppMotion((uint8_t)state, lin, 0.0f);
            break;
        // 停止（保留旋转分量）
        case 8: {
            s_speedData = 0;
            s_vx        = 0.0f;
            s_vy        = 0.0f;
            if      (s_rotFlag == 1) s_wz = +speedToRadS(s_speedUpdate);
            else if (s_rotFlag == 2) s_wz = -speedToRadS(s_speedUpdate);
            else                     s_wz = 0.0f;
            mecanumSetVelocity(s_vx, s_vy, s_wz);
            checkVoltage();
            break;
        }
        // 左旋（平移时减速旋转）
        case 9: {
            s_rotFlag = 1;
            float r   = (s_speedData == 0) ? rot : rot / 3.0f;
            s_wz      = +r;
            mecanumSetVelocity(s_vx, s_vy, s_wz);
            break;
        }
        // 右旋
        case 10: {
            s_rotFlag = 2;
            float r   = (s_speedData == 0) ? rot : rot / 3.0f;
            s_wz      = -r;
            mecanumSetVelocity(s_vx, s_vy, s_wz);
            break;
        }
        // 取消旋转
        case 11: {
            s_rotFlag = 0;
            s_wz      = 0.0f;
            mecanumSetVelocity(s_vx, s_vy, s_wz);
            break;
        }
        default: break;
    }
}

// =========================================================
// CMD F — 避障循环（在避障模式下每帧调用）
// =========================================================
static void handleAvoidLoop() {
    static const uint16_t kAvoidTurnEnterMm     = 400;
    static const uint16_t kAvoidForwardResumeMm = 500;
    static const uint8_t  kAvoidForwardSpeedPct = 26;
    static const uint8_t  kAvoidTurnSpeedPct    = 35;

    int dist = ultrasonicFilter();
    uint8_t s;

    // WonderLLM 设置 LED 后，避障仍可继续运动，但不再覆盖灯色。
    if (s_avoidLedEnabled) {
        if (dist > 0 && dist <= 80) {
            s_ultrasound.Breathing(1, 0, 0, 1, 0, 0);
        } else if (dist > 80 && dist <= 180) {
            s = (uint8_t)map(dist, 80, 180, 0, 255);
            s_ultrasound.Color(255 - s, 0, 0, 255 - s, 0, 0);
        } else if (dist > 180 && dist <= 320) {
            s = (uint8_t)map(dist, 180, 320, 0, 255);
            s_ultrasound.Color(0, 0, s, 0, 0, s);
        } else if (dist > 320 && dist <= 500) {
            s = (uint8_t)map(dist, 320, 500, 0, 255);
            s_ultrasound.Color(0, s, 255 - s, 0, s, 255 - s);
        } else if (dist > 500) {
            s_ultrasound.Color(0, 255, 0, 0, 255, 0);
        }
    }

    // 避障逻辑：小于 400mm 转向避让，大于等于 500mm 恢复前进。
    if (dist > 0 && dist < kAvoidTurnEnterMm) {
        s_avoidTurning = true;
    } else if (dist >= kAvoidForwardResumeMm) {
        s_avoidTurning = false;
    }

    AvoidMotionMode targetMotionMode = s_avoidTurning ? AVOID_MOTION_TURN : AVOID_MOTION_FORWARD;
    if (targetMotionMode == s_avoidMotionMode) {
        return;
    }
    s_avoidMotionMode = targetMotionMode;

    if (targetMotionMode == AVOID_MOTION_TURN) {
        s_speedData = 0;
        s_vx = 0.0f;
        s_vy = 0.0f;
        s_wz = speedToRadS(kAvoidTurnSpeedPct);
        mecanumSetVelocity(s_vx, s_vy, s_wz);
    } else {
        s_speedData = kAvoidForwardSpeedPct;
        s_vx = speedToMps(kAvoidForwardSpeedPct);
        s_vy = 0.0f;
        s_wz = 0.0f;
        mecanumSetVelocity(s_vx, s_vy, s_wz);
    }
}

// =========================================================
// 公开接口
// =========================================================
void bleRemoteControlInit(float maxLinearSpeedMps, float maxAngularSpeedRadS) {
    s_maxLinearSpeedMps   = maxLinearSpeedMps;
    s_maxAngularSpeedRadS = maxAngularSpeedRadS;
    s_avoidFlag           = 0;
    s_avoidTurning        = false;
    s_avoidLedEnabled     = false;
    s_avoidMotionMode     = AVOID_MOTION_NONE;

    bleRemoteInit();   // 启动 BLE NUS 服务

    s_ultrasound.begin();

    armControlInit();

    updateBatteryVoltage();
    setBuzzer(0, 0);
}

uint8_t bleRemoteControlUpdate() {
    uint8_t events = BLE_CONTROL_EVENT_NONE;
    s_buzzerCommandIssued = false;

    bleRemoteUpdate();   // BLE 数据由回调接收，此处保留轮询接口
    buzzerUpdate();

    // ── CMD F（不受避障模式限制）────────────────────────
    int avoidState;
    if (bleRemoteConsumeAvoidCommand(avoidState)) {
        if (avoidState == 1) {
            s_avoidFlag = 1;
            s_avoidTurning = false;
            s_avoidLedEnabled = true;
            s_avoidMotionMode = AVOID_MOTION_NONE;
            armControlLower();
            events |= BLE_CONTROL_EVENT_LED | BLE_CONTROL_EVENT_ARM;
        } else {
            s_avoidFlag = 0;
            s_avoidTurning = false;
            s_avoidLedEnabled = false;
            s_avoidMotionMode = AVOID_MOTION_NONE;
            s_speedData = 0;
            s_vx = 0.0f;
            s_vy = 0.0f;
            s_wz = 0.0f;
            mecanumStop(false);
        }
        events |= BLE_CONTROL_EVENT_MOTION;
    }

    // ── CMD D（不受避障模式限制）────────────────────────
    if (bleRemoteConsumeDataRequest()) {
        int dist = ultrasonicFilter();
        updateBatteryVoltage();
        bleRemoteSendSensorData((uint16_t)dist, s_lastVoltage);
    }

    // ── 避障模式：运行避障循环，忽略 A/B/C/E/G ──────────
    if (s_avoidFlag) {
        handleAvoidLoop();

        // 避障期间这些命令按原协议语义直接忽略，避免退出避障后执行旧命令。
        int ignoredInt;
        uint8_t ignoredByteA, ignoredByteB, ignoredByteC;
        bleRemoteConsumeMotionState(ignoredInt);
        bleRemoteConsumeRgbCommand(ignoredByteA, ignoredByteB, ignoredByteC);
        bleRemoteConsumeSpeedCommand(ignoredByteA);
        bleRemoteConsumeServoCommand(ignoredInt);
        bleRemoteConsumeLiftCommand(ignoredInt);

        armControlUpdate();
        return events;
    }

    // ── CMD A — 运动控制 ─────────────────────────────────
    int motionState;
    if (bleRemoteConsumeMotionState(motionState)) {
        if (motionState >= 0 && motionState <= 11) {
            handleMotionState(motionState);
            events |= BLE_CONTROL_EVENT_MOTION;
            if (s_buzzerCommandIssued) {
                events |= BLE_CONTROL_EVENT_BUZZER;
            }
        }
    }

    // ── CMD B — RGB 颜色 ─────────────────────────────────
    uint8_t r, g, b;
    if (bleRemoteConsumeRgbCommand(r, g, b)) {
        s_ultrasound.Color(r, g, b, r, g, b);
        events |= BLE_CONTROL_EVENT_LED;
    }

    // ── CMD C — 速度档位 ─────────────────────────────────
    uint8_t speed;
    if (bleRemoteConsumeSpeedCommand(speed)) {
        s_speedUpdate = speed;
        bleRemoteSendSpeedAck(s_speedUpdate);
    }

    // ── CMD E — 舵机（机械爪开合，扩展板 PWM 2 通道）────────────
    int offset;
    if (bleRemoteConsumeServoCommand(offset)) {
        armControlSetClawOffset(offset);
        events |= BLE_CONTROL_EVENT_ARM;
    }

    // ── CMD G — 舵机（机械爪高低，扩展板 PWM 1 通道）───────
    int liftCommand;
    if (bleRemoteConsumeLiftCommand(liftCommand)) {
        armControlHandleHeightCommand(liftCommand);
        events |= BLE_CONTROL_EVENT_ARM;
    }

    armControlUpdate();
    return events;
}

void bleRemoteControlReleaseMotion() {
    s_avoidFlag = 0;
    s_avoidTurning = false;
    s_avoidLedEnabled = false;
    s_avoidMotionMode = AVOID_MOTION_NONE;
    s_rotFlag = 0;
    s_speedData = 0;
    s_vx = 0.0f;
    s_vy = 0.0f;
    s_wz = 0.0f;
}

void bleRemoteControlReleaseLed() {
    s_avoidLedEnabled = false;
}

void bleRemoteControlReleaseBuzzer() {
    s_buzzerOffAt = 0;
}

void bleRemoteControlGetDirection(uint8_t state, float& vx, float& vy) {
    vx = state < 8 ? kAppMotionMap[state].vxScale : 0.0f;
    vy = state < 8 ? kAppMotionMap[state].vyScale : 0.0f;
}
