#!/usr/bin/env bash
# 安装 config/custom_nodes.tsv 里列出的自定义节点（按 pin 的 commit）及其依赖
set -euo pipefail

BUNDLE_DIR="${BUNDLE_DIR:-/opt/bundle}"
COMFYUI_DIR="${COMFYUI_DIR:-/opt/ComfyUI}"
VENV_DIR="${VENV_DIR:-/opt/venv}"
PIP_INDEX="${PIP_INDEX:-https://pypi.org/simple}"
PIP="$VENV_DIR/bin/pip"
NODES_FILE="$BUNDLE_DIR/config/custom_nodes.tsv"
CN_DIR="$COMFYUI_DIR/custom_nodes"

mkdir -p "$CN_DIR"

checkout_pinned() {
  local repo="$1" dir="$2" ref="$3"
  if [ -f "$dir/.git/HEAD" ]; then return 0; fi
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin "$repo"
  if git -C "$dir" fetch -q --depth 1 origin "$ref" 2>/dev/null; then
    git -C "$dir" checkout -q FETCH_HEAD
  else
    git -C "$dir" fetch -q origin
    git -C "$dir" checkout -q "$ref"
  fi
  git -C "$dir" remote set-url origin "$repo"
}

failed_nodes=""
while IFS=$'\t' read -r repo dir ref; do
  [ -z "${repo:-}" ] && continue
  case "$repo" in \#*) continue ;; esac
  echo "==> 节点 $dir"
  if ! checkout_pinned "$repo" "$CN_DIR/$dir" "$ref"; then
    echo "[error] 克隆失败：$dir"; failed_nodes="$failed_nodes $dir"; continue
  fi
  if [ -f "$CN_DIR/$dir/requirements.txt" ]; then
    if ! "$PIP" install -r "$CN_DIR/$dir/requirements.txt" --index-url "$PIP_INDEX"; then
      echo "[warn] $dir 依赖安装失败（不阻断构建）"
    fi
  fi
  if [ -f "$CN_DIR/$dir/install.py" ]; then
    ( cd "$CN_DIR/$dir" && "$VENV_DIR/bin/python" install.py ) || echo "[warn] $dir install.py 失败（不阻断构建）"
  fi
done < "$NODES_FILE"

if [ -n "$failed_nodes" ]; then
  echo "[fatal] 以下节点未安装成功:$failed_nodes" >&2
  exit 1
fi
echo "==> 自定义节点安装完成：$(( $(ls -1 "$CN_DIR" | wc -l) )) 个目录"
