"""Torch-free inference for the dense five-class Wi-Fi model.

Mirrors DenseClassifier in train_dense_classifier.py with NumPy so the demo
does not need torch (Windows Smart App Control blocks torch DLLs here).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

from pt_numpy import load_checkpoint

HERE = Path(__file__).resolve().parent
NX, NY = 76, 64
W, H = 8.23, 6.95
MODELS = {"empty": "empty_model.pt", "furnished": "furniture_model.pt"}
LABELS = ["弱", "较弱", "中", "较强", "强"]


def conv2d(x: np.ndarray, weight: np.ndarray, bias: np.ndarray) -> np.ndarray:
    """3x3, stride 1, padding 1."""
    padded = np.pad(x, ((0, 0), (0, 0), (1, 1), (1, 1)))
    windows = np.lib.stride_tricks.sliding_window_view(padded, (3, 3), axis=(2, 3))
    out = np.einsum("nchwij,ocij->nohw", windows, weight, optimize=True)
    out += bias[None, :, None, None]
    return out.astype(np.float32, copy=False)


def group_norm(x: np.ndarray, weight: np.ndarray, bias: np.ndarray, groups: int = 4) -> np.ndarray:
    n, c, h, w = x.shape
    reshaped = x.reshape(n, groups, c // groups, h, w)
    mean = reshaped.mean(axis=(2, 3, 4), keepdims=True)
    var = reshaped.var(axis=(2, 3, 4), keepdims=True)
    normed = (reshaped - mean) / np.sqrt(var + 1e-5)
    normed = normed.reshape(n, c, h, w)
    return normed * weight[None, :, None, None] + bias[None, :, None, None]


def silu(x: np.ndarray) -> np.ndarray:
    return x / (1.0 + np.exp(-x))


def avg_pool2(x: np.ndarray) -> np.ndarray:
    n, c, h, w = x.shape
    return x.reshape(n, c, h // 2, 2, w // 2, 2).mean(axis=(3, 5))


def _source_index(dst: np.ndarray, in_size: int, out_size: int) -> np.ndarray:
    scale = in_size / out_size
    return np.maximum((dst + 0.5) * scale - 0.5, 0.0)


def bilinear(x: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    n, c, h, w = x.shape
    sy = _source_index(np.arange(out_h, dtype=np.float64), h, out_h)
    sx = _source_index(np.arange(out_w, dtype=np.float64), w, out_w)
    y0 = np.floor(sy).astype(np.int64)
    x0 = np.floor(sx).astype(np.int64)
    y1 = np.where(y0 < h - 1, y0 + 1, y0)
    x1 = np.where(x0 < w - 1, x0 + 1, x0)
    wy = sy - y0
    wx = sx - x0
    top = (
        x[:, :, y0][:, :, :, x0] * (1 - wx)[None, None, None, :]
        + x[:, :, y0][:, :, :, x1] * wx[None, None, None, :]
    )
    bottom = (
        x[:, :, y1][:, :, :, x0] * (1 - wx)[None, None, None, :]
        + x[:, :, y1][:, :, :, x1] * wx[None, None, None, :]
    )
    return top * (1 - wy)[None, None, :, None] + bottom * wy[None, None, :, None]


def block(x: np.ndarray, weights: dict, prefix: str) -> np.ndarray:
    x = conv2d(x, weights[f"{prefix}.0.weight"], weights[f"{prefix}.0.bias"])
    x = group_norm(x, weights[f"{prefix}.1.weight"], weights[f"{prefix}.1.bias"])
    x = silu(x)
    x = conv2d(x, weights[f"{prefix}.3.weight"], weights[f"{prefix}.3.bias"])
    x = group_norm(x, weights[f"{prefix}.4.weight"], weights[f"{prefix}.4.bias"])
    return silu(x)


def forward(x: np.ndarray, weights: dict) -> np.ndarray:
    a = block(x, weights, "e1")
    b = block(avg_pool2(a), weights, "e2")
    c = block(avg_pool2(b), weights, "mid")
    d = block(np.concatenate([bilinear(c, b.shape[2], b.shape[3]), b], axis=1), weights, "d2")
    joined = np.concatenate([bilinear(d, a.shape[2], a.shape[3]), a], axis=1)
    logits = block(joined, weights, "d1")
    head_w = weights["head.weight"][:, :, 0, 0]
    return np.einsum("nchw,oc->nohw", logits, head_w) + weights["head.bias"][None, :, None, None]


def build_input(observed: list[dict], mean: float, scale: float) -> np.ndarray:
    gx, gy = np.meshgrid(
        (np.arange(NX) + 0.5) * W / NX, (np.arange(NY) + 0.5) * H / NY
    )
    q = np.stack([gx.ravel(), gy.ravel()], axis=1)
    xy = np.array([[float(r["x_m"]), float(r["y_m"])] for r in observed])
    values = np.array([float(r["rssi_median_dbm"]) for r in observed])
    dist = ((q[:, None] - xy[None]) ** 2).sum(axis=2)
    weight = 1 / np.maximum(dist, 1e-12)
    base = (weight @ values) / weight.sum(axis=1)
    x = np.zeros((6, NY, NX), dtype=np.float32)
    x[2] = 1
    x[3] = (base.reshape(NY, NX) - mean) / scale
    x[4] = np.sqrt(dist.min(axis=1)).reshape(NY, NX) / np.hypot(W, H)
    x[5] = np.exp(-dist / (2 * 0.6**2)).sum(axis=1).reshape(NY, NX) / 20
    for item in observed:
        i = min(NY - 1, int(float(item["y_m"]) / H * NY))
        j = min(NX - 1, int(float(item["x_m"]) / W * NX))
        x[0, i, j] = (float(item["rssi_median_dbm"]) - mean) / scale
        x[1, i, j] = 1
    return x[None]


def read_points(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        raw = list(csv.DictReader(fh))
    observed = []
    seen = set()
    for row in raw:
        name = str(row.get("point_id", "")).strip().upper()
        if len(name) != 3 or name[0] != "P" or not name[1:].isdigit():
            raise ValueError(f"无效测点：{name!r}")
        r, c = int(name[1]), int(name[2])
        if not (1 <= r <= 7 and 1 <= c <= 9):
            raise ValueError(f"测点越界：{name}")
        if name in seen:
            raise ValueError(f"重复测点：{name}")
        seen.add(name)
        value = float(row["rssi_dbm"])
        if not np.isfinite(value):
            raise ValueError(f"测点 {name} 的 RSSI 不是有限数")
        observed.append(
            {
                "point_id": name,
                "x_m": round((c - 0.5) * W / 9, 6),
                "y_m": round((r - 0.5) * H / 7, 6),
                "rssi_median_dbm": value,
            }
        )
    if len(observed) != 20:
        raise ValueError(f"这一版模型需要正好 20 个测点，当前 {len(observed)} 个")
    return observed


def predict(environment: str, input_path) -> dict:
    """Run the model in-process and return the JSON-ready payload."""
    if environment not in MODELS:
        raise ValueError(f"未知模型：{environment}")
    observed = read_points(Path(input_path))
    config, weights = load_checkpoint(HERE / MODELS[environment])
    x = build_input(observed, config["mean_dbm"], config["scale_db"])
    logits = forward(x, weights)[0]
    shifted = logits - logits.max(axis=0, keepdims=True)
    probs = np.exp(shifted)
    probs /= probs.sum(axis=0, keepdims=True)
    classes = probs.argmax(axis=0).astype(int)
    confidence = probs.max(axis=0)
    counts = np.bincount(classes.ravel(), minlength=5).tolist()

    return {
        "environment": environment,
        "labels": LABELS,
        "grid": {"nx": NX, "ny": NY, "width_m": W, "height_m": H},
        "classes": classes.tolist(),
        "confidence": np.round(confidence, 3).tolist(),
        "points": observed,
        "stats": {
            "class_counts": counts,
            "mean_confidence": round(float(confidence.mean()), 3),
        },
        "engine": "numpy",
        "note": "64×76 五档分类由训练好的模型权重输出；示例输入为模拟值，非实测标定。",
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=MODELS, default="empty")
    parser.add_argument("--input", required=True)
    args = parser.parse_args()

    payload = predict(args.model, args.input)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
