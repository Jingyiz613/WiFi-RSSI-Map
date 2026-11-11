#include "LineFollower.h"

#include <Wire.h>

namespace {

TwoWire& kLineWire = Wire1;

}  // namespace

LineFollower::LineFollower() : raw_(0) {}

void LineFollower::begin() {
  kLineWire.beginTransmission(LINE_FOLLOWER_I2C_ADDR);
  const uint8_t error = kLineWire.endTransmission();
  if (error == 0) {
    Serial.printf("[LineFollower] Found at 0x%02X\n", LINE_FOLLOWER_I2C_ADDR);
  } else {
    Serial.printf("[LineFollower] Not found at 0x%02X (error=%u)\n",
                  LINE_FOLLOWER_I2C_ADDR, error);
  }
}

bool LineFollower::writeRegister(uint8_t value) {
  kLineWire.beginTransmission(LINE_FOLLOWER_I2C_ADDR);
  kLineWire.write(value);
  return kLineWire.endTransmission() == 0;
}

bool LineFollower::readRegister(uint8_t reg, uint8_t& value) {
  if (!writeRegister(reg)) {
    return false;
  }

  const uint8_t received = kLineWire.requestFrom(LINE_FOLLOWER_I2C_ADDR, (uint8_t)1);
  if (received != 1 || !kLineWire.available()) {
    return false;
  }

  value = kLineWire.read();
  return true;
}

bool LineFollower::update() {
  return readRegister(1, raw_);
}

bool LineFollower::getSensor(uint8_t index) const {
  return index < 4 && ((raw_ >> index) & 0x01) != 0;
}

uint8_t LineFollower::getRaw() const {
  return raw_ & 0x0F;
}
