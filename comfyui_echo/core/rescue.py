"""救援还原点适配（Echo 的 RescueManager）。

一个还原点 = 插件目录的文件级备份 + `pip freeze` 环境快照；
还原时可选择连依赖环境一起回滚。Echo 把它做成免费功能，
原版 TE 启动器是 VIP 门禁项。

索引文件：`data/rescue/index.json`，备份实体在 `data/rescue/<时间戳>/`。
"""
from __future__ import annotations

import time

from . import _bridge as B
from . import paths as _paths

__all__ = ["list_points", "create", "restore", "delete", "describe_point"]


def list_points() -> list:
    return B.rescue().list_points()


def create(root: str, plugins=None, note: str = ""):
    """为给定插件（缺省=当前全部启用插件）建还原点。返回 RestorePoint 或 None。"""
    d = _paths.dirs(root)
    names = list(plugins or [])
    if not names:
        from . import plugins as _plugins
        names = _plugins.capture_enabled(root)
    return B.rescue().create(d["comfyui"], d["python_exe"], names, note=note)


def restore(point_id: str, restore_env: bool = True) -> list:
    """还原到某个还原点。返回错误列表（空 = 成功）。"""
    return B.rescue().restore(point_id, restore_env=restore_env)


def delete(point_id: str) -> bool:
    return B.rescue().delete(point_id)


def describe_point(point) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(point.created_at))
    names = "、".join(point.plugins[:3]) or "(无插件)"
    if len(point.plugins) > 3:
        names += f" 等 {len(point.plugins)} 个"
    return f"{point.id}    {when}    {names}" + (f"    [{point.note}]" if point.note else "")
