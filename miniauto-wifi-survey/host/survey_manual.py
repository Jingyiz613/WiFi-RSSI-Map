"""手动搬移采集：按规划点位人工移动采集节点，由电脑逐点读取 RSSI。

适用于 ESP32-C6 开发板，也可用于不便运行自动路线时的 MiniPro 小车。

用法：
  tools\\py.cmd host\\survey_manual.py --target 192.168.1.50:62587 --layout layout.json
  tools\\py.cmd host\\survey_manual.py --target 192.168.1.50:62587 --rows 7 --columns 9

流程：脚本提示当前点位 -> 把节点放到该格中心 -> 按回车 -> 多次采样取中位数 -> 下一个点。
每个点完成后立即写入 CSV/JSON，意外中断不会丢失已完成的数据。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
import urllib.request
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from console import fix_console_encoding  # noqa: E402
from layout import Layout  # noqa: E402
from route_planner import GridSpec, PlanError, make_serpentine_plan  # noqa: E402

FIELDS = [
    "point_id", "row", "column", "x_m", "y_m",
    "rssi_dbm", "rssi_min", "rssi_max", "samples",
    "ssid", "bssid", "channel", "error",
]
REDACTED = "[redacted]"


def status_url(target: str) -> str:
    target = target.strip().rstrip("/")
    if target.startswith(("http://", "https://")):
        return target + "/espectre/v1/wifi"
    return f"http://{target}/espectre/v1/wifi"


def read_status(target: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(status_url(target), timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sample_point(target: str, count: int, interval: float) -> dict:
    """连续读取 count 次，返回中位数、最值以及关联 AP 信息。"""
    readings: list[float] = []
    meta: dict = {}
    for index in range(count):
        try:
            data = read_status(target)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        if not data.get("connected"):
            return {"error": "采集节点未连接 Wi-Fi"}
        rssi = data.get("rssi_dbm")
        if isinstance(rssi, (int, float)) and math.isfinite(float(rssi)):
            readings.append(float(rssi))
            meta = data
        if index + 1 < count:
            time.sleep(interval)
    if not readings:
        return {"error": "没有有效 RSSI"}
    middle = statistics.median(readings)
    return {
        "rssi_dbm": int(round(middle)),
        "rssi_min": int(round(min(readings))),
        "rssi_max": int(round(max(readings))),
        "samples": len(readings),
        "bssid": meta.get("bssid"),
        "ssid": meta.get("ssid"),
        "channel": meta.get("channel"),
    }


def redact_row(row: dict) -> dict:
    """隐藏能直接标识接入点的字段，保留信道和信号值。"""
    safe = dict(row)
    for field in ("ssid", "bssid"):
        if safe.get(field):
            safe[field] = REDACTED
    return safe


def save_results(out_csv: Path, out_json: Path, settings: dict, rows: list[dict]) -> None:
    """保存当前进度；启用脱敏时同样作用于 JSON。"""
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    safe_rows = [redact_row(row) if settings.get("redact") else row for row in rows]
    with out_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(safe_rows)

    safe_settings = dict(settings)
    if safe_settings.get("redact") and safe_settings.get("layout"):
        safe_settings["layout"] = PureWindowsPath(safe_settings["layout"]).name
    payload = {"settings": safe_settings, "rows": safe_rows}
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_plan(args: argparse.Namespace):
    if args.layout:
        layout = Layout.load(args.layout)
        spec, ap, start = layout.spec, layout.ap, layout.start
        blocked = sorted(layout.blocked())
    else:
        spec = GridSpec(args.rows, args.columns, args.width, args.height)
        ap = spec.parse(args.ap)
        start = spec.parse(args.start)
        blocked = [spec.parse(item) for item in args.blocked]
    plan = make_serpentine_plan(spec, ap, blocked, start)
    return spec, plan


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("必须大于 0")
    return value


def non_negative_float(text: str) -> float:
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError("不能小于 0")
    return value


def main(argv: list[str] | None = None) -> int:
    fix_console_encoding()
    parser = argparse.ArgumentParser(description="手动搬移 RSSI 采集")
    parser.add_argument("--target", required=True,
                        help="采集节点地址，例如 192.168.1.50:62587")
    parser.add_argument("--layout", default=None,
                        help="布局文件；不填则使用命令行网格参数")
    parser.add_argument("--rows", type=int, default=7)
    parser.add_argument("--columns", type=int, default=9)
    parser.add_argument("--width", type=float, default=8.23)
    parser.add_argument("--height", type=float, default=6.95)
    parser.add_argument("--start", default="P11")
    parser.add_argument("--ap", default="P68")
    parser.add_argument("--blocked", nargs="*", default=[])
    parser.add_argument("--samples", type=positive_int, default=10)
    parser.add_argument("--interval", type=non_negative_float, default=0.25)
    parser.add_argument("--output", default="manual_survey.csv")
    parser.add_argument("--json", default="manual_survey.json")
    parser.add_argument("--redact", action="store_true",
                        help="将 SSID/BSSID 替换为 [redacted]，默认关闭")
    args = parser.parse_args(argv)

    try:
        spec, plan = build_plan(args)
    except (PlanError, ValueError) as exc:
        print(f"规划失败：{exc}", file=sys.stderr)
        return 2

    out_csv = Path(args.output)
    out_json = Path(args.json)
    settings = vars(args)
    rows: list[dict] = []

    def persist() -> None:
        save_results(out_csv, out_json, settings, rows)

    print("=" * 62)
    print(f"手动搬移采集  {spec.rows}x{spec.columns}  格距 "
          f"{spec.cell_width_m:.3f} x {spec.cell_height_m:.3f} m")
    print(f"共 {len(plan)} 个点。把采集节点放到指定格子中心，按回车采样。")
    print("输入 s 跳过当前点，r 重测上一点，q 退出并保存。")
    if args.redact:
        print("隐私模式：输出文件中的 SSID/BSSID 将被隐藏。")
    print("=" * 62)

    index = 0
    try:
        while index < len(plan):
            stop = plan[index]
            cell = stop.cell
            x, y = spec.center(cell)
            label = spec.label(cell)
            print(f"\n[{index + 1}/{len(plan)}]  {label}  坐标 ({x:.2f}, {y:.2f}) m")
            answer = input("  放好后按回车（s 跳过 / r 重测 / q 退出）> ").strip().lower()
            if answer == "q":
                break
            if answer == "s":
                rows.append({
                    "point_id": label, "row": cell[0], "column": cell[1],
                    "x_m": round(x, 3), "y_m": round(y, 3),
                    "rssi_dbm": None, "samples": 0, "error": "skipped",
                })
                persist()
                index += 1
                continue
            if answer == "r":
                if index > 0:
                    rows.pop()
                    index -= 1
                continue

            result = sample_point(args.target, args.samples, args.interval)
            if "error" in result:
                print(f"  采样失败：{result['error']}")
                retry = input("  按回车重试，s 跳过，q 退出 > ").strip().lower()
                if retry == "q":
                    break
                if retry == "s":
                    rows.append({
                        "point_id": label, "row": cell[0], "column": cell[1],
                        "x_m": round(x, 3), "y_m": round(y, 3),
                        "rssi_dbm": None, "samples": 0, "error": result["error"],
                    })
                    persist()
                    index += 1
                continue

            row = {
                "point_id": label, "row": cell[0], "column": cell[1],
                "x_m": round(x, 3), "y_m": round(y, 3), **result,
            }
            rows.append(row)
            persist()
            print(f"  RSSI {result['rssi_dbm']} dBm  "
                  f"(min {result['rssi_min']} / max {result['rssi_max']}, "
                  f"{result['samples']} 次)")
            index += 1
    except (KeyboardInterrupt, EOFError):
        print("\n采集中断，正在保存已完成的数据...")

    persist()
    valid = [row for row in rows if row.get("rssi_dbm") is not None]
    print("\n" + "=" * 62)
    print(f"完成 {len(valid)}/{len(plan)} 个点")
    if valid:
        values = [row["rssi_dbm"] for row in valid]
        print(f"RSSI 范围 {min(values)} ~ {max(values)} dBm")
    print(f"CSV : {out_csv.resolve()}")
    print(f"JSON: {out_json.resolve()}")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
