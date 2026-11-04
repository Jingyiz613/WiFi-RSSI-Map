"""离线自主采集客户端：电脑只负责下发计划、收取结果，中间可以完全断开。

典型流程（全部走 USB，不需要互联网，也不需要连被测 Wi-Fi）：

    # 1. 插上 USB，规划并下发任务（不会动）
    python host/survey_offline.py plan --ap P68

    # 2. 开始执行，看到 running 后拔掉 USB，小车自己跑
    python host/survey_offline.py run --ap P68 --detach

    # 3. 跑完重新插上 USB，取回结果并生成 CSV
    python host/survey_offline.py dump --ap P68

可选：
    python host/survey_offline.py status       查询当前状态
    python host/survey_offline.py abort        中止并立即停车
    python host/survey_offline.py dump --ap P68 --no-wait
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from console import fix_console_encoding
from layout import Layout
from route_planner import GridSpec, PlanError, make_serpentine_plan, plan_to_dict

BAUD = 115200
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "output"


class LinkError(RuntimeError):
    """串口通信失败。"""


# ---------------------------------------------------------------- 串口


def pick_port(explicit: str) -> str:
    if explicit:
        return explicit
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError as exc:
        raise LinkError("缺少 pyserial，请先安装：pip install pyserial") from exc

    ports = list(list_ports.comports())
    if not ports:
        raise LinkError("没有检测到串口，请确认小车已用 USB 数据线连接")
    import re

    bridges = [p for p in ports if re.search(r"CH34|CP21|FTDI|USB-SERIAL|Silicon|UART", p.description or "", re.I)]
    if len(bridges) == 1:
        print(f"自动识别串口：{bridges[0].device} ({bridges[0].description})")
        return bridges[0].device
    if len(ports) == 1:
        print(f"自动识别串口：{ports[0].device} ({ports[0].description})")
        return ports[0].device

    print("检测到多个串口：")
    for i, p in enumerate(ports, 1):
        print(f"  [{i}] {p.device}  {p.description}")
    choice = input(f"请选择编号 (1-{len(ports)}): ").strip()
    try:
        idx = int(choice) - 1
    except ValueError as exc:
        raise LinkError("输入无效") from exc
    if not 0 <= idx < len(ports):
        raise LinkError("编号超出范围")
    return ports[idx].device


class Car:
    def __init__(self, port: str) -> None:
        try:
            import serial  # type: ignore
        except ImportError as exc:
            raise LinkError("缺少 pyserial，请先安装：pip install pyserial") from exc
        self._ser = serial.Serial(port, BAUD, timeout=0.2)
        self._buf = b""
        time.sleep(1.2)
        self._ser.reset_input_buffer()

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass

    def send(self, line: str) -> None:
        self._ser.write((line + "\n").encode("ascii", "replace"))
        self._ser.flush()

    def readline(self, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                return line.decode("utf-8", "replace").strip()
            chunk = self._ser.read(256)
            if chunk:
                self._buf += chunk
        return None

    def wait(self, prefix: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.readline(max(0.05, deadline - time.monotonic()))
            if line and line.startswith(prefix):
                return line
        raise LinkError(f"等待 {prefix} 超时")


# ---------------------------------------------------------------- 计划


@dataclass
class StepOut:
    label: str
    dx: float
    dy: float
    sample: int


def build_steps(spec: GridSpec, plan, start: tuple[int, int], x_scale: float, y_scale: float) -> list[StepOut]:
    """把（点位 + 途经格）展开成固件能执行的逐格位移序列。"""
    steps: list[StepOut] = []
    current = start
    for stop in plan:
        for cell in stop.via:
            dr = cell[0] - current[0]
            dc = cell[1] - current[1]
            if abs(dr) + abs(dc) != 1:
                raise PlanError(f"途经格不连续：{spec.label(current)} -> {spec.label(cell)}")
            steps.append(
                StepOut(
                    label=spec.label(cell),
                    dx=dc * spec.cell_width_m * x_scale,
                    dy=dr * spec.cell_height_m * y_scale,
                    sample=0,
                )
            )
            current = cell
        if stop.cell != current:
            raise PlanError(f"路线与点位不一致：{spec.label(current)} -> {stop.point_id}")
        steps.append(StepOut(label=stop.point_id, dx=0.0, dy=0.0, sample=1))
    return steps


def make_plan(args) -> tuple[GridSpec, list, list[StepOut]]:
    spec = GridSpec(rows=args.rows, columns=args.columns, width_m=args.width, height_m=args.height)
    ap = spec.parse(args.ap)
    blocked = [spec.parse(b) for b in args.blocked] if args.blocked else []
    start = spec.parse(args.start)
    plan = make_serpentine_plan(spec, ap, blocked, start)
    steps = build_steps(spec, plan, start, args.x_scale, args.y_scale)
    return spec, plan, steps


# ---------------------------------------------------------------- 动作


def cmd_plan(car: Car, args) -> int:
    spec, plan, steps = make_plan(args)
    print(f"网格 {spec.rows}×{spec.columns}，{len(plan)} 个采样点，{len(steps)} 步")
    print(f"路由器 {spec.label(spec.parse(args.ap))}，起点 {args.start}")

    car.send("SURVEY_CLEAR")
    car.wait("SURVEY_ACK,CLEAR", 5)

    car.send(f"SURVEY_BEGIN {args.speed:.3f} {int(args.dwell * 1000)} {args.samples} {len(steps)}")
    car.wait("SURVEY_ACK,BEGIN", 5)
    print(f"已下发参数：速度 {args.speed} m/s，静置 {args.dwell}s，每点 {args.samples} 次")

    for i, s in enumerate(steps, 1):
        car.send(f"SURVEY_STEP {s.label} {s.dx:.4f} {s.dy:.4f} {s.sample}")
        car.wait("SURVEY_ACK,STEP", 5)
        if i % 20 == 0 or i == len(steps):
            print(f"  已下发 {i}/{len(steps)} 步")

    car.send("SURVEY_END")
    car.wait("SURVEY_ACK,END", 15)
    print("计划已保存到小车（此时还不会动）")

    meta = {
        "created_local": datetime.now().astimezone().isoformat(),
        "mode": "offline_autonomous",
        "settings": {
            "rows": spec.rows,
            "columns": spec.columns,
            "room_width_m": spec.width_m,
            "room_height_m": spec.height_m,
            "ap_point": args.ap.upper(),
            "start_point": args.start.upper(),
            "speed_mps": args.speed,
            "dwell_seconds": args.dwell,
            "samples_per_point": args.samples,
            "x_scale": args.x_scale,
            "y_scale": args.y_scale,
        },
        "steps": len(steps),
        **plan_to_dict(spec, plan, spec.parse(args.ap)),
    }
    out_dir = Path(args.output) / f"plan_{spec.label(spec.parse(args.ap))}_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"计划副本：{out_dir / 'plan.json'}")
    return 0


def cmd_run(car: Car, args) -> int:
    if args.plan_first:
        cmd_plan(car, args)
    car.send("SURVEY_RUN")
    line = car.wait("SURVEY_STATE", 10)
    print(line)
    if "running" not in line:
        print("启动失败，请检查计划是否已下发完整")
        return 1

    if args.detach:
        print()
        print("小车已开始按计划行驶。现在可以拔掉 USB（小车靠电池供电）。")
        print("跑完后重新插上 USB，执行：")
        print(f"  python host/survey_offline.py dump --ap {args.ap} --port {args.port or 'COMx'}")
        return 0

    print("监控中（Ctrl+C 停止监控，不影响小车继续跑）…")
    done = 0
    try:
        while True:
            line = car.readline(5.0)
            if not line:
                continue
            if line.startswith("SURVEY_STATE,"):
                parts = line.split(",")
                if len(parts) >= 4 and parts[1] in ("done", "error", "aborted"):
                    print(f"结束：{parts[1]}，完成 {parts[2]}/{parts[3]}")
                    return 0 if parts[1] == "done" else 1
                if len(parts) >= 4 and parts[2].isdigit():
                    d = int(parts[2])
                    if d != done and (d % 5 == 0 or d == 1):
                        done = d
                        print(f"  {parts[2]}/{parts[3]}  {parts[4] if len(parts) > 4 else ''}")
    except KeyboardInterrupt:
        print("\n已停止监控；小车仍在按计划行驶。")
        return 0


def cmd_calibrate(car: Car, args) -> int:
    """只走一步，用来测方向与校正距离，不需要连被测 WiFi。"""
    dist = abs(args.distance)
    if not 0.05 <= dist <= 1.10:
        raise LinkError("测试距离应在 0.05..1.10 m")
    dx = dist if args.axis == "x" else 0.0
    dy = dist if args.axis == "y" else 0.0
    direction = "向右" if args.axis == "x" else "向前"

    car.send("SURVEY_CLEAR")
    car.wait("SURVEY_ACK,CLEAR", 5)
    car.send(f"SURVEY_BEGIN {args.speed:.3f} 500 1 1")
    car.wait("SURVEY_ACK,BEGIN", 5)
    car.send(f"SURVEY_STEP T1 {dx:.4f} {dy:.4f} 1")
    car.wait("SURVEY_ACK,STEP", 5)
    car.send("SURVEY_END")
    car.wait("SURVEY_ACK,END", 15)

    print(f"即将让小车{direction}走 {dist:.3f} m。")
    print("确认前方 1.5 m 内没有障碍物后按回车；Ctrl+C 取消。")
    try:
        input()
    except (KeyboardInterrupt, EOFError):
        print("已取消")
        return 1

    car.send("SURVEY_RUN")
    line = car.wait("SURVEY_STATE", 10)
    print(line)
    if "running" not in line:
        return 1

    while True:
        line = car.readline(5.0)
        if not line:
            continue
        if line.startswith("SURVEY_STATE,"):
            parts = line.split(",")
            if len(parts) >= 2 and parts[1] in ("done", "error", "aborted"):
                print(f"结束：{parts[1]}")
                break

    print()
    print("用卷尺量轮子实际的位移，然后：")
    print("  新系数 = 原系数 × 目标距离 ÷ 实测距离")
    print(f"  本次目标 {dist:.3f} m，若实测 {dist * 0.93:.3f} m，新系数 ≈ 1.075")
    return 0


def cmd_status(car: Car, args) -> int:
    car.send("SURVEY_STATUS")
    line = car.wait("SURVEY_STATE", 5)
    print(line)
    return 0


def cmd_abort(car: Car, args) -> int:
    car.send("SURVEY_ABORT")
    car.wait("SURVEY_ACK,ABORT", 5)
    print("已发送中止，小车应立即停车")
    return 0


def cmd_dump(car: Car, args) -> int:
    wifi = {}
    car.send("STATUS")
    for _ in range(30):
        line = car.readline(1.0)
        if not line:
            break
        if line.startswith("{"):
            try:
                wifi = json.loads(line)
            except json.JSONDecodeError:
                pass
            break

    car.send("SURVEY_DUMP")
    header = car.wait("SURVEY_RESULT", 10)
    parts = header.split(",")
    if len(parts) < 3:
        raise LinkError(f"结果头格式异常：{header}")
    total, done, samples = int(parts[1]), int(parts[2]), int(parts[3]) if len(parts) > 3 else 0
    print(f"小车报告：{done}/{total} 步完成，每点 {samples} 次采样")

    rows: list[list[str]] = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        line = car.readline(5.0)
        if line is None:
            continue
        if line.startswith("SURVEY_DUMP_END"):
            break
        if line.startswith("SURVEY_R,"):
            rows.append(line.split(","))

    measured = [r for r in rows if len(r) >= 7 and int(r[4]) > 0]
    if not measured:
        print("没有采到任何数据（可能还没开始跑，或者中途失败）", file=sys.stderr)
        return 1

    spec = GridSpec(rows=args.rows, columns=args.columns, width_m=args.width, height_m=args.height)
    x0, y0 = spec.center(spec.parse(args.start))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output) / f"AP_{args.ap.upper()}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_path = out_dir / "rss_raw_offline.csv"
    sum_path = out_dir / "rss_summary_offline.csv"

    x, y = x0, y0
    points: list[dict] = []
    with raw_path.open("w", newline="", encoding="utf-8-sig") as raw_f, sum_path.open(
        "w", newline="", encoding="utf-8-sig"
    ) as sum_f:
        raw_w = csv.DictWriter(
            raw_f,
            fieldnames=[
                "timestamp", "point_id", "row", "column", "x_m", "y_m",
                "sample_index", "rssi_dbm", "bssid", "channel", "ssid", "position_source",
            ],
        )
        raw_w.writeheader()
        sum_w = csv.DictWriter(
            sum_f,
            fieldnames=[
                "point_id", "row", "column", "x_m", "y_m", "valid_samples",
                "rssi_median_dbm", "rssi_mean_dbm", "rssi_std_db",
                "rssi_min_dbm", "rssi_max_dbm", "wifi_channel", "position_source",
            ],
        )
        sum_w.writeheader()

        now = datetime.now().astimezone().isoformat()
        for r in rows:
            label = r[1]
            dx, dy = float(r[2]), float(r[3])
            n = int(r[4])
            ok = int(r[5]) if len(r) > 5 else 0
            batt = int(r[6]) if len(r) > 6 else 0
            values = [int(v) for v in r[7 : 7 + n]]
            x += dx
            y += dy
            if n == 0:
                continue

            try:
                row_i = int(label[1])
                col_i = int(label[2])
            except (IndexError, ValueError):
                row_i = col_i = 0

            for i, v in enumerate(values, 1):
                raw_w.writerow(
                    {
                        "timestamp": now,
                        "point_id": label,
                        "row": row_i,
                        "column": col_i,
                        "x_m": round(x, 4),
                        "y_m": round(y, 4),
                        "sample_index": i,
                        "rssi_dbm": v,
                        "bssid": wifi.get("bssid", ""),
                        "channel": wifi.get("channel", ""),
                        "ssid": wifi.get("ssid", ""),
                        "position_source": "encoder_nominal_offline",
                    }
                )

            points.append(
                {
                    "point_id": label,
                    "row": row_i,
                    "column": col_i,
                    "x_m": round(x, 4),
                    "y_m": round(y, 4),
                    "valid_samples": len(values),
                    "battery_mv": batt,
                    "move_ok": ok,
                }
            )
            sum_w.writerow(
                {
                    "point_id": label,
                    "row": row_i,
                    "column": col_i,
                    "x_m": round(x, 4),
                    "y_m": round(y, 4),
                    "valid_samples": len(values),
                    "rssi_median_dbm": statistics.median(values),
                    "rssi_mean_dbm": round(statistics.mean(values), 2),
                    "rssi_std_db": round(statistics.stdev(values), 2) if len(values) > 1 else 0,
                    "rssi_min_dbm": min(values),
                    "rssi_max_dbm": max(values),
                    "wifi_channel": wifi.get("channel", ""),
                    "position_source": "encoder_nominal_offline",
                }
            )

    meta = {
        "created_local": datetime.now().astimezone().isoformat(),
        "mode": "offline_autonomous",
        "settings": {
            "rows": spec.rows,
            "columns": spec.columns,
            "room_width_m": spec.width_m,
            "room_height_m": spec.height_m,
            "ap_point": args.ap.upper(),
            "start_point": args.start.upper(),
        },
        "wifi": wifi,
        "steps_reported": total,
        "steps_done": done,
        "measured_points": len(points),
        "points": points,
        "position_source": "encoder_nominal_offline",
    }
    (out_dir / "experiment.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    all_values = [p["valid_samples"] for p in points]
    print()
    print(f"已导出 {len(points)} 个点，共 {sum(all_values)} 条采样")
    print(f"输出目录：{out_dir}")
    print(f"  {raw_path.name}")
    print(f"  {sum_path.name}")
    print(f"  experiment.json")
    if wifi:
        print(f"被测网络：{wifi.get('ssid')}  {wifi.get('bssid')}  信道 {wifi.get('channel')}")
    return 0


# ---------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="离线自主采集：电脑只下发任务和收取结果")
    p.add_argument("action", choices=["plan", "run", "status", "dump", "abort", "calibrate"])
    p.add_argument("--port", default="", help="USB 串口，留空自动识别")
    p.add_argument("--ap", default="P22", help="路由器所在点位，如 P68")
    p.add_argument("--start", default="P11", help="小车摆放的起点点位")
    p.add_argument("--rows", type=int, default=7)
    p.add_argument("--columns", type=int, default=9)
    p.add_argument("--width", type=float, default=8.23, help="房间宽度（米，列方向）")
    p.add_argument("--height", type=float, default=6.95, help="房间纵深（米，行方向）")
    p.add_argument("--blocked", nargs="*", default=[], help="障碍点，如 P34 P35")
    p.add_argument("--layout", default="", help="布局文件（含家具），给了就覆盖 --ap/--start/--blocked/房间尺寸")
    p.add_argument("--speed", type=float, default=0.08)
    p.add_argument("--dwell", type=float, default=3.0, help="每点静置秒数")
    p.add_argument("--samples", type=int, default=10)
    p.add_argument("--x-scale", type=float, default=1.0)
    p.add_argument("--y-scale", type=float, default=1.0)
    p.add_argument("--output", default=str(DEFAULT_OUT))
    p.add_argument("--detach", action="store_true", help="run 后立即返回，便于拔线")
    p.add_argument("--axis", choices=["x", "y"], default="x", help="calibrate 的测试轴")
    p.add_argument("--distance", type=float, default=0.2, help="calibrate 的测试距离（米）")
    p.add_argument("--no-plan", dest="plan_first", action="store_false", help="run 前不再重复下发计划")
    return p


def main(argv: list[str] | None = None) -> int:
    fix_console_encoding()
    args = build_parser().parse_args(argv)
    if args.layout:
        lay = Layout.load(args.layout)
        args.rows = lay.spec.rows
        args.columns = lay.spec.columns
        args.width = lay.spec.width_m
        args.height = lay.spec.height_m
        args.ap = lay.spec.label(lay.ap)
        args.start = lay.spec.label(lay.start)
        args.blocked = [lay.spec.label(c) for c in sorted(lay.blocked())]
    if not 0.03 <= args.speed <= 0.12:
        print("速度必须在 0.03..0.12 m/s", file=sys.stderr)
        return 2
    if not 1 <= args.samples <= 32:
        print("每点采样次数必须在 1..32", file=sys.stderr)
        return 2

    try:
        port = pick_port(args.port)
        car = Car(port)
    except Exception as exc:
        print(f"串口打开失败：{exc}", file=sys.stderr)
        return 2

    try:
        handlers = {
            "plan": cmd_plan,
            "run": cmd_run,
            "status": cmd_status,
            "dump": cmd_dump,
            "abort": cmd_abort,
            "calibrate": cmd_calibrate,
        }
        return handlers[args.action](car, args)
    except (LinkError, PlanError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    finally:
        car.close()


if __name__ == "__main__":
    raise SystemExit(main())
