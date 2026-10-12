"""注意力后端选择：SAGE / FLASH / CK（Comfy Kitchen INT8）。

参考实现只认 SAGE / FLASH 两档，没有 CK（其程序内`--use-ck-attention` /
`ck_attention` / `comfy_kitchen` 三个关键字出现次数均为 0）。本模块让本实现
直接支持 CK —— 因为本实现自己拼启动参数，不需要 sitecustomize 之类的 Hook。

四档取值
--------
默认    跟随 `sage_attention` 开关（兼容旧配置：勾了就是 SAGE，没勾就不传）
SAGE    --use-sage-attention
FLASH   --use-flash-attention
CK      --use-ck-attention

⚠️ ComfyUI 的 `comfy/ldm/modules/attention.py` 里，CK 是**覆盖式**的：
   先按 sage/flash/xformers/pytorch 选出optimized_attention，随后
   `if comfy_kitchen_attention_enabled(): optimized_attention = attention_comfy_kitchen_int8`。
   两者同时传 = CK 赢、不报错。

⚠️ 但如果 comfy-kitchen 没编译 INT8 注意力，传 --use-ck-attention 会让 ComfyUI
   直接 `logging.error(...)` + `exit(-1)` 把进程杀掉。所以切CK 前必须实测，
   见 `probe_ck()`。
"""
from __future__ import annotations

import os
import subprocess
from typing import List, Tuple

# 界面下拉里的四档
ATTENTION_MODES: List[str] = ["默认", "SAGE", "FLASH", "CK"]

# 各档对应的 ComfyUI 命令行开关
FLAG_BY_MODE = {
    "SAGE": "--use-sage-attention",
    "FLASH": "--use-flash-attention",
    "CK": "--use-ck-attention",
}

# 三者互斥，出现多个时按 CK > FLASH > SAGE 的优先级由 ComfyUI 自己决定，
# 但我们主动只传一个，避免歧义。
CONFLICT_FLAGS = (
    "--use-sage-attention",
    "--use-flash-attention",
    "--use-ck-attention",
)

CREATE_NO_WINDOW = 0x08000000
if hasattr(subprocess, "CREATE_NO_WINDOW"):
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW

_PROBE_CODE = (
    "try:\n"
    "    import comfy_kitchen as ck\n"
    "    print('PROBE_INT8=' + str(bool(ck.int8_attention_is_available())))\n"
    "    import torch\n"
    "    print('PROBE_TORCH=' + str(torch.__version__))\n"
    "except Exception as e:\n"
    "    print('PROBE_ERROR=' + repr(e))\n"
)


def normalize_mode(mode: str) -> str:
    """把任意输入规整成四档之一；无法识别时回退「默认」。"""
    m = (mode or "").strip().upper()
    for name in ATTENTION_MODES:
        if name.upper() == m:
            return name
    return "默认"


def resolve_mode(cfg) -> str:
    """从配置里读注意力档位。"""
    return normalize_mode(cfg.get("attention_mode", "默认") or "默认")


def build_flags(cfg) -> List[str]:
    """根据配置产出注意力相关的命令行开关（互斥，最多一个）。

    「默认」档沿用旧语义：`sage_attention` 为真才传 SAGE。
    """
    mode = resolve_mode(cfg)
    if mode == "默认":
        return ["--use-sage-attention"] if cfg.get("sage_attention") else []
    flag = FLAG_BY_MODE.get(mode)
    return [flag] if flag else []


def strip_conflicts(args: List[str]) -> List[str]:
    """从自定义启动参数里剔除注意力开关，避免和所选档位打架。"""
    if not args:
        return []
    return [a for a in args if a not in CONFLICT_FLAGS]


def describe(mode: str) -> str:
    """给界面用的一行说明。

    刻意压短：这段文字显示在「启动参数 → 注意力」页签里，卡片很窄，
    原文「默认档：跟随设置里的 SageAttention 勾选（不勾则用 PyTorch 注意力）」
    会折行折得只剩「力）」两个字单占一行。完整解释挂在该标签的 tooltip 上。
    """
    mode = normalize_mode(mode)
    if mode == "默认":
        return "默认档：跟随设置里的 SageAttention 勾选"
    return f"{mode} 档：{FLAG_BY_MODE.get(mode, '?')}"


def probe_ck(python_path: str, comfyui_path: str = "", timeout: int = 120) -> Tuple[bool, str]:
    """实测 comfy-kitchen 的 INT8 注意力是否可用。返回 (可用, 说明)。

    必须在 ComfyUI 自己的解释器上跑（comfy_kitchen 装在 ComfyUI 的 site-packages 里）。
    """
    if not python_path or not os.path.isfile(python_path):
        return False, f"找不到解释器：{python_path or '(未指定)'}"
    cwd = comfyui_path if (comfyui_path and os.path.isdir(comfyui_path)) else None
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        r = subprocess.run(
            [python_path, "-c", _PROBE_CODE],
            cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
        out = r.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return False, f"检测超时（{timeout}s）"
    except Exception as e:
        return False, f"检测失败：{e}"

    if "PROBE_INT8=True" in out:
        tv = ""
        for line in out.splitlines():
            if line.startswith("PROBE_TORCH="):
                tv = line.split("=", 1)[1].strip()
        return True, f"可用{('（torch ' + tv + '）') if tv else ''}"
    if "PROBE_INT8=False" in out:
        return False, "不可用：当前 comfy-kitchen 没编译 INT8 注意力"
    for line in out.splitlines():
        if line.startswith("PROBE_ERROR="):
            return False, "comfy_kitchen 导入失败：" + line.split("=", 1)[1].strip()
    tail = out.strip().splitlines()[-1] if out.strip() else "(无输出)"
    return False, f"无法判断：{tail}"