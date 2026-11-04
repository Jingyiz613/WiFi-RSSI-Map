#include <Wire.h>
#include <string.h>
#include "Ultrasound.h"

static TwoWire& ultrasoundWire = Wire1;
static uint8_t s_lastRgbMode = 0xFF;
static uint8_t s_lastRgb[6] = {0};
static bool s_rgbCacheValid = false;

Ultrasound::Ultrasound() {
}

void Ultrasound::begin() {
  s_rgbCacheValid = false;
}

bool Ultrasound::wireWriteByte(uint8_t addr, uint8_t val) {
  ultrasoundWire.beginTransmission(addr);
  ultrasoundWire.write(val);
  return ultrasoundWire.endTransmission() == 0;
}

bool Ultrasound::wireWriteDataArray(uint8_t addr, uint8_t reg, uint8_t *val, unsigned int len) {
  ultrasoundWire.beginTransmission(addr);
  ultrasoundWire.write(reg);
  for (unsigned int i = 0; i < len; i++) {
    ultrasoundWire.write(val[i]);
  }
  return ultrasoundWire.endTransmission() == 0;
}

int Ultrasound::wireReadDataArray(uint8_t addr, uint8_t reg, uint8_t *val, unsigned int len) {
  if (!wireWriteByte(addr, reg)) {
    return -1;
  }

  ultrasoundWire.requestFrom(addr, (uint8_t)len);
  unsigned int i = 0;
  while (ultrasoundWire.available()) {
    if (i >= len) {
      return -1;
    }
    val[i++] = ultrasoundWire.read();
  }
  return i;
}

void Ultrasound::Breathing(uint8_t r1, uint8_t g1, uint8_t b1, uint8_t r2, uint8_t g2, uint8_t b2) {
  uint8_t breathing[6] = {r1, g1, b1, r2, g2, b2};
  uint8_t value = RGB_WORK_BREATHING_MODE;

  if (s_rgbCacheValid && s_lastRgbMode == value &&
      memcmp(s_lastRgb, breathing, sizeof(breathing)) == 0) {
    return;
  }
  if (wireWriteDataArray(ULTRASOUND_I2C_ADDR, RGB_WORK_MODE, &value, 1) &&
      wireWriteDataArray(ULTRASOUND_I2C_ADDR, RGB1_R_BREATHING_CYCLE, breathing, 6)) {
    s_lastRgbMode = value;
    memcpy(s_lastRgb, breathing, sizeof(breathing));
    s_rgbCacheValid = true;
  } else {
    s_rgbCacheValid = false;
  }
}

void Ultrasound::Color(uint8_t r1, uint8_t g1, uint8_t b1, uint8_t r2, uint8_t g2, uint8_t b2) {
  uint8_t rgb[6] = {r1, g1, b1, r2, g2, b2};
  uint8_t value = RGB_WORK_SIMPLE_MODE;

  if (s_rgbCacheValid && s_lastRgbMode == value &&
      memcmp(s_lastRgb, rgb, sizeof(rgb)) == 0) {
    return;
  }
  if (wireWriteDataArray(ULTRASOUND_I2C_ADDR, RGB_WORK_MODE, &value, 1) &&
      wireWriteDataArray(ULTRASOUND_I2C_ADDR, RGB1_R, rgb, 6)) {
    s_lastRgbMode = value;
    memcpy(s_lastRgb, rgb, sizeof(rgb));
    s_rgbCacheValid = true;
  } else {
    s_rgbCacheValid = false;
  }
}

uint16_t Ultrasound::GetDistance() {
  uint16_t distance = 0;
  if (wireReadDataArray(ULTRASOUND_I2C_ADDR, 0, (uint8_t *)&distance, 2) != 2) {
    return 0;
  }
  return distance;
}
