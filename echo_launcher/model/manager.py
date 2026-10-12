"""模型 / LoRA 管理器：扫描、查重、备注、Civitai 匹配缓存。

沿用既有的表结构：
    notes(rel_path PK, text_note, image_rel_path, updated_at)
    file_hashes(rel_path PK, sha256, size, mtime, updated_at)
    civitai_matches(rel_path PK, sha256, status_code, fetched_at, model_id, version_id, ...)
    civitai_prefs(rel_path PK, use_preview_as_note_image, note_image_rel_path, updated_at)
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

MODEL_EXT = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx", ".sft"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


@dataclass
class ModelFile:
    rel_path: str
    abs_path: str
    size: int = 0
    mtime: int = 0
    kind: str = ""            # checkpoint / lora / vae / controlnet / upscale / clip / embeddings
    sha256: str = ""
    text_note: str = ""
    note_image: str = ""
    civitai_id: Optional[int] = None
    civitai_version: Optional[int] = None

    @property
    def size_mb(self) -> float:
        return self.size / 1048576


class ModelManager:
    def __init__(self, models_root: str, db_path: str):
        self.root = models_root
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            c = self._conn.cursor()
            c.execute("""CREATE TABLE IF NOT EXISTS notes (
                rel_path TEXT PRIMARY KEY,
                text_note TEXT NOT NULL DEFAULT '',
                image_rel_path TEXT,
                updated_at INTEGER NOT NULL DEFAULT 0)""")
            c.execute("""CREATE TABLE IF NOT EXISTS file_hashes (
                rel_path TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime INTEGER NOT NULL,
                updated_at INTEGER NOT NULL DEFAULT 0)""")
            c.execute("""CREATE TABLE IF NOT EXISTS civitai_matches (
                rel_path TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                fetched_at INTEGER NOT NULL DEFAULT 0,
                model_id INTEGER,
                version_id INTEGER,
                payload TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS civitai_prefs (
                rel_path TEXT PRIMARY KEY,
                use_preview_as_note_image INTEGER NOT NULL DEFAULT 0,
                note_image_rel_path TEXT,
                updated_at INTEGER NOT NULL DEFAULT 0)""")
            self._conn.commit()

    # ---------- 扫描 ----------
    def scan(self, exts=MODEL_EXT) -> List[ModelFile]:
        out: List[ModelFile] = []
        if not os.path.isdir(self.root):
            return out
        notes = self._all_notes()
        hashes = self._all_hashes()
        matches = self._all_matches()
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() not in exts:
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, self.root).replace("\\", "/")
                st = os.stat(full)
                rec = hashes.get(rel)
                m = matches.get(rel, {})
                n = notes.get(rel, {})
                out.append(ModelFile(
                    rel_path=rel, abs_path=full, size=st.st_size, mtime=int(st.st_mtime),
                    kind=guess_kind(rel), sha256=(rec or {}).get("sha256", ""),
                    text_note=n.get("text_note", ""), note_image=n.get("image_rel_path", "") or "",
                    civitai_id=m.get("model_id"), civitai_version=m.get("version_id"),
                ))
        return sorted(out, key=lambda x: x.rel_path.lower())

    # ---------- 哈希 ----------
    def hash_file(self, rel_path: str, chunk: int = 1 << 22) -> str:
        full = os.path.join(self.root, rel_path)
        h = hashlib.sha256()
        with open(full, "rb") as f:
            for block in iter(lambda: f.read(chunk), b""):
                h.update(block)
        return h.hexdigest()

    def ensure_hash(self, mf: ModelFile) -> str:
        if mf.sha256:
            return mf.sha256
        sha = self.hash_file(mf.rel_path)
        mf.sha256 = sha
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO file_hashes VALUES (?,?,?,?,?)",
                (mf.rel_path, sha, mf.size, mf.mtime, int(time.time())))
            self._conn.commit()
        return sha

    def _all_hashes(self) -> Dict[str, dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT rel_path, sha256, size, mtime FROM file_hashes")
            return {r[0]: {"sha256": r[1], "size": r[2], "mtime": r[3]} for r in cur}

    def duplicates(self, files: List[ModelFile]) -> Dict[str, List[ModelFile]]:
        """按 (sha256) 分组找重复；只返回有重复的组。"""
        groups: Dict[str, List[ModelFile]] = {}
        for f in files:
            if f.sha256:
                groups.setdefault(f.sha256, []).append(f)
        return {k: v for k, v in groups.items() if len(v) > 1}

    def duplicates_by_size(self, files: List[ModelFile]) -> Dict[int, List[ModelFile]]:
        """免哈希的快速查重：先按大小分组。"""
        groups: Dict[int, List[ModelFile]] = {}
        for f in files:
            groups.setdefault(f.size, []).append(f)
        return {k: v for k, v in groups.items() if len(v) > 1}

    # ---------- 备注 ----------
    def _all_notes(self) -> Dict[str, dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT rel_path, text_note, image_rel_path FROM notes")
            return {r[0]: {"text_note": r[1] or "", "image_rel_path": r[2]} for r in cur}

    def set_note(self, rel_path: str, text: str = "", image: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO notes VALUES (?,?,?,?)",
                (rel_path, text, image or None, int(time.time())))
            self._conn.commit()

    def get_note(self, rel_path: str) -> Tuple[str, str]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT text_note, image_rel_path FROM notes WHERE rel_path=?", (rel_path,))
            r = cur.fetchone()
        return (r[0] or "", r[1] or "") if r else ("", "")

    # ---------- Civitai 缓存 ----------
    def _all_matches(self) -> Dict[str, dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT rel_path, model_id, version_id, status_code FROM civitai_matches")
            return {r[0]: {"model_id": r[1], "version_id": r[2], "status_code": r[3]}
                    for r in cur}

    def save_match(self, rel_path: str, sha256: str, status: int,
                   model_id: Optional[int], version_id: Optional[int],
                   payload: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO civitai_matches VALUES (?,?,?,?,?,?,?)",
                (rel_path, sha256, status, int(time.time()), model_id, version_id, payload))
            self._conn.commit()

    def get_match_payload(self, rel_path: str) -> str:
        with self._lock:
            cur = self._conn.execute(
                "SELECT payload FROM civitai_matches WHERE rel_path=?", (rel_path,))
            r = cur.fetchone()
        return r[0] if r and r[0] else ""

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ---------- 放置位置判定（本地实现，不需要服务端） ----------
KIND_RULES = [
    ("lora", ("loras", "lora", "lycoris")),
    ("vae", ("vae",)),
    ("controlnet", ("controlnet", "t2i_adapter", "ipadapter")),
    ("upscale", ("upscale", "esrgan", "realesrgan", "4x")),
    ("clip", ("clip", "text_encoders", "t5", "llava")),
    ("embeddings", ("embeddings", "embedding", "textual_inversion")),
    ("checkpoint", ("checkpoints", "checkpoint", "stable-diffusion", "sd")),
]


def guess_kind(rel_path: str) -> str:
    p = rel_path.lower()
    for kind, keys in KIND_RULES:
        if any(k in p for k in keys):
            return kind
    return "checkpoint"


def target_subdir(kind: str) -> str:
    return {
        "lora": "loras", "vae": "vae", "controlnet": "controlnet",
        "upscale": "upscale_models", "clip": "clip", "embeddings": "embeddings",
        "checkpoint": "checkpoints",
    }.get(kind, "checkpoints")
