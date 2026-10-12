"""便携包 / ComfyUI 路径适配。

支持两种目录布局（UI 只要求「选一个有效目录」，两种都认）：

A. **便携包根目录**（ComfyUI 官方 Windows 包那种）
       <root>/python_embeded/python.exe
       <root>/ComfyUI/main.py
B. **直连 ComfyUI 本体目录**（Echo 的 comfyui_path 语义）
       <root>/main.py  +  comfy/ 或 comfyui_version.py 或 nodes.py
   解释器由 Echo 的 `resolve_python()` 去找（同级的 python/ venv/ 或上一层 python_embeded/）。

`dirs()` 的返回键沿用启动器 UI 的命名，因此 UI 不需要任何改动。

⚠️ 两个必须防的坑
------------------
1. `os.path.normpath("")` 会返回 `"."`（当前目录）而不是空串，
   所以所有路径换算都必须先 `strip()` 判空，否则空配置会被当成有效路径。
2. 「含 main.py」**不足以**证明一个目录是 ComfyUI —— 任何 Python 项目的入口都叫
   main.py。布局 B 额外要求 `comfy/` / `comfyui_version.py` / `nodes.py` 三者之一，
   避免把普通项目目录（包括本项目自己）误认成 ComfyUI。
"""
from __future__ import annotations

import os
import re
import subprocess

from echo_launcher.core.process import resolve_python

_DRIVE_LETTERS = "CDEFGHIJ"

# 进程级缓存（见 python_version / gpu_list 的说明）
_PY_VER_CACHE: dict = {}
_GPU_LIST_CACHE = None


def is_comfyui_dir(path: str) -> bool:
    """目录是否「像 ComfyUI 本体」（含 main.py 且有 ComfyUI 的特征文件/目录）。"""
    d = (path or "").strip()
    if not d or not os.path.isfile(os.path.join(d, "main.py")):
        return False
    return (os.path.isfile(os.path.join(d, "comfyui_version.py"))
            or os.path.isdir(os.path.join(d, "comfy"))
            or os.path.isfile(os.path.join(d, "nodes.py")))


def layout(root: str):
    """返回 (comfyui_dir, python_exe)；无法识别时返回推测值，供错误提示使用。"""
    r = (root or "").strip()
    if not r:
        return "", ""
    r = os.path.normpath(r)

    nested = os.path.join(r, "ComfyUI")
    if os.path.isfile(os.path.join(nested, "main.py")):
        py = os.path.join(r, "python_embeded", "python.exe")
        if not os.path.isfile(py):
            py = resolve_python(nested, "", log=None) or py
        return nested, py

    if is_comfyui_dir(r):
        return r, (resolve_python(r, "", log=None) or "")

    # 无法判定：按便携包布局给值（validate_root 会给出明确错误）
    return nested, os.path.join(r, "python_embeded", "python.exe")


def comfyui_dir_of(root: str) -> str:
    return layout(root)[0]


def python_of(root: str) -> str:
    return layout(root)[1]


def validate_root(root: str):
    """校验目录，返回错误信息；有效返回 None。"""
    r = (root or "").strip()
    if not r or not os.path.isdir(r):
        return "目录不存在"
    r = os.path.normpath(r)
    nested = os.path.join(r, "ComfyUI")
    if os.path.isfile(os.path.join(nested, "main.py")):
        return None
    if is_comfyui_dir(r):
        return None
    if os.path.isdir(nested):
        return "缺少 ComfyUI\\main.py"
    return "该目录下既没有 ComfyUI\\main.py，也不是 ComfyUI 本体目录"


def is_valid_root(root: str) -> bool:
    return validate_root(root) is None


def dirs(root: str) -> dict:
    """便携包内常用目录（键名沿用启动器 UI）。"""
    r = (root or "").strip()
    r = os.path.normpath(r) if r else ""
    cui, py = layout(r)
    return {
        "root": r,
        "comfyui": cui,
        "python_exe": py,
        "custom_nodes": os.path.join(cui, "custom_nodes"),
        "custom_nodes_disabled": os.path.join(cui, "custom_nodes_disabled"),
        "models": os.path.join(cui, "models"),
        "outputs": os.path.join(cui, "output"),
        "inputs": os.path.join(cui, "input"),
        "workflows": os.path.join(cui, "user", "default", "workflows"),
    }


def comfyui_version(root: str) -> str:
    """从 ComfyUI/comfyui_version.py 读版本号。"""
    p = os.path.join(dirs(root)["comfyui"], "comfyui_version.py")
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        m = re.search(r"__version__\s*=\s*[\"']([^\"']+)", text)
        return m.group(1) if m else "未知"
    except OSError:
        return "未知"


def python_version(root: str) -> str:
    """目标解释器版本。

    结果按解释器路径缓存：`python.exe --version` 是子进程调用（实测约 160 ms，
    冷启动更慢），而解释器在一次会话里不会变，重复探测纯属浪费。
    """
    exe = dirs(root)["python_exe"]
    if not exe or not os.path.isfile(exe):
        return "未知"
    key = os.path.normcase(os.path.abspath(exe))
    if key in _PY_VER_CACHE:
        return _PY_VER_CACHE[key]
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                           timeout=20, encoding="utf-8", errors="replace")
        out = (r.stdout or r.stderr or "").strip()
    except Exception:
        out = "未知"
    _PY_VER_CACHE[key] = out
    return out


def gpu_list() -> list:
    """所有 N 卡型号（按 `nvidia-smi` 的序号排列，下标就是 CUDA 设备号）；没 N 卡返回 []。

    进程内缓存一次：nvidia-smi 是子进程调用（实测约 200 ms，首帧更慢），
    切页时反复调用会造成明显卡顿。要强制重测用 `clear_probe_cache()`。
    """
    global _GPU_LIST_CACHE
    if _GPU_LIST_CACHE is not None:
        return _GPU_LIST_CACHE
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace")
        names = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    except Exception:
        names = []
    _GPU_LIST_CACHE = names
    return _GPU_LIST_CACHE


def gpu_name() -> str:
    """首块 N 卡型号；无 N 卡返回空串。"""
    lst = gpu_list()
    return lst[0] if lst else ""


def clear_probe_cache():
    """清掉解释器 / 显卡探测缓存（界面上的「刷新」用它强制重新实测）。"""
    global _GPU_LIST_CACHE
    _PY_VER_CACHE.clear()
    _GPU_LIST_CACHE = None


def auto_discover() -> list:
    """扫描各盘符根目录找便携包（直接形态或嵌套形态）。返回 [{remark, root}]。"""
    found, seen = [], set()

    def _check(path, remark):
        path = os.path.normpath(path)
        if path in seen:
            return
        if is_valid_root(path):
            seen.add(path)
            found.append({"remark": remark or os.path.basename(path), "root": path})

    for letter in _DRIVE_LETTERS:
        base = f"{letter}:\\"
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            low = name.lower()
            if "comfy" not in low:
                continue
            full = os.path.join(base, name)
            if not os.path.isdir(full):
                continue
            _check(full, name)
            try:
                subs = os.listdir(full)
            except OSError:
                continue
            for sub in subs:
                if sub.lower().startswith("comfyui_windows_portable"):
                    _check(os.path.join(full, sub), f"{name}/{sub}")
    return found
