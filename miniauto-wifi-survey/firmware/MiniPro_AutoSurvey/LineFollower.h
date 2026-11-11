#pragma once

#include <Arduino.h>

#define LINE_FOLLOWER_I2C_ADDR 0x78

class LineFollower {
public:
  LineFollower();

  void begin();
  bool update();
  bool getSensor(uint8_t index) const;
  uint8_t getRaw() const;

private:
  uint8_t raw_;

  bool writeRegister(uint8_t value);
  bool readRegister(uint8_t reg, uint8_t& value);
};
