"""模型 / LoRA 管理器适配（Echo 的 model/*）。

- 扫描 / 查重 / 备注（SQLite 四表）：`echo_launcher.model.manager.ModelManager`
- Civitai 按 sha256 反查：`echo_launcher.model.civitai.CivitaiClient`

管理器按 **models 根目录**缓存实例（换了 ComfyUI 包就换一个），
备注库固定用 `data/model_notes.sqlite3`（我们拷贝过来的那份结构）。
"""
from __future__ import annotations

import csv
import os
import time

from echo_launcher.model.civitai import CivitaiClient
from echo_launcher.model.manager import ModelManager, guess_kind, target_subdir

from . import _bridge as B
from . import modelmeta as MM

__all__ = [
    "manager", "civitai", "guess_kind", "target_subdir",
    "scan", "duplicates_by_size", "duplicates", "hash_all",
    "set_note", "get_note", "match_one", "match_batch", "open_dir_of",
    "export_csv",
]

_DB_PATH = os.path.join(B.DATA_DIR, "model_notes.sqlite3")
_CACHE_DIR = os.path.join(B.DATA_DIR, "civitai_cache")

# {models_root: ModelManager}
_managers: dict = {}


def manager(models_root: str) -> ModelManager:
    """按 models 根目录取（或新建）管理器。根目录为空时返回 None。"""
    if not models_root:
        raise ValueError("未配置 ComfyUI 路径，拿不到 models 目录")
    key = os.path.normcase(os.path.abspath(models_root))
    m = _managers.get(key)
    if m is None:
        m = ModelManager(models_root, _DB_PATH)
        _managers[key] = m
    return m


def civitai(proxy: str = "") -> CivitaiClient:
    return CivitaiClient(B.http_client(proxy=proxy), _CACHE_DIR)


# --------------------------------------------------------------------------
# 扫描与查重
# --------------------------------------------------------------------------
def scan(models_root: str) -> list:
    """扫描 models 目录。

    ⚠ 扫完**要就地修正 `kind`**：Echo 的 `guess_kind()` 兜底值是 `"checkpoint"`，
    会把 `bge-m3/`、`diffusion_models/`、`sams/`、`ultralytics/`、`LLM/` 这些
    **全标成 checkpoint**。换成我们自己的"目录优先"映射（`modelmeta.refine_kind`），
    认不出就给 `unknown`，不假装。
    （不动上游文件、只在本层覆盖结果；`target_subdir()` 全项目没人调用，改 kind 没有副作用。）
    """
    files = manager(models_root).scan()
    for f in files:
        f.kind = MM.refine_kind(f.rel_path)
    return files


def duplicates_by_size(models_root: str, files=None) -> dict:
    m = manager(models_root)
    return m.duplicates_by_size(list(files if files is not None else m.scan()))


def duplicates(models_root: str, files=None) -> dict:
    m = manager(models_root)
    return m.duplicates(list(files if files is not None else m.scan()))


def hash_all(models_root: str, files, report=None) -> int:
    """逐个算 sha256（慢，走后台线程）。返回成功的个数。"""
    m = manager(models_root)
    ok = 0
    for i, f in enumerate(files, 1):
        if report:
            report(i, len(files), f)
        try:
            if f.sha256:
                ok += 1
                continue
            m.ensure_hash(f)
            ok += 1
        except Exception as e:
            B.log().error(f"{f.rel_path}: 哈希失败 {e}")
    return ok


# --------------------------------------------------------------------------
# 备注
# --------------------------------------------------------------------------
def set_note(models_root: str, rel_path: str, text: str = "", image: str = ""):
    manager(models_root).set_note(rel_path, text, image)


def get_note(models_root: str, rel_path: str):
    return manager(models_root).get_note(rel_path)


def open_dir_of(models_root: str, abs_path: str) -> str:
    return os.path.dirname(abs_path or "")


def export_csv(models_root: str, dest_path: str, files=None) -> str:
    """导出模型清单 CSV（UTF-8 BOM，Excel/WPS 直接打开不乱码）。

    ⚠ **必须带上「底模 / 用途 / 架构」**（2026-10-11 补）：这三列是模型页的主价值，
    导清单却不带，等于把最有用的一列丢了。数据取自 `modelmeta` 的缓存表。
    """
    rows = list(files if files is not None else scan(models_root))
    meta = MM.load_all(models_root) if models_root else {}
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    with open(dest_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["相对路径", "类型", "底模", "用途", "架构", "大小(字节)",
                         "大小(MB)", "SHA256", "备注", "备注图片",
                         "Civitai模型ID", "Civitai版本ID", "修改时间"])
        for item in rows:
            m = meta.get(item.rel_path) or {}
            writer.writerow([
                item.rel_path, item.kind, m.get("family", ""), m.get("purpose", ""),
                m.get("arch", ""), item.size, f"{item.size_mb:.2f}", item.sha256,
                item.text_note, item.note_image, item.civitai_id or "",
                item.civitai_version or "", time.strftime("%Y-%m-%d %H:%M:%S",
                                                           time.localtime(item.mtime)),
            ])
    return dest_path


# --------------------------------------------------------------------------
# Civitai 匹配
# --------------------------------------------------------------------------
def match_one(models_root: str, model_file, proxy: str = "") -> dict:
    """按哈希反查单个模型。返回 {"ok":..., "status":..., "info":..., "error":...}"""
    m = manager(models_root)
    c = civitai(proxy)
    try:
        sha = m.ensure_hash(model_file)
    except Exception as e:
        return {"ok": False, "error": f"哈希计算失败：{e}"}

    data = c.by_hash(sha)
    if not data:
        return {"ok": False, "error": "查询失败（多为网络/代理问题）"}
    status = int(data.get("status") or 0)
    if status != 200:
        m.save_match(model_file.rel_path, sha, status, None, None, "")
        return {"ok": False, "status": status,
                "error": f"C 站没有这个哈希（HTTP {status}）"}

    info = c.summarize(data)
    m.save_match(model_file.rel_path, sha, 200,
                 info.get("model_id"), info.get("version_id"), str(info))
    model_file.civitai_id = info.get("model_id")
    model_file.civitai_version = info.get("version_id")

    preview = info.get("preview")
    saved = ""
    if preview:
        dest = os.path.join(_CACHE_DIR, f"{info.get('version_id') or sha[:12]}.jpg")
        if c.download_preview(preview, dest):
            saved = dest
            if not model_file.text_note:
                model_file.text_note = info.get("name", "")
            m.set_note(model_file.rel_path, model_file.text_note, saved)
            model_file.note_image = saved
    return {"ok": True, "status": 200, "info": info, "preview": saved}


def match_batch(models_root: str, files, proxy: str = "", report=None,
                pause: float = 0.6) -> dict:
    """批量反查（礼貌限速）。返回 {"ok":n, "skip":n, "fail":n}"""
    m = manager(models_root)
    c = civitai(proxy)
    ok = skip = fail = 0
    total = len(files)
    for i, f in enumerate(files, 1):
        if report:
            report(i, total, f)
        try:
            sha = m.ensure_hash(f)
        except Exception as e:
            B.log().error(f"{f.rel_path}: 哈希失败 {e}")
            fail += 1
            continue
        data = c.by_hash(sha)
        if not data or data.get("status") != 200:
            skip += 1
            m.save_match(f.rel_path, sha, int(data.get("status") or 0) if data else 0,
                         None, None, "")
        else:
            info = c.summarize(data)
            m.save_match(f.rel_path, sha, 200, info.get("model_id"),
                         info.get("version_id"), str(info))
            f.civitai_id = info.get("model_id")
            preview = info.get("preview")
            if preview:
                dest = os.path.join(_CACHE_DIR, f"{info.get('version_id') or sha[:12]}.jpg")
                if c.download_preview(preview, dest):
                    if not f.text_note:
                        f.text_note = info.get("name", "")
                    m.set_note(f.rel_path, f.text_note, dest)
                    f.note_image = dest
            ok += 1
        if pause:
            time.sleep(pause)
    return {"ok": ok, "skip": skip, "fail": fail}
