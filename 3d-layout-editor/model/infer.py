"""Run the trained dense classifier on exactly 20 RSSI points and print JSON.

Input CSV columns: point_id,rssi_dbm  (point_id = P{row}{col}, row 1-7, col 1-9)
Output: 64x76 five-class grid, per-cell confidence, and the input points.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from train_dense_classifier import DenseClassifier, build_input, NX, NY, W, H  # noqa: E402

MODELS = {
    "empty": "empty_model.pt",
    "furnished": "furniture_model.pt",
}
LABELS = ["弱", "较弱", "中", "较强", "强"]


def read_points(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def to_observed(rows: list[dict]) -> list[dict]:
    observed: list[dict] = []
    seen: set[str] = set()
    for raw in rows:
        name = str(raw.get("point_id", "")).strip().upper()
        if len(name) != 3 or name[0] != "P" or not name[1:].isdigit():
            raise ValueError(f"无效测点：{name!r}，应为 P{1}-P79")
        row, col = int(name[1]), int(name[2])
        if not (1 <= row <= 7 and 1 <= col <= 9):
            raise ValueError(f"测点越界：{name}，行 1-7、列 1-9")
        if name in seen:
            raise ValueError(f"重复测点：{name}")
        seen.add(name)
        try:
            value = float(raw.get("rssi_dbm"))
        except (TypeError, ValueError):
            raise ValueError(f"测点 {name} 的 RSSI 不是数字")
        if not np.isfinite(value):
            raise ValueError(f"测点 {name} 的 RSSI 不是有限数")
        observed.append(
            {
                "point_id": name,
                "x_m": round((col - 0.5) * W / 9, 6),
                "y_m": round((row - 0.5) * H / 7, 6),
                "rssi_median_dbm": value,
            }
        )
    if len(observed) != 20:
        raise ValueError(f"这一版模型需要正好 20 个测点，当前 {len(observed)} 个")
    return observed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=MODELS, default="empty")
    parser.add_argument("--input", required=True)
    args = parser.parse_args()

    observed = to_observed(read_points(Path(args.input)))
    checkpoint = torch.load(
        HERE / MODELS[args.model], map_location="cpu", weights_only=True
    )
    config = checkpoint["config"]
    model = DenseClassifier()
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    torch.set_num_threads(2)

    x, _ = build_input(observed, config["mean_dbm"], config["scale_db"])
    with torch.no_grad():
        logits = model(torch.from_numpy(x)[None])
        probs = torch.softmax(logits, dim=1)[0]
        classes = probs.argmax(dim=0).numpy().astype(int)
        confidence = probs.max(dim=0).values.numpy()

    counts = np.bincount(classes.ravel(), minlength=5).tolist()
    payload = {
        "environment": args.model,
        "labels": LABELS,
        "grid": {"nx": NX, "ny": NY, "width_m": W, "height_m": H},
        "classes": classes.tolist(),
        "confidence": np.round(confidence, 3).tolist(),
        "points": observed,
        "stats": {
            "class_counts": counts,
            "mean_confidence": round(float(confidence.mean()), 3),
        },
        "note": "64×76 五档分类由训练好的模型输出；输入若为示例 CSV 则为模拟值，非实测标定。",
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
