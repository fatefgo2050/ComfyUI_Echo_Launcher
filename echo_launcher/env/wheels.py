"""一键安装重型轮子：自动探测 Python / CUDA 版本并拼出直链。

全部使用公开源（pytorch 官方、GitHub Release、HuggingFace）。
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

NO_WINDOW = 0x08000000

TORCH_INDEX = {
    "cpu": "https://download.pytorch.org/whl/cpu",
    "cu118": "https://download.pytorch.org/whl/cu118",
    "cu121": "https://download.pytorch.org/whl/cu121",
    "cu124": "https://download.pytorch.org/whl/cu124",
    "cu126": "https://download.pytorch.org/whl/cu126",
    "cu128": "https://download.pytorch.org/whl/cu128",
    "cu129": "https://download.pytorch.org/whl/cu129",
    "cu130": "https://download.pytorch.org/whl/cu130",
    "cu132": "https://download.pytorch.org/whl/cu132",
}

# SageAttention：按 torch 版本挑 wheel（woct0rdho 的 Windows 预编译）
SAGE_URLS = [
    "https://github.com/woct0rdho/SageAttention/releases/download/v2.2.0-windows.post5/sageattention-2.2.0+cu130torch2.10.0andhigher.post5-cp310-abi3-win_amd64.whl",
    "https://github.com/woct0rdho/SageAttention/releases/download/v2.2.0-windows.post4/sageattention-2.2.0+cu130torch2.9.0andhigher.post4-cp39-abi3-win_amd64.whl",
    "https://github.com/woct0rdho/SageAttention/releases/download/v2.2.0-windows.post3/sageattention-2.2.0+cu128torch2.8.0.post3-cp39-abi3-win_amd64.whl",
]

XFORMERS_URLS = [
    "https://huggingface.co/Wildminder/AI-windows-whl/resolve/main/xformers-0.0.34+torch2.11cu130-cp39-abi3-win_amd64.whl",
    "https://huggingface.co/Wildminder/AI-windows-whl/resolve/main/xformers-0.0.34+torch2.10cu130-cp39-abi3-win_amd64.whl",
]


@dataclass
class EnvProbe:
    python_path: str
    python_version: str = ""
    cuda_version: str = ""
    torch_version: str = ""
    gpu_name: str = ""

    @property
    def cuda_tag(self) -> str:
        """返回 pytorch 索引用的 tag，如 cu128；无显卡则 cpu。"""
        if not self.cuda_version:
            return "cpu"
        v = self.cuda_version.replace(".", "")
        return f"cu{v}" if v.isdigit() else "cpu"


def probe(python_path: str) -> EnvProbe:
    p = EnvProbe(python_path=python_path)
    code = (
        "import sys,json;"
        "print(sys.version.split()[0]);"
        "tv='';cv='';gn='';"
        "try:\n import torch;tv=torch.__version__;cv=torch.version.cuda or ''\n"
        "except Exception:pass\n"
        "print(json.dumps({'torch':tv,'cuda':cv,'gpu':gn}))"
    )
    try:
        r = subprocess.run([python_path, "-c", code], capture_output=True, text=True,
                           timeout=120, encoding="utf-8", errors="replace",
                           creationflags=NO_WINDOW)
        lines = (r.stdout or "").strip().splitlines()
        if lines:
            p.python_version = lines[0].strip()
        if len(lines) > 1:
            import json as _j
            try:
                d = _j.loads(lines[-1])
                p.torch_version = d.get("torch", "")
                p.cuda_version = d.get("cuda", "") or ""
                p.gpu_name = d.get("gpu", "")
            except Exception:
                pass
    except Exception:
        pass
    if not p.cuda_version:
        p.cuda_version = _cuda_from_nvidia_smi()
    p.gpu_name = p.gpu_name or _gpu_name()
    return p


def _cuda_from_nvidia_smi() -> str:
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=30,
                           creationflags=NO_WINDOW)
        m = re.search(r"CUDA Version:\s*([\d.]+)", r.stdout or "")
        return m.group(1) if m else ""
    except Exception:
        return ""


def _gpu_name() -> str:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=30,
                           creationflags=NO_WINDOW)
        return (r.stdout or "").strip().splitlines()[0] if r.stdout else ""
    except Exception:
        return ""


def torch_index(cuda_tag: str) -> str:
    return TORCH_INDEX.get(cuda_tag, TORCH_INDEX["cpu"])


def pick_sage(torch_version: str) -> Optional[str]:
    """按 torch 版本选一个 SageAttention wheel。"""
    if not torch_version:
        return SAGE_URLS[0]
    m = re.match(r"(\d+)\.(\d+)", torch_version)
    if not m:
        return SAGE_URLS[0]
    major, minor = int(m.group(1)), int(m.group(2))
    if (major, minor) >= (2, 10):
        return SAGE_URLS[0]
    if (major, minor) >= (2, 9):
        return SAGE_URLS[1]
    return SAGE_URLS[2]


def pick_xformers(torch_version: str) -> str:
    return XFORMERS_URLS[0]


def install_torch(python_path: str, cuda_tag: str, version: str = "") -> Tuple[bool, str]:
    idx = torch_index(cuda_tag)
    spec = []
    if version:
        spec = [f"torch=={version}", "torchvision", "torchaudio"]
    else:
        spec = ["torch", "torchvision", "torchaudio"]
    cmd = [python_path, "-m", "pip", "install"] + spec + ["--index-url", idx]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)


def install_wheel_url(python_path: str, url: str) -> Tuple[bool, str]:
    cmd = [python_path, "-m", "pip", "install", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)


def install_package(python_path: str, name: str, extra_index: str = "") -> Tuple[bool, str]:
    cmd = [python_path, "-m", "pip", "install", name]
    if extra_index:
        cmd += ["--extra-index-url", extra_index]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)


def candidate_urls(kind: str, torch_version: str) -> List[str]:
    if kind == "sage":
        u = pick_sage(torch_version)
        return [u] if u else []
    if kind == "xformers":
        return [pick_xformers(torch_version)]
    return []
