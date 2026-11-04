"""离线测试：不连接任何硬件，验证路线规划与采集落盘逻辑。

运行：python tests/test_offline.py
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))

from robot_client import WifiReading
from route_planner import GridSpec, PlanError, make_serpentine_plan, plan_summary
from survey_runner import SurveyOptions, run_survey

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  {detail}")


def test_grid() -> None:
    print("[grid]")
    spec = GridSpec()
    check("默认网格 7x9", (spec.rows, spec.columns) == (7, 9))
    check("P22 解析", spec.parse("p22") == (2, 2))
    check("P68 解析", spec.parse("P68") == (6, 8))
    check("越界拒绝", _raises(lambda: spec.parse("P88")))
    check("非法编号拒绝", _raises(lambda: spec.parse("X22")))
    x, y = spec.center((2, 2))
    check("P22 中心坐标", abs(x - 1.5 * spec.cell_width_m) < 1e-9 and abs(y - 1.5 * spec.cell_height_m) < 1e-9)
    check("米制回映射", spec.nearest_cell(x, y) == (2, 2))
    check("角落映射", spec.nearest_cell(0.0, 0.0) == (1, 1))


def test_plan() -> None:
    print("[plan]")
    spec = GridSpec()
    plan = make_serpentine_plan(spec, (2, 2))
    ids = [s.point_id for s in plan]
    check("P22 场景共 62 个测量点", len(plan) == 62, f"实际 {len(plan)}")
    check("跳过 P22", "P22" not in ids)
    check("首点 P11", ids[0] == "P11")
    check("次点 P12", ids[1] == "P12")
    check("P23 不经过 P22", _no_p22(plan, spec))
    summary = plan_summary(spec, plan, (2, 2))
    check("摘要测量点数", summary["measurement_stops"] == 62)
    check("摘要跳过格数", summary["skipped_cells"] == 1)
    check("AP 坐标写入摘要", summary["ap_point"] == "P22")

    # 障碍格
    plan2 = make_serpentine_plan(spec, (2, 2), blocked=[(3, 3), (3, 4)])
    check("额外障碍后为 60 点", len(plan2) == 60, f"实际 {len(plan2)}")
    check("障碍格未被测量", not any(s.cell in {(3, 3), (3, 4)} for s in plan2))

    # 起点可配置
    plan3 = make_serpentine_plan(spec, (2, 2), start=(1, 1))
    check("起点配置生效", plan3[0].point_id == "P11")

    # 起点被禁行
    check("起点禁行报错", _raises(lambda: make_serpentine_plan(spec, (2, 2), blocked=[(1, 1)])))

    # 走廊阻断
    blocked_wall = [(r, 5) for r in range(1, 8)]

    def wall_plan():
        return make_serpentine_plan(GridSpec(7, 9), (7, 9), blocked=blocked_wall)

    check("整列阻断时报错", _raises(wall_plan))

    # 4 位编号限制
    check("超过 9 行拒绝", _raises(lambda: GridSpec(rows=10)))


def test_runner() -> None:
    print("[runner]")
    spec = GridSpec(rows=3, columns=3, width_m=3.0, height_m=3.0)
    plan = make_serpentine_plan(spec, (2, 2))
    robot = FakeRobot()
    with tempfile.TemporaryDirectory() as tmp:
        options = SurveyOptions(samples_per_point=3, settle_seconds=0.0, interval_seconds=0.2)
        result = run_survey(robot, spec, plan, (2, 2), Path(tmp), options, start=(1, 1))
        out = result.output_dir
        check("输出目录存在", out.is_dir())
        check("完成点数 = 8", len(result.completed) == 8, f"实际 {len(result.completed)}")
        check("读数条数 = 24", result.readings == 24, f"实际 {result.readings}")
        summary_rows = list(csv.DictReader((out / "rss_summary_autonomous.csv").open(encoding="utf-8-sig")))
        check("summary 行数 = 8", len(summary_rows) == 8)
        raw_rows = list(csv.DictReader((out / "rss_raw_autonomous.csv").open(encoding="utf-8-sig")))
        check("raw 行数 = 24", len(raw_rows) == 24)
        meta = json.loads((out / "experiment.json").read_text(encoding="utf-8"))
        check("experiment 状态 finished", meta["status"] == "finished")
        check("记录的 AP 点位", meta["summary"]["ap_point"] == "P22")
        check("arm 已调用", robot.armed)
        check("stop 已调用", robot.stopped)
        check("未尝试斜向移动", all(abs(m[0]) < 1e-9 or abs(m[1]) < 1e-9 for m in robot.moves))

        # 中途故障应保留已有点
        robot2 = FakeRobot(fail_after_readings=7)
        try:
            run_survey(robot2, spec, plan, (2, 2), Path(tmp), options, start=(1, 1))
            check("故障应抛异常", False)
        except RuntimeError:
            check("故障抛异常", True)
        aborted = [p for p in Path(tmp).iterdir() if p.is_dir()]
        meta2 = json.loads((aborted[0] / "experiment.json").read_text(encoding="utf-8"))
        check("中断状态记录", meta2["status"] == "aborted")
        check("中断保留已完成点", len(meta2["completed_points"]) >= 1)


class FakeRobot:
    def __init__(self, fail_after_readings: int | None = None) -> None:
        self.armed = False
        self.stopped = False
        self.moves: list[tuple[float, float]] = []
        self.count = 0
        self.fail_after_readings = fail_after_readings

    def arm(self) -> None:
        self.armed = True

    def wait(self, seconds: float) -> None:
        pass

    def move(self, dx: float, dy: float, speed: float) -> None:
        self.moves.append((dx, dy))

    def read_wifi(self) -> WifiReading:
        if self.fail_after_readings is not None and self.count >= self.fail_after_readings:
            raise RuntimeError("simulated link failure")
        self.count += 1
        return WifiReading(rssi_dbm=-40 - (self.count % 5), ssid="Xiaomi_2D1F_2.4G", bssid="AA:BB:CC:DD:EE:FF", channel=11, ip="192.168.31.12")

    def stop(self) -> None:
        self.stopped = True


def test_offline_steps() -> None:
    """离线自主采集：逐格位移序列必须连续、单轴，且采样点数量正确。"""
    from survey_offline import build_steps

    spec = GridSpec()
    ap = spec.parse("P68")
    plan = make_serpentine_plan(spec, ap)
    steps = build_steps(spec, plan, (1, 1), 1.0, 1.0)

    check("离线：采样步数等于测量点数", sum(s.sample for s in steps) == len(plan),
          f"{sum(s.sample for s in steps)} != {len(plan)}")
    check("离线：所有位移都是单轴", all(s.dx == 0 or s.dy == 0 for s in steps))
    check("离线：单步位移不超过 1.10 m", all(abs(s.dx) + abs(s.dy) <= 1.10 for s in steps))
    check("离线：首步是起点原地采样",
          steps[0].label == "P11" and steps[0].dx == 0 and steps[0].dy == 0,
          f"{steps[0].label} {steps[0].dx} {steps[0].dy}")

    x, y = spec.center((1, 1))
    for s in steps:
        x += s.dx
        y += s.dy
    last = [s for s in steps if s.sample][-1]
    lx, ly = spec.center(spec.parse(last.label))
    check("离线：累计位移与末点位一致", abs(x - lx) < 1e-6 and abs(y - ly) < 1e-6,
          f"({x:.4f},{y:.4f}) vs ({lx:.4f},{ly:.4f})")

    check("离线：途经格都带实际位移",
          all(abs(s.dx) + abs(s.dy) > 0.05 for s in steps if not s.sample))


def _no_p22(plan, spec: GridSpec) -> bool:
    for stop in plan:
        for cell in stop.via:
            if cell == (2, 2):
                return False
    return True


def _raises(fn) -> bool:
    try:
        fn()
    except (PlanError, ValueError):
        return True
    return False


def main() -> int:
    test_grid()
    test_plan()
    test_runner()
    test_offline_steps()
    print()
    print(f"通过 {PASSED} 项")
    if FAILED:
        print("失败项：")
        for item in FAILED:
            print("  -", item)
        return 1
    print("全部离线测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
