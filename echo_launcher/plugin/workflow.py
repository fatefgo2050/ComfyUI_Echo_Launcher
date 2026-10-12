"""工作流解析：从 workflow JSON 里提取节点类型，反查所需插件。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Set

MAX_DEPTH = 5


@dataclass
class WorkflowInfo:
    path: str
    name: str
    node_types: List[str] = field(default_factory=list)
    resolved: Dict[str, str] = field(default_factory=dict)   # 节点 -> 插件
    missing: List[str] = field(default_factory=list)         # 未识别到的节点
    required_plugins: List[str] = field(default_factory=list)
    parse_error: str = ""


def find_workflows(dirs: List[str], max_depth: int = MAX_DEPTH) -> List[str]:
    """在给定目录里递归找 workflow 文件（.json / .workflow.json）。"""
    out: List[str] = []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        base_depth = d.rstrip(os.sep).count(os.sep)
        for root, subdirs, files in os.walk(d):
            depth = root.count(os.sep) - base_depth
            if depth >= max_depth:
                subdirs[:] = []
            subdirs[:] = [s for s in subdirs if s not in {".git", "__pycache__"}]
            for fn in files:
                if fn.lower().endswith(".json"):
                    out.append(os.path.join(root, fn))
    return sorted(set(out))


def extract_node_types(path: str) -> List[str]:
    """兼容两种 workflow 格式：
    - UI 格式（带 nodes 数组，节点里有 type）
    - API 格式（{ "3": {"class_type": "KSampler", ...} }）
    """
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return []
    types: Set[str] = set()

    nodes = data.get("nodes")
    if isinstance(nodes, list):
        for n in nodes:
            if isinstance(n, dict):
                t = n.get("type") or n.get("widget", {}).get("name")
                if isinstance(t, str):
                    types.add(t)
                # 有些工作流把类型藏在 widgets_values 之外的地方
                wv = n.get("widgets_values")
                if isinstance(wv, list) and isinstance(t, str):
                    pass
    # API 格式
    for k, v in data.items():
        if k in {"nodes", "links", "groups", "last_node_id", "last_link_id", "version", "extra"}:
            continue
        if isinstance(v, dict) and isinstance(v.get("class_type"), str):
            types.add(v["class_type"])
    return sorted(types)


def analyze(path: str, resolve_fn=None, comfyui_path: str = "") -> WorkflowInfo:
    """解析单个工作流；resolve_fn 为 节点名 -> 插件名 的查询函数。"""
    info = WorkflowInfo(path=path, name=os.path.basename(path))
    try:
        info.node_types = extract_node_types(path)
    except Exception as e:
        info.parse_error = str(e)
        return info

    for t in info.node_types:
        plugin = resolve_fn(t, comfyui_path) if resolve_fn else None
        if plugin:
            info.resolved[t] = plugin
        else:
            info.missing.append(t)
    info.required_plugins = sorted(set(info.resolved.values()))
    return info


def analyze_many(paths: List[str], resolve_fn=None, comfyui_path: str = "") -> List[WorkflowInfo]:
    return [analyze(p, resolve_fn, comfyui_path) for p in paths]
