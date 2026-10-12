"""视频：元数据探测与逐帧解码（PyAV）。"""
from __future__ import annotations

from typing import Dict, Iterator, Optional

import av
from PIL import Image


def probe(path: str) -> Dict[str, object]:
    """读容器/流信息；某些 ComfyUI 输出会把 workflow 写在 metadata 里。"""
    out: Dict[str, object] = {}
    try:
        with av.open(path) as container:
            stream = next((s for s in container.streams if s.type == "video"), None)
            if stream is None:
                return out
            out["width"] = int(stream.width or 0)
            out["height"] = int(stream.height or 0)
            try:
                out["fps"] = round(float(stream.average_rate), 3) if stream.average_rate else 0
            except Exception:
                out["fps"] = 0
            dur = None
            if container.duration:
                dur = float(container.duration) / av.time_base
            elif stream.duration and stream.time_base:
                dur = float(stream.duration * stream.time_base)
            out["duration"] = round(dur, 2) if dur else 0
            out["codec"] = stream.codec_context.name if stream.codec_context else ""
            meta = dict(container.metadata or {})
            for k in ("comment", "description", "prompt", "workflow"):
                if k in meta:
                    out[k] = str(meta[k])[:4000]
    except Exception:
        return out
    return out


def iter_frames(path: str, max_size: int = 640,
                start: float = 0.0) -> Iterator[Image.Image]:
    """按时间轴解码并缩放到 max_size（长边），产出 PIL 图像。"""
    with av.open(path) as container:
        stream = container.streams.video[0]
        fps = float(stream.average_rate) if stream.average_rate else 24.0
        if start:
            try:
                container.seek(int(start / stream.time_base), stream=stream)
            except Exception:
                pass
        for frame in container.decode(stream):
            img = frame.to_image()
            w, h = img.size
            scale = min(1.0, max_size / max(w, h))
            if scale < 1.0:
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            yield img, fps


def thumbnail(path: str, max_size: int = 320) -> Optional[Image.Image]:
    """取中间附近一帧做封面。"""
    try:
        dur = float(probe(path).get("duration") or 0)
        start = max(dur / 3, 0)
        for img, _ in iter_frames(path, max_size=max_size, start=start):
            return img
    except Exception:
        return None
    return None


def seek_frame(path: str, time_sec: float = 0.0,
                max_size: int = 640) -> Optional[Image.Image]:
    """精确跳到指定秒数那一帧并返回缩放后的 PIL 图像（用于帧定位/导出）。

    用容器级 seek 到目标时间戳附近再解码，取到第一帧即返回，避免逐帧遍历。
    """
    try:
        with av.open(path) as container:
            stream = container.streams.video[0]
            tb = float(stream.time_base) if stream.time_base else 0.0
            if tb and time_sec > 0:
                try:
                    container.seek(int(time_sec / tb), stream=stream, backward=True)
                except Exception:
                    pass
            for frame in container.decode(stream):
                img = frame.to_image()
                w, h = img.size
                scale = min(1.0, max_size / max(w, h))
                if scale < 1.0:
                    img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                return img
    except Exception:
        return None
    return None
