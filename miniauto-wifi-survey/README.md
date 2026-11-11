# MiniAuto Wi-Fi 采集系统

让 miniAuto 麦克纳姆轮小车按规划路线自动行驶，并在房间每个网格点上采集路由器 RSSI，最后生成可直接用于热力图的数据。

支持两种运行方式，按被测网络的条件选：

| 方式 | 电脑连被测 Wi-Fi | 适用场景 |
|---|---|---|
| **在线模式** | 是 | 被测网络能上网，或电脑有第二个网口 |
| **离线模式** | 不需要 | 被测网络不能上网，电脑要一直保持在线 |

## 四个核心能力

| 需求 | 实现 |
|---|---|
| 手动选择路由器位置 | 图形界面点选网格格子，或输入 `P68` / `x_m,y_m` 米制坐标 |
| 小车自动行驶 | 编码器定距 + BFS 避障蛇形路线，走到每个格子中心 |
| 烧录到小车 | `tools/flash.ps1` 一键烧录（串口自动识别 + 芯片校验） |
| 完全离线采集 | USB 下发任务 → 拔线 → 小车自己跑完 → 插线取数据 |
| 自定义家具 | 鼠标拖动框选任意尺寸矩形家具，按安全间距判定禁行格 |

## 目录结构

```
miniauto-wifi-survey/
├── 启动控制台.bat              双击启动图形界面
├── layout.json                当前房间布局（界面自动保存）
├── layout.example.json        带家具的布局示例
├── host/
│   ├── survey_gui.py          图形界面（选点 + 预览 + 在线/离线采集）
│   ├── survey_cli.py          在线模式命令行入口
│   ├── survey_offline.py      离线模式：下发任务 / 校准 / 导出结果
│   ├── layout.py              米制家具模型、格子判定、JSON 存取、冲突校验
│   ├── route_planner.py       网格建模、BFS 避障、蛇形路线
│   ├── route_plot.py          路线预览图
│   ├── robot_client.py        USB 密钥 + HTTP 运动控制
│   ├── survey_runner.py       在线采集执行与落盘
│   ├── provision_wifi.py      USB 串口配网
│   └── console.py             Windows 控制台中文编码修正
├── firmware/
│   ├── MiniPro_AutoSurvey/    ESP32 Arduino 工程源码
│   └── bin/                   已编译固件（4 个 .bin）
├── tools/
│   ├── flash.ps1              一键烧录
│   ├── build.ps1              arduino-cli 编译
│   ├── setup_env.ps1          创建 Python 环境
│   └── py.cmd                 自动挑选可用的 Python 解释器
├── tests/
│   ├── test_offline.py        离线测试（41 项）
│   └── test_layout.py         家具布局测试（16 项）
├── docs/
│   ├── 使用说明.md             在线模式操作手册
│   └── 离线采集说明.md         离线模式操作手册
└── requirements.txt
```

## 快速开始

所有命令都在项目根目录执行。

### 1. 准备 Python 环境

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup_env.ps1
```

没有 `python` 命令时，`tools\py.cmd` 会自动找回机器上装了依赖的环境。

### 2. 烧录固件

先确认板子（不写入任何东西）：

```powershell
powershell -ExecutionPolicy Bypass -File tools\flash.ps1 -Identify
```

确认 `Chip` 是 `ESP32-...` 后烧录：

```powershell
powershell -ExecutionPolicy Bypass -File tools\flash.ps1 -Backup
```

串口会自动识别；有多个时脚本会列出来让你选。写入前会再核对芯片型号，不匹配直接拒绝。

> 会覆盖小车现有固件，`-Backup` 会先备份当前 4 MB Flash。原有 APP 手动遥控仍保留，APP 接管会中止自动任务。

### 3. 配网

```powershell
tools\py.cmd host\provision_wifi.py --ssid 你的WiFi名 --password 你的密码
```

不需要填串口，脚本自动识别。成功会打印 `小车地址：192.168.x.x:62587`。

### 4. 选路由器位置并采集

**在线模式**（电脑能连被测 Wi-Fi）：双击 `启动控制台.bat`，点选路由器格子 → 生成预览 → 填小车地址 → 点「③ 开始自动采集」。

**离线模式**（电脑不能连被测 Wi-Fi）：

```powershell
# 校准（首次必做）
tools\py.cmd host\survey_offline.py calibrate --axis x --distance 0.2
tools\py.cmd host\survey_offline.py calibrate --axis y --distance 0.2

# 下发并启动，看到 running 后拔线
tools\py.cmd host\survey_offline.py run --ap P68 --x-scale 1.07

# 跑完插回 USB，取数据
tools\py.cmd host\survey_offline.py dump --ap P68
```

界面里的「④ 离线下发并开始」「⑤ 读取离线结果」是同样功能的按钮版。
详细说明见 [docs/离线采集说明.md](docs/离线采集说明.md)。

小车必须放在起点格（默认 P11），车头朝向 P21 方向（行号增大的方向）。

### 5. 标记家具（可选）

房间里有沙发、床、柜子这类不能压过去的家具时，先在网格上标出来：

1. 点选「家具」模式
2. **按住左键拖动**，拉出一个矩形框住家具，松手
3. 输入名字，确定

尺寸不必对齐格子，程序按厘米记录真实位置。2.13 m 宽的沙发就画 2.13 m，不会被迫占满 3 格。

判定规则：格子中心离家具小于「安全间距」（默认 10 cm）就判为不可用。所以家具压到格子边缘不会误伤整格，只有真正挡住采样点的格子才会被排除。

安全间距可在右侧「房间与安全间距」里改：小车窄就调到 8 cm，想更保守就调到 15 cm。

双击清单可改名，选中后点「删除选中」可删除。布局自动保存到 `layout.json`，下次打开自动恢复。

**家具之间要留出 1 格通道**，否则小车开不进去，界面会显示红色警告。

## 命令行用法

```powershell
# 只生成路线预览，不碰硬件
tools\py.cmd host\survey_cli.py --ap P68 --preview-image

# 米制坐标选路由器（3.2 m, 1.5 m）
tools\py.cmd host\survey_cli.py --ap 3.2,1.5 --preview-image

# 在线模式正式采集（必须先完成实车校准）
tools\py.cmd host\survey_cli.py --ap P68 --run --calibrated --target 192.168.31.12:62587

# 离线模式
tools\py.cmd host\survey_offline.py run --ap P68 --detach
tools\py.cmd host\survey_offline.py status
tools\py.cmd host\survey_offline.py abort
tools\py.cmd host\survey_offline.py dump --ap P68
```

## 输出数据

每次采集生成 `output/AP_<点位>_<时间戳>/`：

| 文件 | 内容 |
|---|---|
| `rss_raw_*.csv` | 每条原始读数（含 BSSID、信道、SSID、时间） |
| `rss_summary_*.csv` | 每个点的中位数/均值/标准差/最值 |
| `experiment.json` | 完整参数、路线、完成进度 |

文件名里的 `autonomous` 表示在线模式，`offline` 表示离线模式，**列结构完全一致**。
两种模式的数据都能直接交给 `wifi-rss-toolkit` 的热力图脚本。

## 安全机制

在线模式：

- 上电默认不运动，必须显式 `arm` 才接受运动指令
- 控制密钥每次上电随机生成，只能通过 USB 读取，不写入文件
- 心跳中断 2 秒固件自动刹停
- 只接受单轴、0.05~1.10 m、0.03~0.12 m/s 的运动指令
- 手机 APP 接管时自动退出自动模式
- 正式采集需要 `--calibrated` 显式声明已校准

离线模式（无人值守，额外兜底）：

- 计划必须在 `SURVEY_RUN` 后才会动，上电永远不动
- 单段超时判失败，原地重试 1 次；连续 2 次失败立即停车
- 电机板报故障立即停车
- 电池低于 6500 mV 警告，低于 6000 mV 停车
- 每采完一个点写入 NVS，中途断电不丢已完成数据
- 断电重启后标记 `aborted`，已采数据仍可 `dump` 导出

## 已知限制

**这不是带定位建图的自主导航。** 没有摄像头、激光雷达或全向避障，绕行只依据手工标记的禁行格。编码器到位不等于车身实际位置准确，麦轮横移和打滑会累积误差（约 ±5 cm/格）。首次使用必须先做方向和距离校准。

**离线模式拔线后无法急停。** 只能靠固件超时保护，或者人工按住小车/切断电源。务必确认路线清空、电量充足再拔线。

运行前必须清空路线上的杂物，并留出车身宽度的净空。人员需在旁看护，能随时切断动力电源。

## 硬件参数

当前固件按以下参数编译。如果你的小车不同，改 `firmware/MiniPro_AutoSurvey/FactoryControl.cpp` 顶部常量后用 `tools/build.ps1` 重新编译：

| 参数 | 值 |
|---|---|
| 主控 | ESP32（Dev Module，4 MB Flash，Huge APP 分区） |
| 底盘 I2C | SDA=21, SCL=22, 地址 0x24 |
| 轮距（左右） | 0.140 m |
| 轴距（前后） | 0.145 m |
| 轮径 | 0.065 m |
| RSSI 端口 | 62587 |
| 离线存储 | NVS，最多 160 步 × 32 次采样 |

## 重新编译固件

工具链已装在工作区的 `tools\` 目录（本项目上一级），无需联网。`build.ps1` 会自动找到它：

```powershell
tools\build.ps1
```

或手动：

```powershell
tools\arduino-cli\arduino-cli.exe compile `
  --fqbn "esp32:esp32:esp32:PartitionScheme=huge_app" `
  --output-dir firmware\build `
  --config-file tools\arduino-cli.yaml `
  firmware\MiniPro_AutoSurvey
```

编译后把 `firmware\build\` 里的 3 个 `.bin` 复制到 `firmware\bin\`（`boot_app0.bin` 不用换）。
