#include "ArmControl.h"

#include "BoardDriver.h"

namespace {

static int32_t s_heightTargetCdeg = (int32_t)ARM_DEFAULT_HEIGHT_ANGLE * 100L;
static int32_t s_heightOutputCdeg = (int32_t)ARM_DEFAULT_HEIGHT_ANGLE * 100L;
static int32_t s_clawTargetCdeg   = (int32_t)ARM_DEFAULT_CLAW_ANGLE * 100L;
static int32_t s_clawOutputCdeg   = (int32_t)ARM_DEFAULT_CLAW_ANGLE * 100L;
static int     s_lastHeightWrittenAngle = -1;
static int     s_lastClawWrittenAngle   = -1;
static bool    s_heightServoEnabled = true;
static bool    s_clawServoEnabled   = true;
static uint32_t s_lastSmoothUpdateMs = 0;
static int8_t  s_heightJogDirection = 0;
static uint32_t s_heightJogDeadlineMs = 0;

static int clampInt(int value, int minValue, int maxValue) {
    if (value < minValue) return minValue;
    if (value > maxValue) return maxValue;
    return value;
}

static int32_t angleToCdeg(int angle) {
    return (int32_t)angle * 100L;
}

static int cdegToAngle(int32_t angleCdeg) {
    return (int)((angleCdeg + 50L) / 100L);
}

static uint8_t toDriverChannel(uint8_t channel) {
#if ARM_PWM_CHANNELS_ARE_ZERO_BASED
    return channel;
#else
    return (uint8_t)(channel - 1);
#endif
}

static uint16_t angleToPulseUs(int angle) {
    angle = clampInt(angle, 0, 180);
    const long pulse = ARM_SERVO_MIN_PULSE_US +
        ((long)angle * (ARM_SERVO_MAX_PULSE_US - ARM_SERVO_MIN_PULSE_US)) / 180L;
    return (uint16_t)pulse;
}

static void writeServoAngle(uint8_t pwmChannel, int angle, uint16_t time_ms) {
    setPwmServo(toDriverChannel(pwmChannel), angleToPulseUs(angle), time_ms);
}

static void writeServoAngleCached(uint8_t pwmChannel, int angle, uint16_t time_ms,
                                  int& lastWrittenAngle, bool forceWrite) {
    angle = clampInt(angle, 0, 180);
    if (!forceWrite && lastWrittenAngle == angle) {
        return;
    }

    writeServoAngle(pwmChannel, angle, time_ms);
    lastWrittenAngle = angle;
}

static int32_t moveTowardCdeg(int32_t currentCdeg, int32_t targetCdeg,
                              uint16_t speedDps, uint32_t elapsedMs) {
    int32_t deltaCdeg = targetCdeg - currentCdeg;
    if (deltaCdeg == 0) {
        return currentCdeg;
    }

    int32_t maxDeltaCdeg = ((int32_t)speedDps * 100L * (int32_t)elapsedMs) / 1000L;
    if (maxDeltaCdeg < 1) {
        maxDeltaCdeg = 1;
    }

    if (deltaCdeg > maxDeltaCdeg) {
        return currentCdeg + maxDeltaCdeg;
    }
    if (deltaCdeg < -maxDeltaCdeg) {
        return currentCdeg - maxDeltaCdeg;
    }
    return targetCdeg;
}

static int32_t clampHeightCdeg(int32_t angleCdeg) {
    const int32_t minCdeg = angleToCdeg(ARM_HEIGHT_MIN_ANGLE);
    const int32_t maxCdeg = angleToCdeg(ARM_HEIGHT_MAX_ANGLE);
    if (angleCdeg < minCdeg) return minCdeg;
    if (angleCdeg > maxCdeg) return maxCdeg;
    return angleCdeg;
}

static void stopHeightJog() {
    s_heightJogDirection = 0;
    s_heightJogDeadlineMs = 0;
}

static uint32_t elapsedBeforeDeadline(uint32_t nowMs, uint32_t elapsedMs,
                                      uint32_t deadlineMs) {
    if ((int32_t)(nowMs - deadlineMs) <= 0) {
        return elapsedMs;
    }

    uint32_t expiredMs = nowMs - deadlineMs;
    if (expiredMs >= elapsedMs) {
        return 0;
    }
    return elapsedMs - expiredMs;
}

static void updateHeightJog(uint32_t nowMs, uint32_t elapsedMs) {
    if (s_heightJogDirection == 0) {
        return;
    }

    uint32_t jogElapsedMs = elapsedBeforeDeadline(nowMs, elapsedMs, s_heightJogDeadlineMs);
    if (jogElapsedMs > 0) {
        int32_t deltaCdeg = ((int32_t)ARM_HEIGHT_JOG_SPEED_DPS * 100L *
                             (int32_t)jogElapsedMs) / 1000L;
        if (deltaCdeg < 1) {
            deltaCdeg = 1;
        }
        s_heightTargetCdeg = clampHeightCdeg(
            s_heightTargetCdeg + (int32_t)s_heightJogDirection * deltaCdeg
        );
    }

    if ((int32_t)(nowMs - s_heightJogDeadlineMs) > 0) {
        stopHeightJog();
    }
}

static void startHeightJog(int direction) {
    if (direction == 0) {
        stopHeightJog();
        return;
    }

    s_heightServoEnabled = true;
    s_heightJogDirection = (direction > 0) ? 1 : -1;
    s_heightJogDeadlineMs = millis() + ARM_HEIGHT_JOG_COMMAND_TIMEOUT_MS;
}

static void setHeightTarget(int angle, uint16_t time_ms, bool forceImmediate) {
    angle = clampInt(angle, ARM_HEIGHT_MIN_ANGLE, ARM_HEIGHT_MAX_ANGLE);
    stopHeightJog();
    s_heightTargetCdeg = angleToCdeg(angle);
    s_heightServoEnabled = true;

    if (forceImmediate || time_ms > 0) {
        s_heightOutputCdeg = s_heightTargetCdeg;
        writeServoAngleCached(ARM_HEIGHT_PWM_CHANNEL, angle, time_ms,
                              s_lastHeightWrittenAngle, true);
    }
}

static void setClawTarget(int angle, uint16_t time_ms, bool forceImmediate) {
    angle = clampInt(angle, ARM_CLAW_MIN_ANGLE, ARM_CLAW_MAX_ANGLE);
    s_clawTargetCdeg = angleToCdeg(angle);
    s_clawServoEnabled = true;

    if (forceImmediate || time_ms > 0) {
        s_clawOutputCdeg = s_clawTargetCdeg;
        writeServoAngleCached(ARM_CLAW_PWM_CHANNEL, angle, time_ms,
                              s_lastClawWrittenAngle, true);
    }
}

static void updateSmoothServo(uint8_t pwmChannel, int32_t& outputCdeg,
                              int32_t targetCdeg, uint16_t speedDps,
                              bool servoEnabled, int& lastWrittenAngle,
                              uint32_t elapsedMs) {
    if (!servoEnabled) {
        return;
    }

    outputCdeg = moveTowardCdeg(outputCdeg, targetCdeg, speedDps, elapsedMs);
    writeServoAngleCached(pwmChannel, cdegToAngle(outputCdeg), 0,
                          lastWrittenAngle, lastWrittenAngle < 0);
}

}  // namespace

void armControlInit() {
    s_lastSmoothUpdateMs = millis();
    setHeightTarget(ARM_DEFAULT_HEIGHT_ANGLE, 0, true);
    setClawTarget(ARM_DEFAULT_CLAW_ANGLE, 0, true);
}

void armControlUpdate() {
    uint32_t nowMs = millis();
    uint32_t elapsedMs = nowMs - s_lastSmoothUpdateMs;
    if (elapsedMs < ARM_SMOOTH_UPDATE_INTERVAL_MS) {
        return;
    }
    s_lastSmoothUpdateMs = nowMs;

    updateHeightJog(nowMs, elapsedMs);

    updateSmoothServo(ARM_HEIGHT_PWM_CHANNEL, s_heightOutputCdeg, s_heightTargetCdeg,
                      ARM_HEIGHT_SMOOTH_SPEED_DPS, s_heightServoEnabled,
                      s_lastHeightWrittenAngle, elapsedMs);
    updateSmoothServo(ARM_CLAW_PWM_CHANNEL, s_clawOutputCdeg, s_clawTargetCdeg,
                      ARM_CLAW_SMOOTH_SPEED_DPS, s_clawServoEnabled,
                      s_lastClawWrittenAngle, elapsedMs);
}

void armControlHome(uint16_t time_ms) {
    armControlSetHeightAngle(ARM_HEIGHT_HOME_ANGLE, time_ms);
    armControlSetClawAngle(ARM_CLAW_HOME_ANGLE, time_ms);
}

void armControlRelease() {
    setPwmServo(toDriverChannel(ARM_HEIGHT_PWM_CHANNEL), 0, 0);
    setPwmServo(toDriverChannel(ARM_CLAW_PWM_CHANNEL), 0, 0);
    stopHeightJog();
    s_heightServoEnabled = false;
    s_clawServoEnabled = false;
    s_lastHeightWrittenAngle = -1;
    s_lastClawWrittenAngle = -1;
}

void armControlSetHeightAngle(int angle, uint16_t time_ms) {
    setHeightTarget(angle, time_ms, false);
}

void armControlSetClawAngle(int angle, uint16_t time_ms) {
    setClawTarget(angle, time_ms, false);
}

void armControlRaise(uint16_t time_ms) {
    armControlSetHeightAngle(ARM_HEIGHT_RAISED_ANGLE, time_ms);
}

void armControlLower(uint16_t time_ms) {
    armControlSetHeightAngle(ARM_HEIGHT_LOWER_ANGLE, time_ms);
}

void armControlCenterHeight(uint16_t time_ms) {
    armControlSetHeightAngle(ARM_HEIGHT_HOME_ANGLE, time_ms);
}

void armControlStepHeight(int direction, uint16_t time_ms) {
    stopHeightJog();
    if (direction > 0) {
        armControlSetHeightAngle(armControlGetHeightAngle() + ARM_HEIGHT_STEP_ANGLE, time_ms);
    } else if (direction < 0) {
        armControlSetHeightAngle(armControlGetHeightAngle() - ARM_HEIGHT_STEP_ANGLE, time_ms);
    }
}

void armControlHandleHeightCommand(int command, uint16_t time_ms) {
    if (command > 0) {
        if (time_ms > 0) {
            armControlStepHeight(1, time_ms);
        } else {
            startHeightJog(1);
        }
    } else if (command < 0) {
        if (time_ms > 0) {
            armControlStepHeight(-1, time_ms);
        } else {
            startHeightJog(-1);
        }
    } else {
        armControlSetHeightAngle(ARM_HEIGHT_HOME_ANGLE, time_ms);
    }
}

void armControlSetHeightOffset(int offset, uint16_t time_ms) {
    armControlSetHeightAngle(ARM_HEIGHT_HOME_ANGLE + offset, time_ms);
}

void armControlSetClawOffset(int offset, uint16_t time_ms) {
    armControlSetClawAngle(ARM_CLAW_HOME_ANGLE + offset, time_ms);
}

int armControlGetHeightAngle() {
    return cdegToAngle(s_heightTargetCdeg);
}

int armControlGetClawAngle() {
    return cdegToAngle(s_clawTargetCdeg);
}

int armControlGetEstimatedHeightAngle() {
    return cdegToAngle(s_heightOutputCdeg);
}

int armControlGetEstimatedClawAngle() {
    return cdegToAngle(s_clawOutputCdeg);
}

int armControlGetCurrentHeightAngle() {
    return armControlGetEstimatedHeightAngle();
}

int armControlGetCurrentClawAngle() {
    return armControlGetEstimatedClawAngle();
}

bool armControlIsHeightAtTarget(uint8_t toleranceDeg) {
    int32_t deltaCdeg = s_heightOutputCdeg - s_heightTargetCdeg;
    if (deltaCdeg < 0) {
        deltaCdeg = -deltaCdeg;
    }
    return deltaCdeg <= (int32_t)toleranceDeg * 100L;
}

bool armControlIsClawAtTarget(uint8_t toleranceDeg) {
    int32_t deltaCdeg = s_clawOutputCdeg - s_clawTargetCdeg;
    if (deltaCdeg < 0) {
        deltaCdeg = -deltaCdeg;
    }
    return deltaCdeg <= (int32_t)toleranceDeg * 100L;
}
