#!/usr/bin/env python3
"""RunPod queue-based Serverless worker（ComfyUI 后端）。

一个常驻的 queue worker：容器启动时把 ComfyUI 作为子进程拉起来（或复用外部实例），
之后每个作业把一张 API 格式工作流丢给本地 ComfyUI 执行，取回输出图片。

作业输入（RunPod 队列任务的 input 字段）::

    {
      "input": {
        "workflow": { ... },                 # 必需，ComfyUI API 格式工作流（dict 或 JSON 字符串）
        "images": [                          # 可选，给 LoadImage 用的输入图（base64）
          {"name": "ref.png", "image": "<base64>", "subfolder": ""}
        ],
        "extra_data": { ... }                # 可选，原样透传给 /prompt
      }
    }

返回::

    {
      "prompt_id": "...",
      "status": {...},                       # ComfyUI 的 history status
      "images": [{"filename": "...", "type": "base64", "data": "..."}]
    }

失败时返回 `{"error": "..."}`（RunPod 会把它作为作业结果返回）。

环境变量：
    COMFY_BASE_URL       复用已在跑的 ComfyUI（默认自己拉起 127.0.0.1:8188）
    COMFY_JOB_TIMEOUT    单个作业超时秒数，默认 1800
    COMFY_POLL_INTERVAL  轮询 /history 间隔，默认 1.5
    COMFY_READY_TIMEOUT  ComfyUI 启动等待秒数，默认 600
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import runpod

COMFYUI_DIR = os.environ.get("COMFYUI_DIR", "/opt/ComfyUI")
BASE = os.environ.get("COMFY_BASE_URL", "http://127.0.0.1:8188").rstrip("/")
JOB_TIMEOUT = float(os.environ.get("COMFY_JOB_TIMEOUT", "1800"))
POLL_INTERVAL = float(os.environ.get("COMFY_POLL_INTERVAL", "1.5"))
READY_TIMEOUT = float(os.environ.get("COMFY_READY_TIMEOUT", "600"))

_proc = None


def _request(path, data=None, method=None, timeout=60, raw_body=None, content_type=None):
    headers = {"User-Agent": "comfyui-runpod-bundle/1.0"}
    body = raw_body
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type", "")


def _json(path, data=None, method=None, timeout=60):
    raw, _ = _request(path, data=data, method=method, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


def wait_until_ready(timeout=None):
    timeout = READY_TIMEOUT if timeout is None else timeout
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            _json("/system_stats", timeout=5)
            return True
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(2)
    raise RuntimeError("ComfyUI 在 %ss 内未就绪：%s" % (timeout, last_err))


def start_comfy():
    """拉起 ComfyUI 子进程；若给了 COMFY_BASE_URL 则复用外部实例。"""
    global _proc
    if os.environ.get("COMFY_BASE_URL"):
        print("[handler] 复用外部 ComfyUI: %s" % BASE, flush=True)
        wait_until_ready()
        return None
    port = urllib.parse.urlparse(BASE).port or 8188
    cmd = [sys.executable, "main.py", "--listen", "127.0.0.1", "--port", str(port), "--disable-auto-launch"]
    cmd += [a for a in os.environ.get("COMFY_EXTRA_ARGS", "").split() if a]
    print("[handler] 启动 ComfyUI: %s" % " ".join(cmd), flush=True)
    _proc = subprocess.Popen(cmd, cwd=COMFYUI_DIR)
    wait_until_ready()
    print("[handler] ComfyUI 就绪", flush=True)
    return _proc


def upload_image(name, data_b64, subfolder=""):
    """把 base64 图片写进 ComfyUI 的 input 目录（走 /upload/image）。"""
    if "," in data_b64[:64] and data_b64.strip().startswith("data:"):
        data_b64 = data_b64.split(",", 1)[1]
    blob = base64.b64decode(data_b64)
    boundary = "----comfybundle" + uuid.uuid4().hex
    parts = []
    parts.append(("--" + boundary + "\r\n").encode())
    parts.append(('Content-Disposition: form-data; name="image"; filename="%s"\r\n' % name).encode())
    parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
    parts.append(blob)
    parts.append(b"\r\n")
    parts.append(("--" + boundary + "\r\n").encode())
    parts.append(b'Content-Disposition: form-data; name="overwrite"\r\n\r\n')
    parts.append(b"true\r\n")
    parts.append(("--" + boundary + "--\r\n").encode())
    body = b"".join(parts)
    raw, _ = _request(
        "/upload/image",
        raw_body=body,
        content_type="multipart/form-data; boundary=" + boundary,
        timeout=180,
    )
    return json.loads(raw.decode("utf-8"))


def run_workflow(workflow, extra_data=None, timeout=None):
    """提交工作流并等它跑完，返回 (prompt_id, history)。"""

    timeout = JOB_TIMEOUT if timeout is None else timeout
    payload = {"prompt": workflow}
    if extra_data:
        payload["extra_data"] = extra_data
    queued = _json("/prompt", payload, method="POST", timeout=120)
    prompt_id = queued.get("prompt_id")
    if not prompt_id:
        raise RuntimeError("ComfyUI 未返回 prompt_id：%s" % queued)
    deadline = time.time() + timeout
    while time.time() < deadline:
        history = _json("/history/" + prompt_id, timeout=60)
        if prompt_id in history:
            record = history[prompt_id]
            # /history 的内层记录不一定回填 prompt_id，用 /prompt 返回的那个补上
            record.setdefault("prompt_id", prompt_id)
            return prompt_id, record
        time.sleep(POLL_INTERVAL)
    raise TimeoutError("作业 %s 超过 %ss 未完成" % (prompt_id, timeout))


def collect_images(history):
    images = []
    for node_output in (history.get("outputs") or {}).values():
        for img in node_output.get("images", []) or []:
            query = urllib.parse.urlencode(
                {
                    "filename": img.get("filename", ""),
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                }
            )
            raw, _ = _request("/view?" + query, timeout=180)
            images.append(
                {
                    "filename": img.get("filename", ""),
                    "subfolder": img.get("subfolder", ""),
                    "type": "base64",
                    "data": base64.b64encode(raw).decode("ascii"),
                }
            )
    return images


def handler(job):
    job_input = job.get("input") or {}
    workflow = job_input.get("workflow")
    if isinstance(workflow, str):
        try:
            workflow = json.loads(workflow)
        except json.JSONDecodeError as exc:
            return {"error": "input.workflow 不是合法 JSON：%s" % exc}
    if not isinstance(workflow, dict) or not workflow:
        return {"error": "input.workflow 必填，且为 ComfyUI API 格式工作流（prompt 字典）"}

    try:
        for item in job_input.get("images") or []:
            if not isinstance(item, dict) or "image" not in item:
                return {"error": "input.images 每项需形如 {name, image(base64), subfolder?}"}
            upload_image(item.get("name") or "input.png", item["image"], item.get("subfolder", ""))
        prompt_id, history = run_workflow(workflow, job_input.get("extra_data"))
        return {
            "prompt_id": prompt_id,
            "status": history.get("status"),
            "images": collect_images(history),
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        return {"error": "ComfyUI HTTP %s: %s" % (exc.code, detail)}
    except Exception as exc:  # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


if __name__ == "__main__":
    start_comfy()
    runpod.serverless.start({"handler": handler})
