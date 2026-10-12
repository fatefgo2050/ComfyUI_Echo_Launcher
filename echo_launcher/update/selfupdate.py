"""自更新：走 GitHub Releases（公开接口，不用私有服务器）。

Windows 下正在运行的 exe 无法自我覆盖，所以流程是：
    下载新版本 -> 解压到临时目录 -> 生成 update.bat -> 启动 bat 并退出本进程
bat 会等主进程退出后复制文件并重新启动。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from typing import List, Optional

from ..net.http_client import HttpClient
from ..net.mirrors import GITHUB_API


@dataclass
class Release:
    tag: str
    name: str
    notes: str
    assets: List[dict]

    def pick(self, keywords=("win", "windows", ".zip", ".exe")) -> Optional[dict]:
        for a in self.assets:
            n = (a.get("name") or "").lower()
            if all(k in n for k in ("win",)) and (n.endswith(".zip") or n.endswith(".exe")):
                return a
        for a in self.assets:
            n = (a.get("name") or "").lower()
            if n.endswith(".zip") or n.endswith(".exe"):
                return a
        return None


def _version_tuple(tag: str):
    nums = re.findall(r"\d+", tag or "")
    return tuple(int(x) for x in nums[:4]) or (0,)


def latest_release(client: HttpClient, repo: str) -> Optional[Release]:
    """repo 形如 owner/name。"""
    url = f"{GITHUB_API}/repos/{repo}/releases/latest"
    try:
        r = client.get(url, timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
    except Exception:
        return None
    return Release(
        tag=d.get("tag_name", ""),
        name=d.get("name", "") or d.get("tag_name", ""),
        notes=(d.get("body") or "")[:2000],
        assets=d.get("assets") or [],
    )


def newer_than(tag: str, current: str) -> bool:
    return _version_tuple(tag) > _version_tuple(current)


def download_asset(client: HttpClient, asset: dict, dest: str,
                   progress=None) -> Optional[str]:
    url = asset.get("browser_download_url")
    if not url:
        return None
    try:
        return client.download(url, dest, progress=progress, timeout=120)
    except Exception:
        return None


def apply_archive(archive: str, target_dir: str, restart_cmd: List[str]) -> str:
    """解压并用 bat 覆盖替换。返回 bat 路径。"""
    tmp = tempfile.mkdtemp(prefix="te_update_")
    with zipfile.ZipFile(archive) as z:
        z.extractall(tmp)

    # zip 里可能多一层目录
    inner = tmp
    entries = os.listdir(tmp)
    if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
        inner = os.path.join(tmp, entries[0])

    bat = os.path.join(tempfile.gettempdir(), "te_update.bat")
    restart = " ".join(f'"{c}"' for c in restart_cmd)
    with open(bat, "w", encoding="gbk", errors="ignore") as f:
        f.write("@echo off\n")
        f.write("timeout /t 2 /nobreak >nul\n")
        f.write(f'xcopy "{inner}\\*" "{target_dir}\\" /E /Y /Q\n')
        f.write(f'start "" {restart}\n')
        f.write(f'rd /s /q "{tmp}"\n')
        f.write("del \"%~f0\"\n")
    subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000)
    return bat


def current_launch_cmd() -> List[str]:
    """构造重启命令：打包后是 exe，开发时是 python main.py。

    onefile 下 sys.executable / sys.argv[0] 指向用户双击的原始 exe（不是临时解压
    目录的副本）；NUITKA_ONEFILE_BINARY 显式记录了原始 exe 路径，优先使用。
    standalone 模式下 NUITKA_ONEFILE_BINARY 未设置，退化为 sys.executable。
    """
    if getattr(sys, "frozen", False):
        exe = os.environ.get("NUITKA_ONEFILE_BINARY") or sys.executable
        return [exe]
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "main.py")
    return [sys.executable, main_py]
