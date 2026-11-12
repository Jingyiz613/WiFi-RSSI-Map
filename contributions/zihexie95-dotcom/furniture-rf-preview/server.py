#!/usr/bin/env python3
"""miniAuto 3D 布局编辑器 —— 本地服务。

读取 data/ 下的 layout JSON，复用原系统的 layout.py 生成格子状态与校验结果，
交给浏览器做 3D 渲染和拖动编辑；编辑结果可以保存成新的布局文件。

只依赖 Python 标准库，现场离线可用。
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
CORE = ROOT / "core"
WEB = ROOT / "web"
DATA = ROOT / "data"
VENDOR = ROOT.parents[2] / "3d-layout-editor" / "web" / "vendor"
sys.path.insert(0, str(CORE))

from layout import Layout  # noqa: E402
from route_planner import PlanError, make_serpentine_plan  # noqa: E402

# 家具名 -> (几何类型, 默认高度 m)。布局文件里没有高度字段，这里按名称给合理默认值。
KIND_TABLE = (
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
        out.append(
            {
                "index": index,
                "name": item.name,
                "kind": kind,
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
        if "/api/" in (str(args[0]) if args else ""):
            super().log_message(fmt, *args)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def translate_path(self, path: str) -> str:
        asset = urlparse(path).path.removeprefix("/vendor/")
        if path.startswith("/vendor/") and asset in {"three.module.js", "OrbitControls.js", "LICENSE-three.txt"}:
            return str(VENDOR / asset)
        return super().translate_path(path)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
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
        if parsed.path not in ("/api/analyze", "/api/save", "/api/rf-precise"):
            self._json({"error": "接口不存在"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 <= length <= 262144:
                raise ValueError("请求体过大")
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._json({"error": f"请求体解析失败：{exc}"}, 400)
            return

        if parsed.path == "/api/rf-precise":
            try:
                from rf_precise import compare
                layout = Layout.from_dict(payload["layout"])
                if layout.spec.rows * layout.spec.columns > 100:
                    raise ValueError("精算最多支持 100 格")
                self._json(compare(build_scene(layout, "preview"), payload))
            except ModuleNotFoundError:
                self._json({"error": "未安装射线追踪依赖；快速透射预览仍可用"}, 503)
            except (ValueError, KeyError, TypeError) as exc:
                self._json({"error": f"精算参数无效：{exc}"}, 400)
            except Exception as exc:
                self._json({"error": f"射线追踪失败：{exc}"}, 500)
            return

        if parsed.path == "/api/analyze":
            try:
                layout = Layout.from_dict(payload)
                self._json(state_payload(layout))
            except Exception as exc:
                self._json({"error": f"分析失败：{exc}"}, 500)
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


def pick_port(start: int = 8765, tries: int = 40) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("没有可用端口，请用 --port 指定。")


def main() -> None:
    parser = argparse.ArgumentParser(description="miniAuto 3D 布局编辑器")
    parser.add_argument("--port", type=int, default=0, help="固定端口，0 表示自动选择")
    parser.add_argument("--layout", default="layout.json", help="默认加载的布局文件")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    port = args.port or pick_port()
    handler = partial(Handler, directory=str(WEB))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/?layout={args.layout}"

    print("=" * 60)
    print("  miniAuto Wi-Fi 采集系统 · 3D 布局编辑器")
    print("=" * 60)
    print(f"  地址：{url}")
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
