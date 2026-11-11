#!/usr/bin/env python3
"""miniAuto 3D 布局编辑器 —— 本地服务。

读取 data/ 下的 layout JSON，复用原系统的 layout.py 生成格子状态与校验结果，
交给浏览器做 3D 渲染和拖动编辑；编辑结果可以保存成新的布局文件。

只依赖 Python 标准库，现场离线可用。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

def _resolve_dirs() -> tuple[Path, Path]:
    """Return (writable app dir, read-only bundle dir).

    When frozen by PyInstaller the bundled resources live in sys._MEIPASS while
    user data (saved layouts) must sit next to the executable.
    """
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        bundle = Path(getattr(sys, "_MEIPASS", app_dir))
    else:
        app_dir = Path(__file__).resolve().parent
        bundle = app_dir
    return app_dir, bundle


ROOT, BUNDLE = _resolve_dirs()
CORE = BUNDLE / "core"
WEB = BUNDLE / "web"
DATA = ROOT / "data"
MODEL_DIR = BUNDLE / "model"
sys.path.insert(0, str(CORE))

from layout import Layout  # noqa: E402
from route_planner import PlanError, make_serpentine_plan  # noqa: E402

# 家具名 -> (几何类型, 默认高度 m)。布局文件里没有高度字段，这里按名称给合理默认值。
KIND_TABLE = (
    (("砖墙240", "砖墙 240", "brick_240"), "brick_240", 2.2),
    (("砖墙120", "砖墙 120", "brick_120"), "brick_120", 2.2),
    (("木门", "wood door", "wood_door"), "wood_door", 2.1),
    (("玻璃窗", "window", "glass_window"), "glass_window", 1.2),
    (("混凝土", "concrete"), "concrete", 2.2),
    (("金属板", "metal panel", "metal_panel"), "metal_panel", 2.0),
    (("床头柜", "nightstand"), "nightstand", 0.55),
    (("边几", "side_table", "side table"), "side_table", 0.55),
    (("沙发", "sofa", "couch"), "sofa", 0.86),
    (("茶几", "coffee"), "coffee_table", 0.45),
    (("餐桌", "dining", "饭桌"), "table", 0.75),
    (("电视柜", "tv stand", "tv_stand"), "tv_stand", 0.5),
    (("电视", "tv", "屏幕", "screen"), "tv", 0.62),
    (("床", "bed"), "bed", 0.56),
    (("椅", "chair", "凳", "stool"), "chair", 0.95),
    (("桌", "desk", "table"), "desk", 0.75),
    (("衣柜", "wardrobe", "柜", "cabinet", "书架", "shelf"), "cabinet", 1.85),
    (("冰箱", "fridge"), "fridge", 1.82),
    (("洗衣机", "washer"), "washer", 0.92),
    (("空调", "aircon"), "aircon", 1.9),
    (("绿植", "plant", "花"), "plant", 1.05),
    (("地毯", "rug", "carpet"), "rug", 0.02),
    (("箱", "box", "柜子"), "box", 0.7),
)


def classify(name: str) -> tuple[str, float]:
    text = str(name or "").lower()
    for keys, kind, height in KIND_TABLE:
        if any(k in text for k in keys):
            return kind, height
    return "box", 0.75


def _cell_point(spec, cell) -> tuple[float, float]:
    x, y = spec.center(cell)
    return round(x, 4), round(y, 4)


def _room_payload(layout: Layout) -> dict:
    spec = layout.spec
    return {
        "rows": spec.rows,
        "columns": spec.columns,
        "width_m": spec.width_m,
        "height_m": spec.height_m,
        "cell_width_m": round(spec.cell_width_m, 5),
        "cell_height_m": round(spec.cell_height_m, 5),
    }


def furniture_meta(layout: Layout) -> list[dict]:
    spec = layout.spec
    out = []
    for index, item in enumerate(layout.furniture):
        kind, height = classify(item.name)
        override = item.material if item.material in MATERIALS else ""
        material = override or KIND_MATERIAL.get(kind, "synthetic")
        out.append(
            {
                "index": index,
                "name": item.name,
                "kind": kind,
                "material": override,
                "material_auto": KIND_MATERIAL.get(kind, "synthetic"),
                "material_used": material,
                "material_label": MATERIALS[material]["label"],
                "x1": round(item.x1, 4),
                "y1": round(item.y1, 4),
                "x2": round(item.x2, 4),
                "y2": round(item.y2, 4),
                "cx": round((item.x1 + item.x2) / 2, 4),
                "cz": round((item.y1 + item.y2) / 2, 4),
                "width_m": round(item.width_m, 4),
                "depth_m": round(item.height_m, 4),
                "area_m2": round(item.area_m2, 3),
                "height_m": height,
                "blocked_cells": len(item.cells(spec, layout.clearance_m)),
            }
        )
    return out


def cells_meta(layout: Layout) -> list[dict]:
    spec = layout.spec
    manual = set(layout.manual_blocked)
    furniture_cells = layout.furniture_cells()
    cells = []
    for cell in spec.all_cells():
        x, y = _cell_point(spec, cell)
        reason = None
        owner = None
        if cell in furniture_cells:
            reason = "furniture"
            item = layout.furniture_at(cell)
            owner = item.name if item else None
        elif cell in manual:
            reason = "manual"
        cells.append(
            {
                "row": cell[0],
                "column": cell[1],
                "label": spec.label(cell),
                "x_m": x,
                "y_m": y,
                "blocked": reason is not None,
                "reason": reason,
                "owner": owner,
            }
        )
    return cells


def overlap_problems(layout: Layout) -> list[str]:
    """原系统只校验 AP/起点与连通性，这里补一条家具重叠提示。"""
    items = layout.furniture
    problems = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if a.x1 < b.x2 - 1e-6 and b.x1 < a.x2 - 1e-6 and a.y1 < b.y2 - 1e-6 and b.y1 < a.y2 - 1e-6:
                problems.append(f"家具「{a.name}」与「{b.name}」重叠")
    return problems[:4]


# ---------------- 简化物理仿真：布局 -> 路径损耗 -> 热力图 ----------------
# 参数来自资料包的华为典型障碍衰减与 ITU 材料先验，只用于展示相对变化，
# 不是本房间的实测标定值。

BANDS = {
    "2.4g": {"label": "2.4 GHz", "freq_mhz": 2437.0},
    "5g": {"label": "5 GHz", "freq_mhz": 5180.0},
}
DEFAULT_BAND = "2.4g"

# 典型障碍物衰减（dB），来自团队资料表；只用于展示相对变化，不是本房间实测标定。
MATERIALS = {
    "synthetic": {"label": "合成材料", "thickness_mm": 20, "loss": {"2.4g": 2.0, "5g": 3.0}},
    "asbestos": {"label": "石棉", "thickness_mm": 8, "loss": {"2.4g": 3.0, "5g": 4.0}},
    "wood_door": {"label": "木门", "thickness_mm": 40, "loss": {"2.4g": 3.0, "5g": 4.0}},
    "glass_window": {"label": "玻璃窗", "thickness_mm": 50, "loss": {"2.4g": 4.0, "5g": 7.0}},
    "tinted_glass": {"label": "有色厚玻璃", "thickness_mm": 80, "loss": {"2.4g": 8.0, "5g": 10.0}},
    "brick_120": {"label": "砖墙 120mm", "thickness_mm": 120, "loss": {"2.4g": 10.0, "5g": 20.0}},
    "brick_240": {"label": "砖墙 240mm", "thickness_mm": 240, "loss": {"2.4g": 15.0, "5g": 25.0}},
    "bulletproof_glass": {"label": "防弹玻璃", "thickness_mm": 120, "loss": {"2.4g": 25.0, "5g": 35.0}},
    "concrete": {"label": "混凝土", "thickness_mm": 240, "loss": {"2.4g": 25.0, "5g": 30.0}},
    "metal": {"label": "金属", "thickness_mm": 80, "loss": {"2.4g": 30.0, "5g": 35.0}},
}

KIND_MATERIAL = {
    "sofa": "synthetic",
    "bed": "synthetic",
    "cabinet": "synthetic",
    "nightstand": "synthetic",
    "side_table": "synthetic",
    "desk": "synthetic",
    "table": "synthetic",
    "chair": "synthetic",
    "coffee_table": "synthetic",
    "tv": "tinted_glass",
    "tv_stand": "synthetic",
    "fridge": "metal",
    "washer": "metal",
    "plant": "synthetic",
    "rug": "synthetic",
    "aircon": "synthetic",
    "box": "synthetic",
    "brick_120": "brick_120",
    "brick_240": "brick_240",
    "wood_door": "wood_door",
    "glass_window": "glass_window",
    "concrete": "concrete",
    "metal_panel": "metal",
}

# 家电是薄壳金属/玻璃，不能按 80mm 实心金属算
MATERIAL_SCALE = {"fridge": 0.35, "washer": 0.3, "tv": 0.5, "aircon": 0.3}

TX_EIRP_DBM = 22.0
CLUTTER_DB = 6.0
AP_HEIGHT_M = 1.0
RX_HEIGHT_M = 0.9
QUALITY_THRESHOLDS = (-40.0, -35.0, -30.0, -25.0)
QUALITY_LABELS = ("弱", "偏弱", "中", "好", "强")


def _segment_hits_rect(x1, y1, x2, y2, xmin, ymin, xmax, ymax) -> bool:
    """Liang-Barsky：线段是否与轴对齐矩形相交。"""
    dx = x2 - x1
    dy = y2 - y1
    p = (-dx, dx, -dy, dy)
    q = (x1 - xmin, xmax - x1, y1 - ymin, ymax - y1)
    u1, u2 = 0.0, 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1e-12:
            if qi < 0:
                return False
        else:
            r = qi / pi
            if pi < 0:
                if r > u2:
                    return False
                u1 = max(u1, r)
            else:
                if r < u1:
                    return False
                u2 = min(u2, r)
    return u1 <= u2


def _material_loss(item, kind: str, band: str, height_m: float) -> tuple[float, str]:
    """穿透损耗：用户指定材质优先，否则按家具类型推断。

    自动推断时家电按薄壳折减；手动选材质则按材料表原值计算。
    """
    override = item.material if item.material in MATERIALS else ""
    if override:
        material = override
        scale = 1.0
    else:
        material = KIND_MATERIAL.get(kind, "synthetic")
        scale = MATERIAL_SCALE.get(kind, 1.0)
    base = MATERIALS[material]["loss"][band] * scale
    height = max(float(height_m or 0.0), 0.0)
    if height <= 0.3:
        return 0.0, material
    factor = 1.0 if height >= 1.0 else (height - 0.3) / 0.7
    return base * max(0.0, min(1.0, factor)), material


def simulate_layout(layout: Layout, band: str = DEFAULT_BAND) -> dict:
    if band not in BANDS:
        band = DEFAULT_BAND
    frequency_mhz = BANDS[band]["freq_mhz"]
    spec = layout.spec
    ap_x, ap_y = spec.center(layout.ap)
    points = []

    for cell in spec.all_cells():
        cx, cy = spec.center(cell)
        dx = cx - ap_x
        dy = cy - ap_y
        distance = max(0.8, (dx * dx + dy * dy + (AP_HEIGHT_M - RX_HEIGHT_M) ** 2) ** 0.5)
        fspl = 20.0 * math.log10(distance) + 20.0 * math.log10(frequency_mhz) - 27.55

        penetration = 0.0
        hit_materials = []
        for item in layout.furniture:
            kind, height = classify(item.name)
            loss, material = _material_loss(item, kind, band, height)
            if loss <= 0:
                continue
            if _segment_hits_rect(ap_x, ap_y, cx, cy, item.x1, item.y1, item.x2, item.y2):
                penetration += loss
                hit_materials.append(
                    {"label": MATERIALS[material]["label"], "db": round(loss, 1)}
                )
        penetration = min(penetration, 40.0)

        rssi = TX_EIRP_DBM - fspl - penetration - CLUTTER_DB
        quality = 0
        for threshold in QUALITY_THRESHOLDS:
            if rssi >= threshold:
                quality += 1

        points.append(
            {
                "row": cell[0],
                "column": cell[1],
                "label": spec.label(cell),
                "x_m": round(cx, 4),
                "y_m": round(cy, 4),
                "distance_m": round(distance, 3),
                "penetration_db": round(penetration, 2),
                "materials": hit_materials,
                "rssi_dbm": round(rssi, 2),
                "quality": quality,
                "quality_label": QUALITY_LABELS[quality],
            }
        )

    values = [p["rssi_dbm"] for p in points]
    counts = [sum(1 for p in points if p["quality"] == q) for q in range(5)]
    return {
        "points": points,
        "stats": {
            "min_dbm": round(min(values), 2),
            "max_dbm": round(max(values), 2),
            "mean_dbm": round(sum(values) / len(values), 2),
            "strong_cells": counts[3] + counts[4],
            "quality_counts": counts,
        },
        "params": {
            "band": band,
            "frequency_mhz": frequency_mhz,
            "tx_eirp_dbm": TX_EIRP_DBM,
            "clutter_db": CLUTTER_DB,
            "ap_height_m": AP_HEIGHT_M,
            "rx_height_m": RX_HEIGHT_M,
            "thresholds_dbm": list(QUALITY_THRESHOLDS),
            "quality_labels": list(QUALITY_LABELS),
            "materials": MATERIALS,
            "note": "简化物理模型，穿透衰减按材料表与频段取值；用于展示相对变化，非实测标定。",
        },
    }


def state_payload(layout: Layout, file_name: str = "") -> dict:
    spec = layout.spec
    try:
        # 不再展示“小车开不进去”一类提示，当前只关心家具摆放
        problems = [p for p in layout.validate() if "小车" not in p]
    except PlanError as exc:  # 极端布局兜底，不让接口失败
        problems = [str(exc)]
    problems = problems + overlap_problems(layout)

    stats = layout.stats()
    stats["furniture_count"] = len(layout.furniture)

    return {
        "file": file_name,
        "layout_version": 2,
        "room": _room_payload(layout),
        "clearance_m": round(layout.clearance_m, 3),
        "ap": {
            "label": spec.label(layout.ap),
            "row": layout.ap[0],
            "column": layout.ap[1],
            "x_m": _cell_point(spec, layout.ap)[0],
            "y_m": _cell_point(spec, layout.ap)[1],
        },
        "start": {
            "label": spec.label(layout.start),
            "row": layout.start[0],
            "column": layout.start[1],
            "x_m": _cell_point(spec, layout.start)[0],
            "y_m": _cell_point(spec, layout.start)[1],
        },
        "furniture": furniture_meta(layout),
        "cells": cells_meta(layout),
        "manual_blocked": [spec.label(c) for c in sorted(layout.manual_blocked)],
        "stats": stats,
        "problems": problems,
    }


def _read_example_ids() -> list[str]:
    with (MODEL_DIR / "example_input.csv").open(newline="", encoding="utf-8-sig") as fh:
        return [row["point_id"].strip().upper() for row in csv.DictReader(fh)]


_MODEL_PYTHON = None
_MODEL_MODULE = None


def _model_python() -> list[str]:
    """Pick a Python that can import numpy; the NumPy inference needs it."""
    global _MODEL_PYTHON
    if _MODEL_PYTHON is not None:
        return _MODEL_PYTHON
    explicit = os.environ.get("MINIAUTO_MODEL_PYTHON")
    candidates = [[explicit]] if explicit else []
    launcher = shutil.which("py")
    if launcher:
        candidates.extend([[launcher, "-3.13"], [launcher, "-3.12"], [launcher, "-3.11"]])
    candidates.append([sys.executable])
    for command in candidates:
        try:
            probe = subprocess.run(
                command + ["-c", "import numpy"], capture_output=True, timeout=20
            )
            if probe.returncode == 0:
                _MODEL_PYTHON = command
                return command
        except Exception:
            continue
    _MODEL_PYTHON = [sys.executable]
    return _MODEL_PYTHON


def _predict_inprocess(environment: str, input_path: Path) -> dict:
    """Load the NumPy model inside this process (the only option once frozen)."""
    global _MODEL_MODULE
    if _MODEL_MODULE is None:
        model_path = str(MODEL_DIR)
        if model_path not in sys.path:
            sys.path.insert(0, model_path)
        import numpy_infer  # noqa: PLC0415

        _MODEL_MODULE = numpy_infer
    return _MODEL_MODULE.predict(environment, input_path)


def predict_layout(payload: dict) -> dict:
    """Run the trained 20-point dense classifier and return its 64x76 grid."""
    environment = str(payload.get("environment") or "empty")
    if environment not in ("empty", "furnished"):
        raise ValueError("environment 只能是 empty 或 furnished")
    mode = str(payload.get("input") or "layout")
    if mode not in ("example", "layout"):
        raise ValueError("input 只能是 example 或 layout")
    band = str(payload.get("band") or DEFAULT_BAND)
    if band not in BANDS:
        raise ValueError("band 只能是 2.4g 或 5g")

    temp_path = None
    try:
        if mode == "example":
            input_path = MODEL_DIR / "example_input.csv"
        else:
            layout = Layout.from_dict(payload.get("layout") or {})
            simulated = simulate_layout(layout, band)
            by_cell = {(p["row"], p["column"]): p["rssi_dbm"] for p in simulated["points"]}
            rows = []
            for point_id in _read_example_ids():
                key = (int(point_id[1]), int(point_id[2]))
                if key not in by_cell:
                    raise ValueError(f"布局里没有测点 {point_id}")
                rows.append({"point_id": point_id, "rssi_dbm": by_cell[key]})
            handle = tempfile.NamedTemporaryFile(
                "w", suffix=".csv", delete=False, encoding="utf-8", newline=""
            )
            temp_path = handle.name
            writer = csv.DictWriter(handle, fieldnames=["point_id", "rssi_dbm"])
            writer.writeheader()
            writer.writerows(rows)
            handle.close()
            input_path = Path(temp_path)

        try:
            return _predict_inprocess(environment, input_path)
        except Exception as exc:  # fall back to an external interpreter when available
            if getattr(sys, "frozen", False):
                raise RuntimeError(f"模型推理失败：{exc}") from exc
            command = _model_python() + [
                str(MODEL_DIR / "numpy_infer.py"),
                "--model",
                environment,
                "--input",
                str(input_path),
            ]
            proc = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", timeout=120
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()[-600:]
                raise RuntimeError(detail or f"模型进程退出码 {proc.returncode}")
            return json.loads(proc.stdout)
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def build_scene(layout: Layout, file_name: str) -> dict:
    payload = state_payload(layout, file_name)
    spec = layout.spec
    blocked = layout.blocked()

    route = {"stops": [], "points": [], "summary": {}, "distance_m": 0.0, "error": None}
    try:
        plan = make_serpentine_plan(spec, layout.ap, sorted(blocked), layout.start)
        points: list[tuple[float, float]] = []

        def push(cell) -> None:
            p = spec.center(cell)
            if not points or abs(p[0] - points[-1][0]) > 1e-6 or abs(p[1] - points[-1][1]) > 1e-6:
                points.append(p)

        push(layout.start)
        for stop in plan:
            for cell in stop.via:
                push(cell)
            route["stops"].append(
                {
                    "point_id": stop.point_id,
                    "row": stop.cell[0],
                    "column": stop.cell[1],
                    "x_m": round(spec.center(stop.cell)[0], 4),
                    "y_m": round(spec.center(stop.cell)[1], 4),
                    "via": [spec.label(c) for c in stop.via],
                }
            )

        distance = 0.0
        for a, b in zip(points, points[1:]):
            distance += ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
        route["points"] = [[round(x, 4), round(y, 4)] for x, y in points]
        route["distance_m"] = round(distance, 2)
        route["summary"] = {
            "measurement_stops": len(plan),
            "total_moves": sum(len(s.via) for s in plan),
            "skipped_cells": spec.rows * spec.columns - len(plan),
        }
    except PlanError as exc:
        route["error"] = str(exc)

    payload["route"] = route
    payload["stats"]["route_distance_m"] = route["distance_m"]
    return payload


def list_layouts() -> list[dict]:
    out = []
    for path in sorted(DATA.glob("*.json")):
        try:
            layout = Layout.load(path)
            out.append(
                {
                    "file": path.name,
                    "furniture": len(layout.furniture),
                    "rows": layout.spec.rows,
                    "columns": layout.spec.columns,
                    "ap": layout.spec.label(layout.ap),
                    "start": layout.spec.label(layout.start),
                    "mtime": int(path.stat().st_mtime),
                }
            )
        except Exception:
            out.append({"file": path.name, "error": "无法读取"})
    return out


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # 安静一点
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/materials":
            self._json({"bands": BANDS, "materials": MATERIALS, "kind_material": KIND_MATERIAL})
            return
        if parsed.path == "/api/layouts":
            self._json({"layouts": list_layouts()})
            return
        if parsed.path == "/api/scene":
            query = parse_qs(parsed.query)
            name = (query.get("file") or ["layout.json"])[0]
            safe = Path(name).name
            if not safe.lower().endswith(".json") or not (DATA / safe).is_file():
                self._json({"error": f"找不到布局文件：{name}"}, 404)
                return
            try:
                self._json(build_scene(Layout.load(DATA / safe), safe))
            except Exception as exc:
                self._json({"error": f"读取布局失败：{exc}"}, 500)
            return
        if parsed.path == "/api/health":
            self._json({"ok": True})
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/analyze", "/api/save", "/api/simulate", "/api/predict"):
            self._json({"error": "接口不存在"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._json({"error": f"请求体解析失败：{exc}"}, 400)
            return

        if parsed.path == "/api/analyze":
            try:
                layout = Layout.from_dict(payload)
                self._json(state_payload(layout))
            except Exception as exc:
                self._json({"error": f"分析失败：{exc}"}, 500)
            return

        if parsed.path == "/api/simulate":
            try:
                layout = Layout.from_dict(payload)
                band = str(payload.get("band") or DEFAULT_BAND)
                self._json(simulate_layout(layout, band))
            except Exception as exc:
                self._json({"error": f"仿真失败：{exc}"}, 500)
            return

        if parsed.path == "/api/predict":
            try:
                self._json(predict_layout(payload))
            except Exception as exc:
                self._json({"error": f"AI 补全失败：{exc}"}, 503)
            return

        # /api/save
        file_name = Path(str(payload.get("file") or "layout.edited.json")).name
        if not file_name.lower().endswith(".json"):
            self._json({"error": "文件名必须以 .json 结尾"}, 400)
            return
        try:
            layout = Layout.from_dict(payload.get("layout") or {})
            layout.save(DATA / file_name)
            self._json(
                {
                    "ok": True,
                    "file": file_name,
                    "problems": state_payload(layout)["problems"],
                }
            )
        except Exception as exc:
            self._json({"error": f"保存失败：{exc}"}, 500)


def pick_port(start: int = 8765, tries: int = 40, host: str = "127.0.0.1") -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host or "127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("没有可用端口，请用 --port 指定。")


def lan_address() -> str:
    """Best-effort LAN IPv4 of this machine (no traffic is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def ensure_data_dir() -> None:
    """Create the writable data folder and seed bundled layouts on first run."""
    DATA.mkdir(parents=True, exist_ok=True)
    bundled = BUNDLE / "data"
    if bundled.is_dir() and bundled.resolve() != DATA.resolve():
        for src in bundled.glob("*.json"):
            dst = DATA / src.name
            if not dst.exists():
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="miniAuto 3D 布局编辑器")
    parser.add_argument("--port", type=int, default=0, help="固定端口，0 表示自动选择")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，0.0.0.0 表示允许局域网访问")
    parser.add_argument("--layout", default="layout.json", help="默认加载的布局文件")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    host = args.host or "127.0.0.1"
    shared = host in ("0.0.0.0", "::", "")
    ensure_data_dir()
    port = args.port or pick_port(host=host)
    handler = partial(Handler, directory=str(WEB))
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://127.0.0.1:{port}/?layout={args.layout}"

    print("=" * 60)
    print("  miniAuto Wi-Fi 采集系统 · 3D 布局编辑器")
    print("=" * 60)
    print(f"  地址：{url}")
    if shared:
        print(f"  共享：http://{lan_address()}:{port}/?layout={args.layout}")
        print("        （同一局域网/Wi-Fi 的同伴用这个地址；首次会弹防火墙提示，请允许）")
    print(f"  数据：{DATA}")
    print("  操作：拖动家具移动，Alt 自由移动，Ctrl+Z 撤销")
    print("  关闭：Ctrl+C 或直接关掉这个窗口")
    print("=" * 60)

    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
