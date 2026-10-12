"""媒体库适配（Echo 的 media/*）。

- 扫描输出目录、缩略图缓存：`echo_launcher.media.browser.MediaLibrary`
- 生成参数提取：`media.meta.extract()`（PNG tEXt/zTXt + EXIF + 同名 txt + 视频元数据）
- 视频探测/帧定位：`media.video.probe/seek_frame`

适配层额外提供 **PIL → QPixmap** 的转换（Qt 界面要用），
其余逻辑一律走 Echo。
"""
from __future__ import annotations

import io
import json
import os
import threading
import time

from echo_launcher.media import video as _video
from echo_launcher.media.browser import MediaItem, MediaLibrary

from . import _bridge as B

__all__ = [
    "MediaItem", "library", "scan", "thumb_pixmap_bytes", "full_pixmap_bytes",
    "load_meta", "delete", "probe", "seek_pixmap_bytes", "save_frame",
    "meta_lines",
    # 分组 / 标签 / 导入（本次新增）
    "GROUP_MODES", "group_items", "all_tags", "tags_for", "set_tags",
    "add_tags", "remove_tag", "import_files", "collect_paths",
]

# --------------------------------------------------------------------------
# 标签（新增功能：Echo 的媒体库没有标签概念，这里自己存）
# --------------------------------------------------------------------------
_TAGS_FILE = os.path.join(B.DATA_DIR, "media_tags.json")
_tags_lock = threading.RLock()
#: `_load_tags()` 的缓存：`{"v": ((mtime_ns, size), 解析好的 dict)}`
_tags_cache: dict = {}


def _load_tags() -> dict:
    """读标签表（**带缓存**）。

    ⚠ 这里原来每次调用都重新 `open` + `json.load`，而 `tags_for()` 是**逐条**调它的 ——
    媒体页筛选、`_render()` 给每个格子拼标签、搜索命中判定，一圈下来就是"同一个文件读 N 次"。
    现在按 `(mtime_ns, size)` 校验：文件没动就直接给内存里那份。
    返回**浅拷贝**，调用方随便改都不污染缓存；`_save_tags()` 会清掉缓存。
    """
    try:
        st = os.stat(_TAGS_FILE)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    hit = _tags_cache.get("v")
    if hit is not None and hit[0] == key:
        return dict(hit[1])
    try:
        with open(_TAGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data = data if isinstance(data, dict) else {}
    except Exception:
        data = {}
    _tags_cache["v"] = (key, data)
    return dict(data)


def _save_tags(data: dict) -> None:
    os.makedirs(os.path.dirname(_TAGS_FILE), exist_ok=True)
    tmp = _TAGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _TAGS_FILE)
    _tags_cache.clear()         # 写完立刻失效：下一次读拿到的是盘上的新内容


def all_tags() -> list:
    """所有已用过的标签（按使用次数降序，便于界面排序展示）。"""
    data = _load_tags()
    counter: dict = {}
    for tags in data.values():
        for t in (tags or []):
            counter[t] = counter.get(t, 0) + 1
    return [t for t, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def tags_for(path: str) -> list:
    return list(_load_tags().get(os.path.normcase(path), []) or [])


def set_tags(path: str, tags) -> None:
    with _tags_lock:
        data = _load_tags()
        data[os.path.normcase(path)] = sorted({t.strip() for t in tags if t and t.strip()})
        _save_tags(data)


def add_tags(path: str, tags) -> list:
    with _tags_lock:
        data = _load_tags()
        key = os.path.normcase(path)
        cur = set(data.get(key, []) or [])
        cur.update(t.strip() for t in (tags or []) if t and t.strip())
        data[key] = sorted(cur)
        _save_tags(data)
    return data[key]


def remove_tag(path: str, tag: str) -> list:
    with _tags_lock:
        data = _load_tags()
        key = os.path.normcase(path)
        cur = set(data.get(key, []) or [])
        cur.discard((tag or "").strip())
        data[key] = sorted(cur)
        _save_tags(data)
    return data[key]


# --------------------------------------------------------------------------
# 分组（新增功能）
# --------------------------------------------------------------------------
GROUP_MODES = ["默认", "按日期", "按类型", "按目录"]


def group_items(items, mode: str):
    """按模式分组，返回 [(组名, [MediaItem...])]，保持时间倒序。"""
    if mode == "按类型":
        buckets: dict = {}
        for it in items:
            buckets.setdefault("视频" if it.kind == "video" else "图片", []).append(it)
        return [(k, buckets[k]) for k in ("图片", "视频") if k in buckets]

    if mode == "按目录":
        buckets = {}
        for it in items:
            parent = os.path.dirname(it.path)
            buckets.setdefault(parent, []).append(it)
        return sorted(buckets.items(), key=lambda kv: kv[0].lower())

    if mode == "按日期":
        now = time.time()
        day = 86400
        buckets = {"今天": [], "昨天": [], "最近 7 天": [], "最近 30 天": [], "更早": []}
        for it in items:
            age = now - it.mtime
            if age < day:
                buckets["今天"].append(it)
            elif age < 2 * day:
                buckets["昨天"].append(it)
            elif age < 7 * day:
                buckets["最近 7 天"].append(it)
            elif age < 30 * day:
                buckets["最近 30 天"].append(it)
            else:
                buckets["更早"].append(it)
        return [(k, v) for k, v in buckets.items() if v]

    return [("", list(items))]


# --------------------------------------------------------------------------
# 拖拽导入（新增功能）
# --------------------------------------------------------------------------
def _is_media(path: str) -> bool:
    from echo_launcher.media.browser import IMAGE_EXT, VIDEO_EXT
    return os.path.splitext(path)[1].lower() in (IMAGE_EXT | VIDEO_EXT)


def collect_paths(entries) -> list:
    """把拖进来的路径摊平成媒体文件列表（目录会递归展开）。"""
    out = []
    for p in entries or []:
        p = (p or "").strip()
        if not p:
            continue
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git"}]
                for fn in files:
                    full = os.path.join(root, fn)
                    if _is_media(full):
                        out.append(full)
        elif os.path.isfile(p) and _is_media(p):
            out.append(p)
    return out


def import_files(dest_dir: str, paths, move: bool = False, report=None) -> dict:
    """把外部媒体文件导入（复制或移动）到输出目录。

    同名文件自动加 `_1` `_2` 后缀，不覆盖已有文件。
    返回 {"ok":n, "fail":n, "dest":[...]}
    """
    import shutil
    os.makedirs(dest_dir, exist_ok=True)
    ok = fail = 0
    dests = []
    for i, src in enumerate(paths, 1):
        if report:
            report(i, len(paths), os.path.basename(src))
        try:
            base = os.path.basename(src)
            name, ext = os.path.splitext(base)
            dst = os.path.join(dest_dir, base)
            n = 1
            while os.path.exists(dst):
                dst = os.path.join(dest_dir, f"{name}_{n}{ext}")
                n += 1
            if move:
                shutil.move(src, dst)
            else:
                shutil.copy2(src, dst)
            dests.append(dst)
            ok += 1
        except Exception as e:
            B.log().error(f"导入失败 {src}: {e}")
            fail += 1
    return {"ok": ok, "fail": fail, "dest": dests}

_META_LABELS = (
    ("checkpoint", "模型"), ("loras", "LoRA"), ("seed", "种子"),
    ("steps", "步数"), ("cfg", "CFG"), ("sampler", "采样器"),
    ("scheduler", "调度器"), ("denoise", "降噪"), ("vae", "VAE"),
    ("duration", "时长(秒)"), ("fps", "帧率"), ("codec", "编码"),
)


def library() -> MediaLibrary:
    """媒体库单例（缩略图缓存放在 data/thumbs）。"""
    return B._once("media_lib",
                   lambda: MediaLibrary(os.path.join(B.DATA_DIR, "thumbs")))


def scan(dirs, recursive: bool = True, limit: int = 2000):
    return library().scan(list(dirs or []), recursive=recursive, limit=limit)


def load_meta(item: MediaItem) -> dict:
    return library().load_meta(item)


def delete(item: MediaItem) -> bool:
    return library().delete(item)


def probe(path: str) -> dict:
    return _video.probe(path)


def save_frame(item: MediaItem, time_sec: float, dest: str,
               max_size: int = 2000) -> bool:
    img = _video.seek_frame(item.path, time_sec, max_size=max_size)
    if img is None:
        return False
    try:
        img.save(dest)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# PIL -> Qt
# --------------------------------------------------------------------------
def _pil_to_png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def thumb_pixmap_bytes(item: MediaItem, size: int = 180) -> bytes:
    """取缩略图并转成 PNG 字节（供 QPixmap.loadFromData 使用）。"""
    img = library().thumb(item, size)
    return _pil_to_png_bytes(img) if img is not None else b""


def full_pixmap_bytes(item: MediaItem, max_size: int = 360) -> bytes:
    img = library().full_image(item, max_size=max_size)
    if img is None and item.kind == "video":
        img = _video.thumbnail(item.path, max_size=max_size)
    return _pil_to_png_bytes(img) if img is not None else b""


def seek_pixmap_bytes(item: MediaItem, time_sec: float, max_size: int = 360) -> bytes:
    img = _video.seek_frame(item.path, time_sec, max_size=max_size)
    return _pil_to_png_bytes(img) if img is not None else b""


def meta_lines(item: MediaItem, meta: dict) -> list:
    """把元数据整理成几行给人看的文本（沿用 Echo 的字段顺序）。"""
    lines = [f"文件：{item.name}",
             f"大小：{item.size_mb:.1f} MB    尺寸：{item.width}x{item.height}", ""]
    for key, label in _META_LABELS:
        v = meta.get(key)
        if v:
            lines.append(f"{label}：{v}")
    pos = meta.get("positive")
    if pos:
        lines.append("\n提示词：\n" + str(pos)[:1500])
    neg = meta.get("negative")
    if neg:
        lines.append("\n负向：\n" + str(neg)[:600])
    return lines
