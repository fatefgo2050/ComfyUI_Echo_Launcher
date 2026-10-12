"""自更新适配（走 GitHub Releases）。

Windows 下正在运行的 exe 不能自我覆盖，所以流程是：
    下载新版本 -> 解压到临时目录 -> 生成 update.bat -> 启动 bat 并退出本进程
bat 会等主进程退出后 xcopy 覆盖并重新启动。

配置键 `update_repo` 形如 `owner/name`，在设置页填写（**输入框已预填 `DEFAULT_REPO`**）。

⚠ 关于「覆盖到哪儿」（2026-10-12 修）：
- **冻结态**：目标 = `dirname(sys.executable)`，即 exe 与 `resources/` 同级的那层
  —— 这正是免安装包里被替换的那层，`app_dir()` 负责算它。
- **源码运行**：**没有**可覆盖的安装目录（早先的实现用 `dirname(dirname(__file__))`，
  会覆盖到项目根，把源码目录糊上一份打包产物）。所以 `app_dir()` 返回空串，
  由设置页提示「源码运行请 git pull」并放弃安装。
- ⚠ 旧实现在 onefile 下目标算成了 `%TEMP%\\_MEIxxxx\\`（`__file__` 在临时解压目录里，
  退出即删）→ 覆盖过去等于什么都没更新。**别再退回那种写法。**
"""
from __future__ import annotations

import os
import sys

from echo_launcher.update import selfupdate as _u

from . import _bridge as B

try:                                    # 用合并版自己的版本号
    from comfyui_echo import __version__ as APP_VERSION
except Exception:                       # pragma: no cover
    APP_VERSION = "0.0.0"

__all__ = ["latest_release", "latest_release_ex", "newer_than", "download_asset",
           "apply_archive", "current_launch_cmd", "current_version", "repo",
           "DEFAULT_REPO", "is_frozen", "app_dir"]

#: 出厂默认的自更新仓库。设置页的输入框会**预填**它（仍可改成自己的 fork），
#: 配置里没写或写空时也用它 —— 这样新装/重置配置后「检查更新」开箱可用。
DEFAULT_REPO = "fatefgo2050/ComfyUI_Echo_Launcher"


def current_version() -> str:
    return APP_VERSION


def repo() -> str:
    return (B.config().get("update_repo") or "").strip() or DEFAULT_REPO


def is_frozen() -> bool:
    """是否以打包好的 exe 在跑 —— **只有冻结态才有能被覆盖的"安装目录"**。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """自更新的**覆盖目标目录**；源码运行返回空串（调用方据此放弃安装）。

    冻结态取 `dirname(sys.executable)`：PyInstaller onefile 下 `sys.executable` 仍指向
    用户双击的那个原始 exe（不是 `_MEIPASS` 里的副本），它所在的那层就是安装目录。
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return ""


def latest_release_ex(proxy: str = ""):
    """取最新 Release，返回 ``(Release | None, 失败原因)``。

    ★ 先按配置走代理，**失败再直连重试一次**。为什么必须兜底：GitHub API 未认证配额
    是「每 IP 60 次/小时」，而拉起的代理常是**共享出口**，配额早被别处用光 → 403；
    此时本机直连往往还有余量（实测：代理 403 / 直连 200）。反过来，直连被墙的用户
    也能靠第一步的代理走通。两条路都挂在最后才报错，并带回具体原因。
    """
    attempts = []
    if proxy:
        attempts.append((proxy, False))     # ① 按配置走代理
    attempts.append(("", True))             # ② 直连兜底（也覆盖"没配代理"的常态）

    err = "未知原因"
    for p, direct in attempts:
        try:
            rel, err = _u.latest_release_ex(B.http_client(proxy=p, direct=direct), repo())
        except Exception as e:              # 连构造 client 都可能抛（如证书配置异常）
            rel, err = None, f"{type(e).__name__}: {str(e)[:160]}"
        if rel is not None:
            return rel, ""
    return None, err


def latest_release(proxy: str = ""):
    """兼容旧调用：只取 Release（失败返回 None）。"""
    rel, _err = latest_release_ex(proxy)
    return rel


def newer_than(tag: str, current: str = "") -> bool:
    return _u.newer_than(tag, current or current_version())


def download_asset(asset: dict, dest: str, proxy: str = "", progress=None):
    return _u.download_asset(B.http_client(proxy=proxy), asset, dest, progress=progress)


def apply_archive(archive: str, target_dir: str, restart_cmd=None):
    return _u.apply_archive(archive, target_dir,
                            list(restart_cmd or current_launch_cmd()))


def current_launch_cmd():
    """重启命令：打包后是 exe，源码运行时是 [python, main.py]（由内核处理）。"""
    return _u.current_launch_cmd()
