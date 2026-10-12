"""Civitai 公开接口：按文件 sha256 反查模型（无需服务端中转）。

接口：GET https://civitai.com/api/v1/model-versions/by-hash/{sha256}
"""
from __future__ import annotations

import os
from typing import Optional

from ..net.http_client import HttpClient
from ..net.mirrors import CIVITAI_API


class CivitaiClient:
    def __init__(self, client: HttpClient, cache_dir: str):
        self.http = client
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def by_hash(self, sha256: str) -> Optional[dict]:
        """返回原始 JSON；404 表示 C 站没有这个哈希。"""
        url = f"{CIVITAI_API}/model-versions/by-hash/{sha256}"
        try:
            r = self.http.get(url, timeout=25)
        except Exception:
            return None
        if r.status_code == 404:
            return {"status": 404}
        if r.status_code != 200:
            return {"status": r.status_code}
        try:
            d = r.json()
        except Exception:
            return None
        d["status"] = 200
        return d

    @staticmethod
    def summarize(data: dict) -> dict:
        """从返回里挑出常用字段。"""
        if not data or data.get("status") != 200:
            return {}
        images = data.get("images") or []
        preview = ""
        for img in images:
            if isinstance(img, dict) and img.get("url"):
                preview = img["url"]
                break
        return {
            "model_id": data.get("modelId") or (data.get("model") or {}).get("id"),
            "version_id": data.get("id"),
            "name": data.get("name") or (data.get("model") or {}).get("name", ""),
            "base_model": data.get("baseModel", ""),
            "trained_words": data.get("trainedWords") or [],
            "description": (data.get("description") or "")[:600],
            "preview": preview,
            "download_url": data.get("downloadUrl", ""),
        }

    def download_preview(self, url: str, dest: str) -> bool:
        try:
            self.http.download(url, dest, timeout=60)
            return os.path.isfile(dest)
        except Exception:
            return False
