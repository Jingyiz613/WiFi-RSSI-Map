"""小车通信模块：USB 取控制密钥 + HTTP 运动控制 + RSSI 读取。

设计要点
--------
- 固件每次上电随机生成 32 位控制密钥，只能通过 USB 串口 `SURVEY_KEY` 读取，
  不写入任何文件，避免密钥泄漏到仓库。
- 固件只接受单轴、单次 0.05~1.10 m 的运动，长距离由本模块自动拆分。
- 每个动作都维持心跳；心跳中断 2 秒固件会自行刹停。
- 所有异常都抛 RobotError，由上层中止任务并请求停车。
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


class RobotError(RuntimeError):
    """小车通信或运动失败。"""


@dataclass
class WifiReading:
    rssi_dbm: int
    ssid: str | None
    bssid: str | None
    channel: int | None
    ip: str | None

    @property
    def identity(self) -> tuple[str | None, int | None, str | None]:
        """用于检测是否切换了 AP/BSSID/信道。"""
        return self.ssid, self.channel, self.bssid


class Transport(Protocol):
    """可替换的 HTTP 传输层，便于离线测试。"""

    def request(self, url: str, payload: dict | None, headers: dict[str, str]) -> dict: ...


class UrllibTransport:
    """默认传输层：直连局域网，不走系统代理。"""

    def __init__(self, timeout: float = 1.5) -> None:
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, url: str, payload: dict | None, headers: dict[str, str]) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise RobotError(f"HTTP {exc.code}：{detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RobotError(f"无法连接小车：{exc}") from exc
        except json.JSONDecodeError as exc:
            raise RobotError("小车返回了非 JSON 响应") from exc


class Robot:
    """运动与 RSSI 控制客户端。"""

    def __init__(self, target: str, key: str, transport: Transport | None = None) -> None:
        base = target.strip()
        if base.startswith("http://"):
            base = base[len("http://") :]
        self.base = "http://" + base.rstrip("/")
        self.key = key
        self.command_id = 0
        self.transport = transport or UrllibTransport()

    # ---- 底层命令 ----
    def _command(self, op: str, **fields: Any) -> dict:
        headers = {"X-Survey-Key": self.key}
        result = self.transport.request(self.base + "/survey/command", {"op": op, **fields}, headers)
        try:
            self.command_id = max(self.command_id, int(result["command_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RobotError("小车返回的命令状态格式异常") from exc
        return result

    def alive(self) -> dict:
        state = self._command("heartbeat")
        if state.get("state") not in ("ready", "queued", "moving", "done"):
            raise RobotError(f"任务已中止：{state.get('state')} / {state.get('reason')}")
        return state

    def wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.alive()
            time.sleep(min(0.25, max(0.0, end - time.monotonic())))

    # ---- 运动 ----
    def arm(self) -> None:
        self._command("arm")

    def stop(self) -> None:
        try:
            self._command("stop")
        except RobotError:
            pass  # 固件心跳超时会兜底刹停

    def move(self, dx_m: float, dy_m: float, speed_mps: float) -> None:
        """把一次位移拆成固件允许的若干单轴指令。"""
        if abs(dx_m) > 1e-9 and abs(dy_m) > 1e-9:
            raise RobotError("固件只接受单轴运动，不能同时给 dx 和 dy")
        total = abs(dx_m) + abs(dy_m)
        if total < 0.05:
            return
        pieces = max(1, math.ceil(total / 1.0))
        for _ in range(pieces):
            self.command_id += 1
            sent_id = self.command_id
            self._command(
                "move",
                id=sent_id,
                dx=dx_m / pieces,
                dy=dy_m / pieces,
                speed=speed_mps,
            )
            deadline = time.monotonic() + total / pieces / speed_mps * 2.5 + 5
            while time.monotonic() < deadline:
                state = self.alive()
                if state.get("command_id") != sent_id:
                    raise RobotError("命令编号不匹配，拒绝继续")
                if state.get("state") == "done":
                    break
                time.sleep(0.2)
            else:
                raise RobotError("等待运动完成超时")

    # ---- 测量 ----
    def read_wifi(self) -> WifiReading:
        self.alive()
        value = self.transport.request(self.base + "/espectre/v1/wifi", None, {})
        rssi = value.get("rssi_dbm")
        if (
            not value.get("connected")
            or isinstance(rssi, bool)
            or not isinstance(rssi, (int, float))
            or not math.isfinite(rssi)
            or not -127 <= rssi < 0
        ):
            raise RobotError("Wi-Fi 读数不可用，任务中止")
        return WifiReading(
            rssi_dbm=int(rssi),
            ssid=value.get("ssid"),
            bssid=value.get("bssid"),
            channel=value.get("channel"),
            ip=value.get("ip"),
        )


def read_key_over_usb(port_name: str, timeout_s: float = 8.0) -> str:
    """通过 USB 串口读取本次上电的控制密钥。"""
    try:
        import serial  # type: ignore
    except ImportError as exc:
        raise RobotError("缺少 pyserial，请先安装依赖") from exc

    port = serial.Serial(None, baudrate=115200, timeout=0.2, write_timeout=1)
    port.dtr = False
    port.rts = False
    port.port = port_name
    try:
        port.open()
        port.reset_input_buffer()
        port.write(b"SURVEY_KEY\n")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                value = json.loads(port.readline())
            except (ValueError, UnicodeError):
                continue
            key = value.get("survey_key") if isinstance(value, dict) else None
            if isinstance(key, str) and len(key) == 32:
                return key
        raise RobotError(
            f"串口 {port_name} 上没有读到控制密钥。请确认已烧录 AutoSurvey 固件，且小车刚上电未重启。"
        )
    finally:
        port.close()
