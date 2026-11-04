#pragma once

#include <Arduino.h>

// =========================================================
// Arm PWM servo port mapping
// =========================================================
// Expansion-board labels are normally 1~4. The low-level setPwmServo()
// driver uses 0~3, so ArmControl converts board labels automatically.
// If your target project already uses 0~3 here, set this macro to 1.
#ifndef ARM_PWM_CHANNELS_ARE_ZERO_BASED
#define ARM_PWM_CHANNELS_ARE_ZERO_BASED 0
#endif

// Board PWM channel 1: claw height/lift servo.
#ifndef ARM_HEIGHT_PWM_CHANNEL
#define ARM_HEIGHT_PWM_CHANNEL 1
#endif

// Board PWM channel 2: claw open/close servo.
// Change this macro if your claw servo is wired to another PWM channel.
#ifndef ARM_CLAW_PWM_CHANNEL
#define ARM_CLAW_PWM_CHANNEL 2
#endif

// =========================================================
// Servo pulse range
// =========================================================
#ifndef ARM_SERVO_MIN_PULSE_US
#define ARM_SERVO_MIN_PULSE_US 500
#endif

#ifndef ARM_SERVO_MAX_PULSE_US
#define ARM_SERVO_MAX_PULSE_US 2500
#endif

// =========================================================
// Mechanical angle limits
// =========================================================
// Tune these after real-machine calibration. Final target angles are clamped
// by these limits before output.
#ifndef ARM_HEIGHT_MIN_ANGLE
#define ARM_HEIGHT_MIN_ANGLE 58
#endif

#ifndef ARM_HEIGHT_MAX_ANGLE
#define ARM_HEIGHT_MAX_ANGLE 132
#endif

#ifndef ARM_HEIGHT_HOME_ANGLE
#define ARM_HEIGHT_HOME_ANGLE 65
#endif

// Action-group positions. The lift direction follows the protocol convention:
// smaller angle presses down and larger angle raises up.
#ifndef ARM_HEIGHT_LOWER_ANGLE
#define ARM_HEIGHT_LOWER_ANGLE ARM_HEIGHT_MIN_ANGLE
#endif

#ifndef ARM_HEIGHT_RAISED_ANGLE
#define ARM_HEIGHT_RAISED_ANGLE ARM_HEIGHT_MAX_ANGLE
#endif

// CMD G is an action command, not an absolute angle:
//   G|90|$ raises by this many degrees, G|-90|$ lowers by this many degrees,
//   G|0|$ returns to ARM_HEIGHT_HOME_ANGLE.
#ifndef ARM_HEIGHT_STEP_ANGLE
#define ARM_HEIGHT_STEP_ANGLE 3
#endif

#ifndef ARM_CLAW_MIN_ANGLE
#define ARM_CLAW_MIN_ANGLE 0
#endif

#ifndef ARM_CLAW_MAX_ANGLE
#define ARM_CLAW_MAX_ANGLE 180
#endif

#ifndef ARM_CLAW_HOME_ANGLE
#define ARM_CLAW_HOME_ANGLE 90
#endif

#ifndef ARM_CLAW_CLOSED_ANGLE
#define ARM_CLAW_CLOSED_ANGLE ARM_CLAW_HOME_ANGLE
#endif

// Grab-specific claw angle. Positive claw angles open farther according to
// the protocol convention, so this can be tuned independently from HOME.
#ifndef ARM_CLAW_GRAB_ANGLE
#define ARM_CLAW_GRAB_ANGLE (ARM_CLAW_CLOSED_ANGLE + 12)
#endif

#ifndef ARM_CLAW_OPEN_ANGLE
#define ARM_CLAW_OPEN_ANGLE ARM_CLAW_MAX_ANGLE
#endif

// Power-on pose. Keep protocol HOME and the action-group positions separate.
#ifndef ARM_DEFAULT_HEIGHT_ANGLE
#define ARM_DEFAULT_HEIGHT_ANGLE ARM_HEIGHT_LOWER_ANGLE
#endif

#ifndef ARM_DEFAULT_CLAW_ANGLE
#define ARM_DEFAULT_CLAW_ANGLE ARM_CLAW_OPEN_ANGLE
#endif

#ifndef ARM_DEFAULT_MOVE_TIME_MS
#define ARM_DEFAULT_MOVE_TIME_MS 0
#endif

// Smooth output is handled in armControlUpdate(). APP commands may arrive every
// 50ms, while this layer emits smaller servo steps between commands.
#ifndef ARM_SMOOTH_UPDATE_INTERVAL_MS
#define ARM_SMOOTH_UPDATE_INTERVAL_MS 15
#endif

#ifndef ARM_HEIGHT_SMOOTH_SPEED_DPS
#define ARM_HEIGHT_SMOOTH_SPEED_DPS 75
#endif

#ifndef ARM_HEIGHT_JOG_SPEED_DPS
#define ARM_HEIGHT_JOG_SPEED_DPS 60
#endif

#ifndef ARM_HEIGHT_JOG_COMMAND_TIMEOUT_MS
#define ARM_HEIGHT_JOG_COMMAND_TIMEOUT_MS 120
#endif

#ifndef ARM_CLAW_SMOOTH_SPEED_DPS
#define ARM_CLAW_SMOOTH_SPEED_DPS 220
#endif

#if ARM_SERVO_MIN_PULSE_US >= ARM_SERVO_MAX_PULSE_US
#error "ARM_SERVO_MIN_PULSE_US must be less than ARM_SERVO_MAX_PULSE_US"
#endif

#if ARM_HEIGHT_MIN_ANGLE > ARM_HEIGHT_MAX_ANGLE
#error "ARM_HEIGHT_MIN_ANGLE must be <= ARM_HEIGHT_MAX_ANGLE"
#endif

#if ARM_HEIGHT_LOWER_ANGLE < ARM_HEIGHT_MIN_ANGLE || ARM_HEIGHT_LOWER_ANGLE > ARM_HEIGHT_MAX_ANGLE
#error "ARM_HEIGHT_LOWER_ANGLE must be within the height angle limits"
#endif

#if ARM_HEIGHT_RAISED_ANGLE < ARM_HEIGHT_MIN_ANGLE || ARM_HEIGHT_RAISED_ANGLE > ARM_HEIGHT_MAX_ANGLE
#error "ARM_HEIGHT_RAISED_ANGLE must be within the height angle limits"
#endif

#if ARM_HEIGHT_STEP_ANGLE <= 0
#error "ARM_HEIGHT_STEP_ANGLE must be > 0"
#endif

#if ARM_CLAW_MIN_ANGLE > ARM_CLAW_MAX_ANGLE
#error "ARM_CLAW_MIN_ANGLE must be <= ARM_CLAW_MAX_ANGLE"
#endif

#if ARM_CLAW_CLOSED_ANGLE < ARM_CLAW_MIN_ANGLE || ARM_CLAW_CLOSED_ANGLE > ARM_CLAW_MAX_ANGLE
#error "ARM_CLAW_CLOSED_ANGLE must be within the claw angle limits"
#endif

#if ARM_CLAW_GRAB_ANGLE < ARM_CLAW_MIN_ANGLE || ARM_CLAW_GRAB_ANGLE > ARM_CLAW_MAX_ANGLE
#error "ARM_CLAW_GRAB_ANGLE must be within the claw angle limits"
#endif

#if ARM_CLAW_OPEN_ANGLE < ARM_CLAW_MIN_ANGLE || ARM_CLAW_OPEN_ANGLE > ARM_CLAW_MAX_ANGLE
#error "ARM_CLAW_OPEN_ANGLE must be within the claw angle limits"
#endif

#if ARM_DEFAULT_HEIGHT_ANGLE < ARM_HEIGHT_MIN_ANGLE || ARM_DEFAULT_HEIGHT_ANGLE > ARM_HEIGHT_MAX_ANGLE
#error "ARM_DEFAULT_HEIGHT_ANGLE must be within the height angle limits"
#endif

#if ARM_DEFAULT_CLAW_ANGLE < ARM_CLAW_MIN_ANGLE || ARM_DEFAULT_CLAW_ANGLE > ARM_CLAW_MAX_ANGLE
#error "ARM_DEFAULT_CLAW_ANGLE must be within the claw angle limits"
#endif

#if ARM_SMOOTH_UPDATE_INTERVAL_MS <= 0
#error "ARM_SMOOTH_UPDATE_INTERVAL_MS must be > 0"
#endif

#if ARM_HEIGHT_SMOOTH_SPEED_DPS <= 0
#error "ARM_HEIGHT_SMOOTH_SPEED_DPS must be > 0"
#endif

#if ARM_HEIGHT_JOG_SPEED_DPS <= 0
#error "ARM_HEIGHT_JOG_SPEED_DPS must be > 0"
#endif

#if ARM_HEIGHT_JOG_COMMAND_TIMEOUT_MS <= ARM_SMOOTH_UPDATE_INTERVAL_MS
#error "ARM_HEIGHT_JOG_COMMAND_TIMEOUT_MS must be greater than ARM_SMOOTH_UPDATE_INTERVAL_MS"
#endif

#if ARM_CLAW_SMOOTH_SPEED_DPS <= 0
#error "ARM_CLAW_SMOOTH_SPEED_DPS must be > 0"
#endif

#if ARM_PWM_CHANNELS_ARE_ZERO_BASED
#if ARM_HEIGHT_PWM_CHANNEL < 0 || ARM_HEIGHT_PWM_CHANNEL > 3
#error "ARM_HEIGHT_PWM_CHANNEL must be 0~3 when ARM_PWM_CHANNELS_ARE_ZERO_BASED=1"
#endif
#if ARM_CLAW_PWM_CHANNEL < 0 || ARM_CLAW_PWM_CHANNEL > 3
#error "ARM_CLAW_PWM_CHANNEL must be 0~3 when ARM_PWM_CHANNELS_ARE_ZERO_BASED=1"
#endif
#else
#if ARM_HEIGHT_PWM_CHANNEL < 1 || ARM_HEIGHT_PWM_CHANNEL > 4
#error "ARM_HEIGHT_PWM_CHANNEL must be 1~4 when ARM_PWM_CHANNELS_ARE_ZERO_BASED=0"
#endif
#if ARM_CLAW_PWM_CHANNEL < 1 || ARM_CLAW_PWM_CHANNEL > 4
#error "ARM_CLAW_PWM_CHANNEL must be 1~4 when ARM_PWM_CHANNELS_ARE_ZERO_BASED=0"
#endif
#endif

void armControlInit();
void armControlUpdate();
void armControlHome(uint16_t time_ms = ARM_DEFAULT_MOVE_TIME_MS);
void armControlRelease();

void armControlSetHeightAngle(int angle, uint16_t time_ms = ARM_DEFAULT_MOVE_TIME_MS);
void armControlSetClawAngle(int angle, uint16_t time_ms = ARM_DEFAULT_MOVE_TIME_MS);

void armControlRaise(uint16_t time_ms = ARM_DEFAULT_MOVE_TIME_MS);
void armControlLower(uint16_t time_ms = ARM_DEFAULT_MOVE_TIME_MS);
void armControlCenterHeight(uint16_t time_ms = ARM_DEFAULT_MOVE_TIME_MS);

void armControlStepHeight(int direction, uint16_t time_ms = ARM_DEFAULT_MOVE_TIME_MS);
void armControlHandleHeightCommand(int command, uint16_t time_ms = ARM_DEFAULT_MOVE_TIME_MS);

void armControlSetHeightOffset(int offset, uint16_t time_ms = ARM_DEFAULT_MOVE_TIME_MS);
void armControlSetClawOffset(int offset, uint16_t time_ms = ARM_DEFAULT_MOVE_TIME_MS);

int armControlGetHeightAngle();
int armControlGetClawAngle();
int armControlGetEstimatedHeightAngle();
int armControlGetEstimatedClawAngle();
// Compatibility aliases. These values are software estimates, not servo feedback.
int armControlGetCurrentHeightAngle();
int armControlGetCurrentClawAngle();
bool armControlIsHeightAtTarget(uint8_t toleranceDeg = 1);
bool armControlIsClawAtTarget(uint8_t toleranceDeg = 1);
