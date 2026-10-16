#!/usr/bin/env python3
"""构建期自检：torch 版本、ComfyUI 核心文件、关键依赖、节点目录数量。

注意：ComfyUI 的 comfy.model_management 在 **import 阶段**就会执行
    total_vram = get_total_memory(get_torch_device())
进而调用 torch.cuda.current_device()，在没有 GPU 的 docker build 环境里会直接抛
    RuntimeError: Found no NVIDIA driver on your system
所以构建期**不能** import comfy.sd / comfy.model_management —— 这里只在检测到 GPU 时才导入，
真正的设备自检放到运行时的 start.sh（那时 Pod 已经有 GPU）。
"""
import os
import sys

COMFYUI_DIR = os.environ.get("COMFYUI_DIR", "/opt/ComfyUI")
sys.path.insert(0, COMFYUI_DIR)
os.chdir(COMFYUI_DIR)

import torch  # noqa: E402

print("torch:", torch.__version__, "| cuda build:", torch.version.cuda)

required = ["main.py", "comfy", "comfy_extras", "requirements.txt"]
missing = [r for r in required if not os.path.exists(os.path.join(COMFYUI_DIR, r))]
if missing:
    print("[fatal] ComfyUI 核心缺少: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
print("ComfyUI core files: OK")

import folder_paths  # noqa: E402,F401  (不依赖 CUDA)

print("folder_paths import: OK")

for mod in ("transformers", "safetensors", "aiohttp", "einops", "yaml"):
    __import__(mod)
print("关键依赖 import: OK")

cn = os.path.join(COMFYUI_DIR, "custom_nodes")
dirs = sorted(d for d in os.listdir(cn) if os.path.isdir(os.path.join(cn, d))) if os.path.isdir(cn) else []
print("custom_nodes:", len(dirs))
if not dirs:
    print("[fatal] custom_nodes 为空", file=sys.stderr)
    sys.exit(1)

if torch.cuda.is_available() and torch.cuda.device_count() > 0:
    import comfy.sd  # noqa: E402,F401
    import comfy.model_management  # noqa: E402,F401
    print("comfy.sd import: OK (GPU 可见)")
else:
    print("跳过 comfy.sd 导入：构建期无 GPU（运行时由 start.sh 自检）")

print("selfcheck: OK")
