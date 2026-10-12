"""配置适配：Echo 的扁平配置 <-> 启动器 UI 的嵌套视图。

为什么要转换
------------
- **启动器 UI** 期望 `{"paths": [...], "launch": {...}, "modes": {...}}` 这样的嵌套结构，
  并且会直接 `cfg["paths"].append(...)` / `cfg.setdefault("modes", {})[name] = ...`。
- **Echo 功能层** 的 `Config` 是扁平键（`path_configs` / `fp16_accumulation` / `attention_mode`
  / `enable_proxy` …），这些键名与 `launcher_config.json` 的历史格式一致，
  Echo 自己的设置窗口、注意力接管、shim 都按这套键读写。

所以这里做双向映射：`load()` 产出 UI 视图，`save()` 只回写被映射的键，
**Echo 独有键（theme / github_token / update_repo / attention_shim_enabled /
default_selected_plugins / minimize_to_tray / main_window_geometry …）原样保留**，
这样 Echo 功能层与启动器 UI 共用同一份 launcher_config.json，不会互相覆盖。
"""
from __future__ import annotations

import os
from copy import deepcopy

from . import _bridge as B
from . import paths as _paths

PROJECT_DIR = B.BASE_DIR
DATA_DIR = B.DATA_DIR

# UI 里 PyPI 镜像下拉的选项（键名与 Echo net.mirrors.PYPI_MIRRORS 保持一致，
# 这样适配层的 pipenv 可以直接把键名交给 Echo 解析索引地址）
PYPI_MIRRORS = {
    "官方": "",
    "阿里云": "https://mirrors.aliyun.com/pypi/simple/",
    "清华": "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "腾讯云": "https://mirrors.cloud.tencent.com/pypi/simple/",
}

DEFAULT_MODE = "默认模式"

# ComfyUI 的 --verbose 只接受这几个等级名（`comfy/cli_args.py` 的 LOG_LEVELS）；
# 传别的（比如下拉里的「默认」）会让 ComfyUI 直接 parser.error 退出，代码 2。
VERBOSE_LEVELS = ("DEBUG", "DETAIL", "INFO", "WARNING", "ERROR", "CRITICAL")

# 启动器 UI 的 launch 段默认值
DEFAULT_LAUNCH = {
    "fp16_accumulation": True,
    "cuda_malloc": True,
    "sage_attention": False,
    "attention_mode": "默认",      # 默认 / SAGE / FLASH / CK（Echo 四档）
    "attention_shim_enabled": False,
    "reserve_vram": False,
    "cuda_device": -1,             # -1 = 不指定
    "port": 0,                     # 0 = 默认 8188
    "auto_browser": True,
    "extra_args": "",
    "env_vars": "",
    "launcher_proxy": False,
    "launcher_proxy_url": "http://127.0.0.1:7890",
    "comfyui_proxy": False,
    "hf_mirror": False,
    "pypi_mirror": "阿里云",
    "expert": {
        # ---- 复选框（对齐 TE「专家设置」的通用设置组，键名尽量沿用 Echo 旧键）----
        "enable_manager": False,       # --enable-manager（ComfyUI-Manager）
        "disable_xformers": False,     # --disable-xformers
        "dont_print_server": False,    # --dont-print-server
        "disable_metadata": False,     # --disable-metadata
        "compress_response": False,    # --enable-compress-response-body
        "cors_listen": False,          # --listen 0.0.0.0 --enable-cors-header <origin>
        "cors_origin": "*",
        "disable_smart_memory": False, # --disable-smart-memory
        "force_upcast_attention": False,
        "dont_upcast_attention": False,
        "deterministic": False,        # --deterministic
        "mmap_torch_files": False,     # --mmap-torch-files
        "disable_mmap": False,         # --disable-mmap（safetensors）
        "async_offload": False,        # --async-offload
        "force_non_blocking": False,   # --force-non-blocking
        "quick_test": False,           # --quick-test-for-ci
        "log_stdout": False,           # --log-stdout
        "channels_last": False,        # --force-channels-last
        # ---- 下拉 ----
        "verbose": "",                 # ""=不设置；DEBUG/DETAIL/INFO/WARNING/ERROR/CRITICAL
        "unet_precision": "默认",       # 默认/fp16/fp32/bf16/fp64/fp8_e4m3fn/fp8_e5m2/fp8_e8m0fnu
        "vae_precision": "默认",        # 默认/fp16/fp32/bf16
        "text_enc_precision": "默认",   # 默认/fp16/fp32/bf16/fp8_e4m3fn/fp8_e5m2
        "global_precision": "默认",     # 默认/fp16/fp32
        "vram_mode": "默认",            # 默认/gpu-only/highvram/lowvram/novram/cpu
        "cache_mode": "默认",           # 默认/classic/none
    },
}


# --------------------------------------------------------------------------
# 路径双向换算
# --------------------------------------------------------------------------
# 注意：`os.path.normpath("")` 返回 "."（当前目录），所以这里一律先 strip 判空，
# 否则空配置条目会被当成「指向项目目录的有效路径」写回配置。
def _comfyui_dir(root: str) -> str:
    """便携包根目录 -> 含 main.py 的那一层。"""
    r = (root or "").strip()
    return _paths.comfyui_dir_of(r) if r else ""


def _python_of(root: str) -> str:
    """便携包根目录 -> python_embeded\\python.exe（不存在则留空，由 Echo 自行解析）。"""
    r = (root or "").strip()
    return _paths.python_of(r) if r else ""


def _root_of(entry: dict) -> str:
    """Echo 的 path_configs 条目 -> 便携包根目录；空条目返回空串。"""
    cui_raw = (entry.get("comfyui_path") or "").strip()
    if not cui_raw:
        return ""
    cui = os.path.normpath(cui_raw)
    parent = os.path.dirname(cui)
    if (os.path.basename(cui).lower() == "comfyui"
            and os.path.isdir(os.path.join(parent, "python_embeded"))):
        return parent
    return cui


# --------------------------------------------------------------------------
# 对外：load / save / current_root
# --------------------------------------------------------------------------
def load() -> dict:
    """读取配置，产出启动器 UI 期望的嵌套视图。"""
    raw = B.config().raw()

    paths = []
    for entry in (raw.get("path_configs") or []):
        if not isinstance(entry, dict):
            continue
        root = _root_of(entry)
        if not root:
            continue
        paths.append({
            "remark": entry.get("remark") or os.path.basename(root) or root,
            "root": root,
        })
    idx = _safe_int(raw.get("current_config_index"), 0)
    if not (0 <= idx < len(paths)):
        idx = 0

    launch = deepcopy(DEFAULT_LAUNCH)
    launch["fp16_accumulation"] = bool(raw.get("fp16_accumulation", True))
    launch["cuda_malloc"] = bool(raw.get("cuda_malloc", True))
    launch["sage_attention"] = bool(raw.get("sage_attention", False))
    launch["attention_mode"] = raw.get("attention_mode") or "默认"
    launch["attention_shim_enabled"] = bool(raw.get("attention_shim_enabled", False))
    launch["reserve_vram"] = bool(raw.get("reserve_vram", False))
    launch["cuda_device"] = _safe_int(raw.get("cuda_device"), -1) \
        if raw.get("cuda_device") not in (None, "") else -1
    if raw.get("enable_custom_port"):
        launch["port"] = _safe_int(raw.get("custom_port"), 0)
    launch["auto_browser"] = bool(raw.get("auto_browser", True))
    launch["extra_args"] = (raw.get("custom_command") or "").strip() \
        if raw.get("enable_custom_command") else ""
    launch["env_vars"] = raw.get("custom_env_vars") or ""
    launch["launcher_proxy"] = bool(raw.get("enable_proxy", False))
    launch["launcher_proxy_url"] = raw.get("proxy_address") or "http://127.0.0.1:7890"
    launch["comfyui_proxy"] = bool(raw.get("enable_comfyui_proxy", False))
    launch["hf_mirror"] = bool(raw.get("enable_hf_mirror", False))
    launch["pypi_mirror"] = raw.get("pypi_mirror") or "阿里云"
    ex = raw.get("expert")
    if isinstance(ex, dict):
        launch["expert"].update({k: v for k, v in ex.items()
                                 if k in DEFAULT_LAUNCH["expert"]})
    # 兼容旧配置：verbose 曾存过 int（-1=不设置）或下拉文案「默认」，
    # 这两种都不是合法等级名，必须归一成 ""，否则会拼出 `--verbose 默认` 让 ComfyUI 直接退出。
    if launch["expert"].get("verbose") not in VERBOSE_LEVELS:
        launch["expert"]["verbose"] = ""

    return {
        "paths": paths,
        "current_path_index": idx,
        "modes": deepcopy(raw.get("modes") or {DEFAULT_MODE: {"plugins": []}}),
        "last_mode": raw.get("last_mode") or DEFAULT_MODE,
        "apply_mode_on_launch": bool(raw.get("apply_mode_on_launch", False)),
        "workflow_folders": list(raw.get("workflow_folders") or []),
        "launch": launch,
    }


def save(view: dict) -> None:
    """把 UI 视图回写到 Echo 的 launcher_config.json（只覆盖被映射的键）。"""
    view = view or {}
    entries = []
    for p in (view.get("paths") or []):
        root = (p or {}).get("root") or ""
        if not root:
            continue
        entries.append({
            "remark": (p.get("remark") or os.path.basename(root) or root),
            "comfyui_path": _comfyui_dir(root),
            "python_path": _python_of(root),
        })
    if not entries:
        # Echo 的 Config.current_entry() 假定列表非空
        entries = [{"remark": "", "comfyui_path": "", "python_path": ""}]

    idx = _safe_int(view.get("current_path_index"), 0)
    if not (0 <= idx < len(entries)):
        idx = 0

    l = view.get("launch") or {}
    port = _safe_int(l.get("port"), 0)
    dev = _safe_int(l.get("cuda_device"), -1)
    extra = (l.get("extra_args") or "").strip()

    mapped = {
        "path_configs": entries,
        "current_config_index": idx,
        "modes": view.get("modes") or {DEFAULT_MODE: {"plugins": []}},
        "last_mode": view.get("last_mode") or DEFAULT_MODE,
        "apply_mode_on_launch": bool(view.get("apply_mode_on_launch", False)),
        "workflow_folders": list(view.get("workflow_folders") or []),
        # ---- 启动参数 ----
        "fp16_accumulation": bool(l.get("fp16_accumulation", True)),
        "cuda_malloc": bool(l.get("cuda_malloc", True)),
        "sage_attention": bool(l.get("sage_attention", False)),
        "attention_mode": l.get("attention_mode") or "默认",
        "attention_shim_enabled": bool(l.get("attention_shim_enabled", False)),
        "reserve_vram": bool(l.get("reserve_vram", False)),
        "cuda_device": dev if dev >= 0 else None,
        "enable_custom_port": port > 0,
        "custom_port": str(port) if port > 0 else "",
        "auto_browser": bool(l.get("auto_browser", True)),
        "enable_custom_command": bool(extra),
        "custom_command": extra,
        "custom_env_vars": l.get("env_vars") or "",
        # ---- 网络 ----
        "enable_proxy": bool(l.get("launcher_proxy", False)),
        "proxy_address": l.get("launcher_proxy_url") or "http://127.0.0.1:7890",
        "enable_comfyui_proxy": bool(l.get("comfyui_proxy", False)),
        "enable_hf_mirror": bool(l.get("hf_mirror", False)),
        "pypi_mirror": l.get("pypi_mirror") or "阿里云",
        # ---- 进阶开关（Echo 无原生键，单独存一块）----
        "expert": l.get("expert") or {},
    }

    c = B.config()
    for k, v in mapped.items():
        c.set(k, v, autosave=False)
    c.save()


def get_echo(key: str, default=None):
    """读 Echo 独有键（启动器 UI 视图里没有的那些：update_repo / github_token /
    theme / minimize_to_tray / disable_ssl_verify / enable_duplicate_plugin_check …）。"""
    return B.config().get(key, default)


def set_echo(key: str, value) -> None:
    """写 Echo 独有键。注意只用于 **UI 视图里不存在** 的键；
    被映射的键（launch 段 / paths / modes）一律走 `save()`，否则 UI 视图会与磁盘不一致。"""
    B.config().set(key, value)


def current_root(cfg: dict) -> str:
    """当前选中包的根目录；无效返回空串。"""
    paths = (cfg or {}).get("paths") or []
    i = _safe_int((cfg or {}).get("current_path_index"), 0)
    if 0 <= i < len(paths):
        return (paths[i] or {}).get("root", "") or ""
    return ""


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
