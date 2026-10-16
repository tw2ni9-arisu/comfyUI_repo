#!/usr/bin/env python3
"""按 config/models.manifest.json 下载模型文件。

特性：
  * 断点续传（HTTP Range）、失败重试、多线程并发
  * 下载后逐文件 SHA256 校验，不符则重下
  * HuggingFace 直链可一键改写为国内镜像（--hf-mirror）
  * Civitai「需登录」文件使用 CIVITAI_TOKEN / --civitai-token
  * 支持网络卷：--models-dir 时 manifest 里 models/ 下的文件落到该目录

用法：
  python download_models.py --list
  python download_models.py --dest-root /opt/ComfyUI --models-dir /workspace/models
  python download_models.py --verify-only
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import threading
import time
import urllib.request

CHUNK = 8 * 1024 * 1024
UA = "comfyui-runpod-bundle/1.0"
PROGRESS_EVERY = 512 * 1024 * 1024
_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def resolve_dest(rel: str, dest_root: str, models_dir: str | None) -> str:
    rel = rel.replace("\\", "/")
    if models_dir and rel.startswith("models/"):
        return os.path.join(models_dir, rel[len("models/"):])
    return os.path.join(dest_root, rel)


def build_sources(entry: dict, hf_mirror: str | None, civitai_token: str | None):
    out = []
    for s in entry.get("sources", []):
        url = s["url"]
        needs_token = bool(s.get("needs_token"))
        if needs_token and not civitai_token:
            continue
        if hf_mirror and "huggingface.co" in url:
            url = url.replace("https://huggingface.co", hf_mirror.rstrip("/"))
        out.append({"url": url, "needs_token": needs_token, "kind": s.get("kind", "")})
    return out


def download_one(url: str, dest: str, token: str | None, retries: int = 5, timeout: int = 120) -> None:
    tmp = dest + ".part"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    headers = {"User-Agent": UA}
    if token:
        headers["Authorization"] = "Bearer " + token
    last_err = None
    for attempt in range(1, retries + 1):
        have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        hdrs = dict(headers)
        if have:
            hdrs["Range"] = f"bytes={have}-"
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                if have and status == 200:
                    have = 0  # 服务端忽略了 Range，从头来
                mode = "ab" if have else "wb"
                written = have
                next_report = written + PROGRESS_EVERY
                with open(tmp, mode) as f:
                    while True:
                        b = resp.read(CHUNK)
                        if not b:
                            break
                        f.write(b)
                        written += len(b)
                        if written >= next_report:
                            log(f"      ... {os.path.basename(dest)} {human(written)}")
                            next_report = written + PROGRESS_EVERY
            os.replace(tmp, dest)
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(min(30, 3 * attempt))
    raise RuntimeError(f"下载失败（{retries} 次重试）：{url} -> {last_err}")


def verify(entry: dict, dest: str) -> str:
    if not os.path.exists(dest):
        return "missing"
    size = os.path.getsize(dest)
    if entry.get("size") and size != entry["size"]:
        return f"size({human(size)} != {human(entry['size'])})"
    if entry.get("sha256"):
        actual = sha256_file(dest)
        if actual != entry["sha256"]:
            return f"sha256({actual[:12]}... != {entry['sha256'][:12]}...)"
    return "ok"


def handle(entry: dict, args, token: str | None):
    dest = resolve_dest(entry["path"], args.dest_root, args.models_dir)
    state = verify(entry, dest)
    if state == "ok" and not args.force:
        return entry["path"], "skip", dest
    sources = build_sources(entry, args.hf_mirror, token)
    if not sources:
        return entry["path"], "no-source", dest
    if state != "missing" and not args.force:
        log(f"   校验不符（{state}），重新下载：{entry['path']}")
        try:
            os.remove(dest)
        except OSError:
            pass
    last = None
    for s in sources:
        try:
            download_one(s["url"], dest, token if s["needs_token"] else None)
            st = verify(entry, dest)
            if st == "ok":
                return entry["path"], "ok", dest
            last = st
            os.remove(dest)
        except Exception as e:  # noqa: BLE001
            last = str(e)
    return entry["path"], f"failed({last})", dest


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="下载 ComfyUI 模型包")
    ap.add_argument("--manifest", default=os.path.join(here, "config", "models.manifest.json"))
    ap.add_argument("--dest-root", default=os.environ.get("COMFYUI_DIR", os.path.dirname(here)))
    ap.add_argument("--models-dir", default=os.environ.get("MODELS_DIR") or None)
    ap.add_argument("--civitai-token", default=os.environ.get("CIVITAI_TOKEN") or None)
    ap.add_argument("--hf-mirror", default=os.environ.get("HF_MIRROR") or None)
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("DOWNLOAD_JOBS", "2")))
    ap.add_argument("--only", action="append", default=[], help="只处理路径包含该子串的条目，可重复")
    ap.add_argument("--list", action="store_true", help="只列出条目与落盘位置（含校验结果）")
    ap.add_argument("--verify-only", action="store_true", help="只校验，不下载")
    ap.add_argument("--force", action="store_true", help="已存在也重下")
    args = ap.parse_args()

    with open(args.manifest, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    files = manifest["files"]
    if args.only:
        files = [e for e in files if any(k in e["path"] for k in args.only)]

    if args.list or args.verify_only:
        rc = 0
        for e in files:
            dest = resolve_dest(e["path"], args.dest_root, args.models_dir)
            st = verify(e, dest)
            if st != "ok":
                rc = 1
            print(f"[{st:>10}] {human(e.get('size', 0)):>9}  {dest}")
        print(f"\n共 {len(files)} 个文件；退出码 {rc}")
        return 0 if (args.list or rc == 0) else 1

    if not args.civitai_token and any(
        s.get("needs_token") for e in files for s in e.get("sources", [])
    ):
        log("[提示] 有 Civitai 受限文件但未提供 CIVITAI_TOKEN，将尝试其它来源")

    log(f"==> 目标根目录：{args.dest_root}")
    if args.models_dir:
        log(f"==> models/ 落到：{args.models_dir}")
    log(f"==> 共 {len(files)} 个文件，并发 {args.jobs}\n")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futs = {pool.submit(handle, e, args, args.civitai_token): e for e in files}
        for fut in concurrent.futures.as_completed(futs):
            path, state, dest = fut.result()
            results.append((path, state, dest))
            log(f"[{state:>8}] {path}")

    ok = [r for r in results if r[1] in ("ok", "skip")]
    bad = [r for r in results if r[1] not in ("ok", "skip")]
    log(f"\n完成：{len(ok)}/{len(results)} 就绪")
    for path, state, dest in bad:
        log(f"  未就绪 [{state}] {path}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
