"""插件市场适配（Echo 的 plugin/market.py + plugin/node_map）。

数据源全部公开：
- 插件目录（名称/作者/描述/标签/仓库地址）：ComfyUI-Manager 的 `custom-node-list.json`
  （jsdelivr / raw / ghfast 三源回退），本地缓存 6 小时
- 按节点名反查插件：`plugin.node_map` 的官方映射表（本地扫描优先）

安装走 Echo 的便携 Git（`plugin.gitops.clone`），可选顺带装 requirements。
"""
from __future__ import annotations

import os
import subprocess

from echo_launcher.plugin import gitops
from echo_launcher.plugin.market import MarketItem, item_from_url, repo_dir_name

from . import _bridge as B
from . import nodemap as _nm
from . import paths as _paths

__all__ = [
    "MarketItem", "items", "refresh", "cache_age", "outdated", "search",
    "install", "search_by_node", "is_installed", "installed_names",
    "refresh_extmap",
]

NO_WINDOW = 0x08000000


def _market():
    return B.market()


def items() -> list:
    return _market().load()


def refresh(proxy: str = "") -> int:
    """从公开源拉取最新插件目录，返回条目数（失败 -1）。"""
    return _market().refresh(B.http_client(proxy=proxy))


def cache_age() -> float:
    return _market().cache_age()


def outdated() -> bool:
    return _market().outdated()


def search(keyword: str, limit: int = 200) -> list:
    return _market().search(keyword, limit=limit)


def search_by_node(keyword: str, comfyui_path: str = "") -> list:
    """按节点名搜插件。返回 [(节点名, 插件目录名)]"""
    out = []
    if comfyui_path:
        for node, plugin in _nm.build_local_map(_comfy_root_of(comfyui_path)).items():
            if keyword.lower() in node.lower():
                out.append((node, plugin))
    remote = _nm.build_remote_map(_nm.load_extmap())
    for node, url in remote.items():
        if keyword.lower() in node.lower():
            out.append((node, repo_dir_name(url)))
    seen, uniq = set(), []
    for pair in out:
        if pair not in seen:
            seen.add(pair)
            uniq.append(pair)
    return uniq


def _comfy_root_of(comfyui_path: str) -> str:
    """含 main.py 的目录 -> 便携包根目录（本地扫描需要 root）。"""
    parent = os.path.dirname(os.path.normpath(comfyui_path or ""))
    if os.path.isdir(os.path.join(parent, "python_embeded")):
        return parent
    return comfyui_path or ""


def custom_nodes(root: str) -> str:
    return _paths.dirs(root)["custom_nodes"]


def installed_names(root: str) -> set:
    cn = custom_nodes(root)
    if not os.path.isdir(cn):
        return set()
    from echo_launcher.core import mode as _mode
    return {p.name.lower() for p in _mode.scan_plugins(cn).plugins}


def is_installed(root: str, dir_name: str) -> bool:
    return (dir_name or "").lower() in installed_names(root)


def install(root: str, item_or_url, install_deps: bool = False,
            python_path: str = "", mirror: str = "") -> dict:
    """安装一个插件。item 可以是 MarketItem，也可以是 Git URL 字符串。"""
    item = item_or_url if isinstance(item_or_url, MarketItem) else item_from_url(item_or_url)
    cn = custom_nodes(root)
    os.makedirs(cn, exist_ok=True)
    dest = os.path.join(cn, item.dir_name or item.name)
    result = {"ok": False, "path": dest, "name": item.dir_name or item.name, "error": ""}
    if os.path.exists(dest):
        result["error"] = f"已存在同名目录: {result['name']}"
        return result
    if not item.repository:
        result["error"] = "该插件没有仓库地址"
        return result
    ok, msg = gitops.clone(item.repository, dest, depth=1)
    if not ok:
        result["error"] = (msg or "克隆失败").strip()[:400]
        B.log().error(f"安装 {item.name} 失败: {result['error']}")
        return result
    result["ok"] = True
    B.log().info(f"已安装插件: {item.name} -> {dest}")

    if install_deps:
        req = os.path.join(dest, "requirements.txt")
        if os.path.isfile(req) and python_path:
            try:
                p = subprocess.run([python_path, "-m", "pip", "install", "-r", req],
                                   capture_output=True, text=True, timeout=1800,
                                   creationflags=NO_WINDOW)
                result["deps_ok"] = (p.returncode == 0)
                if p.returncode != 0:
                    result["error"] = f"依赖安装失败（代码 {p.returncode}）"
            except Exception as e:
                result["error"] = f"依赖安装异常：{e}"
        else:
            result["deps_ok"] = None
    return result


def refresh_extmap(proxy: str = "") -> bool:
    """顺手把官方节点映射表也刷一遍（市场页用得上）。"""
    return _nm.refresh_extmap(proxy)
