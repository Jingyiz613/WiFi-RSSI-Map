"""路线规划模块：房间网格建模、蛇形覆盖路线、BFS 避障。

坐标约定
--------
- 格子编号 P<行><列>，行列均为 1..9，例如 P22 表示第 2 行第 2 列。
- 行号沿房间"纵深"方向增长（小车从 P11 出发、车头朝 P21 时，行号是前进方向）。
- 列号沿"横向"方向增长，小车用麦克纳姆轮横移换列。
- 米制坐标原点在房间左上角，x 向右（列方向），y 向下（行方向）。

本模块只做纯计算，不访问网络、不操作硬件，可离线单测。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Sequence

Cell = tuple[int, int]


class PlanError(ValueError):
    """路线无法规划时抛出。"""


@dataclass(frozen=True)
class GridSpec:
    """房间与网格参数。"""

    rows: int = 7
    columns: int = 9
    width_m: float = 8.23
    height_m: float = 6.95

    def __post_init__(self) -> None:
        if not (1 <= self.rows <= 9 and 1 <= self.columns <= 9):
            raise PlanError("行列数必须在 1..9 之间（受 P<行><列> 两位编号限制）")
        if self.width_m <= 0 or self.height_m <= 0:
            raise PlanError("房间尺寸必须为正数")

    # ---- 几何换算 ----
    @property
    def cell_width_m(self) -> float:
        return self.width_m / self.columns

    @property
    def cell_height_m(self) -> float:
        return self.height_m / self.rows

    def center(self, cell: Cell) -> tuple[float, float]:
        """格子中心点的米制坐标。"""
        row, col = cell
        return ((col - 0.5) * self.cell_width_m, (row - 0.5) * self.cell_height_m)

    def cell_bounds(self, cell: Cell) -> tuple[float, float, float, float]:
        """格子覆盖的米制矩形：(x1, y1, x2, y2)，原点在房间左上角。"""
        row, col = cell
        return (
            (col - 1) * self.cell_width_m,
            (row - 1) * self.cell_height_m,
            col * self.cell_width_m,
            row * self.cell_height_m,
        )

    def nearest_cell(self, x_m: float, y_m: float) -> Cell:
        """把米制坐标映射到最近的格子（越界会被夹到边缘）。"""
        col = min(self.columns, max(1, int(x_m // self.cell_width_m) + 1))
        row = min(self.rows, max(1, int(y_m // self.cell_height_m) + 1))
        return row, col

    # ---- 编号 ----
    def label(self, cell: Cell) -> str:
        return f"P{cell[0]}{cell[1]}"

    def parse(self, text: str) -> Cell:
        t = text.strip().upper()
        if len(t) != 3 or t[0] != "P" or not t[1:].isdigit():
            raise PlanError(f"无效点位编号：{text!r}，应形如 P22")
        cell = int(t[1]), int(t[2])
        self.ensure_inside(cell)
        return cell

    def ensure_inside(self, cell: Cell) -> None:
        row, col = cell
        if not (1 <= row <= self.rows and 1 <= col <= self.columns):
            raise PlanError(f"点位越界：{self.label(cell)}，网格为 {self.rows} 行 × {self.columns} 列")

    def all_cells(self) -> list[Cell]:
        return [(r, c) for r in range(1, self.rows + 1) for c in range(1, self.columns + 1)]


@dataclass
class Stop:
    """一个测量点及其到达路径。"""

    cell: Cell
    via: list[Cell] = field(default_factory=list)

    @property
    def point_id(self) -> str:
        return f"P{self.cell[0]}{self.cell[1]}"


def bfs_path(
    start: Cell,
    end: Cell,
    blocked: Iterable[Cell],
    spec: GridSpec,
) -> list[Cell]:
    """四邻域 BFS 求最短路径，返回不含起点、含终点的格子序列。"""
    blocked_set = set(blocked)
    if start in blocked_set or end in blocked_set:
        raise PlanError(f"起点或终点被标记为禁行：{spec.label(start)} -> {spec.label(end)}")
    if start == end:
        return []

    queue: deque[Cell] = deque([start])
    previous: dict[Cell, Cell | None] = {start: None}
    while queue:
        p = queue.popleft()
        r, c = p
        for q in ((r, c + 1), (r, c - 1), (r + 1, c), (r - 1, c)):
            if not (1 <= q[0] <= spec.rows and 1 <= q[1] <= spec.columns):
                continue
            if q in blocked_set or q in previous:
                continue
            previous[q] = p
            if q == end:
                path: list[Cell] = []
                cur: Cell | None = q
                while cur is not None and previous[cur] is not None:
                    path.append(cur)
                    cur = previous[cur]
                return path[::-1]
            queue.append(q)
    raise PlanError(
        f"无法连通：{spec.label(start)} -> {spec.label(end)}，请检查禁行格是否切断了房间"
    )


def make_serpentine_plan(
    spec: GridSpec,
    ap: Cell,
    blocked: Sequence[Cell] = (),
    start: Cell = (1, 1),
) -> list[Stop]:
    """生成蛇形覆盖路线：奇数行从左到右，偶数行从右到左。

    路由器所在格与额外禁行格会被跳过，但仍是不可通行区域，
    绕行由 BFS 处理，绕行途中不采样。
    """
    spec.ensure_inside(ap)
    spec.ensure_inside(start)
    for b in blocked:
        spec.ensure_inside(b)

    excluded = {ap, *blocked}
    if start in excluded:
        raise PlanError(f"起点 {spec.label(start)} 被标记为禁行，无法开始")

    plan: list[Stop] = []
    current = start
    for row in range(1, spec.rows + 1):
        columns = range(1, spec.columns + 1) if row % 2 else range(spec.columns, 0, -1)
        for col in columns:
            target = (row, col)
            if target in excluded:
                continue
            if target == current:
                plan.append(Stop(cell=target, via=[]))
                continue
            via = bfs_path(current, target, excluded, spec)
            plan.append(Stop(cell=target, via=via))
            current = target
    if not plan:
        raise PlanError("没有可测量点位")
    return plan


def plan_summary(spec: GridSpec, plan: Sequence[Stop], ap: Cell) -> dict:
    """生成便于写日志/JSON 的摘要。"""
    return {
        "rows": spec.rows,
        "columns": spec.columns,
        "room_width_m": spec.width_m,
        "room_height_m": spec.height_m,
        "cell_width_m": round(spec.cell_width_m, 6),
        "cell_height_m": round(spec.cell_height_m, 6),
        "ap_point": spec.label(ap),
        "ap_center_m": [round(v, 4) for v in spec.center(ap)],
        "total_cells": spec.rows * spec.columns,
        "measurement_stops": len(plan),
        "skipped_cells": spec.rows * spec.columns - len(plan),
        "total_moves": sum(len(s.via) for s in plan),
    }


def plan_to_dict(spec: GridSpec, plan: Sequence[Stop], ap: Cell) -> dict:
    return {
        "summary": plan_summary(spec, plan, ap),
        "plan": [
            {
                "point_id": s.point_id,
                "row": s.cell[0],
                "column": s.cell[1],
                "x_m": round(spec.center(s.cell)[0], 4),
                "y_m": round(spec.center(s.cell)[1], 4),
                "via": [spec.label(c) for c in s.via],
            }
            for s in plan
        ],
    }
