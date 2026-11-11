#pragma once

#include <Arduino.h>

// =========================================================
// SurveyAuto -- 离线自主采集
//
// 电脑通过 USB 串口一次性下发完整计划，之后可以完全断开。
// 小车按计划逐段移动、在每个点位静置采样，结果存在板载 NVS。
// 跑完后重新连接 USB，用 SURVEY_DUMP 把数据取回。
//
// 串口协议（每行一条，空格分隔，均为 ASCII）：
//   SURVEY_BEGIN <speed_mps> <dwell_ms> <samples> <count>
//   SURVEY_STEP  <label> <dx_m> <dy_m> [sample]  (重复 count 次)
//   SURVEY_END                                  (校验并保存计划)
//   SURVEY_RUN                                  (开始执行)
//   SURVEY_STATUS                               (打印当前状态)
//   SURVEY_ABORT                                (中止，立即停车)
//   SURVEY_DUMP                                 (逐行导出结果)
//   SURVEY_CLEAR                                (清除计划和结果)
//
// 说明：
//   - dx 向右为正，dy 向前为正，单位米；单步只允许一个轴非零。
//   - dx=dy=0 表示原地采样（用于起点）。
//   - sample 省略时为 1；sample=0 表示只经过不停留、不采样（绕行中间格）。
//   - 计划在收到 SURVEY_RUN 之前不会动，任何时刻上电都不会自己跑。
// =========================================================

void surveyAutoSetup();
void surveyAutoUpdate();

// 自主采集正在占用电机时为 true。HTTP 遥控会据此拒绝 arm。
bool surveyAutoBusy();

// 由 WifiSurvey 的串口解析器转发；返回 true 表示这条命令已被处理。
bool surveyAutoHandleSerial(const String& line);

// 立即中止（供外部调用）。
void surveyAutoAbort();
