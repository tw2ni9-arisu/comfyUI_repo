#!/usr/bin/env python3
"""构建期自检：ComfyUI 核心能否 import、torch 版本、节点目录数量。"""
import os
import sys

COMFYUI_DIR = os.environ.get("COMFYUI_DIR", "/opt/ComfyUI")
sys.path.insert(0, COMFYUI_DIR)
os.chdir(COMFYUI_DIR)

import torch  # noqa: E402

print("torch:", torch.__version__, "| cuda build:", torch.version.cuda)

import folder_paths  # noqa: E402,F401
import comfy.sd  # noqa: E402,F401
import comfy.model_management  # noqa: E402,F401

print("ComfyUI core import: OK")

cn = os.path.join(COMFYUI_DIR, "custom_nodes")
dirs = [d for d in os.listdir(cn) if os.path.isdir(os.path.join(cn, d))] if os.path.isdir(cn) else []
print("custom_nodes:", len(dirs))
if not dirs:
    print("[fatal] custom_nodes 为空", file=sys.stderr)
    sys.exit(1)
print("selfcheck: OK")
