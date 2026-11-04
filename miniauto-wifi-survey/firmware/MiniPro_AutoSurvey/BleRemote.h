#pragma once

#include <Arduino.h>

// =========================================================
// BleRemote -- BLE Nordic UART Service (NUS) transport driver
//
// Downlink frame (APP -> MCU) : CMD|p1|p2|p3|$
// Uplink   frame (MCU -> APP) : C|speed|$   /   $distance_mm,voltage_mv$
//
// BLE NUS service UUID : 6E400001-B5A3-F393-E0A9-E50E24DCCA9E
// =========================================================

// Initialize BLE, create NUS service, start advertising ("MiniAutoPro")
void bleRemoteInit();

// Set to 1 while debugging BLE packets. Leave at 0 during normal APP control
// because high-frequency Serial prints can make motion feel uneven.
#ifndef BLE_REMOTE_DEBUG
#define BLE_REMOTE_DEBUG 0
#endif

// No-op for BLE (data arrives via RX callbacks); kept for API compatibility
void bleRemoteUpdate();

// Returns true while a BLE client is connected
bool bleRemoteIsConnected();

// CMD A -- joystick / gravity control, consumes state (0~11)
bool bleRemoteConsumeMotionState(int& state);

// CMD B -- RGB colour, consumes r/g/b (0~255)
bool bleRemoteConsumeRgbCommand(uint8_t& r, uint8_t& g, uint8_t& b);

// CMD C -- speed percentage, consumes speed (0~100)
bool bleRemoteConsumeSpeedCommand(uint8_t& speed);

// CMD D -- sensor data request, consumes flag
bool bleRemoteConsumeDataRequest();

// CMD E -- claw servo offset, consumes offset (-90~90 deg)
bool bleRemoteConsumeServoCommand(int& offset);

// CMD G -- claw height action, consumes command (-90=lower, 0=home, 90=raise)
bool bleRemoteConsumeLiftCommand(int& command);

// CMD F -- avoid mode, consumes state (0 or 1)
bool bleRemoteConsumeAvoidCommand(int& state);

// Uplink: speed ACK frame   C|speed|$
void bleRemoteSendSpeedAck(uint8_t speed);

// Uplink: sensor data frame  $distance_mm,voltage_mv$
void bleRemoteSendSensorData(uint16_t distance_mm, uint32_t voltage_mv);
