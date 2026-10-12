"""ComfyUI 进程适配：参数拼装 + 启停 + Qt 信号。

功能全部来自 Echo：
- 参数拼装的**注意力部分**用 `echo_launcher.core.attention.build_flags()`（默认/SAGE/FLASH/CK 互斥）
- 子进程用 `echo_launcher.core.process.ComfyProcess`（psutil 进程树终止、PYTHONUNBUFFERED、端口工具）
- 解释器定位用 Echo 的 `resolve_python()`
- 就绪判定用 Echo 的 `wait_ready()`

适配层只做两件事：把 UI 的 launch 配置翻译成命令行；把 Echo 的回调/日志队列转成 Qt 信号。
"""
from __future__ import annotations

import os
import shlex
import threading

from PySide6.QtCore import QObject, QTimer, Signal

from echo_launcher.core.attention import (
    build_flags, describe, normalize_mode, probe_ck, resolve_mode, strip_conflicts,
)
from echo_launcher.core.process import (
    ComfyProcess, LaunchRequest, find_free_port, kill_port, port_in_use, resolve_python,
    wait_ready,
)

from . import _bridge as B
from . import paths as _paths
from .config import VERBOSE_LEVELS

# 端口工具的再导出，UI 直接可用（与 Echo 的启动流程一致）
__all__ = [
    "LaunchOptions", "ComfyRunner", "log_pump",
    "port_in_use", "find_free_port", "kill_port", "resolve_python",
    "describe_attention", "normalize_attention", "probe_ck",
]


# --------------------------------------------------------------------------
# 启动参数
# --------------------------------------------------------------------------
class LaunchOptions:
    """把 UI 的 launch 配置翻译成 ComfyUI 命令行参数。

    注意力后端**不再**由 UI 的 sage 勾选框直接决定，而是交给 Echo 的四档解析：
    默认 / SAGE / FLASH / CK，且互斥、最多产出一个开关。
    """

    def __init__(self, launch_cfg: dict):
        self.cfg = dict(launch_cfg or {})

    # ---- 查询 ----
    def attention_mode(self) -> str:
        return resolve_mode(self.cfg)

    def env_vars(self) -> dict:
        env = {}
        for line in (self.cfg.get("env_vars") or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
        return env

    def proxy_url(self) -> str:
        if self.cfg.get("comfyui_proxy"):
            return (self.cfg.get("launcher_proxy_url") or "").strip()
        return ""

    def cuda_device(self):
        try:
            dev = int(self.cfg.get("cuda_device", -1))
        except (TypeError, ValueError):
            return None
        return str(dev) if dev >= 0 else None

    def port(self) -> int:
        try:
            p = int(self.cfg.get("port") or 0)
        except (TypeError, ValueError):
            p = 0
        return p or 8188

    # ---- 参数拼装 ----
    def build_args(self) -> list:
        """产出 ComfyUI 的参数（不含 --port/--listen，那两项由 LaunchRequest 负责）。"""
        l = self.cfg
        args = []

        if l.get("fp16_accumulation", True):
            args += ["--fast", "fp16_accumulation"]
        if l.get("cuda_malloc", True):
            args.append("--cuda-malloc")

        # Echo 的注意力四档（默认档跟随 sage_attention 勾选）
        att = build_flags(l)
        args += att

        if l.get("reserve_vram"):
            args.append("--reserve-vram")
        dev = self.cuda_device()
        if dev is not None:
            args += ["--cuda-device", dev]

        ex = l.get("expert") or {}
        # 复选框组（键 -> 命令行开关；对照 D:\ComfyUI\ComfyUI\comfy\cli_args.py 核对过）
        for key, flag in (
            ("enable_manager", "--enable-manager"),
            ("disable_xformers", "--disable-xformers"),
            ("dont_print_server", "--dont-print-server"),
            ("disable_metadata", "--disable-metadata"),
            ("disable_smart_memory", "--disable-smart-memory"),
            ("force_upcast_attention", "--force-upcast-attention"),
            ("dont_upcast_attention", "--dont-upcast-attention"),
            ("deterministic", "--deterministic"),
            ("mmap_torch_files", "--mmap-torch-files"),
            ("disable_mmap", "--disable-mmap"),
            ("async_offload", "--async-offload"),
            ("force_non_blocking", "--force-non-blocking"),
            ("quick_test", "--quick-test-for-ci"),
            ("log_stdout", "--log-stdout"),
            ("channels_last", "--force-channels-last"),
        ):
            if ex.get(key):
                args.append(flag)
        if ex.get("compress_response"):
            # 注意：官方参数是 --enable-compress-response-body（旧版拼错成 --compress-response-body）
            args.append("--enable-compress-response-body")
        if ex.get("cors_listen"):
            # 监听地址由 LaunchRequest.listen 负责（0.0.0.0），这里只开 CORS 头
            args += ["--enable-cors-header", ex.get("cors_origin") or "*"]
        v = ex.get("verbose") or ""
        # 只有合法等级名才拼（传「默认」/数字会让 ComfyUI 直接 parser.error 退出）
        if v in VERBOSE_LEVELS:
            args += ["--verbose", v]
        # 精度 / 显存 / 缓存下拉（存储值 -> 官方 flag；fp8 系列是**下划线**不是连字符）
        for key, table in (
            ("unet_precision", {
                "fp16": "--fp16-unet", "fp32": "--fp32-unet", "bf16": "--bf16-unet",
                "fp64": "--fp64-unet", "fp8_e4m3fn": "--fp8_e4m3fn-unet",
                "fp8_e5m2": "--fp8_e5m2-unet", "fp8_e8m0fnu": "--fp8_e8m0fnu-unet"}),
            ("vae_precision", {
                "fp16": "--fp16-vae", "fp32": "--fp32-vae", "bf16": "--bf16-vae"}),
            ("text_enc_precision", {
                "fp16": "--fp16-text-enc", "fp32": "--fp32-text-enc",
                "bf16": "--bf16-text-enc", "fp8_e4m3fn": "--fp8_e4m3fn-text-enc",
                "fp8_e5m2": "--fp8_e5m2-text-enc"}),
            ("global_precision", {"fp16": "--force-fp16", "fp32": "--force-fp32"}),
            ("vram_mode", {
                "gpu-only": "--gpu-only", "highvram": "--highvram",
                "lowvram": "--lowvram", "novram": "--novram", "cpu": "--cpu"}),
            ("cache_mode", {"classic": "--cache-classic", "none": "--cache-none"}),
        ):
            flag = table.get(ex.get(key))
            if flag:
                args.append(flag)

        extra = (l.get("extra_args") or "").strip()
        if extra:
            try:
                parts = shlex.split(extra, posix=False)
            except ValueError:
                parts = extra.split()
            # 已明确选档时，剔掉自定义参数里重复的注意力开关（Echo 的 strip_conflicts）
            if att:
                parts = strip_conflicts(parts)
            args += parts
        return args


# --------------------------------------------------------------------------
# 日志泵：把 Echo 的日志队列抽干并转成 Qt 信号
# --------------------------------------------------------------------------
class _LogPump(QObject):
    """Echo 的 QueueHandler 用的是**阻塞式** `queue.put`。

    队列容量 5000，没有消费者时任何写日志的线程都会永久卡住（死锁）。
    tkinter 版由 `MainWindow._bg_poll()` 定时抽干；Qt 版必须有对应角色，
    所以这里用 QTimer 常驻抽干，并把每条日志转成信号。
    """

    line = Signal(str, str)   # 文本, 级别("" / "error" / "warning")

    def __init__(self):
        super().__init__()
        self._log = B.log()
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._drain)
        self._timer.start()

    def _drain(self):
        try:
            items = self._log.drain()
        except Exception:
            return
        for text, level in items:
            if level == "error":
                lv = "error"
            elif level in ("warn", "warning"):
                lv = "warning"
            else:
                lv = ""
            self.line.emit(text, lv)


_PUMP = None


def log_pump() -> _LogPump:
    """全局唯一的日志泵（首次调用时创建，绑定当前 Qt 线程）。"""
    global _PUMP
    if _PUMP is None:
        _PUMP = _LogPump()
    return _PUMP


# --------------------------------------------------------------------------
# 运行器
# --------------------------------------------------------------------------
class ComfyRunner(QObject):
    """ComfyUI 子进程的 Qt 封装（内部是 Echo 的 ComfyProcess）。"""

    output = Signal(str, str)        # 行文本, 级别
    state_changed = Signal(bool)     # 是否运行中
    server_ready = Signal(str)       # http://127.0.0.1:8188
    stopped = Signal(int)            # 退出码

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc = ComfyProcess()
        self._proc.on_exit = self._on_exit
        self._port = 8188
        self._ready_sent = False
        log_pump().line.connect(self.output)

    # ---- 查询 ----
    @property
    def is_running(self) -> bool:
        return self._proc.running

    @property
    def port(self) -> int:
        return self._port

    # ---- 预览 ----
    def preview(self, root: str, opt: LaunchOptions) -> str:
        """返回一行「将要执行的命令」，给控制台打印用。"""
        d = _paths.dirs(root)
        cmd = [d["python_exe"] or "python", "main.py"]
        cmd += ["--port", str(opt.port()), "--listen", self._listen_of(opt)]
        cmd += opt.build_args()
        return " ".join(cmd)

    def _listen_of(self, opt: LaunchOptions) -> str:
        """专家参数勾了「启用 CORS，监听 0.0.0.0（局域网）」时改听 0.0.0.0。"""
        ex = opt.cfg.get("expert") or {}
        return "0.0.0.0" if ex.get("cors_listen") else "127.0.0.1"

    # ---- 启动 ----
    def start(self, root: str, opt: LaunchOptions) -> str:
        """启动 ComfyUI。成功返回 ""，失败返回错误说明。"""
        if self.is_running:
            return "ComfyUI 已在运行中"
        err = _paths.validate_root(root)
        if err:
            return err
        d = _paths.dirs(root)
        py = d["python_exe"]
        if not py or not os.path.isfile(py):
            return f"找不到 Python 解释器：{py or '(未指定)'}"

        self._port = opt.port()
        self._ready_sent = False
        req = LaunchRequest(
            comfyui_path=d["comfyui"],
            python_path=py,
            port=str(self._port),
            listen=self._listen_of(opt),
            extra_args=opt.build_args(),
            env_vars=opt.env_vars(),
            proxy=opt.proxy_url(),
            cuda_device=opt.cuda_device(),
        )
        B.log().info(f"启动 ComfyUI（端口 {self._port}）")
        if not self._proc.start(req):
            return "启动失败：详见控制台"
        self.state_changed.emit(True)
        threading.Thread(target=self._wait_ready, args=(self._port,),
                         name="echo-wait-ready", daemon=True).start()
        return ""

    def _wait_ready(self, port: int):
        try:
            ok = wait_ready(port, timeout=120)
        except Exception:
            ok = False
        if ok and not self._ready_sent:
            self._ready_sent = True
            self.server_ready.emit(f"http://127.0.0.1:{port}")

    def _on_exit(self, code):
        # 由 Echo 的 waiter 线程回调
        self.state_changed.emit(False)
        self.stopped.emit(int(code))

    # ---- 停止 ----
    def stop(self):
        self._proc.stop()


def describe_attention(mode: str) -> str:
    return describe(mode)


def normalize_attention(mode: str) -> str:
    return normalize_mode(mode)
