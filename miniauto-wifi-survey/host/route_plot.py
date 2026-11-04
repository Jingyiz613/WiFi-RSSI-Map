"""路线预览绘图：把房间网格、路由器位置、障碍格和行驶路线画成一张图。"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, Rectangle

from layout import Furniture
from route_planner import Cell, GridSpec, Stop

_CJK_CANDIDATES = ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC")


def _setup_cjk_font() -> None:
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in _CJK_CANDIDATES:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            plt.rcParams["axes.unicode_minus"] = False
            return
    # 没有中文字体时退回英文标注，避免出现方框
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]


_setup_cjk_font()


def draw_plan(
    spec: GridSpec,
    plan: Sequence[Stop],
    ap: Cell,
    blocked: Sequence[Cell] = (),
    start: Cell = (1, 1),
    out_path: str | Path = "route_preview.png",
    title: str | None = None,
    furniture: Sequence[Furniture] = (),
    clearance_m: float = 0.10,
) -> Path:
    """绘制并保存路线预览图，返回实际写入的路径。"""
    blocked_set = set(blocked)
    furniture_cells: set[Cell] = set()
    for item in furniture:
        furniture_cells |= item.cells(spec, clearance_m)
    fig_w = max(7.0, spec.columns * 0.95)
    fig_h = max(5.5, spec.rows * 0.9)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=130)

    # 网格底
    for row in range(1, spec.rows + 1):
        for col in range(1, spec.columns + 1):
            cell = (row, col)
            if cell == ap:
                color = "#ffd6d6"
            elif cell in furniture_cells:
                color = "#d7c8b8"
            elif cell in blocked_set:
                color = "#d9d9d9"
            else:
                color = "#f7fbff"
            ax.add_patch(
                Rectangle((col - 1, row - 1), 1, 1, facecolor=color, edgecolor="#b8c4ce", linewidth=0.8, zorder=1)
            )
            # 点位编号
            ax.text(
                col - 0.5,
                row - 0.82,
                f"P{row}{col}",
                ha="center",
                va="center",
                fontsize=6.2,
                color="#7a8a99",
                zorder=3,
            )

    # 行驶路线（按 via 顺序连成折线）
    points: list[tuple[float, float]] = [(start[1] - 0.5, start[0] - 0.5)]
    for stop in plan:
        for cell in stop.via:
            points.append((cell[1] - 0.5, cell[0] - 0.5))
        points.append((stop.cell[1] - 0.5, stop.cell[0] - 0.5))
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    ax.plot(xs, ys, color="#1f77b4", linewidth=1.3, alpha=0.75, zorder=2)

    # 方向箭头（每隔若干段画一个，避免太密）
    step = max(1, len(points) // 24)
    for i in range(0, len(points) - 1, step):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        if (x0, y0) == (x1, y1):
            continue
        ax.add_patch(
            FancyArrowPatch(
                (x0, y0),
                (x1, y1),
                arrowstyle="-|>",
                mutation_scale=9,
                color="#1f77b4",
                alpha=0.55,
                linewidth=0.8,
                zorder=2,
            )
        )

    # 测量点
    mx = [s.cell[1] - 0.5 for s in plan]
    my = [s.cell[0] - 0.5 for s in plan]
    ax.scatter(mx, my, s=16, c="#1f77b4", zorder=4, label="Measurement stops")
    ax.scatter([start[1] - 0.5], [start[0] - 0.5], s=70, c="#2ca02c", marker="s", zorder=5, label="Start")
    ax.scatter([ap[1] - 0.5], [ap[0] - 0.5], s=190, c="#d62728", marker="*", zorder=6, label="Router / AP")

    # 家具轮廓与名称（米制 → 格子坐标）
    cw, ch = spec.cell_width_m, spec.cell_height_m
    for item in furniture:
        fx = item.x1 / cw
        fy = item.y1 / ch
        fw = item.width_m / cw
        fh = item.height_m / ch
        ax.add_patch(
            Rectangle(
                (fx, fy),
                fw,
                fh,
                facecolor="#5d4037",
                alpha=0.18,
                edgecolor="#4e342e",
                linewidth=1.8,
                zorder=7,
            )
        )
        ax.text(
            fx + fw / 2,
            fy + fh / 2,
            item.name,
            ha="center",
            va="center",
            fontsize=8.5,
            color="#3e2723",
            fontweight="bold",
            zorder=8,
            bbox=dict(boxstyle="round,pad=0.18", facecolor="white", edgecolor="none", alpha=0.8),
        )

    # 障碍格标叉
    for cell in blocked_set:
        ax.plot(
            [cell[1] - 0.82, cell[1] - 0.18],
            [cell[0] - 0.82, cell[0] - 0.18],
            color="#888888",
            linewidth=1.0,
            zorder=5,
        )
        ax.plot(
            [cell[1] - 0.82, cell[1] - 0.18],
            [cell[0] - 0.18, cell[0] - 0.82],
            color="#888888",
            linewidth=1.0,
            zorder=5,
        )

    ax.set_xlim(0, spec.columns)
    ax.set_ylim(spec.rows, 0)  # 行号向下增长，与房间俯视图一致
    ax.set_aspect("equal")
    ax.set_xticks([i + 0.5 for i in range(spec.columns)])
    ax.set_xticklabels([str(i + 1) for i in range(spec.columns)], fontsize=7)
    ax.set_yticks([i + 0.5 for i in range(spec.rows)])
    ax.set_yticklabels([str(i + 1) for i in range(spec.rows)], fontsize=7)
    ax.set_xlabel(f"Column (cell {spec.cell_width_m:.3f} m)")
    ax.set_ylabel(f"Row (cell {spec.cell_height_m:.3f} m)")

    furniture_note = f" | {len(furniture)} furniture" if furniture else ""
    default_title = (
        f"Route preview | AP={spec.label(ap)} | {len(plan)} stops | "
        f"{spec.width_m:.2f} m x {spec.height_m:.2f} m{furniture_note}"
    )
    ax.set_title(title or default_title, fontsize=10)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=3, fontsize=7.5, framealpha=0.9)
    ax.grid(False)
    fig.tight_layout()

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out
