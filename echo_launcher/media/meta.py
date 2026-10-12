"""生成参数提取：从 ComfyUI 写进图片/视频的元数据里还原提示词与参数。"""
from __future__ import annotations

import json
import struct
import zlib
from typing import Dict, List

from PIL import Image

# ---------- PNG ----------
def png_text_chunks(path: str) -> Dict[str, str]:
    """读 PNG 的 tEXt / iTXt / zTXt 块。"""
    out: Dict[str, str] = {}
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return out
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return out
    pos = 8
    n = len(data)
    while pos + 8 <= n:
        try:
            (length,) = struct.unpack_from(">I", data, pos)
            ctype = data[pos + 4:pos + 8]
        except Exception:
            break
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IEND":
            break
        if ctype == b"tEXt":
            if b"\x00" in body:
                k, v = body.split(b"\x00", 1)
                try:
                    out[k.decode("latin1")] = v.decode("utf-8", "replace")
                except Exception:
                    pass
        elif ctype == b"iTXt":
            parts = body.split(b"\x00", 3)
            if len(parts) >= 4:
                try:
                    out[parts[0].decode("latin1")] = parts[3].decode("utf-8", "replace")
                except Exception:
                    pass
        elif ctype == b"zTXt":
            if b"\x00" in body:
                k, rest = body.split(b"\x00", 1)
                try:
                    out[k.decode("latin1")] = zlib.decompress(rest).decode("utf-8", "replace")
                except Exception:
                    pass
    return out


def read_sidecar_txt(path: str) -> str:
    """同名 .txt 兜底（部分工作流/插件会把提示词写在这里）。"""
    import os
    p = os.path.splitext(path)[0] + ".txt"
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def load_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


# ---------- 从 workflow / prompt JSON 里挑参数 ----------
def parse_prompt_graph(data) -> Dict[str, object]:
    """ComfyUI 的 API 格式：{"1": {"class_type": ..., "inputs": {...}}, ...}"""
    if not isinstance(data, dict):
        return {}
    info: Dict[str, object] = {}
    pos_parts: List[str] = []
    neg_parts: List[str] = []
    loras: List[str] = []

    for _, node in data.items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        inputs = node.get("inputs", {}) or {}

        if ct == "KSampler" or ct.startswith("KSampler"):
            for key, target in (("seed", "seed"), ("steps", "steps"), ("cfg", "cfg"),
                                ("sampler_name", "sampler"), ("scheduler", "scheduler"),
                                ("denoise", "denoise")):
                if key in inputs:
                    info[target] = inputs[key]
        elif ct in ("KSamplerAdvanced",):
            info.setdefault("sampler", inputs.get("sampler_name"))
            info.setdefault("steps", inputs.get("steps"))
        elif ct in ("CheckpointLoaderSimple", "ImageOnlyCheckpointLoader"):
            info.setdefault("checkpoint", inputs.get("ckpt_name"))
        elif ct == "VAELoader":
            info.setdefault("vae", inputs.get("vae_name"))
        elif ct == "LoraLoader":
            if inputs.get("lora_name"):
                loras.append(inputs["lora_name"])
        elif ct == "CLIPTextEncode":
            t = inputs.get("text")
            if isinstance(t, str) and t.strip():
                # 简易判定：含负面常见词的归到 negative
                low = t.lower()
                if any(w in low for w in ("badhand", "worst quality", "low quality",
                                          "bad anatomy", "blurry", "watermark", "ugly")):
                    neg_parts.append(t)
                else:
                    pos_parts.append(t)
        elif ct == "EmptyLatentImage":
            info.setdefault("width", inputs.get("width"))
            info.setdefault("height", inputs.get("height"))
        elif ct == "EmptySD3LatentImage":
            info.setdefault("width", inputs.get("width"))
            info.setdefault("height", inputs.get("height"))

    if pos_parts:
        info["positive"] = max(pos_parts, key=len)
    if neg_parts:
        info["negative"] = max(neg_parts, key=len)
    if loras:
        info["loras"] = loras
    return info


def extract(path: str) -> Dict[str, object]:
    """统一入口：图片读 PNG/EXIF/同名txt，视频读容器元数据。"""
    import os
    ext = os.path.splitext(path)[1].lower()
    info: Dict[str, object] = {}
    if ext == ".png":
        chunks = png_text_chunks(path)
        for key in ("prompt", "workflow"):
            if key in chunks:
                data = load_json(chunks[key])
                if key == "prompt" and data:
                    info.update(parse_prompt_graph(data))
                info["has_" + key] = bool(data)
    elif ext in (".jpg", ".jpeg", ".webp"):
        try:
            with Image.open(path) as im:
                raw = im.info.get("exif") or b""
                if isinstance(raw, bytes) and b"prompt" in raw:
                    # EXIF 里的 UserComment 常带 JSON，直接粗暴找第一个 {
                    s = raw.decode("utf-8", "replace")
                    i, j = s.find("{"), s.rfind("}")
                    if i >= 0 and j > i:
                        data = load_json(s[i:j + 1])
                        if data:
                            info.update(parse_prompt_graph(data))
        except Exception:
            pass
    elif ext in (".mp4", ".webm", ".mov", ".avi", ".mkv"):
        try:
            from .video import probe
            info.update(probe(path))
        except Exception:
            pass

    if not info.get("positive"):
        txt = read_sidecar_txt(path)
        if txt.strip():
            info["positive"] = txt.strip()[:4000]
    return info
