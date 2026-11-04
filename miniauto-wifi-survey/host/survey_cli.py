"""命令行采集入口（与 GUI 共用同一套规划/执行模块）。

常用：
  python survey_cli.py --ap P22                      # 只生成路线预览
  python survey_cli.py --ap P68 --run --calibrated    # 正式采集（需已校准）
  python survey_cli.py --ap 3.2,1.5                   # 用米制坐标选路由器
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from console import fix_console_encoding
from robot_client import Robot, RobotError, read_key_over_usb
from layout import Layout
from route_planner import GridSpec, PlanError, make_serpentine_plan, plan_summary
from route_plot import draw_plan
from survey_runner import SurveyOptions, run_survey

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"


def parse_ap(text: str, spec: GridSpec) -> tuple[int, int]:
    text = text.strip()
    if "," in text:
        x_str, y_str = text.split(",", 1)
        return spec.nearest_cell(float(x_str), float(y_str))
    return spec.parse(text)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MiniAuto Wi-Fi 采集（命令行）")
    p.add_argument("--ap", default="P22", help="路由器位置，支持 P22 或 x_m,y_m（默认 P22）")
    p.add_argument("--blocked", nargs="*", default=[], help="额外禁行格，如 --blocked P33 P34")
    p.add_argument("--layout", default="", help="布局文件（含家具），给了就忽略 --ap/--blocked/--start/房间尺寸")
    p.add_argument("--start", default="P11", help="起点格（默认 P11）")
    p.add_argument("--rows", type=int, default=7)
    p.add_argument("--columns", type=int, default=9)
    p.add_argument("--width", type=float, default=8.23)
    p.add_argument("--height", type=float, default=6.95)
    p.add_argument("--samples", type=int, default=10)
    p.add_argument("--settle", type=float, default=3.0)
    p.add_argument("--speed", type=float, default=0.08)
    p.add_argument("--x-scale", type=float, default=1.0)
    p.add_argument("--y-scale", type=float, default=1.0)
    p.add_argument("--ssid", default="", help="锁定测量的 SSID（留空则不校验）")
    p.add_argument("--target", default="192.168.31.12:62587", help="小车 IP:端口")
    p.add_argument("--port", default="COM8", help="USB 串口，用于读取控制密钥")
    p.add_argument("--run", action="store_true", help="真正驱动小车；默认只做离线规划")
    p.add_argument("--calibrated", action="store_true", help="声明已完成实车距离/方向校准")
    p.add_argument("--test-axis", choices=["x", "y"], help="只做一次校准位移测试，不采集")
    p.add_argument("--test-distance", type=float, default=0.2, help="校准测试距离，单位 m（0.05..1.0）")
    p.add_argument("--preview-image", action="store_true", help="同时输出路线预览 PNG")
    p.add_argument("--yes", action="store_true", help="跳过 START 交互确认")
    return p


def main(argv: list[str] | None = None) -> int:
    fix_console_encoding()
    args = build_parser().parse_args(argv)
    try:
        furniture = []
        if args.layout:
            lay = Layout.load(args.layout)
            spec, ap, start = lay.spec, lay.ap, lay.start
            blocked = sorted(lay.blocked())
            furniture = lay.furniture
        else:
            spec = GridSpec(args.rows, args.columns, args.width, args.height)
            ap = parse_ap(args.ap, spec)
            blocked = [spec.parse(b) for b in args.blocked]
            start = spec.parse(args.start)
        plan = make_serpentine_plan(spec, ap, blocked, start)
    except (PlanError, ValueError) as exc:
        print(f"规划失败：{exc}", file=sys.stderr)
        return 2

    summary = plan_summary(spec, plan, ap)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    preview_json = ROOT / "route_preview.json"
    preview_json.write_text(
        json.dumps(
            {
                "settings": vars(args),
                "summary": summary,
                "plan": [
                    {"point_id": s.point_id, "row": s.cell[0], "column": s.cell[1], "via": [spec.label(c) for c in s.via]}
                    for s in plan
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"路线已写入 {preview_json}")

    if args.preview_image:
        image = draw_plan(spec, plan, ap, blocked, start, ROOT / "route_preview.png", furniture=furniture)
        print(f"预览图：{image}")

    if not args.run:
        print("仅预览，未连接小车。加 --run --calibrated 才会实际行驶。")
        return 0
    if args.test_axis:
        if not 0.05 <= args.test_distance <= 1.0:
            print("测试距离应在 0.05..1.0 m", file=sys.stderr)
            return 2
        try:
            key = read_key_over_usb(args.port)
            robot = Robot(args.target, key)
            robot.arm()
            robot.wait(0.5)
            print(f"执行 {args.test_axis.upper()} 轴位移 {args.test_distance} m …")
            if args.test_axis == "x":
                robot.move(args.test_distance, 0.0, args.speed)
            else:
                robot.move(0.0, args.test_distance, args.speed)
            robot.stop()
        except (RobotError, OSError) as exc:
            print(f"测试失败：{exc}", file=sys.stderr)
            return 1
        print("测试位移完成。请用卷尺量实际距离，并计算校准系数。")
        print("  新系数 = 原系数 × 目标距离 ÷ 实测距离")
        return 0
    if not args.calibrated:
        print("拒绝执行：正式采集前必须完成实车校准并加 --calibrated。", file=sys.stderr)
        return 2

    try:
        options = SurveyOptions(
            samples_per_point=args.samples,
            settle_seconds=args.settle,
            speed_mps=args.speed,
            x_scale=args.x_scale,
            y_scale=args.y_scale,
            expected_ssid=args.ssid or None,
        )
        options.validate()
        print(f"通过 {args.port} 读取控制密钥…")
        key = read_key_over_usb(args.port)
        print("密钥已获取。请保持小车供电，不要重启。")
        if not args.yes and input("输入 START 开始，其它任意输入取消：").strip() != "START":
            print("已取消")
            return 0
        robot = Robot(args.target, key)
        result = run_survey(robot, spec, plan, ap, OUTPUT_DIR, options, start=start, progress=print)
    except (RobotError, ValueError, OSError) as exc:
        print(f"采集失败：{exc}", file=sys.stderr)
        return 1
    print(f"完成 {len(result.completed)} 个点，数据保存在 {result.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
