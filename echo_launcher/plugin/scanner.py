"""本地插件扫描与节点映射提取。

识别策略（按成功率排序）：
  1. 运行中的 ComfyUI：GET /object_info -> 权威，但需要 ComfyUI 正在跑
  2. 静态扫描插件源码：AST 提取 NODE_CLASS_MAPPINGS 的注册名 -> 离线可用，覆盖动态注册
  3. 官方映射表兜底（在 node_map 里）

相比旧版纯正则，这里用 AST 能抓到更多动态注册形态：
  - `NODE_CLASS_MAPPINGS = {...}`              字面量字典
  - `NODE_CLASS_MAPPINGS.update({...})`       调用式批量注册
  - `NODE_CLASS_MAPPINGS["X"] = Class`        下标逐个注册
  - `NODE_CLASS_MAPPINGS |= {...}`            3.9+ 合并
  - 整文件没有任何映射、但定义了 `INPUT_TYPES` 的类 -> 用类名兜底（best-effort）
"""
from __future__ import annotations

import ast
import os
import re
from typing import Dict, List, Tuple

from ..core.log import get_log
from ..core.mode import scan_plugins

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "examples"}

# 兜底的旧正则（AST 解析失败时用）
_MAPPING_RE = re.compile(
    r"NODE_CLASS_MAPPINGS\s*(?::\s*[^=]+)?=\s*\{(.*?)\}", re.S)


def _is_ncm(node) -> bool:
    """target 是否为 NODE_CLASS_MAPPINGS（Name 或 Attribute）。"""
    if isinstance(node, ast.Name) and node.id == "NODE_CLASS_MAPPINGS":
        return True
    if isinstance(node, ast.Attribute) and node.attr == "NODE_CLASS_MAPPINGS":
        return True
    return False


def _dict_keys(node) -> List[str]:
    """从 Dict / dict(...) 调用里取字符串键名。"""
    out: List[str] = []
    if isinstance(node, ast.Dict):
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                out.append(k.value)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id == "dict":
        for kw in node.keywords:
            if isinstance(kw.arg, str):
                out.append(kw.arg)
    return out


def _regex_keys(src: str) -> List[str]:
    keys: List[str] = []
    for m in _MAPPING_RE.finditer(src):
        block = m.group(1)
        try:
            tree = ast.parse("{" + block + "}", mode="eval")
            if isinstance(tree.body, ast.Dict):
                for k in tree.body.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.append(k.value)
                continue
        except Exception:
            pass
        for mm in re.finditer(r"""['"]([A-Za-z0-9_\-\.\s/]+)['"]\s*:""", block):
            keys.append(mm.group(1))
    return keys


def extract_node_names(src: str) -> List[str]:
    """从一段源码里提取它注册的节点类型名（去重保持顺序）。"""
    try:
        tree = ast.parse(src)
    except Exception:
        return sorted(set(_regex_keys(src)))

    names: List[str] = []
    for node in ast.walk(tree):
        # NODE_CLASS_MAPPINGS = {...}  /  module.NODE_CLASS_MAPPINGS = {...}
        if isinstance(node, ast.Assign) and _is_ncm(node.targets[0]):
            names += _dict_keys(node.value)
        elif isinstance(node, ast.AnnAssign) and _is_ncm(node.target):
            names += _dict_keys(node.value)
        # NODE_CLASS_MAPPINGS |= {...}
        elif isinstance(node, ast.AugAssign) and _is_ncm(node.target) \
                and isinstance(node.op, ast.BitOr):
            names += _dict_keys(node.value)
        # NODE_CLASS_MAPPINGS.update({...})
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            c = node.value
            if isinstance(c.func, ast.Attribute) and c.func.attr == "update" \
                    and _is_ncm(c.func.value) and c.args:
                names += _dict_keys(c.args[0])
        # NODE_CLASS_MAPPINGS["X"] = Class
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript) \
                and _is_ncm(node.targets[0].value):
            sl = node.targets[0].slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                names.append(sl.value)

    # 兜底：整文件没显式映射、但定义了 INPUT_TYPES 的类 -> 用类名当节点类型
    if not names and "INPUT_TYPES" in src:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and item.name == "INPUT_TYPES":
                        names.append(node.name)
                        break
    return names


def scan_plugin_dir(plugin_dir: str, max_files: int = 400) -> List[str]:
    """扫描单个插件目录，返回它注册的节点名列表。"""
    found: List[str] = []
    count = 0
    for root, dirs, files in os.walk(plugin_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            count += 1
            if count > max_files:
                return sorted(set(found))
            path = os.path.join(root, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    src = f.read()
            except Exception:
                continue
            found.extend(extract_node_names(src))
    return sorted(set(found))


def build_mapping_static(custom_nodes: str) -> Tuple[Dict[str, str], List[str]]:
    """静态扫描整个 custom_nodes，得到 {节点名: 插件名}。"""
    log = get_log()
    mapping: Dict[str, str] = {}
    failed: List[str] = []
    scan = scan_plugins(custom_nodes)
    for item in scan.plugins:
        full = os.path.join(custom_nodes, item.dirname)
        nodes: List[str] = []
        try:
            if os.path.isfile(full):
                with open(full, "r", encoding="utf-8", errors="ignore") as f:
                    src = f.read()
                nodes = extract_node_names(src)
            else:
                nodes = scan_plugin_dir(full)
        except Exception as e:
            failed.append(f"{item.name}: {e}")
            continue
        for n in nodes:
            mapping[n] = item.name
        if nodes:
            log.debug(f"{item.name}: 识别到 {len(nodes)} 个节点")
    return mapping, failed


def fetch_object_info(port: int = 8188, host: str = "127.0.0.1",
                      timeout: float = 5.0) -> Dict[str, str]:
    """从运行中的 ComfyUI 拉取 /object_info，返回 {节点名: 来源插件}。

    ComfyUI 的 object_info 条目里带 `python_module`，形如
    `custom_nodes.ComfyUI-XXX`，据此可反推插件目录名。
    """
    import requests
    url = f"http://{host}:{port}/object_info"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    out: Dict[str, str] = {}
    for node, meta in data.items():
        mod = ""
        if isinstance(meta, dict):
            mod = meta.get("python_module", "") or ""
        # custom_nodes.<插件目录> 或 nodes.<内置>
        if mod.startswith("custom_nodes."):
            rest = mod[len("custom_nodes."):]
            plugin = rest.split(".")[0]
            if plugin:
                out[node] = plugin
    return out
