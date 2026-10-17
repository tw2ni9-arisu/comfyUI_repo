#!/usr/bin/env bash
# 容器入口：准备模型目录 -> 可选自动下载模型 -> 启动 ComfyUI
set -euo pipefail

BUNDLE_DIR="${BUNDLE_DIR:-/opt/bundle}"
COMFYUI_DIR="${COMFYUI_DIR:-/opt/ComfyUI}"
VENV_DIR="${VENV_DIR:-/opt/venv}"
PY="$VENV_DIR/bin/python"
MODELS_DIR="${MODELS_DIR:-}"
AUTO_DOWNLOAD_MODELS="${AUTO_DOWNLOAD_MODELS:-1}"
COMFY_PORT="${COMFY_PORT:-8188}"
COMFY_LISTEN="${COMFY_LISTEN:-0.0.0.0}"

MODEL_SUBDIRS="checkpoints clip clip_vision configs controlnet diffusers diffusion_models \
embeddings hypernetworks loras model_patches photomaker style_models text_encoders unet \
upscale_models vae sams ultralytics SEEDVR2 audio_encoders background_removal detection \
frame_interpolation geometry_estimation optical_flow"

if [ -n "$MODELS_DIR" ]; then
  echo "==> 模型目录（外部卷）：$MODELS_DIR"
  for sub in $MODEL_SUBDIRS; do mkdir -p "$MODELS_DIR/$sub"; done
  cat > "$COMFYUI_DIR/extra_model_paths.yaml" <<YAML
# 由 start.sh 生成：把模型目录指向外部卷
comfyui_bundle:
    base_path: $MODELS_DIR
    checkpoints: checkpoints
    clip: clip
    clip_vision: clip_vision
    configs: configs
    controlnet: controlnet
    diffusers: diffusers
    diffusion_models: diffusion_models
    embeddings: embeddings
    hypernetworks: hypernetworks
    loras: loras
    model_patches: model_patches
    photomaker: photomaker
    style_models: style_models
    text_encoders: text_encoders
    unet: unet
    upscale_models: upscale_models
    vae: vae
    sams: sams
    ultralytics: ultralytics
YAML

  # RunPod 的 S3 兼容层会把含 ASCII 逗号的 key 整体转义，写不出 `loras/细化,风格` 这种目录名，
  # 所以这类文件先传到 `loras/_staging`，这里在文件系统上归位（只生效一次，之后是空操作）。
  if [ -d "$MODELS_DIR/loras/_staging" ]; then
    echo "==> 归位 loras/_staging -> loras/细化,风格"
    mkdir -p "$MODELS_DIR/loras/细化,风格"
    for f in "$MODELS_DIR/loras/_staging/"*; do
      if [ -e "$f" ]; then mv -f "$f" "$MODELS_DIR/loras/细化,风格/"; fi
    done
    rmdir "$MODELS_DIR/loras/_staging" 2>/dev/null || true
  fi

  # 卷上的 controlnet_aux ckpts（上传时落在 custom_nodes/ 下）链进容器内的节点目录
  VOL_CKPTS="$MODELS_DIR/custom_nodes/comfyui_controlnet_aux/ckpts"
  NODE_DIR="$COMFYUI_DIR/custom_nodes/comfyui_controlnet_aux"
  if [ -d "$VOL_CKPTS" ] && [ -d "$NODE_DIR" ]; then
    rm -rf "$NODE_DIR/ckpts"
    ln -s "$VOL_CKPTS" "$NODE_DIR/ckpts"
    echo "==> controlnet_aux ckpts 已指向卷"
  fi
fi

if [ "$AUTO_DOWNLOAD_MODELS" = "1" ]; then
  echo "==> 检查 / 下载模型"
  dl_args=(--dest-root "$COMFYUI_DIR" --jobs "${DOWNLOAD_JOBS:-2}")
  if [ -n "$MODELS_DIR" ]; then dl_args+=(--models-dir "$MODELS_DIR"); fi
  "$PY" "$BUNDLE_DIR/scripts/download_models.py" "${dl_args[@]}" \
      || echo "[warn] 模型未全部就绪，继续启动（可在容器内重跑 download_models.py）"
fi

echo "==> 设备自检"
"$PY" - <<'PY'
import torch
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    print("GPU: %s | VRAM %.1f GB | torch %s (cuda %s)" % (p.name, p.total_memory / 1024**3, torch.__version__, torch.version.cuda))
else:
    print("[warn] 未检测到 GPU，ComfyUI 将以 CPU 运行 —— 请确认 Pod 选了 GPU")
PY

if [ "${BUNDLE_PREPARE_ONLY:-}" = "1" ]; then
  echo "==> 仅准备模式：卷已就绪，交给 handler.py 启动 ComfyUI"
  exit 0
fi

echo "==> 启动 ComfyUI: http://$COMFY_LISTEN:$COMFY_PORT"
cd "$COMFYUI_DIR"
run_args=(main.py --listen "$COMFY_LISTEN" --port "$COMFY_PORT")
if [ -n "${COMFY_EXTRA_ARGS:-}" ]; then
  read -r -a extra <<< "$COMFY_EXTRA_ARGS"
  run_args+=("${extra[@]}")
fi
exec "$PY" "${run_args[@]}"
