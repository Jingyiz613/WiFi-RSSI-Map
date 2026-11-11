#include "WonderLLM.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DEBUG_MODE 0

WonderLLM_Info WonderLLM_hiwonder;

static uint32_t g_next_receive_ms = 0;
static uint8_t g_receive_fail_count = 0;
static bool g_last_receive_i2c_error = false;
static uint8_t g_last_device_state = 0;
// 从机已上报 0xF0“准备下发 MCP 指令”状态，下一次轮询应尽快读取真正的指令数据。
static bool g_mcp_command_incoming = false;

static FrameMode parse_command(WonderLLM_Info* obj, const char* json_str);
static bool send_frame(const uint8_t* data, uint16_t len);
static bool receive_frame(uint8_t* buffer, uint16_t* len);
static bool receive_one_frame(uint8_t* buffer, uint16_t* len);
static bool receive_frame_head(uint16_t* part_ID, uint16_t* part_num, uint16_t* data_len);
static uint8_t calculate_checksum(const uint8_t* data, uint16_t len);
static bool register_tools(void);
static bool parse_json_int_field(const char* json_str, const char* key, int* output);
static bool parse_json_string_field(const char* json_str, const char* key, char* output, size_t output_size);
static uint8_t clamp_u8(int value);

bool WonderLLM_Init(void) {
  uint32_t start_tick = Get_time_now();

  while (Get_time_now() - start_tick <= 3000) {
    if (Detect_WonderLLM()) {
      IIC_Config_MCP_Transmit();
      delay_ms(5);
      bool ok = register_tools();
      IIC_Config_normal_Transmit();
      return ok;
    }
    delay_ms(100);
  }

  return false;
}

void WonderLLM_Info_Get(WonderLLM_Info* obj) {
  if (obj == NULL) {
    return;
  }

  obj->Frame_mode = Frame_NULL;
  uint32_t now = Get_time_now();
  if ((int32_t)(now - g_next_receive_ms) < 0) {
    return;
  }

  uint16_t received_len = sizeof(obj->json_data_raw) - 1;
  g_last_receive_i2c_error = false;
  if (receive_frame((uint8_t*)obj->json_data_raw, &received_len) && received_len > 0) {
    obj->json_data_raw[received_len] = '\0';
    obj->Frame_mode = parse_command(obj, obj->json_data_raw);
    g_receive_fail_count = 0;
    g_next_receive_ms = now + 80;
    return;
  }

  // 从机预告随后将下发 MCP 指令数据：尽快再次轮询，用常规读取路径取回指令，
  // 避免在同一次调用里紧跟读取导致从机尚未就绪而丢包。
  if (g_mcp_command_incoming) {
    g_mcp_command_incoming = false;
    g_receive_fail_count = 0;
    g_next_receive_ms = now + 10;
    return;
  }

  if (!g_last_receive_i2c_error) {
    g_receive_fail_count = 0;
    g_next_receive_ms = now + 300;
    return;
  }

  if (g_receive_fail_count < 20) {
    g_receive_fail_count++;
  }

  if (g_receive_fail_count >= 12) {
    WonderLLM_Recover_I2C();
    g_receive_fail_count = 0;
    g_next_receive_ms = now + 800;
  } else {
    g_next_receive_ms = now + 120 + (uint32_t)g_receive_fail_count * 80;
  }
}

void WonderLLM_Send_Action_Finish(void) {
  const char json_str[] = "{\"command\":\"action_finish\",\"params\":\"true\"}";
  send_frame((const uint8_t*)json_str, strlen(json_str));
}

void WonderLLM_Send_Status(const char* params_str) {
  if (params_str == NULL) {
    return;
  }

  char json_str[128];
  int written = snprintf(json_str, sizeof(json_str), "{\"command\":\"status\",\"params\":%s}", params_str);
  if (written > 0 && written < (int)sizeof(json_str)) {
    send_frame((const uint8_t*)json_str, (uint16_t)written);
  }
}

bool WonderLLM_Request_Vision(void) {
  const char json_str[] =
    "{\"tool_name\":\"mcu.request\",\"command\":\"vision\","
    "\"params\":\"Return only left, right, or none for the road-sign direction.\"}";
  return send_frame((const uint8_t*)json_str, strlen(json_str));
}

uint8_t WonderLLM_Get_Device_State(void) {
  return g_last_device_state;
}

static FrameMode parse_command(WonderLLM_Info* obj, const char* json_str) {
  if (obj == NULL || json_str == NULL) {
    return Frame_NULL;
  }

  if (strstr(json_str, "\"status_name\"") != NULL) {
    char status_name[20] = {0};
    if (!parse_json_string_field(json_str, "status_name", status_name, sizeof(status_name))) {
      return Frame_NULL;
    }
    if (strcmp(status_name, "battery") == 0) return Frame_get_status_battery;
    if (strcmp(status_name, "angle") == 0) return Frame_get_status_angle;
    if (strcmp(status_name, "distance") == 0) return Frame_get_status_distance;
    if (strcmp(status_name, "running_mode") == 0) return Frame_get_status_running_mode;
    return Frame_NULL;
  }

  if (strstr(json_str, "\"vision\"") != NULL) {
    memset(obj->vision_direction, 0, sizeof(obj->vision_direction));
    if (parse_json_string_field(json_str, "vision", obj->vision_direction,
                                sizeof(obj->vision_direction))) {
      return Frame_vision;
    }
    return Frame_NULL;
  }

  if (strstr(json_str, "self.car.stop") != NULL || strstr(json_str, "\"brake\"") != NULL) {
    obj->stop_brake = (strstr(json_str, "true") != NULL || strstr(json_str, "True") != NULL);
    return Frame_stop;
  }

  if (strstr(json_str, "\"move\"") != NULL) {
    int distance = 0;
    int16_t magnitude = 0;
    if (parse_json_int_field(json_str, "distance", &distance)) {
      magnitude = (int16_t)max(distance, 0);
    }
    obj->motion_target_distance = 0;
    obj->motion_strafe_distance = 0;
    if (strstr(json_str, "\"backward\"") != NULL) {
      obj->motion_target_distance = (int16_t)(-magnitude);
    } else if (strstr(json_str, "\"left\"") != NULL) {
      obj->motion_strafe_distance = (int16_t)(-magnitude);
    } else if (strstr(json_str, "\"right\"") != NULL) {
      obj->motion_strafe_distance = magnitude;
    } else {
      obj->motion_target_distance = magnitude;
    }
    return Frame_move;
  }

  if (strstr(json_str, "\"direction\"") != NULL) {
    int angle = 0;
    if (parse_json_int_field(json_str, "angle", &angle)) {
      obj->motion_target_angle = (int16_t)angle;
    }
    if (strstr(json_str, "\"right\"") != NULL && obj->motion_target_angle > 0) {
      obj->motion_target_angle *= -1;
    }
    return Frame_turn;
  }

  if (strstr(json_str, "\"running_mode\"") != NULL ||
      strstr(json_str, "\"mode\"") != NULL) {
    char mode_value[24] = {0};
    obj->running_mode = 0;
    obj->avoid_distance = 0;

    if (!parse_json_string_field(json_str, "running_mode", mode_value, sizeof(mode_value))) {
      parse_json_string_field(json_str, "mode", mode_value, sizeof(mode_value));
    }

    if (strcmp(mode_value, "avoid") == 0 || strcmp(mode_value, "避障") == 0) {
      int distance = 0;
      obj->running_mode = 1;
      if (parse_json_int_field(json_str, "distance", &distance)) {
        if (distance < 1 || distance > 500) {
          return Frame_NULL;
        }
        obj->avoid_distance = (uint16_t)distance;
      }
      return Frame_set_mode_avoid;
    }

    if (strcmp(mode_value, "smart_line_patrol") == 0 || strcmp(mode_value, "智能巡线") == 0) {
      obj->running_mode = 3;
    } else if (strcmp(mode_value, "line_patrol") == 0 || strcmp(mode_value, "巡线") == 0) {
      obj->running_mode = 2;
    } else if (strcmp(mode_value, "transport") == 0 ||
               strcmp(mode_value, "搬运") == 0 ||
               strcmp(mode_value, "智能搬运") == 0) {
      obj->running_mode = 5;
    } else {
      return Frame_NULL;
    }

    int distance = 0;
    if ((obj->running_mode == 2 || obj->running_mode == 3) &&
        parse_json_int_field(json_str, "distance", &distance)) {
      if (distance < 1 || distance > 500) {
        return Frame_NULL;
      }
      obj->avoid_distance = (uint16_t)distance;
    }
    return Frame_set_mode;
  }

  if (strstr(json_str, "\"lr\"") != NULL) {
    int value = 0;
    if (parse_json_int_field(json_str, "lr", &value)) obj->rgb_left[0] = clamp_u8(value);
    if (parse_json_int_field(json_str, "lg", &value)) obj->rgb_left[1] = clamp_u8(value);
    if (parse_json_int_field(json_str, "lb", &value)) obj->rgb_left[2] = clamp_u8(value);
    if (parse_json_int_field(json_str, "rr", &value)) obj->rgb_right[0] = clamp_u8(value);
    if (parse_json_int_field(json_str, "rg", &value)) obj->rgb_right[1] = clamp_u8(value);
    if (parse_json_int_field(json_str, "rb", &value)) obj->rgb_right[2] = clamp_u8(value);
    return Frame_set_led_color;
  }

  if (strstr(json_str, "\"count\"") != NULL) {
    int count = 0;
    if (parse_json_int_field(json_str, "count", &count)) {
      obj->buzzer_count = (char)constrain(count, 0, 10);
    }
    return Frame_set_buzzer;
  }

  if (strstr(json_str, "set_claw") != NULL ||
      strstr(json_str, "\"action\"") != NULL) {
    char action[16] = {0};
    int value = 0;

    obj->claw_channel = 0;
    obj->claw_action = ClawAction_none;
    obj->claw_angle = -1;
    obj->claw_pulse = 0;
    obj->claw_time_ms = 0;
    obj->claw_has_angle = false;
    obj->claw_has_pulse = false;
    obj->claw_has_time = false;

    if (parse_json_string_field(json_str, "action", action, sizeof(action))) {
      if (strcmp(action, "open") == 0 || strcmp(action, "打开") == 0 || strcmp(action, "张开") == 0) {
        obj->claw_action = ClawAction_open;
        obj->claw_channel = 1;
      } else if (strcmp(action, "close") == 0 || strcmp(action, "关闭") == 0 || strcmp(action, "闭合") == 0 || strcmp(action, "夹紧") == 0) {
        obj->claw_action = ClawAction_close;
        obj->claw_channel = 1;
      } else if (strcmp(action, "up") == 0 || strcmp(action, "raise") == 0 || strcmp(action, "lift") == 0 ||
                 strcmp(action, "抬升") == 0 || strcmp(action, "抬起") == 0 || strcmp(action, "升起") == 0) {
        obj->claw_action = ClawAction_up;
        obj->claw_channel = 0;
      } else if (strcmp(action, "down") == 0 || strcmp(action, "lower") == 0 ||
                 strcmp(action, "放下") == 0 || strcmp(action, "下降") == 0 || strcmp(action, "降低") == 0) {
        obj->claw_action = ClawAction_down;
        obj->claw_channel = 0;
      }
    }

    if (parse_json_int_field(json_str, "channel", &value)) {
      if (value >= 1 && value <= 4) {
        obj->claw_channel = (uint8_t)(value - 1);
      } else {
        obj->claw_channel = (uint8_t)constrain(value, 0, 3);
      }
    }

    if (parse_json_int_field(json_str, "angle", &value)) {
      obj->claw_angle = (int16_t)constrain(value, 0, 180);
      obj->claw_has_angle = true;
    }

    if (parse_json_int_field(json_str, "pulse", &value) ||
        parse_json_int_field(json_str, "pulse_us", &value)) {
      obj->claw_pulse = (uint16_t)constrain(value, 500, 2500);
      obj->claw_has_pulse = true;
    }

    if (parse_json_int_field(json_str, "time_ms", &value) ||
        parse_json_int_field(json_str, "time", &value)) {
      obj->claw_time_ms = (uint16_t)constrain(value, 0, 10000);
      obj->claw_has_time = true;
    }

    return Frame_set_claw;
  }

  return Frame_NULL;
}

static bool register_tools(void) {
  const char* const tools[] = {
    tool_move,
    tool_turn,
    tool_stop,
    tool_status,
    tool_mode,
    tool_led,
    tool_buzzer,
    tool_claw,
    tool_finish
  };

  for (size_t i = 0; i < (sizeof(tools) / sizeof(tools[0])); i++) {
    if (!send_frame((const uint8_t*)tools[i], strlen(tools[i]))) {
      return false;
    }
    delay_ms(5);
  }

  return true;
}

static bool send_frame(const uint8_t* data, uint16_t len) {
  if (data == NULL || len == 0) {
    return false;
  }
  return WonderLLM_Send_Data(data, len) == 0;
}

static bool receive_frame_head(uint16_t* part_ID, uint16_t* part_num, uint16_t* data_len) {
  uint8_t header[8];

  int receive_result = WonderLLM_Receive_Data(header, sizeof(header), true);
  if (receive_result != 0) {
    g_last_receive_i2c_error = true;
    return false;
  }
  if (header[0] != 0xAA || header[1] != 0x55) {
    return false;
  }

  *data_len = ((uint16_t)header[2] << 8) | header[3];
  if (*data_len == 0 || *data_len > 31) {
    return false;
  }

  *part_ID = ((uint16_t)header[5] << 8) | header[4];
  *part_num = ((uint16_t)header[7] << 8) | header[6];
  return true;
}

static bool receive_frame(uint8_t* buffer, uint16_t* len) {
  if (buffer == NULL || len == NULL || *len == 0) {
    return false;
  }

  // 读取一帧数据。从机在空闲时会持续返回“状态同步包”（仅 1 字节设备状态），
  // 在有 MCP 指令待下发时则先发一个 0xF0 预告包，再下发真正的指令数据。
  if (!receive_one_frame(buffer, len)) {
    return false;
  }

  // 单字节负载即状态同步包，承载从机当前设备状态字节。
  if (*len == 1) {
    g_last_device_state = buffer[0];

    // 0xF0 表示从机已置“准备下发 MCP 指令”状态：真正的指令数据会在随后的
    // 读事务中下发。这里不立即紧跟读取（此时从机往往尚未把指令搬到发送缓冲，
    // 紧跟读取会读到无效数据并把指令丢弃），而是置位标志让上层尽快再次轮询，
    // 用常规读取路径稳妥地取回指令数据。
    if (buffer[0] == WonderLLM_MCP_PENDING_STATE) {
      g_mcp_command_incoming = true;
      *len = 0;
      return false;
    }

    // 其它设备状态字节仅用于同步，无 JSON 负载，交回上层会被解析为 Frame_NULL。
  }

  return true;
}

static bool receive_one_frame(uint8_t* buffer, uint16_t* len) {
  if (buffer == NULL || len == NULL || *len == 0) {
    return false;
  }

  uint16_t max_data_len = *len;
  uint16_t data_len = 0;
  uint16_t part_ID = 0;
  uint16_t part_num = 0;
  uint16_t buffer_index = 0;

  *len = 0;
  if (!receive_frame_head(&part_ID, &part_num, &data_len) || part_ID != 1 || part_num == 0) {
    return false;
  }

  for (uint16_t i = 1; i <= part_num; i++) {
    if ((buffer_index + data_len) > max_data_len) {
      return false;
    }

    delay_ms(10);
    int receive_result = WonderLLM_Receive_Data(buffer + buffer_index, data_len + 1, true);
    if (receive_result != 0) {
      g_last_receive_i2c_error = true;
      return false;
    }

    uint8_t received_checksum = buffer[buffer_index + data_len];
    if (received_checksum != calculate_checksum(buffer + buffer_index, data_len)) {
      memset(buffer, 0, max_data_len);
      return false;
    }

#if (DEBUG_MODE == 1)
    Serial.print(F("data:"));
    for (uint16_t t = 0; t < data_len; t++) {
      Serial.print((int)buffer[buffer_index + t]);
      Serial.print(' ');
    }
    Serial.println();
#endif

    buffer_index += data_len;

    if (i < part_num) {
      uint16_t next_part_ID = 0;
      uint16_t next_part_num = 0;
      delay_ms(100);

      if (!receive_frame_head(&next_part_ID, &next_part_num, &data_len)) {
        return false;
      }
      if (next_part_ID != (uint16_t)(part_ID + 1) || next_part_num != part_num) {
        return false;
      }
      part_ID = next_part_ID;
    }
  }

  *len = buffer_index;
  return true;
}

static bool parse_json_int_field(const char* json_str, const char* key, int* output) {
  char key_pattern[32];
  const char* key_ptr = NULL;
  const char* colon_ptr = NULL;
  char* end_ptr = NULL;

  if (json_str == NULL || key == NULL || output == NULL) {
    return false;
  }
  if (snprintf(key_pattern, sizeof(key_pattern), "\"%s\"", key) >= (int)sizeof(key_pattern)) {
    return false;
  }

  key_ptr = strstr(json_str, key_pattern);
  if (key_ptr == NULL) {
    return false;
  }
  colon_ptr = strchr(key_ptr + strlen(key_pattern), ':');
  if (colon_ptr == NULL) {
    return false;
  }

  long value = strtol(colon_ptr + 1, &end_ptr, 10);
  if (end_ptr == colon_ptr + 1) {
    return false;
  }

  *output = (int)value;
  return true;
}

static bool parse_json_string_field(const char* json_str, const char* key, char* output, size_t output_size) {
  char key_pattern[32];
  const char* key_ptr = NULL;
  const char* colon_ptr = NULL;
  const char* quote_start = NULL;
  const char* quote_end = NULL;
  size_t value_len = 0;

  if (json_str == NULL || key == NULL || output == NULL || output_size == 0) {
    return false;
  }
  if (snprintf(key_pattern, sizeof(key_pattern), "\"%s\"", key) >= (int)sizeof(key_pattern)) {
    return false;
  }

  key_ptr = strstr(json_str, key_pattern);
  if (key_ptr == NULL) {
    return false;
  }
  colon_ptr = strchr(key_ptr + strlen(key_pattern), ':');
  if (colon_ptr == NULL) {
    return false;
  }
  quote_start = strchr(colon_ptr + 1, '"');
  if (quote_start == NULL) {
    return false;
  }
  quote_end = strchr(quote_start + 1, '"');
  if (quote_end == NULL) {
    return false;
  }

  value_len = (size_t)(quote_end - quote_start - 1);
  if (value_len >= output_size) {
    value_len = output_size - 1;
  }
  memcpy(output, quote_start + 1, value_len);
  output[value_len] = '\0';
  return true;
}

static uint8_t clamp_u8(int value) {
  return (uint8_t)constrain(value, 0, 255);
}

static uint8_t calculate_checksum(const uint8_t* data, uint16_t len) {
  uint8_t checksum = 0;
  for (uint16_t i = 0; i < len; i++) {
    checksum ^= data[i];
  }
  return checksum;
}
