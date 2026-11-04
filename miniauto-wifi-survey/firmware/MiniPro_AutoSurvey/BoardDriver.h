#pragma once
#include <Arduino.h>

// =========================================================
// 扩展板 I2C 地址与引脚配置
// =========================================================
#define EXTEND_I2C_ADDR   0x24   // 扩展板 I2C 7-bit 地址
#define I2C_SDA_PIN       21
#define I2C_SCL_PIN       22

// =========================================================
// 寄存器地址宏定义
// =========================================================

// 系统寄存器 (0x00 ~ 0x0F)
#define REG_DEVICE_ID_L   0x00
#define REG_FW_VER_L      0x02
#define REG_MOTOR_LOCK    0x04   // bit0~3=位置锁定，bit4保留，bit5~7=状态标志

#define BOARD_STATUS_I2C_ERROR          0x20
#define BOARD_STATUS_COMMAND_REJECTED   0x40
#define BOARD_STATUS_FEEDBACK_DIR_ERROR 0x80

// 同步运动控制 (0x05 ~ 0x0F)
#define REG_SYNC_RPM1_L   0x05   // Motor1 目标 RPM×10 (int16_t)
#define REG_SYNC_RPM2_L   0x07   // Motor2 目标 RPM×10 (int16_t)
#define REG_SYNC_RPM3_L   0x09   // Motor3 目标 RPM×10 (int16_t)
#define REG_SYNC_RPM4_L   0x0B   // Motor4 目标 RPM×10 (int16_t)
#define REG_SYNC_DIST_L   0x0D   // 运行距离 cm×10 (uint16_t, 0=纯速度环)
#define REG_SYNC_EXEC     0x0F   // 写非0值触发同步运动

// 编码电机基本控制 (0x10 ~ 0x37)
#define REG_MOTOR1_PWM_L        0x10   // 通道1-4 目标PWM (int16_t, 步长2)
#define REG_MOTOR1_RPM_L        0x18   // 通道1-4 目标RPM×10 (int16_t, 步长2)
#define REG_MOTOR1_ACTUAL_RPM_L 0x20   // 通道1-4 实测RPM×10 (只读, int16_t, 步长2)
#define REG_ENC1_B0             0x28   // 通道1-4 编码器计数 (只读, int32_t, 步长4)

// 控制命令 (0x38 ~ 0x3C)
#define REG_ENC_RESET     0x38   // 编码器复位 (bit0~3)
#define REG_MOTOR_BRAKE   0x39   // 电机刹车 (bit0~3)
#define REG_MOTOR_CALIB   0x3A   // 写=自动校准命令，读非0=命令等待执行
#define REG_DIST_EXEC     0x3B   // 触发距离控制 (bit0~3)
#define REG_DIST_STATUS   0x3C   // bit0~3=距离运动中, bit5=校准成功, bit6=失败, bit7=校准中

#define MOTOR_STATUS_DISTANCE_MASK       0x0F
#define MOTOR_STATUS_CALIBRATION_SUCCESS 0x20
#define MOTOR_STATUS_CALIBRATION_FAILED  0x40
#define MOTOR_STATUS_CALIBRATION_RUNNING 0x80

// 极性与方向 (0x3D ~ 0x44)
#define REG_ENCODER_POL1  0x3D   // 通道1-4 编码器极性 (int8_t, 步长1)
#define REG_OUTPUT_DIR1   0x41   // 通道1-4 输出方向极性 (int8_t, 步长1)

// 距离控制独立参数 (0x45 ~ 0x5C)
#define REG_DIST1_CM_B0   0x45   // 通道1-4 距离参数 cm×10 (int32_t, 步长4)
#define REG_DIST1_RPM_L   0x55   // 通道1-4 巡航速度 RPM×10 (uint16_t, 步长2)

// 间接寻址 PID 与电机配置 (0x5D ~ 0x78)
#define REG_CFG_MOTOR_SEL  0x5D  // 通道选择 (0-3)
#define REG_CFG_PID_KP_B0  0x5E  // PID KP (float)
#define REG_CFG_PID_KI_B0  0x62  // PID KI (float)
#define REG_CFG_PID_KD_B0  0x66  // PID KD (float)
#define REG_CFG_PID_APPLY  0x6A  // 触发 PID 应用
#define REG_CFG_POLE_PAIRS 0x6B  // 极对数 (uint8_t)
#define REG_CFG_GEAR_RATIO 0x6C  // 减速比 (float)
#define REG_CFG_WHEEL_DIA  0x70  // 轮子直径 cm (float)
#define REG_CFG_PPR        0x74  // 编码器 PPR (uint32_t)
#define REG_CFG_CFG_APPLY  0x78  // 触发机械参数应用
#define REG_CFG_CAL_DIR    0x79  // 校准结果方向选择 (0=反向, 1=正向)
#define REG_CFG_MIN_START_L 0x7A // 启动死区 PWM (只读, uint16_t)
#define REG_CFG_MIN_RUN_L  0x7C  // 运行死区 PWM (只读, uint16_t)
#define REG_CFG_FF_X10_L   0x7E  // 前馈斜率×10 (只读, uint16_t)

// PWM 舵机控制寄存器 (0x80 ~ 0x97)
#define REG_SERVO1_TIME_L  0x80  // 通道1 独立运行时间 ms (uint16_t)
#define REG_SERVO2_TIME_L  0x82  // 通道2 独立运行时间 ms (uint16_t)
#define REG_SERVO3_TIME_L  0x84  // 通道3 独立运行时间 ms (uint16_t)
#define REG_SERVO4_TIME_L  0x86  // 通道4 独立运行时间 ms (uint16_t)
#define REG_SERVO1_L       0x88  // 通道1-4 舵机脉宽 us (uint16_t, 步长2, 0=卸力)
#define REG_SERVO1_OFFSET_L 0x90 // 通道1-4 舵机偏差 us (int16_t, 步长2)

// 串口总线舵机透传寄存器 (0x98 ~ 0xF7)
#define REG_BUS_TX_LEN    0x98   // 发送长度
#define REG_BUS_TX_BUF    0x99   // 发送数据区 (48 bytes)
#define REG_BUS_EXEC      0xC9   // 触发执行 (1=发送并等待回复, 2=广播不等回复)
#define REG_BUS_RX_LEN    0xCA   // 接收长度 (只读)；独立写 0xA5 时为安全钥匙
#define REG_SAFETY_KEY    0xCA
#define REG_BUS_RX_BUF    0xCB   // 接收数据区 (45 bytes)
#define SAFETY_KEY_VALUE  0xA5
#define BUS_SERVO_TX_MAX_LEN 48
#define BUS_SERVO_RX_MAX_LEN 45

// 低压报警、电池电压检测与蜂鸣器寄存器 (0xF8 ~ 0xFF)
#define REG_LOW_VOLTAGE_CTRL 0xF8 // bit0=使能，bit7=报警活动
#define REG_LOW_VOLTAGE_MV_L 0xF9 // 报警阈值 mV (uint16_t)
#define REG_BATTERY_MV_L  0xFB   // 电池电压 mV (只读, uint16_t)
#define REG_BUZZER_FREQ_L 0xFD   // 蜂鸣器 PWM 频率 Hz (uint16_t, 0=关闭)
#define REG_BUZZER_DUTY   0xFF   // 蜂鸣器 PWM 占空比 0~99% (uint8_t, 0=关闭)

#define LOW_VOLTAGE_MIN_MV 3200
#define LOW_VOLTAGE_MAX_MV 12600

// =========================================================
// I2C 基础读写工具函数声明
// =========================================================
void    writeReg8(uint8_t regAddr, uint8_t value);
void    writeReg16(uint8_t regAddr, int16_t value);
void    writeReg32(uint8_t regAddr, int32_t value);
void    writeRegFloat(uint8_t regAddr, float value);
uint8_t readReg8(uint8_t regAddr);
uint16_t readReg16(uint8_t regAddr);
int32_t  readReg32(uint8_t regAddr);
void unlockProtectedMotorConfig();

// =========================================================
// 系统信息与状态函数声明
// =========================================================
uint16_t readDeviceId();
uint16_t readFirmwareVersion();
uint8_t readBoardStatus();
uint8_t getMotorLockMask();
bool boardHasI2cError();
bool boardHasRejectedCommand();
bool boardHasFeedbackDirectionError();

// =========================================================
// 编码电机高级控制函数声明
// =========================================================
enum MotorCalibrationStatus : uint8_t {
  MOTOR_CALIB_IDLE = 0,
  MOTOR_CALIB_PENDING,
  MOTOR_CALIB_RUNNING,
  MOTOR_CALIB_SUCCESS,
  MOTOR_CALIB_FAILED,
};

void setMotorConfig(uint8_t channel, uint8_t polePairs, float gearRatio, float wheelDia, uint32_t ppr);
void setMotorPID(uint8_t channel, float kp, float ki, float kd);
void setMotorSpeedPwm(uint8_t channel, int16_t pwm);
void setMotorSpeedRpm(uint8_t channel, float rpm);
float getMotorSpeedRpm(uint8_t channel);
void setMotorLockMask(uint8_t lockMask);
void setMotorLock(uint8_t channel, bool enable);
void setMotorDirection(uint8_t channel, int8_t encoderPolarity, int8_t outputDirection);
bool setMotorDirectionBlock(const int8_t encoderPolarities[4], const int8_t outputDirections[4]);
void motorBrake(uint8_t channel);
void motorBrakeMask(uint8_t motorMask);
void motorAutoCalibrate(uint8_t channel);
void motorAutoCalibrateAll();
void moveMotorDistance(uint8_t channel, float dist_cm, float rpm);
bool motorDistanceDone(uint8_t channel);
MotorCalibrationStatus getMotorCalibrationStatus();
bool motorCalibrationBusy();
bool motorCalibrationSucceeded();
bool motorCalibrationFailed();
bool calibrateMotors(uint8_t motorMask, uint32_t timeoutMs = 300000);
bool motorSyncMove(const float rpms[4], float dist_cm);
void invalidateMotorSyncSpeedCache();
int32_t encoderGetCount(uint8_t channel);
void encoderReset(uint8_t channel);
void encoderResetMask(uint8_t motorMask);
void readMotorCalibration(uint8_t channel, bool positiveDir, uint16_t* minStart, uint16_t* minRun, float* feedForward);

// 文档 API 命名兼容封装
void motor_set_config(uint8_t channel, uint8_t polePairs, float gearRatio, float wheelDia, uint32_t ppr);
void motor_set_pid(uint8_t channel, float kp, float ki, float kd);
void motor_set_speed(uint8_t channel, int16_t pwm);
void motor_set_speed_rpm(uint8_t channel, float rpm);
float motor_get_speed_rpm(uint8_t channel);
void motor_set_lock_mask(uint8_t lockMask);
void motor_set_lock(uint8_t channel, bool enable);
void motor_set_direction(uint8_t channel, int8_t encoderPolarity, int8_t outputDirection);
bool motor_set_direction_block(const int8_t encoderPolarities[4], const int8_t outputDirections[4]);
void motor_brake(uint8_t channel);
void motor_brake_mask(uint8_t motorMask);
void motor_auto_calibrate(uint8_t channel);
void motor_auto_calibrate_all();
void motor_set_distance_cm(uint8_t channel, float dist_cm, float rpm);
bool motor_distance_done(uint8_t channel);
MotorCalibrationStatus motor_calibration_status();
bool motor_calibration_busy();
bool motor_calibration_succeeded();
bool motor_calibration_failed();
bool motor_calibrate(uint8_t motorMask, uint32_t timeout_ms = 300000);
void motor_set_sync_motion(const float rpms[4], float dist_cm);
int32_t encoder_get_count(uint8_t channel);
void encoder_reset(uint8_t channel);
void encoder_reset_mask(uint8_t motorMask);

// =========================================================
// PWM 舵机控制函数声明
// =========================================================
void setPwmServo(uint8_t channel, uint16_t pulse_us, uint16_t time_ms);
void setPwmServoPulse(uint8_t channel, uint16_t pulse_us, uint16_t time_ms = 0);
uint16_t getPwmServoPulse(uint8_t channel);
void setPwmServoOffset(uint8_t channel, int16_t offset_us);
int16_t getPwmServoOffset(uint8_t channel);
void pwm_servo_set_pulse(uint8_t channel, uint16_t pulse_us, uint16_t time_ms = 0);
uint16_t pwm_servo_get_pulse(uint8_t channel);
void pwm_servo_set_offset(uint8_t channel, int16_t offset_us);
int16_t pwm_servo_get_offset(uint8_t channel);

// =========================================================
// 低压报警、电池与蜂鸣器函数声明
// =========================================================
void setLowVoltageAlarmEnabled(bool enable);
bool isLowVoltageAlarmEnabled();
bool isLowVoltageAlarmActive();
bool setLowVoltageThresholdMv(uint16_t threshold_mv);
uint16_t readLowVoltageThresholdMv();
uint16_t readBatteryMv();
void setBuzzer(uint16_t freq_hz, uint8_t duty_percent);
void low_voltage_alarm_set_enabled(bool enable);
bool low_voltage_alarm_is_enabled();
bool low_voltage_alarm_is_active();
bool low_voltage_alarm_set_threshold_mv(uint16_t threshold_mv);
uint16_t low_voltage_alarm_get_threshold_mv();
uint16_t battery_get_mv();
void buzzer_set(uint16_t freq_hz, uint8_t duty_percent);

// =========================================================
// 串口总线舵机透传函数声明
// =========================================================
void    busServoSend(const uint8_t* packet, uint8_t len, uint8_t exec_mode = 1);
uint8_t busServoRecv(uint8_t* rxBuf, uint8_t maxLen, uint16_t timeout_ms);
void    busServoSendAndPrint(const char* desc);
void    printHexPacket(const char* prefix, const uint8_t* buf, uint8_t len);

// =========================================================
// 旧协议 (0x55 0x55) 总线舵机函数声明
// =========================================================
uint8_t oldProtocolCheckSum(const uint8_t* buf);
void oldBusServoMoveTimeWrite(uint8_t id, uint16_t position, uint16_t time_ms);
void oldBusServoMoveTimeRead(uint8_t id);
void oldBusServoMoveTimeWaitWrite(uint8_t id, uint16_t position, uint16_t time_ms);
void oldBusServoMoveStart(uint8_t id);
void oldBusServoMoveStop(uint8_t id);
void oldBusServoIDRead(uint8_t id);
void oldBusServoPosRead(uint8_t id);
void oldBusServoVinRead(uint8_t id);
void oldBusServoTempRead(uint8_t id);
void oldBusServoOrMotorModeWrite(uint8_t id, uint8_t mode, int16_t speed);
void oldBusServoLoadWrite(uint8_t id, uint8_t enable);
void oldProtocolParseReply(const uint8_t* buf, uint8_t len);

// =========================================================
// 新协议 (0xFF 0xFF) 总线舵机函数声明
// =========================================================
uint8_t newProtocolCheckSum(const uint8_t* buf);
void newBusServoWriteData(uint8_t id, uint8_t addr, const uint8_t* data, uint8_t dataLen);
void newBusServoWriteByte(uint8_t id, uint8_t addr, uint8_t value);
void newBusServoWriteWord(uint8_t id, uint8_t addr, int16_t value);
void newBusServoReadData(uint8_t id, uint8_t addr, uint8_t readLen);
void newBusServoQueryStatus(uint8_t id);
void newBusServoRegWrite(uint8_t id, uint8_t addr, const uint8_t* data, uint8_t dataLen);
void newBusServoAction(uint8_t id);
void newBusServoReset(uint8_t id);
void newBusServoSetPosition(uint8_t id, int16_t position, uint16_t duration_ms);
void newBusServoSetEnable(uint8_t id, uint8_t enable);
void newBusServoSetMode(uint8_t id, uint8_t mode);
void newBusServoSetSpeed(uint8_t id, int16_t speed);
void newProtocolParseReply(const uint8_t* buf, uint8_t len);
