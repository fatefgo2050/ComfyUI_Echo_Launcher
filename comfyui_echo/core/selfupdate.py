"""自更新适配（Echo 的 update/selfupdate.py，走 GitHub Releases）。

Windows 下正在运行的 exe 不能自我覆盖，所以流程是：
    下载新版本 -> 解压到临时目录 -> 生成 update.bat -> 启动 bat 并退出本进程
bat 会等主进程退出后 xcopy 覆盖并重新启动。

配置键 `update_repo` 形如 `owner/name`，在设置页填写。
"""
from __future__ import annotations

from echo_launcher.update import selfupdate as _u

from . import _bridge as B

try:                                    # 用合并版自己的版本号
    from comfyui_echo import __version__ as APP_VERSION
except Exception:                       # pragma: no cover
    APP_VERSION = "0.0.0"

__all__ = ["latest_release", "newer_than", "download_asset", "apply_archive",
           "current_launch_cmd", "current_version", "repo"]


def current_version() -> str:
    return APP_VERSION


def repo() -> str:
    return (B.config().get("update_repo") or "").strip()


def latest_release(proxy: str = ""):
    return _u.latest_release(B.http_client(proxy=proxy), repo())


def newer_than(tag: str, current: str = "") -> bool:
    return _u.newer_than(tag, current or current_version())


def download_asset(asset: dict, dest: str, proxy: str = "", progress=None):
    return _u.download_asset(B.http_client(proxy=proxy), asset, dest, progress=progress)


def apply_archive(archive: str, target_dir: str, restart_cmd=None):
    return _u.apply_archive(archive, target_dir,
                            list(restart_cmd or current_launch_cmd()))


def current_launch_cmd():
    """重启命令：打包后是 exe，源码运行时是 [python, main.py]（Echo 已处理）。"""
    return _u.current_launch_cmd()
