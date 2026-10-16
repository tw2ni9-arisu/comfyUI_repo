# syntax=docker/dockerfile:1.7
#
# ComfyUI RunPod Bundle —— 一键构建可导入 RunPod 的镜像
# 构建：  docker build -t comfyui-bundle:cu128 .
# 运行：  docker run --gpus all -p 8188:8188 -v comfyui-models:/workspace/models \
#                  -e MODELS_DIR=/workspace/models comfyui-bundle:cu128
#
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ARG PIP_INDEX=https://pypi.org/simple
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu128
ARG TORCH_SPEC="torch torchvision torchaudio"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    COMFYUI_DIR=/opt/ComfyUI \
    BUNDLE_DIR=/opt/bundle \
    VENV_DIR=/opt/venv \
    COMFY_PORT=8188 \
    HF_HOME=/opt/hf-cache

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3.12 python3.12-venv python3-pip \
        git curl ca-certificates aria2 \
        ffmpeg libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
        build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN python3.12 -m venv "$VENV_DIR" \
    && "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel --index-url "$PIP_INDEX"

ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /opt
COPY . "$BUNDLE_DIR"

# 1) ComfyUI 核心（按 pin 的 commit）+ PyTorch cu128 + 核心依赖
RUN PIP_INDEX="$PIP_INDEX" TORCH_INDEX="$TORCH_INDEX" TORCH_SPEC="$TORCH_SPEC" \
    bash "$BUNDLE_DIR/scripts/install_comfyui.sh"

# 2) 自定义节点（按 pin 的 commit）+ 每个节点自己的依赖
RUN bash "$BUNDLE_DIR/scripts/install_node_deps.sh"

# 3) 自检：确认核心与节点能 import（构建期失败好过运行时才发现）
RUN python "$BUNDLE_DIR/scripts/selfcheck.py"

WORKDIR $COMFYUI_DIR
EXPOSE 8188
VOLUME ["/workspace"]
CMD ["bash", "/opt/bundle/start.sh"]
