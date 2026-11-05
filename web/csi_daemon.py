#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长驻 CSI 采集守护进程：一次连接 ESP32，整轮复用。

由 web/app.py 用 espectre venv 的 python 拉起，通过 stdin/stdout 行协议通信：
  收: {"cmd":"connect","target":"192.168.31.65","pps":100}
  发: {"type":"ready"} 或 {"type":"error","msg":...}
  收: {"cmd":"collect","label":"p11","duration":10,"description":"..."}
  发: STATUS: RECORDING 1/1 | elapsed 1.2/10.0s | capture 120   (兼容 app.py STATUS_RE)
      {"type":"saved","files":["...npz"],"packets":N} 或 {"type":"error",...} / {"type":"aborted"}
  收: {"cmd":"abort"}   中止当前采集，连接保留
  收: {"cmd":"quit"}    断开连接并退出
stdin 关闭(EOF)时自动清理退出。链路断开时发 {"type":"link_error",...} 并退出，
app.py 下一批会自动重新拉起本进程。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] / "tools" / "espectre"
sys.path.insert(0, str(REPO))

from tools.lib.bootstrap import setup_paths  # noqa: F401,E402  (自动配置 sys.path)
from tools.lib.csi_io import CSICollector, DirectRawCSIReceiver  # noqa: E402
from tools.ha_traffic_generator_addon.espectre_traffic_generator import (  # noqa: E402
    ExternalTrafficGenerator,
)
from espectre_cli.device_transport import DirectClient  # noqa: E402

DIRECT_HTTP_PORT = 62587
DEFAULT_PPS = 100.0

_out_lock = threading.Lock()


def emit(obj: dict) -> None:
    with _out_lock:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def emit_status(elapsed: float, duration: float, packets: int) -> None:
    # 与 v3 CLI 相同格式，app.py 的 STATUS_RE 直接识别
    line = (
        f"STATUS: RECORDING 1/1 | elapsed {elapsed:.1f}/{duration:.1f}s "
        f"| capture {packets}"
    )
    with _out_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


class BoardLink:
    """到一块 ESP32 的常驻连接：HTTP 会话 + 宿主机打流 + 后台收包线程。"""

    def __init__(self, target: str, pps: float) -> None:
        target = str(target).strip()
        if not target:
            raise RuntimeError("empty target")
        self.host = target.split(":")[0]
        self.pps = float(pps)
        endpoint = f"http://{target}:{DIRECT_HTTP_PORT}/espectre/v1"

        with DirectClient(endpoint) as control:
            caps = control.request("get", "capabilities")
            raw = caps.get("csi", {})
            if not isinstance(raw, dict) or raw.get("protocol_version") != 1:
                raise RuntimeError("target does not advertise raw HTTP v1")
            if raw.get("marker") != ExternalTrafficGenerator.TRAFFIC_MARKER:
                raise RuntimeError("traffic marker mismatch")
            control.request(
                "patch", "sensing", {"traffic_generator_mode": "external"}
            )
            runtime = control.request("get", "sensing")

        port = int(raw.get("traffic_udp_port", runtime.get("csi_traffic_udp_port", 5555)))

        self.receiver = DirectRawCSIReceiver(
            f"http://{target}", buffer_size=4000, derive_complex=False
        )
        self.receiver.requested_pps = self.pps
        self.generator = ExternalTrafficGenerator(
            [self.host], port=port, rate_pps=self.pps
        )
        self.receiver.start_session()
        self.generator.start()
        self.receiver.bind_stream()
        self.receiver.add_callback(self._on_packet)

        self.recording = False
        self.buf: list = []
        self.buf_lock = threading.Lock()
        self.stop_flag = False
        self.dead: str | None = None
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _on_packet(self, pkt) -> None:
        if not self.recording:
            return
        src = getattr(pkt, "source_ip", None)
        if src is not None and str(src) != self.host:
            return
        with self.buf_lock:
            self.buf.append(pkt)

    def _run_loop(self) -> None:
        while not self.stop_flag:
            try:
                self.receiver.run(timeout=0.5, quiet=True)
            except Exception as e:  # noqa: BLE001
                self.dead = str(e)
                emit({"type": "link_error", "msg": self.dead})
                time.sleep(0.3)   # 给 collect 线程/管道一个把错误发出去的机会
                os._exit(2)       # 链路死了就退出：app.py 会重新拉起并重连，
                return            # 绝不能带着死连接继续装活

    def close(self) -> None:
        self.stop_flag = True
        try:
            self.generator.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.receiver.stop()
        except Exception:  # noqa: BLE001
            pass


class Daemon:
    def __init__(self) -> None:
        self.link: BoardLink | None = None
        self.link_lock = threading.Lock()
        self.abort_event = threading.Event()
        self.collect_thread: threading.Thread | None = None

    # ---------- 命令 ----------

    def cmd_connect(self, cmd: dict) -> None:
        with self.link_lock:
            self._close_link()
            try:
                self.link = BoardLink(
                    cmd.get("target", ""), cmd.get("pps", DEFAULT_PPS)
                )
            except Exception as e:  # noqa: BLE001
                self.link = None
                emit({"type": "error", "cmd": "connect", "msg": str(e)})
                return
        emit({"type": "ready", "target": cmd.get("target", "")})

    def cmd_collect(self, cmd: dict) -> None:
        t = self.collect_thread
        if t and t.is_alive():
            emit({"type": "error", "cmd": "collect", "msg": "busy"})
            return
        self.abort_event.clear()
        self.collect_thread = threading.Thread(
            target=self._do_collect, args=(cmd,), daemon=True
        )
        self.collect_thread.start()

    def cmd_abort(self, _cmd: dict) -> None:
        self.abort_event.set()

    def cmd_quit(self, _cmd: dict) -> None:
        self.abort_event.set()
        t = self.collect_thread
        if t and t.is_alive():
            t.join(timeout=3)
        with self.link_lock:
            self._close_link()
        emit({"type": "bye"})
        sys.exit(0)

    # ---------- 内部 ----------

    def _close_link(self) -> None:
        if self.link is not None:
            self.link.close()
            self.link = None

    def _do_collect(self, cmd: dict) -> None:
        label = str(cmd.get("label", "")).strip()
        duration = float(cmd.get("duration", 10))
        description = str(cmd.get("description", ""))
        with self.link_lock:
            link = self.link
        if link is None:
            emit({"type": "error", "cmd": "collect", "msg": "not connected"})
            return

        with link.buf_lock:
            link.buf = []
            link.recording = True
        t0 = time.monotonic()
        n = 0
        try:
            while True:
                if self.abort_event.is_set():
                    with link.buf_lock:
                        link.recording = False
                        link.buf = []
                    emit({"type": "aborted", "label": label})
                    return
                if link.dead is not None:
                    with link.buf_lock:
                        link.recording = False
                    emit({"type": "error", "cmd": "collect", "msg": "link lost: " + link.dead})
                    return
                elapsed = time.monotonic() - t0
                if elapsed >= duration:
                    break
                with link.buf_lock:
                    n = len(link.buf)
                emit_status(elapsed, duration, n)
                time.sleep(0.2)
        finally:
            with link.buf_lock:
                link.recording = False

        with link.buf_lock:
            packets = list(link.buf)

        if not packets:
            emit({"type": "error", "cmd": "collect", "msg": "No packets captured"})
            return

        try:
            apply_final = getattr(link.receiver, "apply_final_raw_diagnostics", None)
            if callable(apply_final):
                apply_final(packets[-1])
            writer = CSICollector(
                label=label,
                port=0,
                contributor="wifi-heatmap",
                description=description,
                bind_host="127.0.0.1",
                expected_device_count=1,
                expected_source_hosts=[link.host],
                target_pps=link.pps,
            )
            saved_paths = writer.save_samples_by_device(packets)
        except Exception as e:  # noqa: BLE001
            emit({"type": "error", "cmd": "collect", "msg": f"save failed: {e}"})
            return

        if not saved_paths:
            emit({"type": "error", "cmd": "collect", "msg": "No packets captured"})
            return
        emit(
            {
                "type": "saved",
                "label": label,
                "packets": len(packets),
                "files": [p.name for p in saved_paths],
            }
        )


def main() -> None:
    daemon = Daemon()
    handlers = {
        "connect": daemon.cmd_connect,
        "collect": daemon.cmd_collect,
        "abort": daemon.cmd_abort,
        "quit": daemon.cmd_quit,
    }
    emit({"type": "hello"})
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
            except ValueError:
                emit({"type": "error", "msg": "bad json"})
                continue
            handler = handlers.get(str(cmd.get("cmd", "")))
            if handler is None:
                emit({"type": "error", "msg": "unknown cmd"})
                continue
            try:
                handler(cmd)
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001
                emit({"type": "error", "msg": f"{cmd.get('cmd')}: {e}"})
    finally:
        # stdin EOF（app.py 退出或管道断开）→ 清理退出
        daemon.abort_event.set()
        with daemon.link_lock:
            daemon._close_link()
    os._exit(0)


if __name__ == "__main__":
    main()
