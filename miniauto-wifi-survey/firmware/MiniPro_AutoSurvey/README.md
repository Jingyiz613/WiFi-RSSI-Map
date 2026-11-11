# MiniPro AutoSurvey 固件

入口：MiniPro_AutoSurvey.ino。

此目录是在用户提供的 MiniAutoPro_Default 和已验证 Wi-Fi RSSI 版本上新增的自动定距控制版本。实测主控为 ESP32-D0WD、4MB Flash，使用 ESP32 Dev Module、DIO、80MHz、Huge APP 分区，不能按原说明中的 ESP32-S3 配置烧录。

AutoMotion 仅接收认证后的短程运动请求，执行有限距离的编码器运动，并检查心跳、运动反馈和手机接管。HTTP线程不直接操作电机，实际运动在主循环处理。原BLE运动指令会结束自动控制，WonderLLM运动在自动任务持有控制权时暂停。

默认上电不运动，不会重启后续跑。使用步骤、校准要求、定位限制和电脑端程序见上一级目录的使用说明.md。
