# ComfyUI RunPod Bundle

把 **ComfyUI v0.40.0 + 30 个自定义节点 + 41.04 GB 模型清单** 打包成一个可直接导入 RunPod 的仓库。

- **有**：Dockerfile、ComfyUI/节点的固定 commit、逐节点依赖安装脚本、21 个模型文件的 URL + SHA256 清单、下载器、容器入口脚本、GitHub Actions 自动构建镜像
- **没有**：模型二进制（GitHub 单文件 100 MB / 仓库 1 GB 上限，这里是 41 GB）、工作流 JSON

---

## 1. 快速开始

### 方式 A：推到 GitHub → Actions 构建镜像 → RunPod 模板（推荐）

1. 把本目录推到 GitHub 仓库
2. `.github/workflows/docker-publish.yml` 自动构建并推送镜像 `ghcr.io/<用户名>/<仓库名>:latest`
3. RunPod → **Templates → New Template**：

   | 配置项 | 值 |
   |---|---|
   | Container Image | `ghcr.io/<用户名>/<仓库名>:latest` |
   | Container Disk | `40 GB` |
   | Volume Disk（推荐） | `80 GB`，Mount Path `/workspace` |
   | Expose HTTP Ports | `8188` |
   | Env | `MODELS_DIR=/workspace/models`、`CIVITAI_TOKEN=<你的Key>`、`AUTO_DOWNLOAD_MODELS=1` |

4. 部署 Pod，打开 8188 端口。

> 镜像不含模型：首启时 `start.sh` 调 `download_models.py` 按清单下载到 `MODELS_DIR`。挂了网络卷就只下一次。

### 方式 B：在已有 RunPod Pod 里直接跑

```bash
git clone <你的仓库> /opt/bundle
cd /opt/bundle
bash scripts/install_comfyui.sh
bash scripts/install_node_deps.sh
CIVITAI_TOKEN=xxx MODELS_DIR=/workspace/models bash start.sh
```

### 方式 C：本地 Docker

```bash
docker build -t comfyui-bundle:cu128 .
docker run --gpus all -p 8188:8188 -v comfyui-models:/workspace/models \
  -e MODELS_DIR=/workspace/models -e CIVITAI_TOKEN=xxx comfyui-bundle:cu128
```

### 推到 GitHub

```bash
git remote add origin git@github.com:<用户名>/<仓库名>.git
git push -u origin main
```

首次构建约 15–30 分钟，镜像约 20 GB。GHCR 包默认私有，RunPod 拉取要么把包设为 Public，
要么在 RunPod 模板里配镜像仓库凭据。

### 方式 D：RunPod Serverless（queue-based endpoint）

容器入口是 **`rp_handler.py`**（`CMD ["python", "-u", "/rp_handler.py"]`，即官方 worker-basic 模板的写法，
这样 GitHub 部署的 handler 预检才能顺着 Dockerfile 的 CMD 找到 `runpod.serverless.start()`）。两种模式在 `main()` 里自动分流：

- **Serverless**：RunPod 注入 `RUNPOD_ENDPOINT_ID` → 先做卷准备（秒级）并**立刻向 RunPod 注册**，ComfyUI 延迟到第一个任务时才拉起（避免初始化窗口被 1–3 分钟的加载时间拖死）
- **Pod**：没有该变量 → `exec` 转交 `start.sh`，照常启动 ComfyUI Web 服务（8188）

```bash
# 端点（endpoint）配置
Image            ghcr.io/<用户名>/<仓库名>:latest
Network Volume   80 GB（强烈建议：否则每次冷启动都要重下 41GB）
Env              MODELS_DIR=/runpod-volume/models     # ← Serverless 的卷挂载点是 /runpod-volume
                 CIVITAI_TOKEN=<你的Key>
                 AUTO_DOWNLOAD_MODELS=0               # 卷里已预热好就设 0，避免每次冷启动重新校验 41GB
```

> **挂载点别写错**：网络卷挂到 **Serverless worker 是 `/runpod-volume`**，挂到 **Pod 才是 `/workspace`**
> （见 [RunPod 文档：network volume path mapping](https://docs.runpod.io/storage/s3-api)）。
> 卷里如果空着，首次冷启动可以临时用 `AUTO_DOWNLOAD_MODELS=1` 让它自己下；更推荐先按
> 「§2 模型怎么上去」把卷预热好再设回 `0`。

**作业协议**（`rp_handler.py`）：

```json
{
  "input": {
    "workflow": { "3": {"class_type": "KSampler", "inputs": {...}}, "...": {} },
    "images": [{"name": "ref.png", "image": "<base64>", "subfolder": ""}],
    "extra_data": {}
  }
}
```

返回：

```json
{
  "prompt_id": "…",
  "status": {"status_str": "success", "completed": true},
  "images": [{"filename": "ComfyUI_00001_.png", "type": "base64", "data": "…"}]
}
```

失败时返回 `{"error": "..."}`。`input.workflow` 必填，且必须是 **API 格式**工作流
（ComfyUI 界面里「Workflow → Export (API)」导出的那份，不是 UI 格式）。
`input.images` 里的图会先通过 `/upload/image` 写进 ComfyUI 的 input 目录，供 `LoadImage` 节点使用。

> 如果你更想用官方维护的 worker，可以改用 `runpod/worker-comfyui`，把本仓库的
> `config/models.manifest.json` + `scripts/download_models.py` 接进去复用模型清单；
> 本仓库自带的 handler 是它的精简等价实现，好处是 Pod / Serverless 共用同一个镜像。

---

## 2. 模型怎么上去（三选一）

| 方案 | 做法 | 适用 |
|---|---|---|
| **① 容器首启自动下载** | `AUTO_DOWNLOAD_MODELS=1`（默认）+ `CIVITAI_TOKEN`；国内加 `HF_MIRROR=https://hf-mirror.com` | 最省事 |
| **② 本地先灌进网络卷** | `rsync -avP D:/comfyUI/models/ root@<pod>:/workspace/models/`（或 RunPod 网页上传），容器只读卷 | 想控制带宽 |
| **③ 模型烤进镜像** | Dockerfile 里加 `RUN python scripts/download_models.py` | 镜像变 ~60 GB，不推荐 |

### 往网络卷灌数据的三种方式

卷的路径映射（官方文档）：Pod 上是 `/workspace/<path>`，Serverless worker 上是 `/runpod-volume/<path>`，
S3 API 上是 `s3://<NETWORK_VOLUME_ID>/<path>`。

**① 在 Pod 里直接下载（推荐，不占本机上行）** —— 起一个便宜 Pod 挂上卷，在里面跑本仓库的下载器：

```bash
cd /workspace
git clone https://github.com/<用户名>/<仓库名> /opt/bundle
CIVITAI_TOKEN=xxx python3 /opt/bundle/scripts/download_models.py \
    --dest-root /workspace --models-dir /workspace/models --jobs 4
# 校验
python3 /opt/bundle/scripts/download_models.py --verify-only \
    --dest-root /workspace --models-dir /workspace/models
```

**② S3 兼容 API（不用开 Pod）** —— 需卷所在的机房在支持列表内（US-WA-1 / US-CA-2 / EU-CZ-1 …），
桶名 = 网络卷 ID，Access Key = Runpod 用户 ID，Secret = 控制台 S3 API Key：

```bash
aws s3 sync D:/comfyUI/models s3://<NETWORK_VOLUME_ID>/models \
    --region <DATACENTER> --endpoint-url https://s3api-<DATACENTER>.runpod.io/
# 或 rclone（更适合 41GB，可续传、可并发）
rclone copy D:/comfyUI/models <remote>:<NETWORK_VOLUME_ID>/models --progress --transfers 8
```

**③ Pod + scp/rsync**：

```bash
scp -P <POD_PORT> -r D:/comfyUI/models root@<POD_IP>:/workspace/models
# WSL 里更好用：rsync -avP --partial D:/comfyUI/models/ root@<POD_IP>:/workspace/models/
```

> **含 ASCII 逗号的路径**（如 `loras/细化,风格`）：RunPod 的 S3 兼容层会把整条 key 转义，
> 写不出真实逗号。存到 `models/loras/_staging/` 即可，`start.sh` 启动时会在文件系统上归位到真实目录名。
> 另外卷上的 `custom_nodes/comfyui_controlnet_aux/ckpts` 会被自动软链进容器。

> 清单里 4 个 Civitai 文件（shadowforge / k2-void / Krea2Rella / masterpiece）创作者开了「需登录下载」，
> 必须提供 `CIVITAI_TOKEN`（<https://civitai.com/user/account> → API Keys）；其余文件无 token 也能从 HF 直链下。

---

## 3. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `MODELS_DIR` | 空 | 模型目录（网络卷挂载点）。留空用镜像内 `$COMFYUI_DIR/models`，容器重建会丢 |
| `AUTO_DOWNLOAD_MODELS` | `1` | 启动时自动补齐缺失模型 |
| `CIVITAI_TOKEN` | 空 | Civitai API Key |
| `HF_MIRROR` | 空 | 填 `https://hf-mirror.com` 时自动改写 HF 直链 |
| `COMFY_PORT` / `COMFY_LISTEN` | `8188` / `0.0.0.0` | 端口与监听地址 |
| `COMFY_EXTRA_ARGS` | 空 | 透传 `main.py`，如 `--lowvram` |
| `DOWNLOAD_JOBS` | `2` | 并发下载数 |
| `PIP_INDEX` / `TORCH_INDEX` | PyPI / pytorch cu128 | 国内构建可换清华源 |

---

## 4. 校验

```bash
# 只校验（不下载）：逐文件比对 size + SHA256
python scripts/download_models.py --verify-only \
    --dest-root /opt/ComfyUI --models-dir /workspace/models
```

清单：`config/models.manifest.json`（21 个文件，含 `size` / `sha256` / 多来源）。

---

## 5. 目录结构

```
.
├── Dockerfile                    # nvidia/cuda 12.8 + python3.12 + torch cu128 + ComfyUI + 节点
├── start.sh                      # 入口：准备卷 → 下载模型 → 启动 ComfyUI
├── env.example
├── config/
│   ├── comfyui.json              # ComfyUI 固定 commit / 版本组合
│   ├── custom_nodes.tsv          # 30 个节点：repo / 目录名 / commit
│   └── models.manifest.json      # 21 个模型：路径 / 大小 / SHA256 / 来源
├── scripts/
│   ├── install_comfyui.sh        # 装核心（固定 commit）
│   ├── install_node_deps.sh      # 装节点及依赖
│   ├── download_models.py        # 断点续传 + SHA256 校验 + 多源回退
│   ├── cfg.py                    # UTF-8 安全的配置读取器
│   └── selfcheck.py              # 构建期自检
└── .github/workflows/docker-publish.yml
```

---

## 6. 版本组合（与本地已验证环境一致）

| 组件 | 版本 |
|---|---|
| ComfyUI | `v0.40.0` / commit `e0a32bce` |
| Python | 3.12 |
| PyTorch | `2.11.0+cu128`（含 sm_120，RTX 50 系必需） |
| 前端包 | comfyui-frontend-package `1.55.17` |
| 自定义节点 | 30 个，全部按 commit 固定 |
| 模型 | 21 个 / 41.04 GB，全部带 SHA256 |

本地质量基线：ComfyUI 启动后 `/object_info` 共 3043 个节点类，清单涉及的 62 个节点类 100% 注册。

---

## 7. 已知缺失 / 替代（不影响启动）

| 项 | 说明 |
|---|---|
| 鬼针草 LoRA | 全网无公开源，且在原工作流中是**关闭状态**；需要时放入 `models/loras/画师/` |
| `qwen3VL4BAbliteratedComfyui_v10.safetensors` | 该文件名全网无公开源。本 bundle 收录两个可用替代：官方 `qwen3vl_4b_fp8_scaled.safetensors`（Comfy-Org）与 Krea2 专用 `qwen3vl_4b_krea2_abliterated_fp8.safetensors`（saracen9） |

---

## 8. 许可

- 本仓库脚本：MIT
- ComfyUI 本体：GPL-3.0（构建时从上游拉取，不在此仓库分发）
- 各自定义节点：遵循各自仓库许可
- 模型权重：遵循各自来源（Civitai / HuggingFace / Meta）的许可与使用条款，**本仓库不分发任何权重**
