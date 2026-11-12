"""ESP32-C6 手动搬移采集的无硬件测试。"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))

import survey_manual


def test_sample_point_uses_even_sample_median(monkeypatch) -> None:
    readings = iter([10.0, 40.0, 20.0, 30.0])

    def fake_read_status(_target: str) -> dict:
        return {
            "connected": True,
            "rssi_dbm": next(readings),
            "ssid": "test-network",
            "bssid": "aa:bb:cc:dd:ee:ff",
            "channel": 6,
        }

    monkeypatch.setattr(survey_manual, "read_status", fake_read_status)
    monkeypatch.setattr(survey_manual.time, "sleep", lambda _seconds: None)

    result = survey_manual.sample_point("192.0.2.10:62587", count=4, interval=0)

    assert result["rssi_dbm"] == 25
    assert result["rssi_min"] == 10
    assert result["rssi_max"] == 40
    assert result["samples"] == 4
    assert result["ssid"] == "test-network"


def test_sample_point_reports_disconnected_node(monkeypatch) -> None:
    monkeypatch.setattr(
        survey_manual,
        "read_status",
        lambda _target: {"connected": False, "rssi_dbm": None},
    )

    result = survey_manual.sample_point("192.0.2.10:62587", count=1, interval=0)

    assert result == {"error": "采集节点未连接 Wi-Fi"}


def test_save_results_redacts_identifiers(tmp_path) -> None:
    csv_path = tmp_path / "manual_survey.csv"
    json_path = tmp_path / "manual_survey.json"
    rows = [
        {
            "point_id": "P11",
            "row": 1,
            "column": 1,
            "x_m": 0.457,
            "y_m": 0.496,
            "rssi_dbm": -51,
            "rssi_min": -55,
            "rssi_max": -48,
            "samples": 10,
            "ssid": "home-network",
            "bssid": "aa:bb:cc:dd:ee:ff",
            "channel": 6,
            "error": None,
        }
    ]

    survey_manual.save_results(
        csv_path,
        json_path,
        {"redact": True, "layout": r"C:\private\layout.json"},
        rows,
    )

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        saved_rows = list(csv.DictReader(handle))
    assert saved_rows[0]["rssi_dbm"] == "-51"
    assert saved_rows[0]["ssid"] == survey_manual.REDACTED
    assert saved_rows[0]["bssid"] == survey_manual.REDACTED
    assert saved_rows[0]["channel"] == "6"

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["settings"]["layout"] == "layout.json"
    assert payload["rows"][0]["ssid"] == survey_manual.REDACTED
