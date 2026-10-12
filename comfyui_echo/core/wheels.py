"""重型轮子适配（Echo 的 env/wheels.py）。

自动探测 Python / CUDA / torch 版本 → 拼出公开直链 → 一键安装。
数据源：download.pytorch.org 官方索引、woct0rdho/SageAttention 的 GitHub Release、
Wildminder/AI-windows-whl（HuggingFace）。

注意：安装函数都是**阻塞**的（torch 可能装几十分钟），UI 必须放进 Worker。
"""
from __future__ import annotations

import os

from echo_launcher.env import wheels as _w

from . import _bridge as B

__all__ = [
    "CUDA_TAGS", "probe", "torch_index", "pick_sage", "pick_xformers",
    "install_torch", "install_wheel_url", "install_package", "candidate_urls",
    "versions_of", "REPORT_PKGS", "install_torch_stream", "install_url_stream",
]

CUDA_TAGS = ["cpu", "cu118", "cu121", "cu124", "cu126", "cu128",
             "cu129", "cu130", "cu132"]

# 「获取环境信息」要报的包（对齐 TE「ComfyUI维护」那份清单）
REPORT_PKGS = ("torch", "torchvision", "torchaudio", "sageattention",
               "triton-windows", "xformers", "llama-cpp-python")

_VERSIONS_CODE = """\
import json, sys
import importlib.metadata as md
out = {"__python__": sys.version.split()[0]}
for n in sys.argv[1:]:
    try:
        out[n] = md.version(n)
    except Exception:
        out[n] = ""
print(json.dumps(out))
"""


def versions_of(python_path: str, names=None) -> dict:
    """用目标解释器读一批已装包的版本（`importlib.metadata`）。

    一次子进程读全部，返回 {包名: 版本}（没装的给空串）；`__python__` 键是解释器版本。
    解释器不存在 / 读不到时返回空字典（调用方自己兜底）。
    """
    names = list(names or REPORT_PKGS)
    if not python_path or not os.path.isfile(python_path):
        return {}
    try:
        import json as _json
        import subprocess
        r = subprocess.run([python_path, "-c", _VERSIONS_CODE, *names],
                           capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace",
                           creationflags=_w.NO_WINDOW)
        lines = [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
        if not lines:
            return {}
        return _json.loads(lines[-1])
    except Exception as e:
        B.log().warn(f"读取包版本失败: {e}")
        return {}


# ⚠️ 不直接用 Echo 的 `wheels.probe()`：它拼给 `python -c` 的源码是
#   `import sys,json;print(...);tv='';cv='';gn='';try:\n import torch;...`
# —— `gn='';try:` 把复合语句接在分号后面，Python 直接 SyntaxError（实测 returncode=1、
# 无任何输出），于是 python_version / torch_version 永远是空字符串，
# "重型轮子" 从未真正探测到 torch。这里用等价但语法正确的脚本重实现。
_PROBE_CODE = """\
import json, sys
print(sys.version.split()[0])
info = {"torch": "", "cuda": "", "gpu": ""}
try:
    import torch
    info["torch"] = torch.__version__
    info["cuda"] = getattr(torch.version, "cuda", "") or ""
    try:
        info["gpu"] = torch.cuda.get_device_name(0)
    except Exception:
        pass
except Exception:
    pass
print(json.dumps(info))
"""


def probe(python_path: str):
    """探测目标解释器的 Python / torch / CUDA / 显卡，返回 EnvProbe。"""
    from . import _bridge as B
    p = _w.EnvProbe(python_path=(python_path or ""))
    if not python_path:
        return p
    try:
        import subprocess
        r = subprocess.run([python_path, "-c", _PROBE_CODE],
                           capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace",
                           creationflags=_w.NO_WINDOW)
        lines = [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
        if lines:
            p.python_version = lines[0].strip()
        if len(lines) > 1:
            import json as _json
            try:
                d = _json.loads(lines[-1])
                p.torch_version = d.get("torch", "") or ""
                p.cuda_version = d.get("cuda", "") or ""
                p.gpu_name = d.get("gpu", "") or ""
            except Exception as e:
                B.log().warn(f"解析环境探测结果失败: {e}")
    except Exception as e:
        B.log().warn(f"环境探测失败: {e}")
    if not p.cuda_version:
        p.cuda_version = _w._cuda_from_nvidia_smi()
    p.gpu_name = p.gpu_name or _w._gpu_name()
    return p


def torch_index(cuda_tag: str) -> str:
    return _w.torch_index(cuda_tag)


def pick_sage(torch_version: str):
    return _w.pick_sage(torch_version)


def pick_xformers(torch_version: str):
    return _w.pick_xformers(torch_version)


def candidate_urls(kind: str, torch_version: str):
    return _w.candidate_urls(kind, torch_version)


def install_torch(python_path: str, cuda_tag: str, version: str = ""):
    return _w.install_torch(python_path, cuda_tag, version)


def install_wheel_url(python_path: str, url: str):
    return _w.install_wheel_url(python_path, url)


def install_package(python_path: str, name: str, extra_index: str = ""):
    return _w.install_package(python_path, name, extra_index)


# --------------------------------------------------------------------------
# 流式安装（UI 要实时看进度）
# --------------------------------------------------------------------------
# Echo 的 install_torch / install_wheel_url 都是 `subprocess.run(capture_output=True)`，
# 输出要等命令**全部结束**才一次性返回 —— 装 torch 动辄十几分钟，界面就是一片空白。
# 这里照 `core/pipenv.py:run_pip()` 的做法用 Popen 逐行读，实时回调。
def _stream(cmd: list, on_line=None, timeout: int = 3600):
    """跑一条安装命令并逐行回调。返回 (ok, 全文)。"""
    import subprocess
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    lines = []

    def _emit(s):
        lines.append(s)
        if on_line:
            try:
                on_line(s)
            except Exception:
                pass

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                bufsize=1, env=env, creationflags=_w.NO_WINDOW)
    except Exception as e:
        _emit(f"启动安装进程失败: {e}")
        return False, "\n".join(lines)
    try:
        for line in proc.stdout:
            _emit(line.rstrip("\n"))
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        _emit(f"安装超时（{timeout}s），已终止")
        return False, "\n".join(lines)
    return code == 0, "\n".join(lines)


def install_torch_stream(python_path: str, cuda_tag: str, version: str = "",
                         on_line=None):
    """和 `install_torch` 同一条命令，但逐行回调。"""
    idx = torch_index(cuda_tag)
    spec = ([f"torch=={version}", "torchvision", "torchaudio"] if version
            else ["torch", "torchvision", "torchaudio"])
    cmd = [python_path, "-m", "pip", "install"] + spec + ["--index-url", idx]
    return _stream(cmd, on_line=on_line)


def install_url_stream(python_path: str, url: str, on_line=None):
    """和 `install_wheel_url` 同一条命令，但逐行回调。"""
    return _stream([python_path, "-m", "pip", "install", url], on_line=on_line)


def describe(probe_result) -> str:
    return (f"Python {probe_result.python_version} | "
            f"torch {probe_result.torch_version or '未安装'} | "
            f"CUDA {probe_result.cuda_version or '无'} | "
            f"显卡 {probe_result.gpu_name or '无'} | "
            f"标签 {probe_result.cuda_tag}")
