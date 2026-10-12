"""媒体库：扫描输出目录、生成缩略图缓存、读取生成参数。"""
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PIL import Image

from . import meta, video

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


@dataclass
class MediaItem:
    path: str
    name: str
    kind: str = ""          # image / video
    size: int = 0
    mtime: int = 0
    width: int = 0
    height: int = 0
    meta: Dict[str, object] = field(default_factory=dict)

    @property
    def size_mb(self) -> float:
        return self.size / 1048576

    @property
    def prompt(self) -> str:
        return str(self.meta.get("positive") or self.meta.get("prompt") or "")

    @property
    def checkpoint(self) -> str:
        return str(self.meta.get("checkpoint") or "")

    @property
    def seed(self) -> str:
        return str(self.meta.get("seed") or "")


class MediaLibrary:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._lock = threading.RLock()

    # ---------- 扫描 ----------
    def scan(self, dirs: List[str], recursive: bool = True,
             limit: int = 2000) -> List[MediaItem]:
        items: List[MediaItem] = []
        for d in dirs:
            if not d or not os.path.isdir(d):
                continue
            if recursive:
                walker = os.walk(d)
            else:
                walker = [(d, [], os.listdir(d))]
            for root, subdirs, files in walker:
                subdirs[:] = [s for s in subdirs if s not in {"__pycache__", ".git"}]
                for fn in files:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in IMAGE_EXT:
                        kind = "image"
                    elif ext in VIDEO_EXT:
                        kind = "video"
                    else:
                        continue
                    full = os.path.join(root, fn)
                    try:
                        st = os.stat(full)
                    except Exception:
                        continue
                    items.append(MediaItem(path=full, name=fn, kind=kind,
                                           size=st.st_size, mtime=int(st.st_mtime)))
                    if len(items) >= limit:
                        return self._sorted(items)
        return self._sorted(items)

    @staticmethod
    def _sorted(items: List[MediaItem]) -> List[MediaItem]:
        return sorted(items, key=lambda x: x.mtime, reverse=True)

    # ---------- 缩略图 ----------
    def _cache_path(self, item: MediaItem, size: int) -> str:
        key = hashlib.md5(f"{item.path}|{item.mtime}".encode()).hexdigest()
        return os.path.join(self.cache_dir, f"{key}_{size}.jpg")

    def thumb(self, item: MediaItem, size: int = 180) -> Optional[Image.Image]:
        cp = self._cache_path(item, size)
        if os.path.isfile(cp):
            try:
                return Image.open(cp).convert("RGB")
            except Exception:
                pass
        img: Optional[Image.Image] = None
        try:
            if item.kind == "image":
                with Image.open(item.path) as im:
                    im = im.convert("RGB")
                    w, h = im.size
                    item.width, item.height = w, h
                    scale = min(1.0, size / max(w, h))
                    img = im.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                                    Image.LANCZOS)
            else:
                info = video.probe(item.path)
                item.width = int(info.get("width") or 0)
                item.height = int(info.get("height") or 0)
                img = video.thumbnail(item.path, max_size=size)
        except Exception:
            img = None
        if img is not None:
            try:
                img.save(cp, "JPEG", quality=82)
            except Exception:
                pass
        return img

    def full_image(self, item: MediaItem, max_size: int = 1200) -> Optional[Image.Image]:
        try:
            with Image.open(item.path) as im:
                im = im.convert("RGB")
                w, h = im.size
                scale = min(1.0, max_size / max(w, h))
                if scale < 1.0:
                    im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                return im
        except Exception:
            return None

    # ---------- 元数据 ----------
    def load_meta(self, item: MediaItem) -> Dict[str, object]:
        if not item.meta:
            item.meta = meta.extract(item.path)
            if not item.width:
                try:
                    with Image.open(item.path) as im:
                        item.width, item.height = im.size
                except Exception:
                    pass
        return item.meta

    def delete(self, item: MediaItem) -> bool:
        try:
            os.remove(item.path)
        except Exception:
            return False
        for f in os.listdir(self.cache_dir):
            if f.startswith(hashlib.md5(f"{item.path}|".encode()).hexdigest()[:8]):
                try:
                    os.remove(os.path.join(self.cache_dir, f))
                except Exception:
                    pass
        return True
