#ifndef __WonderLLM_H
#define __WonderLLM_H

#include <stdint.h>
#include <stdbool.h>
#include "WonderLLM_porting.h"

#define WonderLLM_MAX_FRAME_SIZE 256

typedef enum {
  Frame_NULL = 0,
  Frame_move,
  Frame_turn,
  Frame_stop,
  Frame_get_status_battery,
  Frame_get_status_angle,
  Frame_get_status_distance,
  Frame_get_status_running_mode,
  Frame_set_mode,
  Frame_set_mode_avoid,
  Frame_set_led_color,
  Frame_set_buzzer,
  Frame_set_claw,
  Frame_vision
} FrameMode;

typedef enum {
  ClawAction_none = 0,
  ClawAction_open,
  ClawAction_close,
  ClawAction_up,
  ClawAction_down
} ClawAction;

typedef struct {
  int16_t motion_target_distance;
  int16_t motion_strafe_distance;
  int16_t motion_target_angle;
  char running_mode;
  uint16_t avoid_distance;
  uint8_t rgb_left[3];
  uint8_t rgb_right[3];
  char buzzer_count;
  bool stop_brake;
  uint8_t claw_channel;
  ClawAction claw_action;
  int16_t claw_angle;
  uint16_t claw_pulse;
  uint16_t claw_time_ms;
  bool claw_has_angle;
  bool claw_has_pulse;
  bool claw_has_time;
  char vision_direction[8];
  FrameMode Frame_mode;
  char json_data_raw[WonderLLM_MAX_FRAME_SIZE];
} WonderLLM_Info;

extern WonderLLM_Info WonderLLM_hiwonder;

bool WonderLLM_Init(void);
void WonderLLM_Info_Get(WonderLLM_Info* obj);
void WonderLLM_Send_Action_Finish(void);
void WonderLLM_Send_Status(const char* params_str);
bool WonderLLM_Request_Vision(void);
uint8_t WonderLLM_Get_Device_State(void);

#endif
