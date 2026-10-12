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


def latest_release_ex(client: HttpClient, repo: str):
    """同 ``latest_release``，但**附带失败原因**（返回 ``(Release | None, 原因)``）。

    旧实现把「非 200」和「抛异常」一律折叠成 ``None``，UI 只能笼统地报
    「仓库不存在 / 无网络 / 无 Release」—— 用户没法知道到底卡在哪。这里把状态码
    翻译成人话，尤其把 **403/429（未认证配额被用光）** 单独点出来：
    GitHub API 未认证是「每 IP 60 次/小时」，**共享代理出口极易被别处用光**，
    而这是自更新最常见的失败，和"仓库写错了"完全是两码事。
    成功时原因是空串。
    """
    url = f"{GITHUB_API}/repos/{repo}/releases/latest"
    try:
        r = client.get(url, timeout=20)
    except Exception as e:
        return None, f"网络请求失败：{type(e).__name__}: {str(e)[:160]}"

    code = r.status_code
    if code == 200:
        try:
            d = r.json()
        except Exception as e:
            return None, f"响应不是合法 JSON：{str(e)[:120]}"
        return Release(
            tag=d.get("tag_name", ""),
            name=d.get("name", "") or d.get("tag_name", ""),
            notes=(d.get("body") or "")[:2000],
            assets=d.get("assets") or [],
        ), ""

    detail = ""
    try:
        detail = str((r.json() or {}).get("message") or "")[:160]
    except Exception:
        pass

    if code == 404:
        return None, "仓库不存在，或该仓库还没有发布任何 Release（HTTP 404）"
    if code in (403, 429):
        if not getattr(client, "github_token", "") and "rate limit" in detail.lower():
            return None, (f"GitHub API 未认证配额已用尽（HTTP {code}）：每 IP 每小时 60 次，"
                          "共享代理出口常被别处用光。到「设置」页填一个 GitHub Token "
                          "即可提到 5000 次/小时。")
        return None, f"访问被拒绝（HTTP {code}）：{detail}"
    return None, f"HTTP {code}" + (f"：{detail}" if detail else "")


def latest_release(client: HttpClient, repo: str) -> Optional[Release]:
    """repo 形如 owner/name。失败一律返回 None（保留旧契约，老调用方无感）。"""
    rel, _err = latest_release_ex(client, repo)
    return rel


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

    # 重启目标优先用**归档根层的 exe**：新版可能改了 exe 名（0.1.0 的
    # `ComfyUI_Echo启动器.exe` → 0.1.1 的 `ComfyUI Echo启动器.exe`），
    # 若还按旧的 restart_cmd 启动，拉起的就是那个没被覆盖的旧 exe。
    restart_cmd = list(restart_cmd)
    if getattr(sys, "frozen", False):
        try:
            exes = sorted(f for f in os.listdir(inner) if f.lower().endswith(".exe"))
        except OSError:
            exes = []
        if exes:
            restart_cmd = [os.path.join(target_dir, exes[0])]

    bat = os.path.join(tempfile.gettempdir(), "te_update.bat")
    restart = " ".join(f'"{c}"' for c in restart_cmd)
    with open(bat, "w", encoding="gbk", errors="ignore") as f:
        f.write("@echo off\n")
        f.write("timeout /t 2 /nobreak >nul\n")
        f.write(f'xcopy "{inner}\\*" "{target_dir}\\" /E /Y /Q\n')
        # 删掉「不在新包里」的 exe：`xcopy /E /Y` **只覆盖、不删除**，而 0.1.0 -> 0.1.1
        # 恰好改了 exe 名（下划线 -> 空格）—— 不清理的话新旧两个 exe 会并排躺着，用户会点错。
        # 判定依据是新包解压目录里有没有同名文件（该目录下一行才 rd 掉，此刻还在）。
        # 免安装目录按文档只放本程序的 exe + resources/，所以按 *.exe 清理是安全的。
        f.write(f'for %%f in ("{target_dir}\\*.exe") do '
                f'if not exist "{inner}\\%%~nxf" del /q "%%f"\n')
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
