"""救援模式：安装/更新插件前建还原点，出问题一键回滚。

还原点包含两部分：
  1. 插件目录（文件级备份）
  2. Python 环境快照（pip freeze）

同类工具常把它做成付费功能，这里全部免费开放。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .log import get_log


@dataclass
class RestorePoint:
    id: str
    created_at: float
    comfyui_path: str
    python_path: str
    plugins: List[str] = field(default_factory=list)     # 备份的插件目录名
    backup_dir: str = ""
    freeze_file: str = ""
    note: str = ""


class RescueManager:
    def __init__(self, base_dir: str):
        self.base_dir = os.path.join(base_dir, "rescue")
        os.makedirs(self.base_dir, exist_ok=True)
        self.index_file = os.path.join(self.base_dir, "index.json")
        self.log = get_log()
        self._points: Dict[str, dict] = {}
        self._load()

    # ---------- 索引 ----------
    def _load(self) -> None:
        if os.path.isfile(self.index_file):
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    self._points = json.load(f)
            except Exception:
                self._points = {}

    def _save(self) -> None:
        tmp = self.index_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._points, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.index_file)

    def list_points(self) -> List[RestorePoint]:
        items = [RestorePoint(**p) for p in self._points.values()]
        return sorted(items, key=lambda x: x.created_at, reverse=True)

    def get(self, rid: str) -> Optional[RestorePoint]:
        d = self._points.get(rid)
        return RestorePoint(**d) if d else None

    # ---------- 创建还原点 ----------
    def create(self, comfyui_path: str, python_path: str,
               plugins: List[str], note: str = "") -> Optional[RestorePoint]:
        custom_nodes = os.path.join(comfyui_path, "custom_nodes")
        if not os.path.isdir(custom_nodes):
            self.log.error(f"custom_nodes 不存在: {custom_nodes}")
            return None

        rid = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = os.path.join(self.base_dir, rid, "plugins")
        os.makedirs(backup_dir, exist_ok=True)

        copied = []
        for name in plugins:
            src = os.path.join(custom_nodes, name)
            if not os.path.exists(src):
                continue
            dst = os.path.join(backup_dir, name)
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns(".git", "__pycache__"))
                else:
                    shutil.copy2(src, dst)
                copied.append(name)
            except Exception as e:
                self.log.error(f"备份插件 {name} 失败: {e}")

        freeze_file = os.path.join(self.base_dir, rid, "requirements.freeze.txt")
        if python_path:
            try:
                out = subprocess.run([python_path, "-m", "pip", "freeze"],
                                     capture_output=True, text=True, timeout=180,
                                     creationflags=0x08000000)
                with open(freeze_file, "w", encoding="utf-8") as f:
                    f.write(out.stdout or "")
            except Exception as e:
                self.log.warn(f"环境快照失败（不影响插件还原）: {e}")

        p = RestorePoint(rid, time.time(), comfyui_path, python_path,
                         copied, backup_dir, freeze_file, note)
        self._points[rid] = p.__dict__
        self._save()
        self.log.info(f"已创建还原点 {rid}，备份 {len(copied)} 个插件")
        return p

    # ---------- 还原 ----------
    def restore(self, rid: str, restore_env: bool = True) -> List[str]:
        p = self.get(rid)
        if not p:
            return [f"还原点不存在: {rid}"]
        custom_nodes = os.path.join(p.comfyui_path, "custom_nodes")
        errors: List[str] = []

        for name in p.plugins:
            src = os.path.join(p.backup_dir, name)
            dst = os.path.join(custom_nodes, name)
            if not os.path.exists(src):
                errors.append(f"备份缺失: {name}")
                continue
            try:
                if os.path.isdir(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                elif os.path.isfile(dst):
                    os.remove(dst)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
            except Exception as e:
                errors.append(f"还原 {name} 失败: {e}")
        self.log.info(f"已还原 {len(p.plugins) - len(errors)} 个插件")

        if restore_env and p.python_path and os.path.isfile(p.freeze_file):
            try:
                subprocess.run(
                    [p.python_path, "-m", "pip", "install", "-r", p.freeze_file],
                    capture_output=True, text=True, timeout=1800,
                    creationflags=0x08000000)
                self.log.info("已按快照还原 Python 依赖")
            except Exception as e:
                errors.append(f"还原依赖失败: {e}")
        return errors

    # ---------- 删除 ----------
    def delete(self, rid: str) -> bool:
        p = self.get(rid)
        if not p:
            return False
        try:
            shutil.rmtree(os.path.dirname(p.backup_dir), ignore_errors=True)
        except Exception:
            pass
        self._points.pop(rid, None)
        self._save()
        return True
