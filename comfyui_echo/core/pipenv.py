"""pip 环境适配（python_embeded 的包管理）。

- 解释器定位：走适配层的 `paths.dirs()`（内部用 Echo 的 `resolve_python`）
- 索引地址：走 Echo 的 `net.mirrors.PYPI_MIRRORS`
- 列包 / 快照：直接用 Echo 的 `env.pipwrap`
- 带实时输出的安装/卸载：Echo 的 `pipwrap._run()` 是阻塞式（没有 on_line 回调），
  而 UI 需要把 pip 输出实时打到日志框，所以这里用同一套「解释器 + 索引 + 代理」
  参数自己起子进程流式读取；其余逻辑与 Echo 保持一致。
"""
from __future__ import annotations

import os
import subprocess
from urllib.parse import urlparse

from echo_launcher.env import pipwrap
from echo_launcher.net.mirrors import PYPI_MIRRORS as _ECHO_MIRRORS

from . import paths as _paths

# UI 下拉用（键名与 Echo 的 mirrors 一致；「官方」用空串表示不加 -i）
PYPI_MIRRORS = {k: ("" if k == "官方" else v) for k, v in _ECHO_MIRRORS.items()}

NO_WINDOW = 0x08000000


def _index_url(mirror_key: str) -> str:
    if not mirror_key or mirror_key == "官方":
        return ""
    return _ECHO_MIRRORS.get(mirror_key, "")


def _python(root: str) -> str:
    return _paths.dirs(root)["python_exe"]


def _base_cmd(root: str, args: list, mirror_key: str) -> list:
    py = _python(root)
    cmd = [py, "-m", "pip"] + list(args)
    index = _index_url(mirror_key)
    if index and args and args[0] in ("install", "download", "wheel"):
        cmd += ["-i", index]
        host = urlparse(index).hostname
        if host:
            cmd += ["--trusted-host", host]
    return cmd


def run_pip(root: str, args: list, mirror_key: str = "", proxy_url: str = "",
            on_line=None, timeout: int = 1800):
    """执行 pip 命令，逐行回调。返回 (code, 完整输出)。"""
    py = _python(root)
    if not py or not os.path.isfile(py):
        msg = f"找不到解释器: {py or '(未指定)'}"
        if on_line:
            on_line(msg)
        return -1, msg

    cmd = _base_cmd(root, args, mirror_key)
    env = os.environ.copy()
    if proxy_url:
        env["HTTP_PROXY"] = proxy_url
        env["HTTPS_PROXY"] = proxy_url
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
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=env, cwd=_paths.dirs(root)["root"], creationflags=NO_WINDOW)
    except Exception as e:
        _emit(f"启动 pip 失败: {e}")
        return -1, "\n".join(lines)

    try:
        for line in proc.stdout:
            _emit(line.rstrip("\n"))
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        _emit(f"pip 超时（{timeout}s），已终止")
        return 124, "\n".join(lines)
    return code, "\n".join(lines)


def list_packages(root: str, mirror_key: str = "") -> list:
    """已装包列表 -> [{"name":..., "version":...}]（Echo 的 pipwrap 实现）。"""
    py = _python(root)
    if not py or not os.path.isfile(py):
        return []
    try:
        return [{"name": n, "version": v} for n, v in pipwrap.list_packages(py)]
    except Exception:
        return []


def freeze(root: str, out_path: str, mirror_key: str = ""):
    """导出环境快照到文件。返回 (ok, 路径)。"""
    code, out = run_pip(root, ["freeze", "--disable-pip-version-check"],
                        mirror_key, timeout=300)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(out)
    except OSError as e:
        return False, f"写入失败: {e}"
    return code == 0, out_path


def install_requirements(root: str, req_file: str, mirror_key: str = "",
                         proxy_url: str = "", on_line=None):
    return run_pip(root, ["install", "-r", req_file], mirror_key, proxy_url, on_line)


def uninstall(root: str, names, mirror_key: str = "", on_line=None):
    if isinstance(names, str):
        names = [names]
    return run_pip(root, ["uninstall", "-y", *names], mirror_key, on_line=on_line)
