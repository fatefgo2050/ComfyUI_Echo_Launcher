"""日志：同时输出到文件与 UI 控制台（线程安全队列）。"""
from __future__ import annotations

import logging
import os
import queue
import threading
from typing import Callable, List, Optional

_LEVEL_STYLE = {
    "ERROR": "error",
    "WARNING": "warn",
    "INFO": "info",
    "DEBUG": "debug",
}


class QueueHandler(logging.Handler):
    """把日志推到队列，UI 线程定时取走显示（避免子线程直接操作 Tk）。"""

    def __init__(self, q: "queue.Queue"):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put((record.getMessage(), _LEVEL_STYLE.get(record.levelname, "info")))
        except Exception:
            pass


class LogBus:
    def __init__(self, log_dir: str):
        os.makedirs(log_dir, exist_ok=True)
        self.queue: "queue.Queue" = queue.Queue(maxsize=5000)
        self.logger = logging.getLogger("echo_launcher")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
        fh = logging.FileHandler(os.path.join(log_dir, "launcher.log"), encoding="utf-8")
        fh.setFormatter(fmt)
        self.logger.addHandler(fh)
        self.logger.addHandler(QueueHandler(self.queue))
        self._subs: List[Callable[[str, str], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Callable[[str, str], None]) -> None:
        with self._lock:
            self._subs.append(fn)

    def push(self, text: str, level: str = "info") -> None:
        """外部（比如子线程）直接推一条 UI 消息。"""
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(text, level)
            except Exception:
                pass

    def drain(self, max_items: int = 200) -> list:
        """UI 线程调用，取走积压消息。"""
        out = []
        for _ in range(max_items):
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return out

    # 便捷方法
    def info(self, msg: str) -> None:
        self.logger.info(msg)

    def warn(self, msg: str) -> None:
        self.logger.warning(msg)

    def error(self, msg: str) -> None:
        self.logger.error(msg)

    def debug(self, msg: str) -> None:
        self.logger.debug(msg)


_BUS: Optional[LogBus] = None


def get_log(base_dir: Optional[str] = None) -> LogBus:
    global _BUS
    if _BUS is None:
        if base_dir is None:
            from .. import BASE_DIR
            base_dir = BASE_DIR
        _BUS = LogBus(os.path.join(base_dir, "logs"))
    return _BUS
