"""通过 USB 串口给小车配置 Wi-Fi，并读出分配到的 IP。

固件只在 USB 串口接收配网命令，凭据保存在板载 NVS，不会回显。
用法：
  python provision_wifi.py --ssid MyWiFi --password 12345678
  python provision_wifi.py --port COM8 --ssid MyWiFi --password 12345678

不写 --port 时自动扫描串口；只有一个候选就直接用，多个会列出让你选。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

from console import fix_console_encoding

DEFAULT_TIMEOUT = 45.0

BRIDGE_HINT = re.compile(r"CH34|CH341|CP21|FTDI|USB-SERIAL|Silicon|UART", re.IGNORECASE)


def list_ports() -> list[tuple[str, str]]:
    """返回 [(device, description), ...]；pyserial 不可用时返回空列表。"""
    try:
        from serial.tools import list_ports as lp  # type: ignore
    except ImportError:
        return []
    return [(p.device, p.description or "") for p in lp.comports()]


def pick_port(explicit: str) -> str | None:
    if explicit:
        return explicit

    ports = list_ports()
    if not ports:
        print("没有检测到串口。请确认小车已用 USB 数据线连接、驱动已安装（CH340 / CP210x）。",
              file=sys.stderr)
        return None

    bridges = [p for p in ports if BRIDGE_HINT.search(p[1])]
    if len(bridges) == 1:
        print(f"自动识别串口：{bridges[0][0]}  ({bridges[0][1]})")
        return bridges[0][0]
    if len(ports) == 1:
        print(f"自动识别串口：{ports[0][0]}  ({ports[0][1]})")
        return ports[0][0]

    print("检测到多个串口：")
    for i, (dev, desc) in enumerate(ports, 1):
        print(f"  [{i}] {dev}  {desc}")
    try:
        choice = input(f"请选择编号 (1-{len(ports)}): ").strip()
        idx = int(choice) - 1
    except (ValueError, EOFError):
        print("输入无效。", file=sys.stderr)
        return None
    if not 0 <= idx < len(ports):
        print("编号超出范围。", file=sys.stderr)
        return None
    return ports[idx][0]


def provision(port_name: str, ssid: str, password: str, timeout: float = DEFAULT_TIMEOUT) -> str | None:
    try:
        import serial  # type: ignore
    except ImportError:
        print("缺少 pyserial，请先安装：pip install pyserial", file=sys.stderr)
        return None

    with serial.Serial(port_name, 115200, timeout=0.5) as port:
        time.sleep(1.5)
        port.reset_input_buffer()
        command = json.dumps({"cmd": "wifi", "ssid": ssid, "password": password})
        port.write((command + "\n").encode("utf-8"))
        port.flush()
        print(f"已向 {port_name} 下发配网命令（SSID={ssid}）。等待连接…")

        deadline = time.monotonic() + timeout
        ip: str | None = None
        while time.monotonic() < deadline:
            line = port.readline().decode("utf-8", "replace").strip()
            if not line:
                continue
            if not line.startswith("{"):
                print("  " + line)
                continue
            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                continue
            if info.get("connected"):
                ip = info.get("ip")
                rssi = info.get("rssi_dbm")
                print(f"已连接：SSID={info.get('ssid')} BSSID={info.get('bssid')} "
                      f"信道={info.get('channel')} RSSI={rssi} dBm IP={ip}")
                return ip
            print(f"  连接中… {info.get('firmware', '')}")
        return None


def main() -> int:
    fix_console_encoding()
    parser = argparse.ArgumentParser(description="给 MiniAuto 小车配置 Wi-Fi")
    parser.add_argument("--port", default="", help="USB 串口，留空则自动扫描")
    parser.add_argument("--ssid", required=True)
    parser.add_argument("--password", default="")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    if not 1 <= len(args.ssid.encode("utf-8")) <= 32:
        print("SSID 长度必须为 1..32 字节", file=sys.stderr)
        return 2
    if len(args.password) > 63:
        print("密码最长 63 字符", file=sys.stderr)
        return 2

    port_name = pick_port(args.port)
    if not port_name:
        return 2

    ip = provision(port_name, args.ssid, args.password, args.timeout)
    if ip:
        print()
        print(f"小车地址：{ip}:62587")
        print(f"可以在采集程序里把「小车地址」填成 {ip}:62587")
        return 0
    print("超时：仍未连接。请检查 SSID/密码，或确认固件已烧录。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
