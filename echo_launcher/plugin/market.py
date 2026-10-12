"""插件市场：直连 GitHub / ComfyUI-Manager 公开数据（无需任何私有服务端）。

数据源：
  - custom-node-list.json  插件目录（名称/作者/描述/标签/仓库地址）
  - extension-node-map.json 节点 -> 插件（见 node_map.py）
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..core.log import get_log
from ..net.http_client import HttpClient
from ..net.mirrors import CUSTOM_NODE_LIST_SOURCES
from . import gitops

CACHE_TTL = 6 * 3600


@dataclass
class MarketItem:
    name: str
    repository: str
    author: str = ""
    description: str = ""
    tags: str = ""
    dir_name: str = ""

    @property
    def installed_path(self) -> str:
        return ""


class Market:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.file = os.path.join(data_dir, "custom-node-list.json")
        self.log = get_log()
        self._items: Optional[List[MarketItem]] = None

    # ---------- 目录 ----------
    def load(self) -> List[MarketItem]:
        if self._items is not None:
            return self._items
        items: List[MarketItem] = []
        if os.path.isfile(self.file):
            try:
                with open(self.file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                nodes = data.get("custom_nodes", []) if isinstance(data, dict) else []
                for n in nodes:
                    if not isinstance(n, dict):
                        continue
                    repo = n.get("repository", "") or ""
                    items.append(MarketItem(
                        name=n.get("title") or n.get("name") or repo.rstrip("/").split("/")[-1],
                        repository=repo,
                        author=n.get("author", "") or "",
                        description=n.get("description", "") or "",
                        tags=",".join(n.get("tags", []) or []),
                        dir_name=repo_dir_name(repo),
                    ))
            except Exception as e:
                self.log.warn(f"插件目录解析失败: {e}")
        self._items = items
        return items

    def refresh(self, client: HttpClient) -> int:
        try:
            data, src = client.get_json_multi(CUSTOM_NODE_LIST_SOURCES, timeout=30)
        except Exception as e:
            self.log.error(f"更新插件目录失败: {e}")
            return -1
        tmp = self.file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self.file)
        self._items = None
        n = len(self.load())
        self.log.info(f"插件目录已更新（{n} 个插件，来源 {src.split('/')[2]}）")
        return n

    def cache_age(self) -> float:
        if not os.path.isfile(self.file):
            return float("inf")
        return time.time() - os.path.getmtime(self.file)

    def outdated(self) -> bool:
        return self.cache_age() > CACHE_TTL

    # ---------- 搜索 ----------
    def search(self, keyword: str, limit: int = 100) -> List[MarketItem]:
        kw = (keyword or "").strip().lower()
        items = self.load()
        if not kw:
            return items[:limit]
        out = []
        for it in items:
            hay = f"{it.name} {it.author} {it.description} {it.tags} {it.repository}".lower()
            if kw in hay:
                out.append(it)
                if len(out) >= limit:
                    break
        return out

    # ---------- 安装 ----------
    def install(self, item: MarketItem, custom_nodes: str, python_path: str = "",
                install_deps: bool = False, depth: int = 1) -> Dict[str, object]:
        dest = os.path.join(custom_nodes, item.dir_name or item.name)
        result = {"ok": False, "path": dest, "deps": [], "error": ""}
        if os.path.exists(dest):
            result["error"] = "目标已存在"
            return result
        if not item.repository:
            result["error"] = "该插件没有仓库地址"
            return result

        ok, msg = gitops.clone(item.repository, dest, depth=depth)
        if not ok:
            result["error"] = msg or "克隆失败"
            self.log.error(f"安装 {item.name} 失败: {result['error']}")
            return result
        result["ok"] = True
        self.log.info(f"已安装插件: {item.name} -> {dest}")

        if install_deps:
            req = os.path.join(dest, "requirements.txt")
            if os.path.isfile(req) and python_path:
                try:
                    p = subprocess.run([python_path, "-m", "pip", "install", "-r", req],
                                       capture_output=True, text=True, timeout=1800,
                                       creationflags=0x08000000)
                    result["deps"] = [req]
                    if p.returncode != 0:
                        result["error"] = f"依赖安装失败: {p.stderr[-500:]}"
                except Exception as e:
                    result["error"] = f"依赖安装异常: {e}"
        return result


def repo_dir_name(url: str) -> str:
    s = (url or "").strip().rstrip("/")
    if not s:
        return ""
    name = s.split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def item_from_url(url: str) -> MarketItem:
    """用户手填 GitHub 链接时构造一个临时条目。"""
    return MarketItem(name=repo_dir_name(url), repository=url, dir_name=repo_dir_name(url))
