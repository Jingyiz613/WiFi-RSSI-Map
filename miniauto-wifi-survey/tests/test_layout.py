"""家具布局测试：米制矩形、格子判定、旧格式兼容、冲突校验、参与路线规划。

运行：python tests/test_layout.py
也可以用 pytest。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))

from layout import Furniture, Layout, blocked_from_layout, describe_layout
from route_planner import GridSpec, PlanError, make_serpentine_plan

SPEC = GridSpec()  # 7 行 × 9 列，8.23 × 6.95 m → 格距 0.9144 × 0.9929


def test_furniture_normalizes_corners() -> None:
    item = Furniture("沙发", x1=3.0, y1=1.5, x2=1.0, y2=0.5)
    assert (item.x1, item.x2) == (1.0, 3.0)
    assert (item.y1, item.y2) == (0.5, 1.5)
    assert round(item.width_m, 3) == 2.0
    assert round(item.height_m, 3) == 1.0
    assert round(item.area_m2, 3) == 2.0


def test_point_distance() -> None:
    item = Furniture("桌", 1.0, 1.0, 2.0, 2.0)
    assert item.distance_to(1.5, 1.5) == 0.0
    assert round(item.distance_to(3.0, 1.5), 3) == 1.0
    assert round(item.distance_to(3.0, 3.0), 3) == round(2 ** 0.5, 3)


def test_cell_center_blocking_uses_clearance() -> None:
    """家具只压到格子边缘时不应整格作废。"""
    cw, ch = SPEC.cell_width_m, SPEC.cell_height_m  # 0.9144, 0.9929
    # 左上角 20×20 cm：离 P11 中心约 39 cm，远大于 10 cm 安全间距
    small = Furniture("角柜", 0.0, 0.0, 0.20, 0.20)
    assert (1, 1) not in small.cells(SPEC, 0.10)
    # 覆盖到中心附近（45×45 cm）：距离约 4.6 cm，应判为不可用
    big = Furniture("柜", 0.0, 0.0, 0.45, 0.45)
    assert (1, 1) in big.cells(SPEC, 0.10)


def test_clearance_zero_only_blocks_center_inside() -> None:
    cw, ch = SPEC.cell_width_m, SPEC.cell_height_m
    item = Furniture("条", 0.0, 0.0, cw * 0.4, ch * 0.4)  # 只盖住左上角，不碰中心
    assert (1, 1) not in item.cells(SPEC, 0.0)
    item2 = Furniture("条", 0.0, 0.0, cw * 0.6, ch * 0.6)  # 盖到中心
    assert (1, 1) in item2.cells(SPEC, 0.0)


def test_furniture_clips_to_room() -> None:
    clipped = Furniture("床", -1.0, -1.0, 1.0, 1.0).clipped(SPEC)
    assert clipped is not None
    assert (clipped.x1, clipped.y1) == (0.0, 0.0)
    assert (clipped.x2, clipped.y2) == (1.0, 1.0)
    assert Furniture("越界", 20.0, 20.0, 25.0, 25.0).clipped(SPEC) is None


def test_layout_blocked_merges_furniture_and_manual() -> None:
    lay = Layout(spec=SPEC, ap=(7, 8), start=(1, 1))
    lay.add_furniture(Furniture("沙发", 2.0, 2.0, 4.0, 3.0))
    lay.manual_blocked.add((5, 5))
    assert (5, 5) in lay.blocked()
    assert lay.stats()["manual_cells"] == 1
    assert lay.stats()["furniture_cells"] >= 1
    assert lay.blocked() >= lay.furniture_cells()


def test_layout_flags_ap_inside_furniture() -> None:
    lay = Layout(spec=SPEC, ap=(2, 2), start=(1, 1))
    cx, cy = SPEC.center((2, 2))
    lay.add_furniture(Furniture("桌子", cx - 0.3, cy - 0.3, cx + 0.3, cy + 0.3))
    problems = lay.validate()
    assert problems and "路由器" in problems[0]


def test_layout_flags_start_inside_furniture() -> None:
    lay = Layout(spec=SPEC, ap=(7, 8), start=(1, 1))
    lay.manual_blocked.add((1, 1))
    problems = lay.validate()
    assert problems and "起点" in problems[0]


def test_layout_flags_enclosed_start() -> None:
    lay = Layout(spec=SPEC, ap=(7, 8), start=(1, 1))
    lay.manual_blocked.add((1, 2))
    lay.manual_blocked.add((2, 1))
    problems = lay.validate()
    assert problems and "开不进去" in problems[0]


def test_layout_roundtrip(tmp_path: Path) -> None:
    lay = Layout(spec=GridSpec(rows=6, columns=8, width_m=7.0, height_m=5.5), ap=(6, 8), start=(1, 1))
    lay.add_furniture(Furniture("床", 0.8, 0.5, 2.6, 1.9))
    lay.add_furniture(Furniture("书桌", 3.1, 3.7, 4.4, 4.2))
    lay.manual_blocked.add((4, 4))
    lay.clearance_m = 0.15

    path = lay.save(tmp_path / "layout.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert data["ap"] == "P68"
    assert data["clearance_m"] == 0.15
    assert len(data["furniture"]) == 2
    assert "x1" in data["furniture"][0]

    back = Layout.load(path)
    assert back.spec == lay.spec
    assert back.ap == lay.ap
    assert back.start == lay.start
    assert back.clearance_m == 0.15
    assert [f.name for f in back.furniture] == ["床", "书桌"]
    assert round(back.furniture[0].x2, 3) == 2.6
    assert back.manual_blocked == {(4, 4)}


def test_layout_reads_v1_row_column_format(tmp_path: Path) -> None:
    """旧版按行列保存的布局要能自动换算成米制。"""
    spec = GridSpec()
    payload = {
        "version": 1,
        "room": {"rows": 7, "columns": 9, "width_m": 8.23, "height_m": 6.95},
        "ap": "P68",
        "start": "P11",
        "furniture": [{"name": "沙发", "row1": 2, "col1": 3, "row2": 3, "col2": 4}],
        "manual_blocked": ["P44"],
    }
    path = tmp_path / "old.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    lay = Layout.load(path)
    item = lay.furniture[0]
    assert round(item.x1, 3) == round(2 * spec.cell_width_m, 3)
    assert round(item.y1, 3) == round(1 * spec.cell_height_m, 3)
    assert round(item.x2, 3) == round(4 * spec.cell_width_m, 3)
    assert round(item.y2, 3) == round(3 * spec.cell_height_m, 3)
    assert lay.manual_blocked == {(4, 4)}


def test_layout_load_tolerates_garbage(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("not json", encoding="utf-8")
    assert Layout.load_or_default(path) == Layout()


def test_layout_duplicate_names_get_suffix() -> None:
    lay = Layout(spec=SPEC, ap=(7, 8), start=(1, 1))
    lay.add_furniture(Furniture("沙发", 0.1, 0.1, 0.6, 0.6))
    second = lay.add_furniture(Furniture("沙发", 1.5, 1.5, 2.0, 2.0))
    assert second.name == "沙发2"


def test_plan_avoids_furniture() -> None:
    lay = Layout(spec=SPEC, ap=(7, 8), start=(1, 1))
    lay.add_furniture(Furniture("沙发", 1.6, 1.4, 4.2, 2.4))
    plan = make_serpentine_plan(lay.spec, lay.ap, sorted(lay.blocked()), lay.start)
    blocked = lay.blocked()

    assert {stop.cell for stop in plan}.isdisjoint(blocked)
    for stop in plan:
        assert all(cell not in blocked for cell in stop.via), f"{stop.point_id} 途经家具格"
    assert len(plan) == len(lay.usable_cells()) - 1  # 路由器格不采样


def test_cell_bounds_matches_center() -> None:
    x1, y1, x2, y2 = SPEC.cell_bounds((3, 5))
    cx, cy = SPEC.center((3, 5))
    assert round((x1 + x2) / 2, 6) == round(cx, 6)
    assert round((y1 + y2) / 2, 6) == round(cy, 6)
    assert round(x2 - x1, 6) == round(SPEC.cell_width_m, 6)


def test_blocked_from_layout(tmp_path: Path) -> None:
    lay = Layout(spec=SPEC, ap=(7, 8), start=(1, 1))
    lay.add_furniture(Furniture("桌", 1.0, 1.0, 1.6, 1.6))
    path = lay.save(tmp_path / "l.json")

    spec, ap, start, blocked = blocked_from_layout(path)
    assert (spec.rows, spec.columns) == (7, 9)
    assert ap == (7, 8) and start == (1, 1)
    assert blocked
    assert "桌" in describe_layout(lay)


if __name__ == "__main__":
    import tempfile

    failures: list[str] = []
    tests = [(name, obj) for name, obj in sorted(globals().items()) if name.startswith("test_")]
    for name, func in tests:
        try:
            if "tmp_path" in func.__code__.co_varnames[: func.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as tmp:
                    func(Path(tmp))
            else:
                func()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failures.append(f"{name}: {exc}")
            print(f"  FAIL  {name}  {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} 通过")
    raise SystemExit(1 if failures else 0)
