#!/usr/bin/env python3
"""拍照识别家具 -> 生成布局（POC）。

调用通义千问-VL（阿里云百炼 DashScope）识别照片里的家具，
输出结构化 JSON：家具类型 + 大致方位 + 估算房间尺寸。

只依赖 Python 标准库，联网调用 DashScope 兼容接口。
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "local_config.json"
API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# 家具名称提示：用简短中文名词，后端 server.py 的 classify() 会映射到具体类型
FURNITURE_HINTS = (
    "沙发", "茶几", "餐桌", "椅子", "办公椅", "床", "衣柜", "矮柜",
    "置物架", "书架", "冰箱", "电视柜", "电视", "书桌", "床头柜",
    "边几", "绿植", "地毯", "洗衣机", "空调", "箱子",
)


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        raise SystemExit(f"缺少配置文件 {CONFIG_PATH}，请先创建并填入 dashscope_api_key。")
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def chat(messages: list[dict], model: str, api_key: str, timeout: int = 90) -> dict:
    payload = {"model": model, "messages": messages}
    req = request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"网络错误：{exc.reason}") from exc


def _extract_content(resp: dict) -> str:
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"接口返回结构异常：{json.dumps(resp, ensure_ascii=False)[:500]}") from exc


def test_connect(config: dict) -> None:
    """连通性测试：纯文本问答，验证 Key 与模型名是否可用。"""
    print(f"模型：{config['model']}")
    print("正在测试连接…")
    resp = chat(
        [{"role": "user", "content": "请只回复两个字：正常"}],
        config["model"],
        config["dashscope_api_key"],
    )
    text = _extract_content(resp)
    usage = resp.get("usage", {})
    print(f"回复：{text.strip()}")
    print(f"用量：{usage}")
    print("连通性测试通过 ✓")


def _extract_json(text: str) -> dict:
    """从模型回复里提取 JSON 对象，容忍 ```json 包裹和前后解释文字。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise RuntimeError(f"模型未返回 JSON：{text[:300]}")
    return json.loads(text[start : end + 1])


RECOGNIZE_PROMPT = """你是家居空间识别助手。仔细看这张房间照片，识别其中的主要家具和大致布局。

请只返回一个 JSON 对象，不要任何解释文字、不要 markdown 代码块标记。格式：

{
  "room_type": "客厅/卧室/餐厅/书房/厨房/其他 之一",
  "estimated_room": {"width_m": 数字, "depth_m": 数字},
  "items": [
    {"name": "家具中文名", "position": "方位", "note": "补充说明，可为空"}
  ]
}

规则：
1. name 用简短的中文名词，尽量从以下词里选，没有完全匹配的用最接近的：沙发、茶几、餐桌、椅子、办公椅、床、衣柜、矮柜、置物架、书架、冰箱、电视柜、电视、书桌、床头柜、边几、绿植、地毯、洗衣机、空调、箱子
2. position 用相对房间的方位：靠左墙 / 靠右墙 / 靠上墙 / 靠下墙 / 中央 / 左上 / 右上 / 左下 / 右下，可加“附近”“靠墙”等说明
3. estimated_room 的单位是米，参考照片里的门高（约2米）、单人床宽（约1.2米）、沙发长（约1.8-2米）、地砖等参照物估算，不要瞎编
4. 忽略小杂物和装饰品，只保留主要家具；没有把握的家具不要写；同款多件家具可合并为一条并在 note 里写数量
5. 如果照片里看不出房间边界或不是室内，estimated_room 填 0 并在 items 里说明"""


def recognize_bytes(data: bytes, mime: str, config: dict) -> dict:
    """识别一张房间照片（字节流），返回结构化 JSON。

    供 server.py 直接调用，避免落盘。
    """
    b64 = base64.b64encode(data).decode("ascii")
    resp = chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": RECOGNIZE_PROMPT},
                ],
            }
        ],
        config["model"],
        config["dashscope_api_key"],
    )
    text = _extract_content(resp)
    parsed = _extract_json(text)
    return {
        "raw": text,
        "parsed": parsed,
        "usage": resp.get("usage", {}),
    }


def recognize(image_path: str | Path, config: dict) -> dict:
    """识别一张房间照片（文件路径），返回结构化 JSON。"""
    path = Path(image_path)
    if not path.is_file():
        raise SystemExit(f"图片不存在：{path}")
    mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return recognize_bytes(path.read_bytes(), mime, config)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = sys.argv[1:]
    if not args or args[0] in ("--test", "test"):
        config = load_config()
        test_connect(config)
        return

    image = args[0]
    config = load_config()
    result = recognize(image, config)
    print(json.dumps(result["parsed"], ensure_ascii=False, indent=2))
    print("\n-- 原始回复 --")
    print(result["raw"])
    print("\n-- 用量 --")
    print(result["usage"])


if __name__ == "__main__":
    main()
