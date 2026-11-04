"""采集执行模块：按规划路线行驶并逐点采样 RSSI。

被 GUI 与命令行共同调用。所有落盘为增量写入，任务中断时保留已完成数据。
"""

from __future__ import annotations

import csv
import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from robot_client import Robot, RobotError, WifiReading
from route_planner import Cell, GridSpec, Stop, plan_to_dict


@dataclass
class SurveyOptions:
    samples_per_point: int = 10
    settle_seconds: float = 3.0
    interval_seconds: float = 1.0
    speed_mps: float = 0.08
    x_scale: float = 1.0
    y_scale: float = 1.0
    expected_ssid: str | None = None

    def validate(self) -> None:
        if not 1 <= self.samples_per_point <= 100:
            raise ValueError("每点采样次数应在 1..100")
        if not 0 <= self.settle_seconds <= 60:
            raise ValueError("静置时间应在 0..60 秒")
        if not 0.2 <= self.interval_seconds <= 10:
            raise ValueError("采样间隔应在 0.2..10 秒")
        if not 0.03 <= self.speed_mps <= 0.12:
            raise ValueError("行驶速度应在 0.03..0.12 m/s（固件限制）")
        if not 0.5 <= self.x_scale <= 2 or not 0.5 <= self.y_scale <= 2:
            raise ValueError("校准系数应在 0.5..2.0")


@dataclass
class SurveyResult:
    output_dir: Path
    completed: list[str] = field(default_factory=list)
    status: str = "running"
    error: str | None = None
    readings: int = 0


ProgressFn = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def run_survey(
    robot: Robot,
    spec: GridSpec,
    plan: Sequence[Stop],
    ap: Cell,
    out_root: Path,
    options: SurveyOptions,
    start: Cell = (1, 1),
    progress: ProgressFn = _noop,
    stop_event=None,
) -> SurveyResult:
    """执行整组采集。返回结果对象；异常会原样抛出并由调用方提示。"""
    options.validate()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_dir = Path(out_root) / f"AP_{spec.label(ap)}_{stamp[:15]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = SurveyResult(output_dir=out_dir)

    metadata = {
        "created_local": datetime.now().astimezone().isoformat(),
        "settings": {
            "rows": spec.rows,
            "columns": spec.columns,
            "room_width_m": spec.width_m,
            "room_height_m": spec.height_m,
            "ap_point": spec.label(ap),
            "samples_per_point": options.samples_per_point,
            "settle_seconds": options.settle_seconds,
            "interval_seconds": options.interval_seconds,
            "speed_mps": options.speed_mps,
            "x_scale": options.x_scale,
            "y_scale": options.y_scale,
        },
        "position_source": "encoder_nominal",
        "completed_points": [],
        "status": "running",
        **plan_to_dict(spec, plan, ap),
    }
    meta_path = out_dir / "experiment.json"

    def flush_meta() -> None:
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    flush_meta()
    raw_path = out_dir / "rss_raw_autonomous.csv"
    summary_path = out_dir / "rss_summary_autonomous.csv"

    try:
        progress("连接小车并接管控制（arm）…")
        robot.arm()
        robot.wait(0.5)

        current: Cell = start
        reference: tuple | None = None

        with raw_path.open("w", newline="", encoding="utf-8-sig") as raw_f, summary_path.open(
            "w", newline="", encoding="utf-8-sig"
        ) as sum_f:
            raw_writer = csv.DictWriter(
                raw_f,
                fieldnames=[
                    "timestamp", "point_id", "row", "column", "x_m", "y_m",
                    "sample_index", "rssi_dbm", "bssid", "channel", "ssid", "position_source",
                ],
            )
            raw_writer.writeheader()
            raw_f.flush()
            sum_writer = csv.DictWriter(
                sum_f,
                fieldnames=[
                    "point_id", "row", "column", "x_m", "y_m", "valid_samples",
                    "rssi_median_dbm", "rssi_mean_dbm", "rssi_std_db",
                    "rssi_min_dbm", "rssi_max_dbm", "wifi_channel", "position_source",
                ],
            )
            sum_writer.writeheader()
            sum_f.flush()

            for index, stop in enumerate(plan, 1):
                if stop_event is not None and stop_event.is_set():
                    raise RobotError("用户中止任务")
                metadata["target_point"] = stop.point_id
                flush_meta()

                for waypoint in stop.via:
                    if stop_event is not None and stop_event.is_set():
                        raise RobotError("用户中止任务")
                    dr = waypoint[0] - current[0]
                    dc = waypoint[1] - current[1]
                    dx = dc * spec.cell_width_m * options.x_scale
                    dy = dr * spec.cell_height_m * options.y_scale
                    progress(f"[{index}/{len(plan)}] 行驶 {spec.label(current)} → {spec.label(waypoint)}")
                    robot.move(dx, dy, options.speed_mps)
                    current = waypoint

                progress(f"[{index}/{len(plan)}] 到达 {stop.point_id}，静置 {options.settle_seconds:.0f}s 后采样")
                robot.wait(options.settle_seconds)

                values: list[int] = []
                channel = None
                for i in range(1, options.samples_per_point + 1):
                    if stop_event is not None and stop_event.is_set():
                        raise RobotError("用户中止任务")
                    reading: WifiReading = robot.read_wifi()
                    identity = reading.identity
                    if reference is None:
                        reference = identity
                    if identity != reference:
                        raise RobotError("Wi-Fi 的 SSID/BSSID/信道发生变化，已停止以避免混合不同部署")
                    if options.expected_ssid and reading.ssid != options.expected_ssid:
                        raise RobotError(
                            f"当前连接的是 {reading.ssid!r}，与设定的 {options.expected_ssid!r} 不符"
                        )
                    values.append(reading.rssi_dbm)
                    channel = reading.channel
                    x_m, y_m = spec.center(current)
                    raw_writer.writerow(
                        {
                            "timestamp": datetime.now().astimezone().isoformat(),
                            "point_id": stop.point_id,
                            "row": current[0],
                            "column": current[1],
                            "x_m": round(x_m, 4),
                            "y_m": round(y_m, 4),
                            "sample_index": i,
                            "rssi_dbm": reading.rssi_dbm,
                            "bssid": reading.bssid,
                            "channel": reading.channel,
                            "ssid": reading.ssid,
                            "position_source": "encoder_nominal",
                        }
                    )
                    raw_f.flush()
                    result.readings += 1
                    if i < options.samples_per_point:
                        robot.wait(options.interval_seconds)

                x_m, y_m = spec.center(current)
                sum_writer.writerow(
                    {
                        "point_id": stop.point_id,
                        "row": current[0],
                        "column": current[1],
                        "x_m": round(x_m, 4),
                        "y_m": round(y_m, 4),
                        "valid_samples": len(values),
                        "rssi_median_dbm": statistics.median(values),
                        "rssi_mean_dbm": round(statistics.mean(values), 2),
                        "rssi_std_db": round(statistics.stdev(values), 2) if len(values) > 1 else 0,
                        "rssi_min_dbm": min(values),
                        "rssi_max_dbm": max(values),
                        "wifi_channel": channel,
                        "position_source": "encoder_nominal",
                    }
                )
                sum_f.flush()
                result.completed.append(stop.point_id)
                metadata["completed_points"].append(stop.point_id)
                flush_meta()

        metadata["status"] = "finished"
        result.status = "finished"
    except BaseException as exc:  # 保留已完成数据并记录原因
        metadata["status"] = "aborted"
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        result.status = "aborted"
        result.error = str(exc)
        raise
    finally:
        try:
            robot.stop()
        except Exception:
            progress("停车指令发送失败，固件心跳超时会自动刹停")
        flush_meta()
    return result
