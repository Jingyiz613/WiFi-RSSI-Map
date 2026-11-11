# miniAuto 3D 布局编辑器

在浏览器里用 3D 沙盘摆放家具、调整路由器位置，为 miniAuto Wi-Fi 采集系统准备房间布局。
纯本地运行，断网可用，不依赖任何云服务。

![界面截图](docs/screenshot.png)

## 功能

- 3D 房间、墙体、地面网格，家具按类型参数化建模
- 拖动家具移动，默认 5 cm 吸附，按住 `Alt` 自由微调
- 右侧家具库，点一下自动找空位添加家具
- 选中家具后删除，支持撤销
- 拖动路由器按格子吸附，位置可保存
- 家具占格按 10 cm 安全间距计算，用于布局校验
- 布局校验：家具重叠、路由器被挡
- 信号热力图：拖动家具或路由器实时重算覆盖，悬停查看预测 dBm
- 频段切换：2.4 GHz / 5 GHz，穿透衰减按材料表与频段取值
- 房间尺寸可调：侧栏输入宽 × 深（2–30 m），家具按比例重新分布并贴边
- 单件家具材质可选：点中家具后，家具正上方浮出材质卡（跟随拖动与视角），也可用侧栏面板；改完立即换成材质色并更新 2.4G/5G 损耗（dB）
- 家具库顶部可预设「新家具材质」，添加时直接带上该材料
- 障碍物库：砖墙 120/240、木门、玻璃窗、混凝土柱、金属板
- 材料衰减表：侧栏可查典型材料厚度与 2.4G/5G 衰减
- AI 补全：从当前布局采样 20 个 RSSI 点，输出 64×76 五档覆盖图
- AI 补全支持空房间 / 家具两套权重；首次运行后跟随布局自动刷新，再点一次「关闭补全」恢复物理热力图
- 演示模式：隐藏编辑面板，适合投屏
- 保存为新的 JSON，不覆盖原布局
- 一键截图、四种视角、图层开关

## 快速开始

需要 Python 3.9+。编辑器和物理热力图只用标准库；AI 补全需要 NumPy：

```bash
python -m pip install numpy
```

不需要安装 PyTorch。启动脚本会自动寻找一个能 `import numpy` 的 Python。

Windows 下双击 `start.bat`，浏览器会自动打开空白房间（`layout.json`），家具由你自己摆；想直接看有家具的示例，在左上角「布局」里切到 `layout.demo.json`。

也可以命令行启动：

```bash
python server.py
```

然后打开 `http://127.0.0.1:8765/`。

## 目录

```
.
├── start.bat            双击启动
├── server.py            本地 HTTP 服务 + 分析/保存 API
├── core/                复用原系统的布局与路线模块
│   ├── layout.py
│   └── route_planner.py
├── model/               AI 补全权重与推理
│   ├── numpy_infer.py   NumPy 推理（默认，不依赖 PyTorch）
│   ├── pt_numpy.py      读取 .pt 权重
│   ├── empty_model.pt   空房间权重
│   ├── furniture_model.pt 家具权重
│   └── example_input.csv 示例 20 点
├── data/                布局 JSON
│   ├── layout.json      空白房间（从零开始摆）
│   ├── layout.demo.json 示例房间（14 件家具）
│   └── layout.example.json
├── web/
│   ├── index.html
│   ├── app.js
│   ├── scene.js
│   ├── style.css
│   └── vendor/          three.js r160（MIT，离线）
└── docs/
    └── screenshot.png
```

## 数据格式

布局 JSON 与原 miniAuto 采集系统兼容：

```json
{
  "version": 2,
  "room": { "rows": 7, "columns": 9, "width_m": 8.23, "height_m": 6.95 },
  "clearance_m": 0.1,
  "ap": "P48",
  "start": "P11",
  "furniture": [
    { "name": "沙发", "x1": 0.3, "y1": 2.6, "x2": 2.55, "y2": 3.55, "material": "metal" }
  ],
  "manual_blocked": []
}
```

家具使用米制轴对齐矩形，原点为房间左上角，与 2D 控制台一致。
`material` 可省略；省略时按家具名称自动推断材料。

## 技术说明

- 前端：原生 three.js（ES module），正交相机等轴测视角，无构建步骤
- 后端：Python 标准库 `http.server`，提供场景、分析、保存接口
- 格子判定与路线规划复用原系统的 `layout.py` / `route_planner.py`，保证 3D 与 2D 结果一致
- 信号热力图使用简化物理模型（自由空间损耗 + 材料穿透衰减），穿透值按团队资料表的典型障碍物衰减表、分 2.4G/5G 取值；用于展示布局与路由器位置对覆盖的相对影响，不是本房间的实测标定
- 家具按材料映射（木质/软包→合成材料、电视→有色玻璃、冰箱/洗衣机→金属薄壳折减）；新增的砖墙/木门/玻璃窗/混凝土/金属板按整块材料计算；手动选中材质时按材料表原值计算
- 穿透判定用家具的估算高度：低于 0.3 m 视为不遮挡，1 m 以上按完整衰减，中间线性折减
- 改房间尺寸时保持 7×9 格，格宽/格深随之变化，家具中心按比例重映射并限制在房间内
- AI 模型按 8.23 × 6.95 m 训练，改房间尺寸后 AI 补全结果仅作相对参考
- AI 补全使用团队训练的 64×76 五档分类模型，输入固定 20 个测点（`P{行}{列}`，行 1-7、列 1-9），输出五档覆盖分类
- AI 推理默认用 NumPy 复现前向，不依赖 PyTorch；`model/infer.py` 保留作 PyTorch 环境参考
- AI 补全固定使用当前布局在 20 个测点的 RSSI，并把当前频段传给物理仿真；首次点「运行模型」后，拖动家具/路由器、改材质、改频段或房间尺寸都会自动重跑
- 材质色用于区分材料：手动指定材质或障碍物按材料表配色，普通家具在自动模式下保留原本外观；家具标签同时显示该材料在当前高度下的 2.4G/5G 有效损耗（dB）
- 示例 CSV 是模拟输入，不能用于宣称实测精度；模型训练数据与验证结果见 `model/` 内的配置和日志
- three.js 已本地化到 `web/vendor/`，不依赖 CDN

## 授权说明

`core/layout.py`、`core/route_planner.py` 来自 miniAuto Wi-Fi 采集系统，公开前请确认你有权发布这部分代码。
three.js 使用 MIT 许可证，见 `web/vendor/LICENSE-three.txt`。

## 致谢

- [three.js](https://threejs.org/) — MIT License
- 原始 miniAuto Wi-Fi 采集系统
