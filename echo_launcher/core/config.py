"""配置模型。

键名沿用原版 launcher_config.json 的命名（保证与旧配置兼容），
但剔除了所有 VIP / 私有服务端相关的键。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from copy import deepcopy
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "path_configs": [{"remark": "", "comfyui_path": "", "python_path": ""}],
    "current_config_index": 0,
    "modes": {"默认模式": {"plugins": []}},
    "last_mode": "默认模式",
    # 启动参数
    "fp16_accumulation": True,
    "cuda_malloc": True,
    "sage_attention": False,      # 「默认」档下是否启用 SAGE
    "attention_mode": "默认",     # 默认 / SAGE / FLASH / CK
    "attention_shim_enabled": False,  # 是否在 ComfyUI 解释器装 shim 强制接管注意力（参考 atswitch）
    "reserve_vram": False,
    "enable_duplicate_plugin_check": True,
    "cuda_device": None,
    "enable_custom_command": False,
    "custom_command": "",
    "custom_env_vars": "",
    "enable_custom_port": False,
    "custom_port": "",
    # 目录引用
    "default_selected_plugins": [],
    "workflow_folders": [],
    "plugin_folders": [],
    # 网络
    "enable_proxy": False,
    "enable_comfyui_proxy": False,
    "proxy_address": "http://127.0.0.1:7890",
    "enable_hf_mirror": False,
    "pypi_mirror": "阿里云",
    "github_token": "",           # 可选，填了之后 GitHub API 限额 60 -> 5000/h
    "disable_ssl_verify": False,
    "update_repo": "",            # 自更新用的 GitHub 仓库（owner/name），留空则用占位符提示
    # 界面
    "theme": "cosmo",
    "minimize_to_tray": False,
    "console_window_size": None,
    # 主窗口尺寸/位置，如 "1600x1000+120+80"；"maximized"=最大化；空=按屏幕自适应
    "main_window_geometry": "",
}


class Config:
    """线程安全的配置读写（JSON，程序目录下便携存放）。"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = deepcopy(DEFAULT_CONFIG)
        self.load()

    # ---------- 基础读写 ----------
    def load(self) -> None:
        with self._lock:
            # 主文件读不出来时先试 .bak 备份：主文件可能因断电/被强杀/被占用而损坏，
            # 若无兜底会静默退回默认值，下一次 save 就把用户配置（路径等）抹掉。
            for candidate in (self.path, self.path + ".bak"):
                if not os.path.isfile(candidate):
                    continue
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict) and loaded:
                        merged = deepcopy(DEFAULT_CONFIG)
                        merged.update(loaded)
                        self._data = merged
                        if candidate != self.path:
                            # 主文件损坏、已从备份恢复：坏文件留档备查
                            try:
                                os.replace(self.path, self.path + ".corrupt")
                            except Exception:
                                pass
                        return
                except Exception:
                    continue
            self._data = deepcopy(DEFAULT_CONFIG)

    def save(self) -> None:
        with self._lock:
            # 覆盖前先备份一份可用的旧配置（见 load 的说明）
            try:
                if os.path.isfile(self.path):
                    shutil.copy2(self.path, self.path + ".bak")
            except Exception:
                pass
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any, autosave: bool = True) -> None:
        with self._lock:
            self._data[key] = value
        if autosave:
            self.save()

    def raw(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    # ---------- ComfyUI 多路径 ----------
    @property
    def path_configs(self) -> List[Dict[str, str]]:
        return self.get("path_configs") or []

    def current_entry(self) -> Dict[str, str]:
        cfgs = self.path_configs
        if not cfgs:
            return {"remark": "", "comfyui_path": "", "python_path": ""}
        idx = self.get("current_config_index", 0) or 0
        if idx >= len(cfgs):
            idx = 0
        return cfgs[idx]

    @property
    def comfyui_path(self) -> str:
        return self.current_entry().get("comfyui_path", "") or ""

    @property
    def python_path(self) -> str:
        return self.current_entry().get("python_path", "") or ""

    def set_comfyui_path(self, path: str, python_path: str = "", remark: str = "") -> None:
        """写入当前条目；若已存在同一路径则复用该条目。"""
        with self._lock:
            cfgs = self.path_configs
            idx = None
            for i, c in enumerate(cfgs):
                if os.path.normcase(c.get("comfyui_path", "")) == os.path.normcase(path):
                    idx = i
                    break
            if idx is None:
                cfgs.append({
                    "remark": remark,
                    "comfyui_path": path,
                    "python_path": python_path,
                })
                idx = len(cfgs) - 1
            else:
                if python_path:
                    cfgs[idx]["python_path"] = python_path
                if remark:
                    cfgs[idx]["remark"] = remark
            self._data["path_configs"] = cfgs
            self._data["current_config_index"] = idx
        self.save()

    # ---------- 模式 ----------
    @property
    def modes(self) -> Dict[str, Dict[str, Any]]:
        return self.get("modes") or {}

    def mode_names(self) -> List[str]:
        return list(self.modes.keys())

    def mode_plugins(self, mode: str) -> List[str]:
        return list((self.modes.get(mode) or {}).get("plugins", []))

    def set_mode_plugins(self, mode: str, plugins: List[str]) -> None:
        with self._lock:
            modes = self.modes
            modes.setdefault(mode, {"plugins": []})["plugins"] = list(plugins)
            self._data["modes"] = modes
        self.save()

    def create_mode(self, mode: str) -> bool:
        with self._lock:
            if mode in self.modes:
                return False
            self._data.setdefault("modes", {})[mode] = {"plugins": []}
        self.save()
        return True

    def delete_mode(self, mode: str) -> bool:
        with self._lock:
            if mode not in self.modes or len(self.modes) <= 1:
                return False
            self._data["modes"].pop(mode, None)
            if self._data.get("last_mode") == mode:
                self._data["last_mode"] = self.mode_names()[0]
        self.save()
        return True

    # ---------- 派生路径 ----------
    def custom_nodes_dir(self) -> str:
        return os.path.join(self.comfyui_path, "custom_nodes") if self.comfyui_path else ""

    def models_dir(self) -> str:
        return os.path.join(self.comfyui_path, "models") if self.comfyui_path else ""

    def output_dir(self) -> str:
        return os.path.join(self.comfyui_path, "output") if self.comfyui_path else ""

    def input_dir(self) -> str:
        return os.path.join(self.comfyui_path, "input") if self.comfyui_path else ""

    def workflow_dir(self) -> str:
        return os.path.join(self.comfyui_path, "user", "default", "workflows") if self.comfyui_path else ""


_CONFIG: Optional[Config] = None


def get_config(base_dir: Optional[str] = None) -> Config:
    """全局单例。base_dir 缺省用程序根目录（便携模式）。"""
    global _CONFIG
    if _CONFIG is None:
        if base_dir is None:
            from .. import BASE_DIR
            base_dir = BASE_DIR
        _CONFIG = Config(os.path.join(base_dir, "launcher_config.json"))
    return _CONFIG
