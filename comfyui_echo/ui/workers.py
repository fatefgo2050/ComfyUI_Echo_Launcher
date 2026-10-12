"""通用后台线程 Worker：把阻塞函数丢进 QThread，结果经信号回主线程。

**report 约定（2026-10-10）**：如果 `fn` 的签名里有 `report` 形参，Worker 会自动把
`self.line.emit` 作为 report 传给它——job 内部 `report("...")` 就等于发 `line` 信号，
主线程经 `on_line` 接住显示。

⚠ 这条是修出来的：插件页/市场页/模型页/媒体页共 9 处 `def job(report)` 一直等着这个参数，
原来 `Worker(job)` 空参调用，一点「卸载/安装/检查更新」就 TypeError: missing 1 required
positional argument: 'report'。修在这里而不是逐个 job 改签名，是因为它一处管全部、
且不破坏「不带 report 的 job」的调用（没这个形参就不注入）。
"""
import inspect
import traceback

from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    line = Signal(str)        # 过程输出行
    done = Signal(object)     # 结果（可为任意对象）
    failed = Signal(str)

    def __init__(self, fn, *args, on_line=None, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        if on_line is not None:
            self.line.connect(on_line)
        # fn 声明了 report 形参且调用方没显式传 -> 自动注入 line.emit
        try:
            params = inspect.signature(fn).parameters
            if "report" in params and "report" not in kwargs:
                self.kwargs["report"] = self.line.emit
        except (TypeError, ValueError):     # 拿不到签名就按原样调用
            pass
        self._stopped = False

    def run(self):
        if self._stopped:
            return
        try:
            result = self.fn(*self.args, **self.kwargs)
            if not self._stopped:
                self.done.emit(result)
        except Exception:
            if not self._stopped:
                self.failed.emit(traceback.format_exc())

    def stop(self):
        self._stopped = True
