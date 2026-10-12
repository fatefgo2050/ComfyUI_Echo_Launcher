"""镜像与代理策略（全部为公开源，不含任何私有服务端）。"""
from __future__ import annotations

import os
from typing import Dict, List

# 节点映射表（ComfyUI-Manager 官方数据）的多源回退顺序
EXTENSION_NODE_MAP_SOURCES: List[str] = [
    "https://cdn.jsdelivr.net/gh/Comfy-Org/ComfyUI-Manager@main/extension-node-map.json",
    "https://raw.githubusercontent.com/Comfy-Org/ComfyUI-Manager/main/extension-node-map.json",
    "https://ghfast.top/https://raw.githubusercontent.com/Comfy-Org/ComfyUI-Manager/main/extension-node-map.json",
]

# 插件目录（含描述/作者/标签/最新 commit）
CUSTOM_NODE_LIST_SOURCES: List[str] = [
    "https://cdn.jsdelivr.net/gh/Comfy-Org/ComfyUI-Manager@main/custom-node-list.json",
    "https://raw.githubusercontent.com/Comfy-Org/ComfyUI-Manager/main/custom-node-list.json",
    "https://ghfast.top/https://raw.githubusercontent.com/Comfy-Org/ComfyUI-Manager/main/custom-node-list.json",
]

GITHUB_API = "https://api.github.com"
HUGGINGFACE_API = "https://huggingface.co/api"
HF_MIRROR = "https://hf-mirror.com"
CIVITAI_API = "https://civitai.com/api/v1"

# GitHub 加速镜像（用于 git fetch/clone：把 https://github.com/ 整体换成镜像前缀）。
# 落地方式见 comfyui_echo/core/core_version.py 的 _mirror_args()：
#   git -c url."<前缀>".insteadOf=https://github.com/ fetch ...
# 这样 origin 仍然是 github.com、origin/master 照常更新，只是真正连的地址换成镜像。
# 键 = 界面下拉里显示的名字，值 = 用来替换 "https://github.com/" 的前缀（空串 = 官方直连）。
# 本机实测（2026-10-09，`git ls-remote https://github.com/Comfy-Org/ComfyUI HEAD`）：
#   官方 2.2s / gh-proxy.com 2.7s / ghproxy.net 4.9s / ghfast.top 9.8s 均返回同一 sha；
#   kkgithub 与 gitclone 取不到（scheme 报错），已剔除 —— 留着只会让用户白等。
GITHUB_MIRRORS: Dict[str, str] = {
    "官方（GitHub 直连）": "",
    "gh-proxy.com": "https://gh-proxy.com/https://github.com/",
    "ghproxy.net": "https://ghproxy.net/https://github.com/",
    "ghfast.top": "https://ghfast.top/https://github.com/",
}

PYPI_MIRRORS: Dict[str, str] = {
    "官方": "https://pypi.org/simple/",
    "阿里云": "https://mirrors.aliyun.com/pypi/simple/",
    "清华": "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "腾讯云": "https://mirrors.cloud.tencent.com/pypi/simple/",
}

# 硬性拦截：确保重构后不会有任何请求打到原作者的私有域名
BLOCKED_HOSTS = (
    "tetae.top",
    "588187.xyz",
    "teynex.com",
)


def hf_base(use_mirror: bool = False) -> str:
    return HF_MIRROR if use_mirror else "https://huggingface.co"


def hf_api(use_mirror: bool = False) -> str:
    return f"{hf_base(use_mirror)}/api" if not use_mirror else HUGGINGFACE_API


def pypi_index(name: str) -> str:
    return PYPI_MIRRORS.get(name, PYPI_MIRRORS["阿里云"])


def portable_git() -> str:
    """优先用随包携带的便携 Git，找不到则回退系统 git。

    便携 Git 位于 ``<程序目录>/resources/git``（**外置**分发）。
    为什么必须外置：Nuitka 的 ``--include-data-dir`` 不收 .exe/.dll
    （实测 resources/git 328 个文件里，155 个二进制全部被丢、cmd/ 整个消失），
    打进 onefile 后这里必然找不到，只能回退到系统 PATH 上的 git ——
    在没装 Git 的机器上，插件 clone/pull/切版本/更新检查会全部失效。

    回退时会打一条 WARN，避免这种故障继续静默发生。
    """
    from .. import RESOURCE_DIR
    for rel in (("git", "cmd", "git.exe"), ("git", "mingw64", "bin", "git.exe")):
        cand = os.path.join(RESOURCE_DIR, *rel)
        if os.path.isfile(cand):
            return cand

    from shutil import which
    fallback = which("git")
    try:
        from ..core.log import get_log
        log = get_log()
        if fallback:
            log.warn(
                f"便携 Git 缺失（{os.path.join(RESOURCE_DIR, 'git')}），"
                f"已回退系统 Git：{fallback}"
            )
        else:
            log.error(
                f"便携 Git 缺失（{os.path.join(RESOURCE_DIR, 'git')}），"
                f"且 PATH 上也没有 git —— 插件安装/更新/切版本将不可用"
            )
    except Exception:
        pass
    return fallback or "git"
