"""注意力后端接管（shim）：把 SAGE / CK 的切换"焊死"到 ComfyUI 解释器。

为什么需要它
------------
`core/attention.py` 负责在**本启动器自己拉起 ComfyUI** 时拼出
`--use-sage-attention` / `--use-ck-attention`。但有些启动方式不受本启动器控制：

  · 参考实现（v7.2.1 根本没 CK 这个开关）
  · 双击 `run_nvidia_gpu.bat` / `run_cpu.bat`
  · 任何手工 `python main.py ...`

这时本启动器拼的参数根本到不了。shim 思路来自 `D:\\ComfyUI_attswitch`：

    <ComfyUI 便携根>\\python_embeded\\Lib\\site-packages\\sitecustomize.py
    <ComfyUI 便携根>\\.attn_mode            ← 模式标记（sage / ck / 不存在=不接管）

Python 解释器**每次启动都会自动导入 sitecustomize**，所以它能在 ComfyUI 读取
命令行参数之前改写 `sys.argv`。无论谁拉起 ComfyUI，模式都生效；
而且它在启动器目录之外，启动器更新/重装也覆盖不到。

本模块与 atswitch 共用 `# ATTNSWITCH-SHIM v1` 标记，因此能识别并接管
已经由 atswitch 安装的 shim（安装/卸载/备份/还原都兼容）。

安全三道闸
----------
1. 只有 `.attn_mode` 存在时才改写 argv；
2. 只有 argv 里出现 `main.py`（确实在启动 ComfyUI）才改写，
   不干扰 pip / git / 其它 python 调用；
3. 整个逻辑包在 `try/except` 里，自身异常绝不拖累 ComfyUI。

⚠️ shim 只支持 sage / ck 两档。FLASH 与「默认」档没有对应的 marker 语义，
    本模块对它们只负责"卸载/休眠"shim（移除 marker），启动参数仍由
    `core/attention.build_flags` 在 launcher 自己的启动流程里传递。
"""
from __future__ import annotations

import os
import subprocess
import threading
from typing import Tuple

# 与 atswitch 共用同一标记，才能互相识别 / 接管彼此安装的 shim。
SHIM_MARK = "# ATTNSWITCH-SHIM v1"

MODE_SAGE = "sage"
MODE_CK = "ck"

CREATE_NO_WINDOW = 0x08000000
if hasattr(subprocess, "CREATE_NO_WINDOW"):
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW


# --------------------------------------------------------------------------
# 内嵌 shim 源码（与 D:\ComfyUI_attswitch\shim\sitecustomize.py 完全一致）
# 这样它一定随本包分发，不依赖任何外部文件。
# --------------------------------------------------------------------------
SHIM_TEMPLATE = r'''# -*- coding: utf-8 -*-
# ATTNSWITCH-SHIM v1
r"""ComfyUI 注意力模式切换 shim（由「注意力切换器」安装与管理）。

安装位置：
    <ComfyUI 便携根>\python_embeded\Lib\site-packages\sitecustomize.py

Python 解释器启动时会自动导入 sitecustomize，因此本文件可以在 ComfyUI 读取
命令行参数之前改写 sys.argv，从而让启动器不必支持新参数也能切换注意力后端。

本文件默认完全惰性：
  · 只有当 <ComfyUI 便携根>\\.attn_mode 存在且内容为 "sage" / "ck" 时才动作；
  · 只有当当前进程确实在启动 ComfyUI（argv 中出现 main.py）时才动作，
    因此不会干扰启动器自身的 pip / git 等 python 调用。

模式语义：
    sage -> 保证 argv 里是 --use-sage-attention   （移除 --use-ck-attention）
    ck   -> 保证 argv 里是 --use-ck-attention     （移除 --use-sage-attention / --use-flash-attention）

彻底还原：删除本文件与 .attn_mode 即可，不影响任何原有文件。
"""

import os
import sys
import time

_SAGE = "--use-sage-attention"
_FLASH = "--use-flash-attention"
_CK = "--use-ck-attention"
_ALL = (_SAGE, _FLASH, _CK)


def _find_comfy_root():
    """从 site-packages 向上找到 ComfyUI 便携包根目录。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return None

    d = here
    for _ in range(5):
        # 情况 A：main.py 与 comfy/ 就在当前层（非便携布局）
        try:
            if os.path.isfile(os.path.join(d, "main.py")) and \
               os.path.isdir(os.path.join(d, "comfy")):
                return d
            # 情况 B：便携布局 <root>/ComfyUI/main.py
            sub = os.path.join(d, "ComfyUI")
            if os.path.isfile(os.path.join(sub, "main.py")) and \
               os.path.isdir(os.path.join(sub, "comfy")):
                return d
        except Exception:
            pass
        parent = os.path.dirname(d)
        if not parent or parent == d:
            break
        d = parent
    return None


def _flag_summary(argv):
    return " ".join(a for a in argv if a in _ALL) or "(none)"


def _note(root, text):
    try:
        with open(os.path.join(root, ".attn_mode.last"), "w", encoding="utf-8") as fh:
            fh.write("%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except Exception:
        pass


def _apply():
    argv = getattr(sys, "argv", None)
    if not isinstance(argv, list) or not argv:
        return
    # 只在真的要跑 ComfyUI 的进程里动手。
    # 注意：`python main.py ...` 时 argv[0] 就是 main.py，所以必须连 argv[0] 一起看。
    if not any(os.path.basename(str(a)).lower() == "main.py" for a in argv):
        return

    root = _find_comfy_root()
    if not root:
        return

    flag = os.path.join(root, ".attn_mode")
    try:
        if not os.path.isfile(flag):
            return                      # 未武装：完全不做任何事
        with open(flag, "r", encoding="utf-8") as fh:
            mode = fh.read().strip().lower()
    except Exception:
        return

    if mode == "ck":
        want = _CK
    elif mode == "sage":
        want = _SAGE
    else:
        return

    new_argv = [a for a in argv if a not in _ALL]
    new_argv.append(want)
    if new_argv != argv:
        del argv[:]
        argv.extend(new_argv)

    _note(root, "mode=%s\tflags=%s" % (mode, _flag_summary(new_argv)))


try:
    _apply()
except Exception:
    # shim 绝不能因为自身异常而影响 ComfyUI 启动
    pass
'''


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
def looks_like_root(d: str) -> bool:
    """ComfyUI 便携包根目录：含 python_embeded\\python.exe 与 ComfyUI\\main.py。"""
    try:
        return (os.path.isfile(os.path.join(d, "python_embeded", "python.exe"))
                and os.path.isfile(os.path.join(d, "ComfyUI", "main.py")))
    except Exception:
        return False


def python_exe(root: str) -> str:
    return os.path.join(root, "python_embeded", "python.exe")


def flag_path(root: str) -> str:
    return os.path.join(root, ".attn_mode")


def last_path(root: str) -> str:
    return os.path.join(root, ".attn_mode.last")


def shim_dst(root: str) -> str:
    return os.path.join(root, "python_embeded", "Lib", "site-packages", "sitecustomize.py")


# --------------------------------------------------------------------------
# 模式标记读写
# --------------------------------------------------------------------------
def get_mark_mode(root: str) -> str:
    """读 .attn_mode：返回 'sage' / 'ck' / ''（未接管）。"""
    try:
        with open(flag_path(root), "r", encoding="utf-8") as fh:
            m = fh.read().strip().lower()
        return m if m in (MODE_SAGE, MODE_CK) else ""
    except Exception:
        return ""


def set_mark_mode(root: str, mode: str) -> bool:
    """写 / 删 .attn_mode。mode 为空串则删除（休眠接管）。"""
    try:
        if not mode:
            if os.path.isfile(flag_path(root)):
                os.remove(flag_path(root))
        else:
            with open(flag_path(root), "w", encoding="utf-8") as fh:
                fh.write(mode)
        # 清掉上次生效记录，让界面明确显示"尚未生效"
        try:
            os.remove(last_path(root))
        except Exception:
            pass
        return True
    except Exception:
        return False


def read_last(root: str) -> str:
    """读上次启动实际用的参数（shim 写入的 .attn_mode.last）。"""
    try:
        with open(last_path(root), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except Exception:
        return ""


# --------------------------------------------------------------------------
# shim 安装 / 卸载
# --------------------------------------------------------------------------
def shim_installed(root: str) -> bool:
    p = shim_dst(root)
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return SHIM_MARK in fh.read(600)
    except Exception:
        return False


def install_shim(root: str, log=None) -> Tuple[bool, str]:
    """把内嵌 shim 写到 python_embeded 的 site-packages。返回 (ok, 说明)。"""
    def _log(s: str) -> None:
        if log:
            log(s)

    dst = shim_dst(root)
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
    except Exception as e:
        return False, "无法创建 site-packages 目录：%s" % e

    # 已有第三方 sitecustomize.py 且不是本工具的 → 备份，绝不覆盖用户文件。
    if os.path.isfile(dst) and not shim_installed(root):
        bak = dst + ".bak-attnswitch"
        if not os.path.isfile(bak):
            try:
                os.replace(dst, bak)
                _log("检测到第三方 sitecustomize.py，已备份为 %s" % os.path.basename(bak))
            except Exception as e:
                return False, "备份原有 sitecustomize.py 失败：%s" % e
        else:
            _log("原有第三方 sitecustomize.py 的备份已存在，跳过备份")

    try:
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(SHIM_TEMPLATE)
    except Exception as e:
        return False, "写入 shim 失败：%s" % e

    if not shim_installed(root):
        return False, "写入后校验失败（标记未找到）"
    return True, dst


def uninstall_shim(root: str, log=None) -> Tuple[bool, str]:
    """移除 shim；若之前备份过第三方 sitecustomize.py 则还原。返回 (ok, 说明)。"""
    def _log(s: str) -> None:
        if log:
            log(s)

    dst = shim_dst(root)
    bak = dst + ".bak-attnswitch"
    if not os.path.isfile(dst):
        return True, "shim 本来就不存在"
    if not shim_installed(root):
        return False, "该 sitecustomize.py 不是本工具安装的，拒绝删除"
    try:
        os.remove(dst)
    except Exception as e:
        return False, "删除 shim 失败：%s" % e
    if os.path.isfile(bak):
        try:
            os.replace(bak, dst)
            _log("已还原原有 sitecustomize.py")
        except Exception as e:
            _log("还原原有 sitecustomize.py 失败：%s" % e)
    return True, "已移除"


# --------------------------------------------------------------------------
# 一键应用：安装 shim + 写模式标记（供"立即应用 / 安装接管"调用）
# --------------------------------------------------------------------------
def apply_attention(root: str, mode: str, log=None) -> Tuple[bool, str]:
    """安装 shim 并把标记设为 mode（'sage'/'ck'/''）。

    返回 (ok, 消息)。mode 为空表示休眠（保留 shim 但不接管）。
    """
    if not root or not looks_like_root(root):
        return False, "ComfyUI 便携根目录无效：%s" % root
    ok, info = install_shim(root, log=log)
    if not ok:
        return False, "安装 shim 失败：%s" % info
    if not set_mark_mode(root, mode):
        return False, "写入模式标记失败：%s" % flag_path(root)
    if mode:
        return True, "已接管为 %s（下次启动 ComfyUI 生效）" % mode.upper()
    return True, "shim 已休眠（不接管，跟随启动参数）"


def detect_armed(root: str) -> bool:
    """该 ComfyUI 是否已被本工具（或 atswitch）接管。"""
    return bool(root) and looks_like_root(root) and shim_installed(root)


def snapshot_state(root: str) -> dict:
    """给界面用的完整状态快照（纯读，不改动任何文件）。"""
    if not root or not looks_like_root(root):
        return {
            "valid": False, "shim_installed": False, "mark_mode": "",
            "last": "", "armed": False,
        }
    return {
        "valid": True,
        "shim_installed": shim_installed(root),
        "mark_mode": get_mark_mode(root),
        "last": read_last(root),
        "armed": detect_armed(root),
    }


# 简单的线程锁，避免界面与后台任务同时改 shim 文件。
# 用 RLock：apply_attention 内部会调用 install_shim / set_mark_mode，
# 它们也都加了锁，可重入锁才能避免自锁死。
_LOCK = threading.RLock()


def guarded(fn):
    """装饰器：串行化 shim 写操作。"""
    def wrap(*a, **k):
        with _LOCK:
            return fn(*a, **k)
    return wrap


# 给 apply_attention / install / uninstall 都加锁
apply_attention = guarded(apply_attention)        # type: ignore[assignment]
install_shim = guarded(install_shim)              # type: ignore[assignment]
uninstall_shim = guarded(uninstall_shim)          # type: ignore[assignment]
set_mark_mode = guarded(set_mark_mode)           # type: ignore[assignment]


def comfyui_root_of(cfg) -> str:
    """从配置里取 ComfyUI 便携根目录（含 main.py 那一层）。"""
    try:
        p = cfg.comfyui_path
    except Exception:
        p = ""
    return p or ""


def resolve_portable_root(comfyui_path: str) -> str:
    """本启动器把 comfyui_path 记成「含 main.py 的那一层」（如 D:/ComfyUI/ComfyUI），
    而 shim 要装在「便携根目录」（python_embeded 与 ComfyUI 同级的上一层，如 D:/ComfyUI）。

    这里向上找一层，返回能通过 looks_like_root 校验的便携根目录；找不到返回 ""。
    """
    if not comfyui_path:
        return ""
    cands = [
        comfyui_path,
        os.path.dirname(comfyui_path),
        os.path.dirname(os.path.dirname(comfyui_path)),
    ]
    for c in cands:
        if c and looks_like_root(c):
            return c
    return ""
