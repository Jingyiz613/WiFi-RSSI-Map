#include "WonderLLM_porting.h"

const char tool_finish[] =
  "{\"command\":\"mcp_setting\",\"params\":\"true\"}";

const char tool_move[] =
  "{\"tool_name\":\"self.car.move\","
  "\"command\":\"控制麦克纳姆轮小车移动指定距离。move 为 forward 前进、backward 后退、left 向左横移、right 向右横移，distance 单位厘米。\","
  "\"params\":[[\"move\",\"string\"],[\"distance\",\"int\",1,200]],"
  "\"block\":\"true\",\"return\":\"false\"}";

const char tool_turn[] =
  "{\"tool_name\":\"self.car.turn\","
  "\"command\":\"控制小车原地转向指定角度。direction 为 left 或 right，angle 单位度。\","
  "\"params\":[[\"direction\",\"string\"],[\"angle\",\"int\",1,360]],"
  "\"block\":\"true\",\"return\":\"false\"}";

const char tool_stop[] =
  "{\"tool_name\":\"self.car.stop\","
  "\"command\":\"让小车立即停止。brake 为 true 时刹车，false 时平滑停止。\","
  "\"params\":[[\"brake\",\"bool\"]],"
  "\"block\":\"true\",\"return\":\"false\"}";

const char tool_status[] =
  "{\"tool_name\":\"self.car.get_status\","
  "\"command\":\"获取小车状态。status_name 可为 battery、angle、distance、running_mode。\","
  "\"params\":[[\"status_name\",\"string\"]],"
  "\"block\":\"true\",\"return\":\"true\"}";

const char tool_mode[] =
  "{\"tool_name\":\"self.car.set_mode\","
  "\"command\":\"切换小车运行模式。running_mode 可为 avoid 避障、line_patrol 巡线、smart_line_patrol 智能巡线、transport 智能搬运。avoid 和巡线模式可设置 distance，单位厘米。\","
  "\"params\":[[\"running_mode\",\"string\"],[\"distance\",\"int\",30,100]],"
  "\"block\":\"true\",\"return\":\"false\"}";

const char tool_led[] =
  "{\"tool_name\":\"self.car.set_led_color\","
  "\"command\":\"设置超声波模块左右 RGB 灯颜色。lr,lg,lb 为左灯，rr,rg,rb 为右灯，范围 0 到 255。\","
  "\"params\":[[\"lr\",\"int\",0,255],[\"lg\",\"int\",0,255],[\"lb\",\"int\",0,255],"
              "[\"rr\",\"int\",0,255],[\"rg\",\"int\",0,255],[\"rb\",\"int\",0,255]],"
  "\"block\":\"true\",\"return\":\"false\"}";

const char tool_buzzer[] =
  "{\"tool_name\":\"self.car.set_buzzer\","
  "\"command\":\"让蜂鸣器响指定次数。count 范围 1 到 10。\","
  "\"params\":[[\"count\",\"int\",1,10]],"
  "\"block\":\"true\",\"return\":\"false\"}";

const char tool_claw[] =
  "{\"tool_name\":\"self.car.set_claw\","
  "\"command\":\"控制机械臂动作。action 为 open 张开爪子、close 闭合爪子、up 抬升机械臂、down 降低机械臂，具体角度由底层实现，无需其他参数。\","
  "\"params\":[[\"action\",\"string\"]],"
  "\"block\":\"true\",\"return\":\"false\"}";

static TwoWire& g_wonderllm_wire = Wire1;

void delay_ms(int ms_num) {
  delay(ms_num);
}

uint32_t Get_time_now(void) {
  return millis();
}

void WonderLLM_Recover_I2C(void) {
  g_wonderllm_wire.setClock(100000);
  g_wonderllm_wire.setTimeOut(20);
}

bool Detect_WonderLLM(void) {
  g_wonderllm_wire.beginTransmission(WonderLLM_SLAVE_ADDRESS);
  delay_ms(15);
  return (g_wonderllm_wire.endTransmission() == 0);
}

void IIC_Config_MCP_Transmit(void) {
  g_wonderllm_wire.setClock(400000);
}

void IIC_Config_normal_Transmit(void) {
  g_wonderllm_wire.setClock(100000);
}

int WonderLLM_Receive_Data(uint8_t* buffer, uint16_t size, bool stop_flag) {
  if (buffer == NULL || size == 0) {
    return 0;
  }

  // 必须严格按 size 请求字节数：从机每次读事务只 i2c_slave_write 一次
  // （帧头 8 字节，或数据 data_len+1 字节）。若请求多于本次实际写入的字节数，
  // 主机会越界读到下一次事务的数据，导致协议错位、始终拼不出完整指令。
  int receive_result = g_wonderllm_wire.requestFrom(WonderLLM_SLAVE_ADDRESS, size, stop_flag);
  if (receive_result == 0) {
    return 1;
  }
  if ((uint16_t)receive_result != size) {
    return 3;
  }

  uint16_t index = 0;
  uint32_t start_time = Get_time_now();
  while ((index < size) && g_wonderllm_wire.available()) {
    buffer[index] = g_wonderllm_wire.read();
    index++;
    if (Get_time_now() - start_time > 10) {
      return 2;
    }
  }

  return (index >= size) ? 0 : 3;
}

int WonderLLM_Send_Data(const uint8_t* buffer, uint16_t len) {
  if (buffer == NULL || len == 0) {
    return 0;
  }

  uint16_t index = 0;
  while (index < len) {
    uint8_t chunk_len = (uint8_t)min((uint16_t)32, (uint16_t)(len - index));

    g_wonderllm_wire.beginTransmission(WonderLLM_SLAVE_ADDRESS);
    for (uint8_t i = 0; i < chunk_len; i++) {
      g_wonderllm_wire.write(buffer[index++]);
    }

    int transmit_result = g_wonderllm_wire.endTransmission(true);
    if (transmit_result != 0) {
      WonderLLM_Recover_I2C();
      return transmit_result;
    }
    delay_ms(1);
  }

  return 0;
}
