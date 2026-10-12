"""ComfyUI 子进程管理：启动参数拼装、端口检测、输出捕获、停止。"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import psutil

from .log import get_log

CREATE_NO_WINDOW = 0x08000000


@dataclass
class LaunchRequest:
    comfyui_path: str
    python_path: str = ""
    port: str = "8188"
    extra_args: List[str] = field(default_factory=list)
    env_vars: Dict[str, str] = field(default_factory=dict)
    proxy: str = ""
    listen: str = "127.0.0.1"
    cuda_device: Optional[str] = None


def resolve_python(comfyui_path: str, python_path: str = "", log=None) -> str:
    """按优先级找一个可用的 Python 解释器，返回绝对路径；找不到返回 ""。

    优先级：显式配置的 python_path → ComfyUI 各种便携布局 → PATH 上的 python。
    抽成模块级函数是为了让 `core.attention.probe_ck()` 也能用同一套解析规则
    （必须在 ComfyUI 自己的解释器里探测 comfy_kitchen）。
    """
    candidates = []
    if python_path:
        candidates.append(python_path)
    # ComfyUI 便携包常见布局
    for rel in ("python/python.exe", "venv/Scripts/python.exe",
                "../python_embeded/python.exe", "../python/python.exe"):
        candidates.append(os.path.normpath(os.path.join(comfyui_path, rel)))
    candidates.append("python")
    for c in candidates:
        if os.path.isabs(c) and os.path.isfile(c):
            return c
        if not os.path.isabs(c):
            from shutil import which
            w = which(c)
            if w:
                return w
    if log is not None:
        log.error(f"找不到可用的 Python 解释器（试过 {len(candidates)} 个候选）")
    return ""


class ComfyProcess:
    """管理一个 ComfyUI 子进程，后台线程读 stdout/stderr 推给日志总线。"""

    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.on_exit: Optional[Callable[[int], None]] = None
        self.on_line: Optional[Callable[[str], None]] = None   # 每收到一行就回调
        self.log = get_log()

    # ---------- 查询 ----------
    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def pid(self) -> Optional[int]:
        return self.proc.pid if self.proc else None

    # ---------- 启动 ----------
    def start(self, req: LaunchRequest) -> bool:
        if self.running:
            self.log.warn("ComfyUI 已在运行")
            return False

        main_py = os.path.join(req.comfyui_path, "main.py")
        if not os.path.isfile(main_py):
            self.log.error(f"找不到 ComfyUI 入口: {main_py}")
            return False

        python = self._resolve_python(req)
        if not python:
            return False

        cmd = [python, main_py]
        cmd += self._build_args(req)
        cmd += list(req.extra_args)

        env = os.environ.copy()
        env.update(req.env_vars or {})
        if req.proxy:
            env["HTTP_PROXY"] = req.proxy
            env["HTTPS_PROXY"] = req.proxy
            env["http_proxy"] = req.proxy
            env["https_proxy"] = req.proxy
        if req.cuda_device:
            env["CUDA_VISIBLE_DEVICES"] = str(req.cuda_device)
        # 让子进程输出不缓冲，日志才能实时
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        self.log.info("启动命令: " + " ".join(cmd))
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=req.comfyui_path,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as e:
            self.log.error(f"启动失败: {e}")
            return False

        self._stop.clear()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        threading.Thread(target=self._waiter, daemon=True).start()
        return True

    def _resolve_python(self, req: LaunchRequest) -> Optional[str]:
        path = resolve_python(req.comfyui_path, req.python_path, log=self.log)
        if not path:
            self.log.error("找不到可用的 Python 解释器，请在路径管理里指定")
        return path or None

    @staticmethod
    def _build_args(req: LaunchRequest) -> List[str]:
        args = ["--port", str(req.port), "--listen", req.listen]
        return args

    # ---------- 输出 ----------
    def _pump(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                line = line.rstrip("\r\n")
                if not line:
                    continue
                self._classify(line)
        except Exception as e:
            self.log.error(f"读取输出中断: {e}")

    ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

    def _classify(self, line: str) -> None:
        clean = self.ANSI_RE.sub("", line)
        low = clean.lower()
        if "error" in low or "traceback" in low or "exception" in low:
            self.log.error(clean)
        elif "warning" in low or "warn" in low or "deprecat" in low:
            self.log.warn(clean)
        else:
            self.log.info(clean)
        if self.on_line:
            try:
                self.on_line(clean)
            except Exception:
                pass

    def _waiter(self) -> None:
        proc = self.proc
        if proc is None:
            return
        code = proc.wait()
        self._stop.set()
        self.log.info(f"ComfyUI 进程退出，代码 {code}")
        if self.on_exit:
            try:
                self.on_exit(code)
            except Exception:
                pass

    # ---------- 停止 ----------
    def stop(self, timeout: float = 8.0) -> bool:
        if not self.running or self.proc is None:
            return True
        self._stop.set()
        pid = self.proc.pid
        killed = []
        try:
            parent = psutil.Process(pid)
            killed = parent.children(recursive=True)
            killed.append(parent)
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass
        deadline = time.time() + timeout
        while time.time() < deadline and self.running:
            time.sleep(0.2)
        if self.running:
            for p in killed:
                try:
                    p.kill()
                except Exception:
                    pass
            try:
                self.proc.kill()
            except Exception:
                pass
        return not self.running


# ---------- 端口工具 ----------
def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def find_free_port(start: int = 8188, limit: int = 50) -> int:
    for p in range(start, start + limit):
        if not port_in_use(p):
            return p
    return start


def pids_on_port(port: int) -> List[int]:
    out = []
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.laddr and c.laddr.port == port and c.pid:
                out.append(c.pid)
    except Exception:
        pass
    return sorted(set(out))


def kill_port(port: int) -> int:
    n = 0
    for pid in pids_on_port(port):
        try:
            psutil.Process(pid).kill()
            n += 1
        except Exception:
            pass
    return n


def wait_ready(port: int, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_in_use(port):
            return True
        time.sleep(0.5)
    return False
