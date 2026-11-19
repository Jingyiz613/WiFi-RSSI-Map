"""房间布局模型：家具矩形、手动障碍格、路由器与起点。

家具用米制矩形描述（x/y 相对房间左上角，单位米），不要求对齐格子边界。
判定「某格能不能用」看格子中心到家具矩形的距离是否小于安全间距：
只有真正被家具挡住或贴得太近的格子才会被排除，家具压到格子边缘不会误伤整格。

布局可保存成 JSON，下次打开界面自动恢复，也可交给命令行复用。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from route_planner import Cell, GridSpec, PlanError

LAYOUT_VERSION = 3
MAX_NAME_LEN = 16
DEFAULT_CLEARANCE_M = 0.10


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class Furniture:
    """一块矩形家具，用「旋转矩形」表示：中心 (cx, cy)、原始宽深 (w, d)、朝向角 angle。

    angle 为家具绕中心旋转的角度（度，0-360）；angle=0 时宽沿 x、深沿 y。
    x1/y1/x2/y2 为旋转后的轴对齐包围盒（AABB），由中心、宽深、角度派生。
    """

    name: str
    cx: float
    cy: float
    w: float
    d: float
    material: str = ""  # 空字符串表示按家具类型自动推断
    angle: float = 0.0  # 朝向角度：0-360 度

    def __post_init__(self) -> None:
        self.cx = float(self.cx)
        self.cy = float(self.cy)
        self.w = max(float(self.w), 0.01)
        self.d = max(float(self.d), 0.01)
        cleaned = " ".join(str(self.name or "家具").split())
        self.name = (cleaned or "家具")[:MAX_NAME_LEN]
        cleaned_mat = "".join(
            ch for ch in str(self.material or "").strip().lower() if ch.isalnum() or ch == "_"
        )
        self.material = cleaned_mat[:24]
        try:
            self.angle = float(self.angle) % 360.0
        except (TypeError, ValueError):
            self.angle = 0.0

    # ---- 派生：轴对齐包围盒 ----
    def _corners(self) -> list[tuple[float, float]]:
        a = math.radians(self.angle)
        ca, sa = math.cos(a), math.sin(a)
        hw, hd = self.w / 2, self.d / 2
        return [
            (self.cx + hw * ca - hd * sa, self.cy + hw * sa + hd * ca),
            (self.cx - hw * ca - hd * sa, self.cy - hw * sa + hd * ca),
            (self.cx + hw * ca + hd * sa, self.cy + hw * sa - hd * ca),
            (self.cx - hw * ca + hd * sa, self.cy - hw * sa - hd * ca),
        ]

    @property
    def x1(self) -> float:
        return min(c[0] for c in self._corners())

    @property
    def y1(self) -> float:
        return min(c[1] for c in self._corners())

    @property
    def x2(self) -> float:
        return max(c[0] for c in self._corners())

    @property
    def y2(self) -> float:
        return max(c[1] for c in self._corners())

    @property
    def width_m(self) -> float:
        """轴对齐包围盒的宽（x 方向）。"""
        return self.x2 - self.x1

    @property
    def height_m(self) -> float:
        """轴对齐包围盒的高（y 方向）。"""
        return self.y2 - self.y1

    @property
    def area_m2(self) -> float:
        return self.w * self.d

    def rotate(self, delta: float) -> "Furniture":
        """绕自身中心旋转 delta 度（正值为逆时针，与 3D 渲染一致）。"""
        self.angle = (self.angle + float(delta)) % 360.0
        return self

    def rotate90(self) -> "Furniture":
        """绕自身中心顺时针旋转 90°（兼容旧调用）。"""
        return self.rotate(90.0)

    def contains_point(self, x_m: float, y_m: float) -> bool:
        a = -math.radians(self.angle)
        ca, sa = math.cos(a), math.sin(a)
        dx = x_m - self.cx
        dy = y_m - self.cy
        lx = dx * ca - dy * sa
        ly = dx * sa + dy * ca
        return abs(lx) <= self.w / 2 and abs(ly) <= self.d / 2

    def distance_to(self, x_m: float, y_m: float) -> float:
        """点到旋转矩形的欧氏距离；点在矩形内返回 0。"""
        a = -math.radians(self.angle)
        ca, sa = math.cos(a), math.sin(a)
        dx = x_m - self.cx
        dy = y_m - self.cy
        lx = dx * ca - dy * sa
        ly = dx * sa + dy * ca
        qx = max(abs(lx) - self.w / 2, 0.0)
        qy = max(abs(ly) - self.d / 2, 0.0)
        return math.hypot(qx, qy)

    def cells(self, spec: GridSpec, clearance_m: float = 0.0) -> set[Cell]:
        """格子中心进入「家具 + 安全间距」范围的格子。"""
        out: set[Cell] = set()
        for cell in spec.all_cells():
            cx, cy = spec.center(cell)
            if self.distance_to(cx, cy) <= clearance_m:
                out.add(cell)
        return out

    def clipped(self, spec: GridSpec) -> "Furniture | None":
        """约束到房间内：中心须在房间范围内；完全在外或过小时返回 None。

        旋转家具允许轴对齐包围盒略超出边缘（家具斜放贴墙是合理的）。
        """
        if self.w < 0.02 or self.d < 0.02:
            return None
        if not (0.0 <= self.cx <= spec.width_m and 0.0 <= self.cy <= spec.height_m):
            return None
        return self

    def describe(self, spec: GridSpec, clearance_m: float = DEFAULT_CLEARANCE_M) -> str:
        count = len(self.cells(spec, clearance_m))
        return (
            f"{self.name}  {self.w * 100:.0f}×{self.d * 100:.0f} cm  "
            f"{self.area_m2:.2f} m2  挡 {count} 格"
        )

    # ---- 序列化 ----
    def to_dict(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "cx": round(self.cx, 3),
            "cy": round(self.cy, 3),
            "w": round(self.w, 3),
            "d": round(self.d, 3),
        }
        if self.angle:
            data["angle"] = round(self.angle, 1)
        if self.material:
            data["material"] = self.material
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], spec: GridSpec | None = None) -> "Furniture":
        material = str(data.get("material") or "")
        angle = float(data.get("angle") or 0)
        # 新格式：中心 + 原始宽深 + 角度
        if "cx" in data and "w" in data:
            return cls(
                name=str(data.get("name", "家具")),
                cx=float(data["cx"]),
                cy=float(data["cy"]),
                w=float(data["w"]),
                d=float(data["d"]),
                material=material,
                angle=angle,
            )
        # 旧格式：轴对齐矩形 x1,y1,x2,y2（正交角度 0/90/180/270）
        if "x1" in data:
            x1, x2 = sorted((float(data["x1"]), float(data.get("x2", data["x1"]))))
            y1, y2 = sorted(
                (float(data.get("y1", 0.0)), float(data.get("y2", data.get("y1", 0.0))))
            )
            a = int(round(angle)) % 360
            if a % 180 == 90:
                w, d = y2 - y1, x2 - x1
            else:
                w, d = x2 - x1, y2 - y1
            return cls(
                name=str(data.get("name", "家具")),
                cx=(x1 + x2) / 2,
                cy=(y1 + y2) / 2,
                w=w,
                d=d,
                material=material,
                angle=float(a),
            )
        # 兼容 v1：行列格式，转成整格矩形
        if spec is None:
            raise PlanError("旧版家具缺少房间参数，无法换算成米制坐标")
        row1 = int(data.get("row1", 1))
        col1 = int(data.get("col1", 1))
        row2 = int(data.get("row2", row1))
        col2 = int(data.get("col2", col1))
        bx1, by1, _, _ = spec.cell_bounds((row1, col1))
        _, _, bx2, by2 = spec.cell_bounds((row2, col2))
        return cls(
            str(data.get("name", "家具")),
            (bx1 + bx2) / 2,
            (by1 + by2) / 2,
            bx2 - bx1,
            by2 - by1,
            material,
            angle,
        )


@dataclass
class Layout:
    """一整套房间布置：网格、路由器、起点、家具、手点的障碍格。"""

    spec: GridSpec = field(default_factory=GridSpec)
    ap: Cell = (2, 2)
    start: Cell = (1, 1)
    furniture: list[Furniture] = field(default_factory=list)
    manual_blocked: set[Cell] = field(default_factory=set)
    clearance_m: float = DEFAULT_CLEARANCE_M
    ap_height_m: float = 1.0  # 路由器天线高度（米）

    # ---- 查询 ----
    def furniture_cells(self) -> set[Cell]:
        cells: set[Cell] = set()
        for item in self.furniture:
            cells |= item.cells(self.spec, self.clearance_m)
        return cells

    def blocked(self) -> set[Cell]:
        """所有不可通行格：家具影响到的格子 + 手动障碍。"""
        return self.furniture_cells() | set(self.manual_blocked)

    def furniture_at(self, cell: Cell) -> Furniture | None:
        x, y = self.spec.center(cell)
        for item in self.furniture:
            if item.distance_to(x, y) <= self.clearance_m:
                return item
        return None

    def furniture_named(self, name: str) -> Furniture | None:
        for item in self.furniture:
            if item.name == name:
                return item
        return None

    # ---- 修改 ----
    def add_furniture(self, item: Furniture) -> Furniture:
        clipped = item.clipped(self.spec)
        if clipped is None:
            raise PlanError("家具太小或完全在房间之外")
        if self.furniture_named(clipped.name):
            base = clipped.name
            index = 2
            while self.furniture_named(f"{base}{index}"):
                index += 1
            clipped.name = f"{base}{index}"[:MAX_NAME_LEN]
        self.furniture.append(clipped)
        return clipped

    def remove_furniture(self, index: int) -> None:
        if 0 <= index < len(self.furniture):
            del self.furniture[index]

    def rename_furniture(self, index: int, name: str) -> None:
        if 0 <= index < len(self.furniture):
            cleaned = " ".join(str(name).split())[:MAX_NAME_LEN] or "家具"
            self.furniture[index].name = cleaned

    def clear_furniture(self) -> None:
        self.furniture.clear()

    # ---- 校验 ----
    def validate(self) -> list[str]:
        """返回问题清单；空列表表示可以直接跑。"""
        problems: list[str] = []
        blocked = self.blocked()

        if self.ap in blocked:
            owner = self.furniture_at(self.ap)
            where = f"家具「{owner.name}」" if owner else "手动障碍格"
            problems.append(f"路由器 {self.spec.label(self.ap)} 落在{where}内，无法在该点采集")
        if self.start in blocked:
            owner = self.furniture_at(self.start)
            where = f"家具「{owner.name}」" if owner else "手动障碍格"
            problems.append(f"起点 {self.spec.label(self.start)} 落在{where}内，小车无法出发")

        if problems:
            return problems

        reachable = self.reachable()
        lost = [
            cell
            for cell in self.spec.all_cells()
            if cell not in blocked and cell not in reachable
        ]
        if lost:
            sample = "、".join(self.spec.label(c) for c in lost[:6])
            more = f" 等 {len(lost)} 格" if len(lost) > 6 else ""
            problems.append(f"{sample}{more} 被家具围住，小车开不进去；请留出通道")
        return problems

    def reachable(self) -> set[Cell]:
        """从起点出发能走到的格子（四邻域）。"""
        blocked = self.blocked()
        if self.start in blocked:
            return set()
        seen = {self.start}
        stack = [self.start]
        while stack:
            row, col = stack.pop()
            for nxt in ((row, col + 1), (row, col - 1), (row + 1, col), (row - 1, col)):
                if not (1 <= nxt[0] <= self.spec.rows and 1 <= nxt[1] <= self.spec.columns):
                    continue
                if nxt in blocked or nxt in seen:
                    continue
                seen.add(nxt)
                stack.append(nxt)
        return seen

    def usable_cells(self) -> list[Cell]:
        blocked = self.blocked()
        return [c for c in self.spec.all_cells() if c not in blocked]

    def stats(self) -> dict[str, int]:
        blocked = self.blocked()
        furniture_cells = len(self.furniture_cells())
        return {
            "total": self.spec.rows * self.spec.columns,
            "furniture_cells": furniture_cells,
            "manual_cells": len(self.manual_blocked),
            "blocked": len(blocked),
            "usable": self.spec.rows * self.spec.columns - len(blocked),
        }

    # ---- 序列化 ----
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": LAYOUT_VERSION,
            "room": {
                "rows": self.spec.rows,
                "columns": self.spec.columns,
                "width_m": self.spec.width_m,
                "height_m": self.spec.height_m,
            },
            "clearance_m": round(self.clearance_m, 3),
            "ap": self.spec.label(self.ap),
            "start": self.spec.label(self.start),
            "ap_height_m": round(self.ap_height_m, 2),
            "furniture": [item.to_dict() for item in self.furniture],
            "manual_blocked": [self.spec.label(c) for c in sorted(self.manual_blocked)],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Layout":
        room = data.get("room") or {}
        spec = GridSpec(
            rows=int(room.get("rows", 7)),
            columns=int(room.get("columns", 9)),
            width_m=float(room.get("width_m", 8.23)),
            height_m=float(room.get("height_m", 6.95)),
        )

        def _cell(text: Any, fallback: Cell) -> Cell:
            try:
                return spec.parse(str(text))
            except (PlanError, TypeError, ValueError):
                return fallback

        layout = cls(
            spec=spec,
            ap=_cell(data.get("ap"), (min(2, spec.rows), min(2, spec.columns))),
            start=_cell(data.get("start"), (1, 1)),
            clearance_m=float(data.get("clearance_m", DEFAULT_CLEARANCE_M)),
            ap_height_m=float(data.get("ap_height_m", 1.0)),
        )
        for raw in data.get("furniture") or []:
            if not isinstance(raw, dict):
                continue
            try:
                clipped = Furniture.from_dict(raw, spec).clipped(spec)
            except (PlanError, TypeError, ValueError):
                continue
            if clipped is not None:
                layout.furniture.append(clipped)
        for text in data.get("manual_blocked") or []:
            try:
                layout.manual_blocked.add(spec.parse(str(text)))
            except (PlanError, TypeError, ValueError):
                continue
        return layout

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return out

    @classmethod
    def load(cls, path: str | Path) -> "Layout":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise PlanError("布局文件格式不对，顶层应为 JSON 对象")
        return cls.from_dict(data)

    @classmethod
    def load_or_default(cls, path: str | Path) -> "Layout":
        try:
            return cls.load(path)
        except (OSError, ValueError, PlanError, json.JSONDecodeError):
            return cls()


def blocked_from_layout(path: str | Path) -> tuple[GridSpec, Cell, Cell, set[Cell]]:
    """命令行辅助：读布局，返回 (网格, 路由器, 起点, 禁行格)。"""
    layout = Layout.load(path)
    return layout.spec, layout.ap, layout.start, layout.blocked()


def describe_layout(layout: Layout) -> str:
    stats = layout.stats()
    lines = [
        f"网格 {layout.spec.rows}×{layout.spec.columns}，"
        f"{layout.spec.width_m:.2f}×{layout.spec.height_m:.2f} m，安全间距 {layout.clearance_m * 100:.0f} cm",
        f"路由器 {layout.spec.label(layout.ap)}，起点 {layout.spec.label(layout.start)}",
        f"家具 {len(layout.furniture)} 件，占用 {stats['furniture_cells']} 格；"
        f"手动障碍 {stats['manual_cells']} 格；可测 {stats['usable']} 格",
    ]
    for item in layout.furniture:
        lines.append("  · " + item.describe(layout.spec, layout.clearance_m))
    return "\n".join(lines)
