# 家具对 Wi-Fi 信号的衰减预览

本原型基于团队的 [3D 布局编辑器](../../../3d-layout-editor)。团队共同采集和整理实验数据。zihexie95-dotcom 负责本目录的信号衰减预览和可选射线追踪。

运行 `python server.py --no-browser`，打开终端显示的本地地址。选择一件家具，填写材质、厚度、频率及收发高度。快速预览按射线与家具外接盒的交点估算单层板透射损耗。模型参数和公式见 [ITU‑R P.2040](https://www.itu.int/rec/R-REC-P.2040/en) 与 [Sionna 电磁传播说明](https://nvlabs.github.io/sionna/v2.1.0/rt/em_primer.html#single-layer-slab)。

可选精算使用 `python -m pip install -r requirements-rt.txt` 安装 Sionna RT。精算比较矩形房间中放置一块等效板前后的接收功率，结果以相对变化表示。当前两种预览均为近似模型，现场测量校准和误差评估仍待完成。

车载 Wi-Fi 诊断位于团队固件的 `WifiSurvey.cpp`。串口 115200 波特会记录接入、获取 IP 和断开原因。诊断代码已在 v10 构建并烧录验证。一次 32 秒配网诊断记录到 9 次原因码 15 和 0 次成功获取 IP。自动行驶尚未通过实车验证。
