#pragma once

#include <Arduino.h>

enum BleRemoteControlEvent : uint8_t {
    BLE_CONTROL_EVENT_NONE   = 0,
    BLE_CONTROL_EVENT_MOTION = 1 << 0,
    BLE_CONTROL_EVENT_LED    = 1 << 1,
    BLE_CONTROL_EVENT_ARM    = 1 << 2,
    BLE_CONTROL_EVENT_BUZZER = 1 << 3,
};

// 初始化 APP 控制器（BLE/LED/超声波/舵机/蜂鸣器）。
void bleRemoteControlInit(float maxLinearSpeedMps, float maxAngularSpeedRadS);

// 每帧调用并返回本轮实际收到的控制类型。
uint8_t bleRemoteControlUpdate();

// WonderLLM 接管对应硬件时，停止 APP 侧持续运行的控制状态。
// 这些函数只释放控制权，不额外改写当前硬件输出。
void bleRemoteControlReleaseMotion();
void bleRemoteControlReleaseLed();
void bleRemoteControlReleaseBuzzer();
// Expose the same physical direction mapping used by the tested phone APP.
void bleRemoteControlGetDirection(uint8_t state, float& vx, float& vy);
