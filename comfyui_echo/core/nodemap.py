"""节点映射 / 工作流识别适配。

数据来源（三路，与 Echo 一致）：
  1. **本地静态扫描**：`echo_launcher.plugin.scanner.build_mapping_static()` ——
     用 AST 抓 `NODE_CLASS_MAPPINGS`（含 `.update()` / 下标 / `|=` / `INPUT_TYPES` 兜底），
     结果按 ComfyUI 路径缓存进 `data/node_mapping_cache.json`
  2. **官方映射表**：`data/extension-node-map.json`（ComfyUI-Manager 公开数据）
  3. 工作流 JSON 解析：`echo_launcher.plugin.workflow.extract_node_types()`

⚠️ 对 Echo 的一处修正
----------------------
Echo 的 `NodeMap.resolve()` / `search_by_node()` 把官方 `extension-node-map.json`
当作「节点名 → 插件」的字典来查，但官方文件的真实结构是
**「插件仓库 URL → [节点名列表, 元数据]」**（例如
`{"https://github.com/x/y": [["NodeA","NodeB"], {"title_aux": "y"}]}`）。
按节点名去查必然查不中，所以 Echo 的「官方表兜底」实际上一直没生效。

本模块按真实结构重建反查表，
官方兜底才真正可用；写入的缓存文件格式不变，Echo 自己的界面照样能读。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from echo_launcher.core import mode as _mode
from echo_launcher.plugin import workflow as _wf
from echo_launcher.plugin.market import repo_dir_name
from echo_launcher.plugin.scanner import extract_node_names, scan_plugin_dir

from . import _bridge as B
from . import paths as _paths

# 本地扫描缓存的有效期（超过就重新扫，保证新装插件能被识别）
LOCAL_CACHE_TTL = 6 * 3600

# 子进程扫描结果的行前缀（子进程可能还会打印别的，靠这个前缀精确取结果行）
_SCAN_SENTINEL = "<<NODEMAP-SCAN>>"
# 全量扫描串行锁：避免多个分析并发触发时各扫一遍
_scan_lock = threading.Lock()


# --------------------------------------------------------------------------
# 官方映射表
# --------------------------------------------------------------------------
def load_extmap() -> dict:
    return B.node_map().load_official()


def refresh_extmap(proxy_url: str = "") -> bool:
    """从公开源（jsdelivr / raw / ghfast 三源回退）拉取最新官方映射表。"""
    client = B.http_client(proxy=proxy_url)
    return B.node_map().update_official(client) >= 0


def ensure_extmap() -> dict:
    """拿到官方映射表；本地没有就尝试联网拉一次，失败返回空表。"""
    data = load_extmap()
    if data:
        return data
    try:
        refresh_extmap()
    except Exception:
        pass
    return load_extmap()


def build_remote_map(extmap: dict) -> dict:
    """官方表 -> {节点名: 插件仓库 URL}（按真实结构解析）。"""
    out = {}
    for url, item in (extmap or {}).items():
        if not isinstance(item, (list, tuple)) or not item:
            continue
        names = item[0] if isinstance(item[0], (list, tuple)) else []
        if isinstance(names, str):
            names = [names]
        for n in names:
            if isinstance(n, str) and n:
                out.setdefault(n, url)
    return out


# --------------------------------------------------------------------------
# 本地插件节点扫描（带缓存）
# --------------------------------------------------------------------------
def _scan_nodes_parallel(cn: str) -> tuple:
    """并发扫描所有插件的节点名。

    Echo 的 `build_mapping_static()` 是串行的，45 个插件实测 26.6 秒；
    这里按 Echo 检查更新时的做法用线程池并发（读文件是 I/O，能真正并行），
    AST 部分仍然调用 Echo 的 `scan_plugin_dir` / `extract_node_names`。
    """
    entries = _mode.scan_plugins(cn).plugins
    mapping, failed = {}, []

    def one(item):
        full = os.path.join(cn, item.dirname)
        try:
            if os.path.isfile(full):
                with open(full, "r", encoding="utf-8", errors="ignore") as f:
                    nodes = extract_node_names(f.read())
            else:
                nodes = scan_plugin_dir(full)
        except Exception as e:
            return item.name, [], f"{item.name}: {e}"
        return item.name, nodes, ""

    if not entries:
        return mapping, failed
    with ThreadPoolExecutor(max_workers=min(8, len(entries))) as ex:
        for name, nodes, err in ex.map(one, entries):
            if err:
                failed.append(err)
                continue
            for n in nodes:
                mapping.setdefault(n, name)
    return mapping, failed


def _scan_nodes_via_subprocess(cn: str):
    """在独立进程里做全量 AST 扫描，返回 `(mapping, failed)`；不可用时返回 None。

    为什么非要开子进程：像 `Comfly.py`（1.1 MB）这种大插件源码，单次 `ast.parse`
    实测要 **1.75 秒**，而且整段 C 解析**完全不放 GIL** —— 放进程内跑，GUI 主线程
    会被硬卡住 1.7 秒（实测主线程心跳最大间隔 1691 ms）。挪到子进程后主线程不受影响。

    任何一步失败都返回 None，由调用方回退到进程内扫描，保证功能不退化。
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--nodemap-scan", cn]
    else:
        cmd = [sys.executable, os.path.join(root, "main.py"), "--nodemap-scan", cn]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=900, cwd=root)
        if proc.returncode != 0:
            return None
        for ln in reversed((proc.stdout or "").splitlines()):
            if ln.startswith(_SCAN_SENTINEL):
                data = json.loads(ln[len(_SCAN_SENTINEL):])
                return dict(data[0]), list(data[1])
        return None
    except Exception:
        return None


def build_local_map(root: str, force: bool = False) -> dict:
    """{节点名: 插件目录名}，按 ComfyUI 路径缓存在 Echo 的 node_mapping_cache.json。"""
    d = _paths.dirs(root)
    cn = d["custom_nodes"]
    if not os.path.isdir(cn):
        return {}
    nm = B.node_map()
    key = d["comfyui"]
    cached = nm.get_path_mapping(key)
    if cached and not force:
        try:
            fresh = nm.cache_age(key) < LOCAL_CACHE_TTL
        except Exception:
            fresh = False
        if fresh:
            return cached
    # 串行化：多个分析 worker 同时过期时，只让一个真扫，其余等结果，别各自全量扫一遍
    with _scan_lock:
        cached = nm.get_path_mapping(key)
        if cached and not force:
            try:
                if nm.cache_age(key) < LOCAL_CACHE_TTL:
                    return cached
            except Exception:
                pass
        result = _scan_nodes_via_subprocess(cn)
        if result is None:                      # 子进程不可用 -> 回退进程内扫描
            result = _scan_nodes_parallel(cn)
        mapping, failed = result
        for f in (failed or []):
            B.log().warn(f"节点扫描: {f}")
        if mapping:
            try:
                nm.set_path_mapping(key, mapping)
            except Exception as exc:
                B.log().warn(f"写入节点映射缓存失败: {exc}")
        return mapping


def local_cache_age(root: str) -> float:
    try:
        return B.node_map().cache_age(_paths.dirs(root)["comfyui"])
    except Exception:
        return float("inf")


# --------------------------------------------------------------------------
# 工作流
# --------------------------------------------------------------------------
def list_workflow_files(root: str, extra_folders: list = None) -> list:
    """收集工作流 json 文件（ComfyUI 自带目录 + 用户附加目录，≤5 层）。"""
    d = _paths.dirs(root)
    bases = [d["workflows"]]
    for f in (extra_folders or []):
        if f and os.path.isdir(f):
            bases.append(f)
    return _wf.find_workflows(bases)


def parse_workflow(path: str) -> list:
    """提取工作流用到的节点类型（兼容 UI 格式与 API 格式）。"""
    try:
        return _wf.extract_node_types(path)
    except Exception:
        return []


def is_builtin_repo(plugin_name: str) -> bool:
    """判断在线映射给出的「插件」是不是 ComfyUI 本体。

    官方 `extension-node-map.json` 把大量**内置节点**也挂在
    `https://github.com/Comfy-Org/ComfyUI` 下（如 PrimitiveInt、EmptyLatentImage），
    `repo_dir_name()` 解析出来就是字面量 `ComfyUI`。它们不是「未安装的插件」，
    照原样算进 missing 会让用户去装一个不存在的插件。
    """
    return (plugin_name or "").strip().lower() in ("comfyui", "comfyui-frontend-package")


def analyze(root: str, wf_path: str, extmap: dict = None) -> dict:
    """分析工作流 -> 所需插件。

    返回 {nodes, results: [{node, plugin, source, installed}], missing_urls}

    `source` 取值：本地扫描 / 在线映射 / 内置节点 / 未识别。
    """
    d = _paths.dirs(root)
    cn = d["custom_nodes"]
    local = build_local_map(root)
    if extmap is None:
        extmap = ensure_extmap()
    remote = build_remote_map(extmap)
    used = parse_workflow(wf_path)

    results, missing_urls = [], []
    for node in dict.fromkeys(used):
        if node in local:
            results.append({"node": node, "plugin": local[node],
                            "source": "本地扫描", "installed": True})
            continue
        url = remote.get(node)
        if url:
            plugin = repo_dir_name(url)
            if is_builtin_repo(plugin):
                # 内置节点：ComfyUI 自带，不需要任何插件
                results.append({"node": node, "plugin": "ComfyUI 内置",
                                "source": "内置节点", "installed": True})
                continue
            installed = (os.path.isdir(os.path.join(cn, plugin))
                         or os.path.isfile(os.path.join(cn, plugin + ".py")))
            results.append({"node": node, "plugin": plugin,
                            "source": "在线映射", "installed": installed})
            if not installed and url not in missing_urls:
                missing_urls.append(url)
        else:
            results.append({"node": node, "plugin": "-",
                            "source": "未识别", "installed": False})

    return {"nodes": used, "results": results, "missing_urls": missing_urls,
            "checked_at": time.time()}


def workflow_plugin_set(root: str, wf_path: str, extmap: dict = None) -> dict:
    """工作流 -> 可写进「模式」白名单的插件集合。

    这是「工作流」与「模式」合流的唯一转换点：`analyze()` 给的是节点维度的明细，
    这里把它收敛成插件维度，并明确区分三种情况，UI 才能说清楚下一步该干什么。

    返回 {
        "nodes":     工作流用到的节点总数,
        "plugins":   [已安装且需要的插件名...]，可直接作为模式白名单,
        "missing":   [{"node", "plugin"}...]，识别出来了但本机没装,
        "unknown":   [节点名...]，本地扫描与在线映射都没查到（多为 ComfyUI 内置节点）,
        "missing_urls": [...]，给「一键安装缺失插件」用,
        "results":   原始节点明细，供「分析明细」表格直接渲染,
    }

    注意插件名用的是 **scan_plugins 的逻辑名**（目录名去掉 .disabled），
    与 `plugins.list_plugin_dirs()` / 模式白名单是同一套命名，能直接对接。
    """
    res = analyze(root, wf_path, extmap)
    plugins, missing, unknown = [], [], []
    seen_missing, seen_builtin = set(), []
    for r in res["results"]:
        name = r["plugin"]
        if name == "-":
            unknown.append(r["node"])
        elif r["source"] == "内置节点":
            seen_builtin.append(r["node"])
        elif r["installed"]:
            if name not in plugins:
                plugins.append(name)
        elif name not in seen_missing:
            # 多个节点可能属于同一个未安装插件，按插件名去重，否则提示里会出现重复项
            seen_missing.add(name)
            missing.append({"node": r["node"], "plugin": name})
    unknown.extend(seen_builtin)
    return {
        "nodes": len(res["nodes"]),
        "plugins": plugins,
        "missing": missing,
        "unknown": unknown,
        "missing_urls": list(res["missing_urls"]),
        "results": res["results"],
    }


def merge_plugin_sets(results: list) -> dict:
    """把多份 `workflow_plugin_set()` 的结果并起来（多选工作流 = 取并集）。

    场景：一次要跑好几份工作流，模式白名单就得覆盖**所有**它们用到的插件。
    节点数按**去重后的节点名**算 —— 直接把各份的 nodes 相加会把重复节点算两遍，
    摘要里那个数字会虚高。
    """
    plugins, missing, unknown, urls, rows = [], [], [], [], []
    seen_u, seen_m = set(), set()
    for res in results:
        for p in res.get("plugins", []):
            if p not in plugins:
                plugins.append(p)
        for m in res.get("missing", []):
            k = m.get("plugin") if isinstance(m, dict) else str(m)
            if k in seen_m:
                continue
            seen_m.add(k)
            missing.append(m)
        for u in res.get("unknown", []):
            if u not in seen_u:
                seen_u.add(u)
                unknown.append(u)
        for u in res.get("missing_urls", []):
            if u not in urls:
                urls.append(u)
        rows.extend(res.get("results", []))
    return {
        "nodes": len({r.get("node") for r in rows}),
        "plugins": plugins,
        "missing": missing,
        "unknown": unknown,
        "missing_urls": urls,
        "results": rows,
    }
