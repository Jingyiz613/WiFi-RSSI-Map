# ESP32-C6 手动搬移采集

当自动小车尚未校准、路线环境复杂，或只需要稳定取得一组房间测点时，可以把 ESP32-C6 开发板当作移动探针：电脑按网格提示点位，人员把开发板放到格子中心，电脑自动读取 RSSI。

这条路线不依赖电机、编码器或自动导航，仍然使用同一套米制布局、测点编号和路线规划。

## 与 MiniPro 自动采集的区别

| | ESP32-C6 手动搬移 | MiniPro 自动采集 |
|---|---|---|
| 移动方式 | 人员按提示搬移开发板 | 小车按编码器路线行驶 |
| 依赖 | Wi-Fi 与 USB 供电 | 小车底盘、校准、电量、场地 |
| 优点 | 坐标准确、硬件依赖少、适合先验证数据 | 自动化程度高、适合重复走测 |
| 限制 | 每个点需要人工操作 | 受打滑、避障和场地影响 |
| RSSI 端口 | 62587 | 62587 |
| 频段 | 2.4GHz | 2.4GHz |

## 一、准备固件

需要 Arduino ESP32 core 3.x。工程目录为 `firmware/Esp32C6Rssi/`。

1. 复制配置模板：

   ```powershell
   Copy-Item firmware\Esp32C6Rssi\secrets.example.h firmware\Esp32C6Rssi\secrets.h
   ```

2. 编辑 `secrets.h`，填写采集节点要连接的 2.4GHz Wi-Fi。该文件已被 `.gitignore` 排除，不要提交真实密码。

3. 使用 Arduino CLI 编译并烧录：

   ```powershell
   arduino-cli compile --fqbn esp32:esp32:esp32c6 firmware\Esp32C6Rssi
   arduino-cli upload -p COM5 --fqbn esp32:esp32:esp32c6 firmware\Esp32C6Rssi
   ```

   将 `COM5` 换成开发板实际串口。若没有 `secrets.h`，固件会使用仅供本地联调的演示 Wi-Fi 配置，不能直接用于正式采集。

4. 打开 115200 波特率串口监视器，发送 `STATUS`，确认返回 `connected`、IP、信道和 RSSI。

## 二、确认 HTTP 接口

电脑和 ESP32-C6 必须连接到同一网络。浏览器或命令行访问：

```powershell
curl http://192.168.1.50:62587/espectre/v1/wifi
```

把地址换成串口打印的节点 IP。正常响应示例：

```json
{
  "firmware": "Esp32C6-RSSI-1.0",
  "connected": true,
  "ssid": "example",
  "bssid": "aa:bb:cc:dd:ee:ff",
  "channel": 6,
  "band": "2.4GHz",
  "rssi_dbm": -48
}
```

如果电脑无法访问，先检查两台设备是否在同一网段、路由器是否开启 AP 隔离，以及节点是否仍在线。

## 三、准备布局

双击 `启动控制台.bat`，完成以下设置并保存：

- 房间实际长宽；
- 路由器所在网格；
- 家具矩形与安全间距；
- 起点和禁行区域。

保存后会生成 `layout.json`。手动采集脚本会读取该文件，因此测点编号、坐标和排除格与自动模式一致。

## 四、开始采集

1. 双击 `启动手动采集.bat`；
2. 输入开发板地址，例如 `192.168.1.50:62587`；
3. 按屏幕提示把开发板放到当前格子中心，保持与正式测量一致的高度；
4. 按回车采样。默认连续读取 10 次并取中位数；
5. 按 `s` 跳过当前点，按 `r` 重测上一点，按 `q` 保存并退出。

每完成一个点都会立即写入：

- `manual_survey.csv`：适合直接用表格软件查看；
- `manual_survey.json`：包含完整参数和结构化结果。

`启动手动采集.bat` 默认启用隐私模式，将 SSID/BSSID 写成 `[redacted]`。如果研究确实需要接入点标识，可直接运行命令行并去掉 `--redact`：

```powershell
tools\py.cmd host\survey_manual.py --target 192.168.1.50:62587 --layout layout.json
```

不要提交包含真实 SSID、BSSID 或户型信息的采集结果。

## 五、结果字段

| 字段 | 含义 |
|---|---|
| `point_id` | 测点编号，例如 P11 |
| `row` / `column` | 网格行列 |
| `x_m` / `y_m` | 测点在房间内的米制坐标 |
| `rssi_dbm` | 多次采样的中位数 |
| `rssi_min` / `rssi_max` | 本次采样的最小值和最大值 |
| `samples` | 有效采样次数 |
| `ssid` / `bssid` | 关联 AP 标识；默认脱敏 |
| `channel` | 2.4GHz 信道 |
| `error` | 跳过或采样失败原因 |

## 六、操作注意事项

- ESP32-C6 固件只采集 2.4GHz RSSI，不包含 5GHz、CSI 或雷达数据。
- 每次把开发板放到同一高度，避免人体遮挡天线方向；测点上方的身体位置应尽量保持一致。
- 默认 10 次采样用于抑制瞬时波动，但不能消除人员走动、门开关等持续变化。
- 中途退出或按 `Ctrl+C` 时会保存已完成点位；重新开始会覆盖同名输出文件。
- 若节点 DHCP 地址变化，重新用串口查看 IP，或先在路由器中设置地址保留。

## 七、无硬件测试

手动采集逻辑可以在没有开发板的情况下运行测试：

```powershell
tools\py.cmd -m pytest tests\test_manual_survey.py -q
```

测试覆盖中位数计算、节点断连错误，以及 SSID/BSSID 脱敏输出。
