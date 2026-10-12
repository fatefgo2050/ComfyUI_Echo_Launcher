"""注意力后端适配：Echo 的四档切换 + 解释器级 shim 接管。

四档（`echo_launcher.core.attention`）：
    默认   跟随 `sage_attention` 勾选
    SAGE   --use-sage-attention
    FLASH  --use-flash-attention
    CK     --use-ck-attention（切之前必须实测 comfy-kitchen INT8 是否可用，
           否则 ComfyUI 会 `exit(-1)` 直接崩）

shim 接管（`echo_launcher.core.attn_shim`）：有些便携包把注意力后端写死在
解释器里，启动参数不生效；shim 在 `sitecustomize.py` 阶段改写 `sys.argv`，
比 ComfyUI 读参数更早。与外部同类小工具共用同一标记。
"""
from __future__ import annotations

from echo_launcher.core import attn_shim
from echo_launcher.core.attention import (
    ATTENTION_MODES, FLAG_BY_MODE, build_flags, describe, normalize_mode, probe_ck,
    resolve_mode, strip_conflicts,
)

__all__ = [
    "ATTENTION_MODES", "FLAG_BY_MODE", "build_flags", "describe", "normalize_mode",
    "probe_ck", "resolve_mode", "strip_conflicts",
    "portable_root", "shim_installed", "mark_mode", "apply_shim", "uninstall_shim",
    "read_last",
]

# 界面档位 -> shim 标记（只有 SAGE / CK 能被 shim 表达，其余=休眠）
_MARK = {"SAGE": "sage", "CK": "ck"}


def portable_root(comfyui_path: str) -> str:
    """含 main.py 的目录 -> 便携根目录（python_embeded 同级那层）。"""
    return attn_shim.resolve_portable_root(comfyui_path)


def shim_installed(root: str) -> bool:
    return bool(root) and attn_shim.shim_installed(root)


def mark_mode(root: str) -> str:
    return attn_shim.get_mark_mode(root) if root else ""


def read_last(root: str) -> str:
    return attn_shim.read_last(root) if root else ""


def mark_for(mode: str) -> str:
    """把界面档位翻译成 shim 能表达的标记（空串 = 休眠，不接管）。"""
    return _MARK.get(normalize_mode(mode), "")


def apply_shim(root: str, mode: str, log=None):
    """安装/更新 shim 并把档位写进标记。返回 (ok, 消息)。"""
    if not root:
        return False, "无法定位便携根目录（需要含 python_embeded 的那一层）"
    return attn_shim.apply_attention(root, mark_for(mode), log=log)


def uninstall_shim(root: str, log=None):
    """卸载 shim，交还控制权给启动参数。返回 (ok, 消息)。"""
    if not root:
        return False, "无法定位便携根目录"
    return attn_shim.uninstall_shim(root, log=log)


def state(root: str) -> dict:
    """接管状态快照（给界面显示）。"""
    if not root:
        return {"installed": False, "mode": "", "last": ""}
    return {
        "installed": attn_shim.shim_installed(root),
        "mode": attn_shim.get_mark_mode(root),
        "last": attn_shim.read_last(root),
    }
