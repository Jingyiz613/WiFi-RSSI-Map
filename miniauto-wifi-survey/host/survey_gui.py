"""交互式采集控制台。

四个核心能力：
1. 在网格上手动点选路由器所在格子（也可直接输入 P22 / x,y 米）。
2. 按住左键拖动框选家具，尺寸不必对齐格子；自动换算成米制存进 layout.json。
3. 自动规划避障蛇形路线并预览。
4. 一键执行自动行驶 + 逐点 RSSI 采集（在线 / 离线两种模式）。

运行：python survey_gui.py
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from layout import DEFAULT_CLEARANCE_M, Furniture, Layout
from robot_client import Robot, RobotError, read_key_over_usb
from route_planner import Cell, GridSpec, PlanError, make_serpentine_plan
from route_plot import draw_plan
from survey_runner import SurveyOptions, run_survey

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
PREVIEW_PATH = PROJECT_ROOT / "route_preview.png"
DEFAULT_LAYOUT_PATH = PROJECT_ROOT / "layout.json"

AP_COLOR = "#ef5350"
BLOCK_COLOR = "#9e9e9e"
START_COLOR = "#66bb6a"
NORMAL_COLOR = "#eef6fc"
FURNITURE_TINT = "#d8c7b5"
TEXT_COLOR = "#37474f"
SNAP_M = 0.01

FURNITURE_COLORS = (
    "#8d6e63",
    "#5c6bc0",
    "#26a69a",
    "#ef5350",
    "#ab47bc",
    "#ffa726",
    "#7e57c2",
    "#42a5f5",
)


class SurveyApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MiniAuto Wi-Fi 采集控制台")
        self.geometry("1180x820")
        self.minsize(1060, 740)

        self.layout_path = DEFAULT_LAYOUT_PATH
        self.layout = Layout.load_or_default(self.layout_path)

        self.edit_mode = tk.StringVar(value="ap")
        self.anchor_cell: Cell | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._draft: tuple[float, float, float, float] | None = None

        self._log_queue: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()

        self.edit_mode.trace_add("write", lambda *_: self._reset_anchor())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_layout()
        self.after(60, self._draw_grid)
        self._pump_log()
        self._log(f"已载入布局：{self.layout_path if self.layout_path.exists() else '（新建）'}")
        self._log("家具模式下按住左键拖动即可框选，尺寸不必对齐格子。")

    # ------------------------------------------------------------ 布局代理
    @property
    def spec(self) -> GridSpec:
        return self.layout.spec

    @property
    def ap(self) -> Cell:
        return self.layout.ap

    @ap.setter
    def ap(self, value: Cell) -> None:
        self.layout.ap = value

    @property
    def start(self) -> Cell:
        return self.layout.start

    @start.setter
    def start(self, value: Cell) -> None:
        self.layout.start = value

    @property
    def blocked(self) -> set[Cell]:
        return self.layout.blocked()

    @property
    def furniture(self) -> list[Furniture]:
        return self.layout.furniture

    @property
    def clearance_m(self) -> float:
        return self.layout.clearance_m

    # ------------------------------------------------------------------ 布局
    def _build_layout(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        left = ttk.Frame(outer)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(outer, width=380)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        # ---- 网格画布 ----
        grid_box = ttk.LabelFrame(left, text="房间网格（拖动框选家具，点击设置路由器/起点/障碍）", padding=8)
        grid_box.pack(fill="both", expand=True)

        mode_row = ttk.Frame(grid_box)
        mode_row.pack(fill="x", pady=(0, 6))
        ttk.Label(mode_row, text="模式：").pack(side="left")
        for text, value in (
            ("路由器", "ap"),
            ("家具（拖动）", "furniture"),
            ("单格障碍", "blocked"),
            ("起点", "start"),
        ):
            ttk.Radiobutton(mode_row, text=text, value=value, variable=self.edit_mode).pack(side="left", padx=3)
        ttk.Label(mode_row, text="  拖动精度 1 cm", foreground="#78909c").pack(side="right")

        self.canvas = tk.Canvas(grid_box, bg="white", highlightthickness=1, highlightbackground="#cfd8dc")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Configure>", lambda _e: self._draw_grid())

        # ---- 家具列表 ----
        furn_box = ttk.LabelFrame(left, text="家具清单（双击重命名）", padding=8)
        furn_box.pack(fill="x", pady=(8, 0))

        list_row = ttk.Frame(furn_box)
        list_row.pack(fill="both", expand=True)
        self.furn_list = tk.Listbox(list_row, height=5, font=("Microsoft YaHei", 9), activestyle="dotbox")
        furn_scroll = ttk.Scrollbar(list_row, command=self.furn_list.yview)
        self.furn_list.configure(yscrollcommand=furn_scroll.set)
        self.furn_list.pack(side="left", fill="both", expand=True)
        furn_scroll.pack(side="right", fill="y")
        self.furn_list.bind("<Double-Button-1>", lambda _e: self._rename_furniture())

        btn_row = ttk.Frame(furn_box)
        btn_row.pack(fill="x", pady=(6, 0))
        ttk.Button(btn_row, text="删除选中", command=self._delete_furniture).pack(side="left")
        ttk.Button(btn_row, text="重命名", command=self._rename_furniture).pack(side="left", padx=4)
        ttk.Button(btn_row, text="清空家具", command=self._clear_furniture).pack(side="left")
        ttk.Button(btn_row, text="清空障碍", command=self._clear_blocked).pack(side="left", padx=4)
        ttk.Label(btn_row, text="家具格不采样", foreground="#78909c").pack(side="right")

        # ---- 房间参数 ----
        param_box = ttk.LabelFrame(right, text="房间与安全间距", padding=8)
        param_box.pack(fill="x")
        self.var_rows = tk.StringVar(value=str(self.spec.rows))
        self.var_cols = tk.StringVar(value=str(self.spec.columns))
        self.var_width = tk.StringVar(value=f"{self.spec.width_m:g}")
        self.var_height = tk.StringVar(value=f"{self.spec.height_m:g}")
        self.var_clearance = tk.StringVar(value=f"{self.clearance_m * 100:g}")

        for i, (label, var) in enumerate(
            (("行数", self.var_rows), ("列数", self.var_cols), ("宽 m", self.var_width), ("高 m", self.var_height))
        ):
            ttk.Label(param_box, text=label).grid(row=i // 2, column=(i % 2) * 2, sticky="w", pady=2)
            ttk.Entry(param_box, textvariable=var, width=9).grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=4)

        ttk.Label(param_box, text="安全间距 cm").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(param_box, textvariable=self.var_clearance, width=9).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Button(param_box, text="应用", command=self._apply_grid).grid(row=2, column=2, sticky="ew", padx=2)
        ttk.Button(param_box, text="保存布局", command=self._save_layout_dialog).grid(row=2, column=3, sticky="ew", padx=2)
        ttk.Label(
            param_box,
            text="安全间距：格子中心离家具多近就判为不可用",
            foreground="#78909c",
            wraplength=340,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # ---- 选点信息 ----
        info_box = ttk.LabelFrame(right, text="当前选择", padding=8)
        info_box.pack(fill="x", pady=(8, 0))
        self.lbl_ap = ttk.Label(info_box, text="", font=("Microsoft YaHei", 10, "bold"))
        self.lbl_ap.pack(anchor="w")
        self.lbl_summary = ttk.Label(info_box, text="", foreground="#546e7a", wraplength=350, justify="left")
        self.lbl_summary.pack(anchor="w", pady=(2, 0))
        self.lbl_warn = ttk.Label(info_box, text="", foreground="#c62828", wraplength=350, justify="left")
        self.lbl_warn.pack(anchor="w")

        quick = ttk.Frame(info_box)
        quick.pack(fill="x", pady=(6, 0))
        ttk.Label(quick, text="直接输入：").pack(side="left")
        self.var_ap_text = tk.StringVar(value="P22")
        ttk.Entry(quick, textvariable=self.var_ap_text, width=8).pack(side="left", padx=4)
        ttk.Button(quick, text="设为路由器", command=self._ap_from_text).pack(side="left")

        # ---- 采集参数 ----
        run_box = ttk.LabelFrame(right, text="采集与通信参数", padding=8)
        run_box.pack(fill="x", pady=(8, 0))
        self.var_samples = tk.StringVar(value="10")
        self.var_settle = tk.StringVar(value="3")
        self.var_speed = tk.StringVar(value="0.08")
        self.var_xscale = tk.StringVar(value="1.0")
        self.var_yscale = tk.StringVar(value="1.0")
        self.var_port = tk.StringVar(value="COM8")
        self.var_target = tk.StringVar(value="192.168.31.12:62587")
        self.var_ssid = tk.StringVar(value="")

        fields = [
            ("每点采样次数", self.var_samples, 0, 0),
            ("静置秒数", self.var_settle, 0, 2),
            ("速度 m/s", self.var_speed, 1, 0),
            ("X 校准", self.var_xscale, 1, 2),
            ("Y 校准", self.var_yscale, 2, 0),
            ("串口", self.var_port, 3, 0),
            ("小车地址", self.var_target, 4, 0),
            ("锁定 SSID", self.var_ssid, 5, 0),
        ]
        for label, var, row, col in fields:
            ttk.Label(run_box, text=label).grid(row=row, column=col, sticky="w", pady=2)
            ttk.Entry(run_box, textvariable=var, width=15).grid(row=row, column=col + 1, sticky="w", padx=4, pady=2)
        ttk.Button(run_box, text="扫描串口", command=self._scan_ports).grid(
            row=3, column=2, columnspan=2, sticky="ew", padx=4
        )

        # ---- 动作按钮 ----
        act = ttk.Frame(right)
        act.pack(fill="x", pady=(10, 0))
        ttk.Button(act, text="① 生成路线预览", command=self._preview).pack(fill="x", pady=2)
        self.btn_flash = ttk.Button(act, text="② 烧录固件（见 tools/flash.ps1）", command=self._show_flash_hint)
        self.btn_flash.pack(fill="x", pady=2)
        self.btn_start = ttk.Button(act, text="③ 开始自动采集", command=self._start)
        self.btn_start.pack(fill="x", pady=2)
        self.btn_stop = ttk.Button(act, text="中止任务", command=self._abort, state="disabled")
        self.btn_stop.pack(fill="x", pady=2)

        ttk.Separator(act, orient="horizontal").pack(fill="x", pady=6)
        ttk.Label(act, text="离线模式（不连被测 WiFi）", foreground="#607d8b").pack(anchor="w")
        ttk.Button(act, text="④ 离线下发并开始（随后可拔线）", command=self._offline_run).pack(fill="x", pady=2)
        ttk.Button(act, text="⑤ 读取离线结果", command=self._offline_dump).pack(fill="x", pady=2)

        # ---- 日志 ----
        log_box = ttk.LabelFrame(right, text="运行日志", padding=6)
        log_box.pack(fill="both", expand=True, pady=(10, 0))
        self.log = tk.Text(log_box, height=9, wrap="word", font=("Consolas", 9), state="disabled")
        scroll = ttk.Scrollbar(log_box, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    # ------------------------------------------------------------------ 画布
    def _geom(self) -> tuple[float, float, float] | None:
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        pad = 16
        avail_w, avail_h = width - 2 * pad, height - 2 * pad - 16
        if avail_w < 60 or avail_h < 60:
            return None
        scale = min(avail_w / self.spec.width_m, avail_h / self.spec.height_m)
        room_w = self.spec.width_m * scale
        room_h = self.spec.height_m * scale
        return pad + (avail_w - room_w) / 2, pad + (avail_h - room_h) / 2, scale

    def _to_px(self, x_m: float, y_m: float) -> tuple[float, float] | None:
        geom = self._geom()
        if geom is None:
            return None
        x0, y0, scale = geom
        return x0 + x_m * scale, y0 + y_m * scale

    def _to_m(self, px: float, py: float) -> tuple[float, float] | None:
        geom = self._geom()
        if geom is None:
            return None
        x0, y0, scale = geom
        return (px - x0) / scale, (py - y0) / scale

    def _cell_at(self, px: float, py: float) -> Cell | None:
        point = self._to_m(px, py)
        if point is None:
            return None
        x_m, y_m = point
        if not (0 <= x_m <= self.spec.width_m and 0 <= y_m <= self.spec.height_m):
            return None
        return self.spec.nearest_cell(x_m, y_m)

    def _draw_grid(self) -> None:
        self.canvas.delete("all")
        geom = self._geom()
        if geom is None:
            return
        x0, y0, scale = geom
        cw, ch = self.spec.cell_width_m, self.spec.cell_height_m
        furniture_cells = self.layout.furniture_cells()
        manual = self.layout.manual_blocked

        self.canvas.create_rectangle(
            x0,
            y0,
            x0 + self.spec.width_m * scale,
            y0 + self.spec.height_m * scale,
            fill="#fbfdff",
            outline="#90a4ae",
        )

        for row in range(1, self.spec.rows + 1):
            for col in range(1, self.spec.columns + 1):
                cell = (row, col)
                cx1 = x0 + (col - 1) * cw * scale
                cy1 = y0 + (row - 1) * ch * scale
                cx2 = cx1 + cw * scale
                cy2 = cy1 + ch * scale
                if cell == self.ap:
                    fill, fg = AP_COLOR, "white"
                elif cell in furniture_cells:
                    fill, fg = FURNITURE_TINT, TEXT_COLOR
                elif cell in manual:
                    fill, fg = BLOCK_COLOR, "white"
                elif cell == self.start:
                    fill, fg = START_COLOR, "white"
                else:
                    fill, fg = NORMAL_COLOR, TEXT_COLOR
                self.canvas.create_rectangle(cx1, cy1, cx2, cy2, fill=fill, outline="#b0bec5")
                self.canvas.create_text(
                    (cx1 + cx2) / 2,
                    (cy1 + cy2) / 2,
                    text=f"P{row}{col}",
                    fill=fg,
                    font=("Consolas", 8),
                )

        # 家具：米制矩形直接映射到画布，精确到厘米
        for index, item in enumerate(self.furniture):
            p1 = self._to_px(item.x1, item.y1)
            p2 = self._to_px(item.x2, item.y2)
            if p1 is None or p2 is None:
                continue
            color = FURNITURE_COLORS[index % len(FURNITURE_COLORS)]
            self.canvas.create_rectangle(
                p1[0],
                p1[1],
                p2[0],
                p2[1],
                fill=color,
                stipple="gray50",
                outline="#3e2723",
                width=2,
            )
            if (p2[0] - p1[0]) > 40 and (p2[1] - p1[1]) > 18:
                self.canvas.create_text(
                    (p1[0] + p2[0]) / 2,
                    (p1[1] + p2[1]) / 2,
                    text=item.name,
                    fill="#212121",
                    font=("Microsoft YaHei", 9, "bold"),
                )

        # 拖动中的临时框
        if self._draft is not None:
            dx1, dy1, dx2, dy2 = self._draft
            self.canvas.create_rectangle(
                dx1, dy1, dx2, dy2, outline="#d32f2f", width=2, dash=(5, 3)
            )

        self.canvas.create_text(
            x0,
            y0 + self.spec.height_m * scale + 10,
            anchor="w",
            text=(
                f"{self.spec.width_m:.2f} m × {self.spec.height_m:.2f} m    "
                f"格距 {cw:.3f} × {ch:.3f} m    安全间距 {self.clearance_m * 100:.0f} cm"
            ),
            fill="#78909c",
            font=("Microsoft YaHei", 8),
        )

        self._refresh_furniture_list()
        self._refresh_status()

    def _refresh_furniture_list(self) -> None:
        self.furn_list.delete(0, "end")
        for item in self.furniture:
            self.furn_list.insert("end", item.describe(self.spec, self.clearance_m))

    def _refresh_status(self) -> None:
        self.lbl_ap.configure(
            text=f"路由器：{self.spec.label(self.ap)}  "
            f"（x={self.spec.center(self.ap)[0]:.2f} m, y={self.spec.center(self.ap)[1]:.2f} m）"
        )
        stats = self.layout.stats()
        try:
            stops = len(self._current_plan())
        except PlanError:
            stops = 0
        self.lbl_summary.configure(
            text=f"测量点 {stops} 个 / 可走 {stats['usable']} 格（家具挡 {stats['furniture_cells']} 格 + "
            f"障碍 {stats['manual_cells']} 格），格距 "
            f"{self.spec.cell_width_m:.3f} × {self.spec.cell_height_m:.3f} m"
        )
        problems = self.layout.validate()
        self.lbl_warn.configure(text="⚠ " + problems[0] if problems else "")
        self.var_ap_text.set(self.spec.label(self.ap))

    # ------------------------------------------------------------------ 交互
    def _on_press(self, event: tk.Event) -> None:
        if self.edit_mode.get() == "furniture":
            self._drag_origin = (event.x, event.y)
            self._draft = None

    def _on_drag(self, event: tk.Event) -> None:
        if self.edit_mode.get() != "furniture" or self._drag_origin is None:
            return
        ox, oy = self._drag_origin
        if abs(event.x - ox) < 3 and abs(event.y - oy) < 3:
            return
        self._draft = (ox, oy, event.x, event.y)
        self._draw_grid()

    def _on_release(self, event: tk.Event) -> None:
        if self.edit_mode.get() == "furniture":
            origin = self._drag_origin
            self._drag_origin = None
            draft = self._draft
            self._draft = None
            if origin and draft and (abs(event.x - origin[0]) > 6 or abs(event.y - origin[1]) > 6):
                self._finish_furniture(origin, (event.x, event.y))
            else:
                self._draw_grid()
                self._log("家具模式：按住左键拖动框选，松手后命名。")
            return

        cell = self._cell_at(event.x, event.y)
        if cell is None:
            return
        self._on_cell(*cell)

    def _snap(self, x_m: float, y_m: float) -> tuple[float, float]:
        width, height = self.spec.width_m, self.spec.height_m
        x = round(max(0.0, min(width, x_m)) / SNAP_M) * SNAP_M
        y = round(max(0.0, min(height, y_m)) / SNAP_M) * SNAP_M
        return round(min(width, max(0.0, x)), 3), round(min(height, max(0.0, y)), 3)

    def _finish_furniture(self, p1: tuple[int, int], p2: tuple[int, int]) -> None:
        m1 = self._to_m(*p1)
        m2 = self._to_m(*p2)
        if m1 is None or m2 is None:
            self._draw_grid()
            return
        x1, y1 = self._snap(*m1)
        x2, y2 = self._snap(*m2)
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if x2 - x1 < 0.05 or y2 - y1 < 0.05:
            self._log(f"框选只有 {max(x2 - x1, y2 - y1) * 100:.0f} cm，太小已忽略（至少 5 cm）")
            self._draw_grid()
            return

        candidate = Furniture("家具", x1, y1, x2, y2)
        for cell in (self.ap, self.start):
            cx, cy = self.spec.center(cell)
            if candidate.distance_to(cx, cy) <= self.clearance_m:
                messagebox.showwarning(
                    "冲突",
                    f"这块家具会挡住{('路由器' if cell == self.ap else '起点')} "
                    f"{self.spec.label(cell)}（安全间距 {self.clearance_m * 100:.0f} cm）",
                )
                self._draw_grid()
                return

        suggested = f"家具{len(self.furniture) + 1}"
        name = simpledialog.askstring(
            "家具名称",
            f"尺寸 {candidate.width_m * 100:.0f} × {candidate.height_m * 100:.0f} cm\n给它起个名字：",
            initialvalue=suggested,
            parent=self,
        )
        if not name:
            self._draw_grid()
            return
        try:
            item = self.layout.add_furniture(Furniture(name, x1, y1, x2, y2))
        except PlanError as exc:
            messagebox.showerror("无法添加", str(exc))
            self._draw_grid()
            return
        self._log(f"已添加家具：{item.describe(self.spec, self.clearance_m)}")
        self._draw_grid()
        self._autosave()

    def _on_cell(self, r: int, c: int) -> None:
        cell = (r, c)
        mode = self.edit_mode.get()
        if mode == "ap":
            if cell in self.blocked:
                owner = self.layout.furniture_at(cell)
                messagebox.showwarning(
                    "提示",
                    f"{self.spec.label(cell)} 在{('家具「' + owner.name + '」') if owner else '障碍格'}里，不能设为路由器",
                )
                return
            self.ap = cell
        elif mode == "blocked":
            if cell == self.ap:
                messagebox.showwarning("提示", "路由器所在格不能同时作为障碍格")
                return
            if self.layout.furniture_at(cell):
                messagebox.showwarning("提示", "该格已被家具占用，删改家具请用下方清单")
                return
            if cell in self.layout.manual_blocked:
                self.layout.manual_blocked.discard(cell)
            else:
                self.layout.manual_blocked.add(cell)
        else:
            if cell in self.blocked:
                messagebox.showwarning("提示", "起点不能是家具或障碍格")
                return
            self.start = cell
        self._draw_grid()
        self._autosave()

    def _reset_anchor(self) -> None:
        self.anchor_cell = None
        self._drag_origin = None
        self._draft = None
        self._draw_grid()

    def _selected_furniture(self) -> int | None:
        selection = self.furn_list.curselection()
        return int(selection[0]) if selection else None

    def _delete_furniture(self) -> None:
        index = self._selected_furniture()
        if index is None:
            messagebox.showinfo("提示", "先在下面的清单里选中一件家具")
            return
        item = self.furniture[index]
        if not messagebox.askyesno("删除家具", f"删除「{item.name}」？"):
            return
        self.layout.remove_furniture(index)
        self._draw_grid()
        self._autosave()

    def _rename_furniture(self) -> None:
        index = self._selected_furniture()
        if index is None:
            return
        item = self.furniture[index]
        name = simpledialog.askstring("重命名", "新的名字：", initialvalue=item.name, parent=self)
        if not name:
            return
        self.layout.rename_furniture(index, name)
        self._draw_grid()
        self._autosave()

    def _clear_furniture(self) -> None:
        if not self.furniture:
            return
        if not messagebox.askyesno("清空家具", f"删除全部 {len(self.furniture)} 件家具？"):
            return
        self.layout.clear_furniture()
        self._draw_grid()
        self._autosave()

    def _clear_blocked(self) -> None:
        self.layout.manual_blocked.clear()
        self._draw_grid()
        self._autosave()

    def _ap_from_text(self) -> None:
        text = self.var_ap_text.get().strip()
        try:
            if "," in text:
                x_str, y_str = text.split(",", 1)
                cell = self.spec.nearest_cell(float(x_str), float(y_str))
            else:
                cell = self.spec.parse(text)
            if cell in self.blocked:
                owner = self.layout.furniture_at(cell)
                raise PlanError(f"该格在{('家具「' + owner.name + '」') if owner else '障碍格'}里")
            self.ap = cell
            self._draw_grid()
            self._autosave()
        except (PlanError, ValueError) as exc:
            messagebox.showerror("输入错误", str(exc))

    def _apply_grid(self) -> None:
        try:
            spec = GridSpec(
                rows=int(self.var_rows.get()),
                columns=int(self.var_cols.get()),
                width_m=float(self.var_width.get()),
                height_m=float(self.var_height.get()),
            )
            clearance_cm = float(self.var_clearance.get())
        except (ValueError, PlanError) as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        if not 0 <= clearance_cm <= 50:
            messagebox.showerror("参数错误", "安全间距应在 0..50 cm")
            return

        kept: list[Furniture] = []
        for item in self.furniture:
            clipped = item.clipped(spec)
            if clipped is not None:
                kept.append(clipped)
        dropped = len(self.furniture) - len(kept)

        self.layout.spec = spec
        self.layout.furniture = kept
        self.layout.clearance_m = clearance_cm / 100.0
        self.layout.manual_blocked = {
            b for b in self.layout.manual_blocked if 1 <= b[0] <= spec.rows and 1 <= b[1] <= spec.columns
        }
        if not (1 <= self.layout.ap[0] <= spec.rows and 1 <= self.layout.ap[1] <= spec.columns):
            self.layout.ap = (min(2, spec.rows), min(2, spec.columns))
        if not (1 <= self.layout.start[0] <= spec.rows and 1 <= self.layout.start[1] <= spec.columns):
            self.layout.start = (1, 1)
        if self.layout.ap in self.layout.blocked():
            for cell in spec.all_cells():
                if cell not in self.layout.blocked():
                    self.layout.ap = cell
                    break

        self._draw_grid()
        if dropped:
            self._log(f"缩小房间时移除了 {dropped} 件完全越界的家具")
        self._autosave()

    # ------------------------------------------------------------------ 存取
    def _autosave(self) -> None:
        try:
            self.layout.save(self.layout_path)
        except OSError as exc:
            self._log(f"[警告] 布局保存失败：{exc}")

    def _save_layout_dialog(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存布局",
            defaultextension=".json",
            initialfile=self.layout_path.name,
            initialdir=str(PROJECT_ROOT),
            filetypes=[("布局文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            self.layout.save(path)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.layout_path = Path(path)
        self._log(f"布局已保存：{path}")

    def _on_close(self) -> None:
        self._autosave()
        self.destroy()

    # ------------------------------------------------------------------ 动作
    def _log(self, text: str) -> None:
        self._log_queue.put(text)

    def _pump_log(self) -> None:
        while True:
            try:
                text = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(120, self._pump_log)

    def _current_plan(self):
        return make_serpentine_plan(self.spec, self.ap, sorted(self.blocked), self.start)

    def _preview(self) -> None:
        try:
            plan = self._current_plan()
            path = draw_plan(
                self.spec,
                plan,
                self.ap,
                sorted(self.blocked),
                self.start,
                PREVIEW_PATH,
                furniture=self.furniture,
                clearance_m=self.clearance_m,
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("预览失败", str(exc))
            return
        self._log(f"路线预览已生成：{path}")
        try:
            import os

            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            pass

    # ------------------------------------------------------------ 离线模式
    def _offline_cmd(self, action: str) -> tuple[bool, str]:
        script = APP_DIR / "survey_offline.py"
        self._autosave()
        cmd = [
            sys.executable,
            str(script),
            action,
            "--layout", str(self.layout_path),
            "--port", self.var_port.get().strip(),
            "--speed", self.var_speed.get().strip(),
            "--dwell", self.var_settle.get().strip(),
            "--samples", self.var_samples.get().strip(),
            "--x-scale", self.var_xscale.get().strip(),
            "--y-scale", self.var_yscale.get().strip(),
        ]
        if action == "run":
            cmd.append("--detach")
        flags = 0x08000000 if sys.platform == "win32" else 0
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except OSError as exc:
            return False, f"无法启动离线采集脚本：{exc}"
        output = (proc.stdout or "") + (proc.stderr or "")
        self._log(output.strip())
        return proc.returncode == 0, output

    def _offline_run(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            messagebox.showwarning("正在运行", "在线采集还在跑，先等它结束或点「中止任务」。")
            return
        ok, output = self._offline_cmd("run")
        if not ok:
            messagebox.showerror("离线启动失败", output.strip() or "请查看运行日志")
            return
        messagebox.showinfo(
            "已开始",
            "计划和启动指令已下发。\n\n"
            "现在可以拔掉 USB 线（小车靠电池供电）。\n"
            "跑完后重新插上 USB，点「⑤ 读取离线结果」。",
        )

    def _offline_dump(self) -> None:
        ok, output = self._offline_cmd("dump")
        if not ok:
            messagebox.showerror("读取失败", output.strip() or "请查看运行日志")
            return
        messagebox.showinfo("已读取", "结果已导出到 output 目录，详见运行日志。")

    def _show_flash_hint(self) -> None:
        messagebox.showinfo(
            "烧录固件",
            "在项目根目录打开 PowerShell 执行：\n\n"
            "  powershell -ExecutionPolicy Bypass -File tools\\flash.ps1\n\n"
            "脚本会自动识别串口、校验芯片、备份现有 Flash 并写入 AutoSurvey 固件。",
        )

    def _scan_ports(self) -> None:
        try:
            from serial.tools import list_ports
        except ImportError:
            messagebox.showerror("缺少依赖", "未安装 pyserial")
            return
        ports = list(list_ports.comports())
        if not ports:
            messagebox.showinfo("扫描结果", "没有发现串口设备")
            return
        text = "\n".join(f"{p.device}  {p.description}" for p in ports)
        self._log("检测到串口：" + text.replace("\n", "; "))
        if len(ports) == 1:
            self.var_port.set(ports[0].device)
        messagebox.showinfo("扫描结果", text)

    def _read_options(self) -> SurveyOptions:
        options = SurveyOptions(
            samples_per_point=int(self.var_samples.get()),
            settle_seconds=float(self.var_settle.get()),
            interval_seconds=1.0,
            speed_mps=float(self.var_speed.get()),
            x_scale=float(self.var_xscale.get()),
            y_scale=float(self.var_yscale.get()),
            expected_ssid=self.var_ssid.get().strip() or None,
        )
        options.validate()
        return options

    def _start(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("提示", "任务正在运行")
            return
        try:
            plan = self._current_plan()
            options = self._read_options()
        except (PlanError, ValueError) as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        problems = self.layout.validate()
        if problems:
            messagebox.showerror("布局有问题", "\n".join(problems))
            return

        confirmed = messagebox.askyesno(
            "开始前确认",
            f"路由器：{self.spec.label(self.ap)}\n"
            f"测量点：{len(plan)} 个\n"
            f"起点：{self.spec.label(self.start)}（车头朝下一列方向）\n"
            f"家具：{len(self.furniture)} 件，挡住 {self.layout.stats()['furniture_cells']} 格\n\n"
            "请清空行驶路线上的杂物，并确保小车电池充足。\n"
            "确认开始自动行驶？",
        )
        if not confirmed:
            return

        self._stop_event.clear()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        job = {
            "port": self.var_port.get().strip(),
            "target": self.var_target.get().strip(),
        }
        self._worker = threading.Thread(target=self._run_job, args=(plan, options, job), daemon=True)
        self._worker.start()

    def _run_job(self, plan, options: SurveyOptions, job: dict) -> None:
        try:
            port = job["port"]
            target = job["target"]
            self._log(f"通过 {port} 读取本次上电的控制密钥…")
            key = read_key_over_usb(port)
            self._log("已取得控制密钥，USB 可以拔掉（保持小车供电、不要重启）")
            robot = Robot(target, key)
            result = run_survey(
                robot,
                self.spec,
                plan,
                self.ap,
                OUTPUT_DIR,
                options,
                start=self.start,
                progress=self._log,
                stop_event=self._stop_event,
            )
            self._log(f"完成 {len(result.completed)} 个点，共 {result.readings} 条读数")
            self._log(f"数据已保存：{result.output_dir}")
        except (RobotError, PlanError, ValueError, OSError) as exc:
            self._log(f"[失败] {exc}")
        except Exception as exc:  # noqa: BLE001
            self._log(f"[异常] {type(exc).__name__}: {exc}")
        finally:
            self.after(0, lambda: self.btn_start.configure(state="normal"))
            self.after(0, lambda: self.btn_stop.configure(state="disabled"))

    def _abort(self) -> None:
        self._stop_event.set()
        self._log("已请求中止，等待当前动作结束…")


def main() -> None:
    app = SurveyApp()
    app.mainloop()


if __name__ == "__main__":
    main()
