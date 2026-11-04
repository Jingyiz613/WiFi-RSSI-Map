#ifndef _WonderLLM_PORTING_H
#define _WonderLLM_PORTING_H

#include <Arduino.h>
#include <stdint.h>
#include <Wire.h>

#ifndef ARDUINO_ARCH_ESP32
#error "MiniAutoPro_Default supports ESP32 only."
#endif

#define WonderLLM_SLAVE_ADDRESS 0x55

// I2C 从机在“空包/状态同步包”里上报的设备状态字节取值。
// 0x00~0x0B 对应从机 DeviceState 枚举（见 device_state.h），用于状态同步。
// 0xF0 为专用状态：从机在真正下发 MCP 指令数据前，会先发送一个携带该状态的
// 状态包，提示主机随后将读取到一条 MCP 指令数据。
#define WonderLLM_MCP_PENDING_STATE 0xF0

extern const char tool_finish[];
extern const char tool_buzzer[];
extern const char tool_led[];
extern const char tool_mode[];
extern const char tool_status[];
extern const char tool_turn[];
extern const char tool_stop[];
extern const char tool_move[];
extern const char tool_claw[];

void delay_ms(int ms_num);
uint32_t Get_time_now(void);
bool Detect_WonderLLM(void);
void WonderLLM_Recover_I2C(void);
void IIC_Config_MCP_Transmit(void);
void IIC_Config_normal_Transmit(void);
int WonderLLM_Receive_Data(uint8_t* buffer, uint16_t size, bool stop_flag);
int WonderLLM_Send_Data(const uint8_t* buffer, uint16_t len);

#endif
