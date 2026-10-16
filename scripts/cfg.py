#!/usr/bin/env python3
"""从 config/<name>.json 里取一个字段，供 shell 调用。

用法：
    python3 cfg.py comfyui.repo
    python3 cfg.py comfyui.ref

显式使用 UTF-8 读取，避免依赖平台默认编码（Windows GBK 会炸）。
"""
import json
import os
import sys


def main() -> int:
    if len(sys.argv) != 2 or "." not in sys.argv[1]:
        print("usage: cfg.py <config-name>.<dotted.key>   e.g. cfg.py comfyui.repo", file=sys.stderr)
        return 2
    name, dotted = sys.argv[1].split(".", 1)
    bundle = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(bundle, "config", name + ".json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cur = data
    for part in dotted.split("."):
        cur = cur[part]
    print(cur)
    return 0


if __name__ == "__main__":
    sys.exit(main())
