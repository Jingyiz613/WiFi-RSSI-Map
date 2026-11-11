# Wi-Fi 热力图 · 一键采集网站（人工采集阶段）

> **本分支（`liubokang/web-collection-site`）贡献说明**
>
> 本分支由 **刘博康** 负责开发，内容是项目**最初的人工采集数据阶段**使用的**一键化采集网站**：
> 在房间里按 62 点网格人工走位，由网站统一驱动 ESP32-C6 采集板完成全部采集、
> 整理、建模、出图与打包导出，**全流程零命令行、网页操作**。
> 真实采集数据成果见 [docs/数据成果/](../docs/数据成果/)；后续自动化方向见
> [docs/电动小车自动采集执行方案.pdf](../docs/电动小车自动采集执行方案.pdf)。

基于 **Wi-Fi CSI（信道状态信息）** 的室内信号感知系统：在房间里按点位走一遍采集流程，
系统自动训练模型并生成**信号强度热力图**。面向竞赛评审与教学演示设计，**零命令行、全流程网页操作**。

> 第三届"兴智杯"参赛作品 · 配套 [ESPectre](https://github.com/francescopace/espectre) 固件/采集器使用

## 功能一览

| 模块 | 说明 |
|---|---|
| 🧭 四步引导流程 | 房型配置 → 数据采集 → 数据整理 → 建模出图，页面上一步一个按钮 |
| 🎭 演示 / 真实双模式 | 没有硬件也能完整演示（模拟数据）；接上 ESP32-C6 板子即切换真实采集 |
| 🔌 常驻采集守护进程 | `web/csi_daemon.py`：与采集板保持长连接，按点下发采集指令，省去每点重连（62 点省约半小时）；断链自动重启重试 |
| 🔥 一键烧录 & 配网 | 浏览器内直接烧录固件（WebSerial + esptool-js），Improv Serial 协议配 Wi-Fi |
| 📍 点位自动生成 | 输入房间尺寸自动布设测点（网格蛇形走位），支持自定义房型与任意点位顺序 |
| 💾 实时保存 | 每采一个点立刻落盘；崩溃重启续采；**随时下载** zip（原始数据 + 进度清单） |
| 📊 建模评估 | 支持 KNN / 随机森林 / SVR 三种模型；≥10 点自动 80/20 留出评估（seed 固定可复现） |
| 🗺️ 成果输出 | 预测热力图、实测 vs 预测对比图、模型评估报告、测点汇总表 |

## 采集规格（真实模式）

- 每点流程：稳定 **3 秒** + 采集 **10 秒 @ 100 pps** ≈ 1000 包 CSI（64/128 子载波）+ RSSI
- 采集节奏与数据处理管线与团队工具链对齐（team-wifi-rss-toolkit 同规格）
- 每轮结束一键导出：`原始数据/`（每点 npz）+ `整理数据/` + `报告/` + `结果图/` + `进度清单.json`

## 真实数据成果（人工采集，已剔除非本轮文件）

| 轮次 | AP 位置 | 成果 |
|---|---|---|
| 第 1 轮（18:39–19:51） | P49 (7.77, 3.48) | [预测热力图](../docs/数据成果/round1_ap_p49/热力图_预测.png) · [测点汇总表](../docs/数据成果/round1_ap_p49/测点汇总表.csv) · [模型评估报告](../docs/数据成果/round1_ap_p49/模型评估报告.md) |
| 第 2 轮（19:52–20:21） | P26 (5.03, 1.49) | [预测热力图](../docs/数据成果/round2_ap_p26/热力图_预测.png) · [测点汇总表](../docs/数据成果/round2_ap_p26/测点汇总表.csv) · [模型评估报告](../docs/数据成果/round2_ap_p26/模型评估报告.md) |

两轮各 62 点、路损拟合指数 n≈1.5 / 1.85，物理规律自洽。完整 npz 原始包见项目数据存档（Release/网盘，npz 体积大不入库）。

## 快速开始

```bash
# 1) 安装 Python 依赖（建议虚拟环境）
pip install -r requirements.txt

# 2) 克隆 ESPectre 采集器到 tools/espectre（含 micro-espectre 工具链与固件）
git clone https://github.com/francescopace/espectre.git tools/espectre

# 3) 启动网站
python web/app.py
# 打开 http://localhost:8000
```

浏览器烧录功能需要的固件二进制（`web/static/firmware/*.bin`）体积较大未入库，
从 `tools/espectre` 构建产物或 [ESPectre Releases](https://github.com/francescopace/espectre/releases) 拷贝即可。

## 目录说明

```
web/                    网站本体（Flask）
├── app.py              主应用：页面、采集编排、整理/出图调度、打包导出
├── csi_daemon.py       常驻采集守护进程（stdin/stdout JSON 行协议，断链自愈）
├── layout.py           房型/点位几何计算
├── templates/          单页前端模板
└── static/             前端 JS/CSS + esptool-js（浏览器内烧录固件）
scripts/                采集与处理管线
├── 02_collect_point.sh / 03_collect_round.sh   单点/整轮采集（CLI 方式）
├── 04_build_dataset.py 原始 npz → 文件级特征表 + 测点汇总表
├── 05_train_and_plot.py 训练评估 + 热力图 + 报告
├── layout.json / sites.csv                     房型与点位配置
└── 06_build_manual_pdf.py 操作手册 PDF 生成
docs/                   电动小车自动采集方案（后续方向）+ 真实数据成果
```

## 后续工作

人工走位太慢（两轮约 1.7 小时）。正在推进 **电动小车自动采集**：
差速底盘 + 陀螺仪/超声波定位纠偏，小车驮着 ESP32-C6 按网格自动走位并自动触发采集，
一轮目标 ≤25 分钟。方案与采购清单见 [docs/](../docs/)，完成后将另开分支提交。
