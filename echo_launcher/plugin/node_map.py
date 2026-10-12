"""节点 → 插件 的映射管理。

两条数据来源：
  1. 官方映射表 extension-node-map.json（ComfyUI-Manager，公开数据）
  2. 本地扫描缓存 node_mapping_cache.json（按 ComfyUI 路径索引，识别率最高）
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

from ..core.log import get_log
from ..net.http_client import HttpClient
from ..net.mirrors import EXTENSION_NODE_MAP_SOURCES

CACHE_TTL = 24 * 3600


class NodeMap:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.official_file = os.path.join(data_dir, "extension-node-map.json")
        self.cache_file = os.path.join(data_dir, "node_mapping_cache.json")
        self.log = get_log()
        self._official: Optional[Dict[str, list]] = None
        self._cache: Dict[str, dict] = {"paths": {}}
        self._lock = threading.RLock()
        self._load_cache()

    # ---------- 官方映射 ----------
    def load_official(self) -> Dict[str, list]:
        if self._official is None:
            if os.path.isfile(self.official_file):
                try:
                    with open(self.official_file, "r", encoding="utf-8") as f:
                        self._official = json.load(f)
                except Exception as e:
                    self.log.warn(f"官方映射表损坏: {e}")
                    self._official = {}
            else:
                self._official = {}
        return self._official

    def update_official(self, client: HttpClient) -> int:
        """从公开源拉取最新映射表，返回条目数。"""
        try:
            data, src = client.get_json_multi(EXTENSION_NODE_MAP_SOURCES, timeout=30)
        except Exception as e:
            self.log.error(f"更新官方映射表失败: {e}")
            return -1
        if not isinstance(data, dict):
            self.log.error("官方映射表格式异常")
            return -1
        tmp = self.official_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self.official_file)
        self._official = data
        self.log.info(f"官方映射表已更新（{len(data)} 条，来源 {src.split('/')[2]}）")
        return len(data)

    # ---------- 本地缓存 ----------
    def _load_cache(self) -> None:
        if os.path.isfile(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {"paths": {}}
        self._cache.setdefault("paths", {})

    def _save_cache(self) -> None:
        tmp = self.cache_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False)
        os.replace(tmp, self.cache_file)

    def get_path_mapping(self, comfyui_path: str) -> Dict[str, str]:
        with self._lock:
            key = os.path.normcase(os.path.abspath(comfyui_path))
            return dict(self._cache["paths"].get(key, {}).get("mappings", {}))

    def set_path_mapping(self, comfyui_path: str, mapping: Dict[str, str]) -> None:
        with self._lock:
            key = os.path.normcase(os.path.abspath(comfyui_path))
            self._cache["paths"].setdefault(key, {})["mappings"] = mapping
            self._cache["paths"][key]["updated_at"] = int(time.time())
            self._save_cache()

    def prune_deleted(self, comfyui_path: str, existing_plugins: List[str]) -> int:
        """移除已删除插件的映射条目。"""
        mapping = self.get_path_mapping(comfyui_path)
        before = len(mapping)
        keep = {k: v for k, v in mapping.items() if v in existing_plugins}
        self.set_path_mapping(comfyui_path, keep)
        return before - len(keep)

    def cache_age(self, comfyui_path: str) -> float:
        key = os.path.normcase(os.path.abspath(comfyui_path))
        ts = self._cache["paths"].get(key, {}).get("updated_at", 0)
        return time.time() - ts if ts else float("inf")

    # ---------- 查询 ----------
    def resolve(self, node_type: str, comfyui_path: str = "") -> Optional[str]:
        """节点名 → 插件名。本地缓存优先，其次官方表。"""
        if comfyui_path:
            m = self.get_path_mapping(comfyui_path)
            if node_type in m:
                return m[node_type]
        off = self.load_official()
        v = off.get(node_type)
        if isinstance(v, list) and v:
            # 官方表结构：{节点名: [[标题, 插件URL里的目录名?], ...]}
            first = v[0]
            if isinstance(first, list) and first:
                cand = first[-1]
            else:
                cand = first
            if isinstance(cand, str):
                return self._url_to_plugin(cand)
        return None

    @staticmethod
    def _url_to_plugin(url_or_name: str) -> str:
        """官方表的值可能是 URL 或仓库名，统一成目录名。"""
        s = url_or_name.strip()
        if "/" in s:
            s = s.rstrip("/").split("/")[-1]
        if s.endswith(".git"):
            s = s[:-4]
        return s

    def search_by_node(self, keyword: str, comfyui_path: str = "") -> List[Tuple[str, str]]:
        """按节点名关键字搜索。"""
        kw = keyword.lower()
        out: List[Tuple[str, str]] = []
        if comfyui_path:
            for node, plugin in self.get_path_mapping(comfyui_path).items():
                if kw in node.lower():
                    out.append((node, plugin))
        for node, v in self.load_official().items():
            if kw in node.lower():
                try:
                    first = v[0]
                    cand = first[-1] if isinstance(first, list) and first else first
                    out.append((node, self._url_to_plugin(str(cand))))
                except Exception:
                    continue
        # 去重保持顺序
        seen = set()
        uniq = []
        for item in out:
            if item not in seen:
                seen.add(item)
                uniq.append(item)
        return uniq
