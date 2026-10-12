"""「模式切换」的核心：把未选中的插件目录重命名为 <name>.disabled。

ComfyUI 原生会跳过带 .disabled 后缀的 custom_nodes 目录，
所以"按需加载插件"不需要任何 ComfyUI 侧改动。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from .log import get_log

DISABLED_SUFFIX = ".disabled"


@dataclass
class PluginEntry:
    name: str            # 逻辑插件名（去掉 .disabled 后缀）
    dirname: str         # 磁盘上的目录名
    enabled: bool
    is_git: bool = False
    size: int = 0


@dataclass
class ScanResult:
    plugins: List[PluginEntry] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)   # 同名目录与 .disabled 并存
    errors: List[str] = field(default_factory=list)


def strip_suffix(name: str) -> str:
    if name.lower().endswith(DISABLED_SUFFIX):
        return name[: -len(DISABLED_SUFFIX)]
    return name


def scan_plugins(custom_nodes: str, with_size: bool = False) -> ScanResult:
    """枚举 custom_nodes 下的插件（目录与单文件 .py 都算）。

    `with_size` 默认关闭：统计目录体积要 `os.walk` 整个插件树，
    本机 44 个插件实测 2.4 万次 stat、单次约 640 ms。运行中的代码
    （模式页列表 / 版本管理 / 节点表 / 更新检查）都不读 `PluginEntry.size`，
    只有在需要展示体积时才传 True，避免每次切页都白扫一遍磁盘。
    """
    res = ScanResult()
    if not custom_nodes or not os.path.isdir(custom_nodes):
        return res

    try:
        entries = sorted(os.listdir(custom_nodes))
    except Exception as e:
        res.errors.append(f"无法读取 custom_nodes: {e}")
        return res

    seen: Dict[str, List[str]] = {}
    for entry in entries:
        if entry.startswith("."):
            continue
        full = os.path.join(custom_nodes, entry)
        logical = strip_suffix(entry)
        seen.setdefault(logical.lower(), []).append(entry)

        if os.path.isdir(full):
            enabled = not entry.lower().endswith(DISABLED_SUFFIX)
            is_git = os.path.isdir(os.path.join(full, ".git"))
            size = 0
            if with_size:
                try:
                    size = sum(
                        os.path.getsize(os.path.join(root, f))
                        for root, _, files in os.walk(full)
                        for f in files
                    )
                except Exception:
                    size = 0
            res.plugins.append(PluginEntry(logical, entry, enabled, is_git, size))
        elif entry.endswith(".py"):
            # 单文件插件（ComfyUI 也支持），逻辑名去掉 .py
            logical_py = entry[:-3]
            enabled = not logical_py.lower().endswith(DISABLED_SUFFIX)
            res.plugins.append(
                PluginEntry(strip_suffix(logical_py), entry, enabled, False,
                            os.path.getsize(full))
            )

    for logical, names in seen.items():
        if len(names) > 1:
            res.conflicts.append(" / ".join(names))
    return res


def apply_mode(custom_nodes: str, enabled_plugins: Iterable[str],
               case_insensitive: bool = True) -> Tuple[int, int, List[str]]:
    """把模式落到磁盘：只有 enabled_plugins 里的插件保持启用，其余全部 .disabled。

    返回 (启用数量, 禁用数量, 错误信息列表)。
    """
    if not custom_nodes or not os.path.isdir(custom_nodes):
        return 0, 0, [f"custom_nodes 目录不存在: {custom_nodes}"]

    wanted = set()
    for p in enabled_plugins or []:
        wanted.add(p.lower() if case_insensitive else p)

    scan = scan_plugins(custom_nodes)
    log = get_log()
    enabled_count = 0
    disabled_count = 0
    errors: List[str] = []

    for item in scan.plugins:
        key = item.name.lower() if case_insensitive else item.name
        should_enable = key in wanted
        if should_enable == item.enabled:
            enabled_count += 1 if should_enable else 0
            disabled_count += 0 if should_enable else 1
            continue

        src = os.path.join(custom_nodes, item.dirname)
        if item.dirname.endswith(".py"):
            base = item.dirname[:-3]
            dst_name = base if should_enable else base + DISABLED_SUFFIX
        else:
            dst_name = item.name if should_enable else item.name + DISABLED_SUFFIX
        dst = os.path.join(custom_nodes, dst_name)

        if os.path.exists(dst):
            errors.append(f"目标已存在，跳过: {dst_name}")
            continue
        try:
            os.rename(src, dst)
            log.info(f"{'启用' if should_enable else '禁用'}插件: {item.name}")
            if should_enable:
                enabled_count += 1
            else:
                disabled_count += 1
        except Exception as e:
            errors.append(f"重命名失败 {item.dirname}: {e}")

    return enabled_count, disabled_count, errors


def enable_all(custom_nodes: str) -> List[str]:
    """恢复所有插件为启用状态（退出模式 / 还原现场时用）。"""
    errors: List[str] = []
    if not os.path.isdir(custom_nodes):
        return errors
    for entry in sorted(os.listdir(custom_nodes)):
        if not entry.lower().endswith(DISABLED_SUFFIX):
            continue
        src = os.path.join(custom_nodes, entry)
        dst = os.path.join(custom_nodes, entry[: -len(DISABLED_SUFFIX)])
        if os.path.exists(dst):
            errors.append(f"目标已存在，跳过: {entry}")
            continue
        try:
            os.rename(src, dst)
        except Exception as e:
            errors.append(f"{entry}: {e}")
    return errors


def find_duplicate_plugins(custom_nodes: str) -> List[Tuple[str, List[str]]]:
    """重复插件检测：不同目录名但内容签名（顶层文件列表+大小）相同。"""
    if not os.path.isdir(custom_nodes):
        return []
    sigs: Dict[str, List[str]] = {}
    for entry in sorted(os.listdir(custom_nodes)):
        full = os.path.join(custom_nodes, entry)
        if not os.path.isdir(full) or entry.startswith("."):
            continue
        try:
            files = sorted(
                (f, os.path.getsize(os.path.join(full, f)))
                for f in os.listdir(full)
                if os.path.isfile(os.path.join(full, f))
            )
            sig = repr(files[:20])
        except Exception:
            continue
        sigs.setdefault(sig, []).append(entry)
    return [(k, v) for k, v in sigs.items() if len(v) > 1]
