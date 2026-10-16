#!/usr/bin/env bash
# 安装 ComfyUI 核心（按 pin 的 commit）+ PyTorch cu128 + 核心依赖
set -euo pipefail

BUNDLE_DIR="${BUNDLE_DIR:-/opt/bundle}"
COMFYUI_DIR="${COMFYUI_DIR:-/opt/ComfyUI}"
VENV_DIR="${VENV_DIR:-/opt/venv}"
PIP_INDEX="${PIP_INDEX:-https://pypi.org/simple}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu128}"
TORCH_SPEC="${TORCH_SPEC:-torch torchvision torchaudio}"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || command -v python)}"
COMFYUI_REPO=$("$PYTHON_BIN" "$BUNDLE_DIR/scripts/cfg.py" comfyui.repo)
COMFYUI_REF=$("$PYTHON_BIN" "$BUNDLE_DIR/scripts/cfg.py" comfyui.ref)
PIP="$VENV_DIR/bin/pip"

# 按 commit 拉取（GitHub 支持按 sha 浅取；失败则退回完整 fetch）
checkout_pinned() {
  local repo="$1" dir="$2" ref="$3"
  if [ -f "$dir/.git/HEAD" ]; then
    echo "[skip] $dir 已存在"
    return 0
  fi
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin "$repo"
  if git -C "$dir" fetch -q --depth 1 origin "$ref" 2>/dev/null; then
    git -C "$dir" checkout -q FETCH_HEAD
  else
    echo "[warn] 按 sha 浅取失败，回退完整 fetch：$repo"
    git -C "$dir" fetch -q origin
    git -C "$dir" checkout -q "$ref"
  fi
  git -C "$dir" remote set-url origin "$repo"
  echo "[ok] $(basename "$dir") @ $ref"
}

echo "==> ComfyUI 核心 $COMFYUI_REF"
checkout_pinned "$COMFYUI_REPO" "$COMFYUI_DIR" "$COMFYUI_REF"

echo "==> PyTorch ($TORCH_SPEC) from $TORCH_INDEX"
# shellcheck disable=SC2086
"$PIP" install $TORCH_SPEC --index-url "$TORCH_INDEX"

echo "==> ComfyUI requirements"
"$PIP" install -r "$COMFYUI_DIR/requirements.txt" --index-url "$PIP_INDEX"

echo "==> 完成：$("$PIP" show torch | sed -n 's/^Version: //p')"
