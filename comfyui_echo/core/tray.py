"""系统托盘 + Windows 通知（Qt 版）。

旧界面版用 `root.after(0, fn)` 把菜单动作转回主线程 —— 那是旧工具链的 API。
这里换成 Qt 的做法：托盘菜单回调（运行在 pystray 自己的线程里）只发信号，
Qt 自动按 QueuedConnection 排到主线程执行，所以**不需要**手动加锁或 postEvent。

图标与通知：直接复用 Echo 的 `make_icon()` / `notify()`。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Signal

from . import _bridge as B
from . import brand

__all__ = ["TrayController", "notify", "make_icon", "available"]

try:
    import pystray
except Exception:                        # pragma: no cover
    pystray = None


def available() -> bool:
    return pystray is not None


# --------------------------------------------------------------------------
# 图标与通知
# --------------------------------------------------------------------------
# 这里**不**复用 Echo 的 `echo_launcher/ui/tray.py`：那个包的 `__init__.py`
# 会 eagerly `from . import console, main_window`，进而拉起旧界面模块 ——
# 在 PySide6 的 venv 里没有那些旧模块，一 import 就崩。所以图标/通知在本模块内实现。
#
# 图标：直接渲染 `resources/echo_logo.svg` 的**完整形态**（`FRAME_FULL` —— 带白色圆角
# 填充 + 描边，托盘/图标位正是它该出现的地方）。⛔ 不要再退回「蓝色圆环 + 橙点」那种自造方标
# （用户 2026-10-10 明确要求删掉），也不要用 `echo_logo.png` 缩放（位图，小尺寸糊）。
# 注：logo 里字标只占方块宽 82%、高 26%，所以 16/24px 下必然看不清，这是比例决定的。
def make_icon(size: int = 64):
    try:
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
        # 托盘要的是**完整形态**（带底托），SVG 文件里原样就是这样，直接读即可
        # —— 不用走 ui.widgets 的扁平化逻辑，避免 core 反向依赖 ui。
        r = QSvgRenderer(os.path.join(B.RESOURCE_DIR, brand.LOGO_SVG))
        if r.isValid():
            im = QImage(size, size, QImage.Format_RGBA8888)
            im.fill(Qt.transparent)
            p = QPainter(im)
            p.setRenderHint(QPainter.Antialiasing, True)
            r.render(p, QRectF(0.0, 0.0, float(size), float(size)))
            p.end()
            from PIL import Image
            return Image.frombytes("RGBA", (size, size), bytes(im.constBits()))
    except Exception:
        pass
    # 兜底（QtSvg 缺失 / 资源丢失）：白底圆角 + 用色块拼出字标的「E」
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1),
                        radius=size * brand.CORNER_RATIO,
                        fill=(255, 255, 255, 255))
    col = brand.rgba(brand.BLUE)
    bw, bh = size * 0.62, size * 0.115
    bx = (size - bw) / 2.0
    for k, frac in enumerate((0.20, 0.44, 0.68)):
        w = bw * (1.0, 0.86, 0.98)[k]
        d.rounded_rectangle((bx, size * frac, bx + w, size * frac + bh),
                            radius=bh / 2.0, fill=col)
    return img


def notify(title: str, msg: str, duration: str = "short") -> bool:
    """Windows 10/11 通知；失败返回 False（调用方可降级）。"""
    try:
        from win11toast import toast
        toast(title, msg, duration=duration)
        return True
    except Exception:
        return False


class TrayController(QObject):
    """托盘控制器。所有菜单动作都转成 Qt 信号，由主窗口连接处理。"""

    show_requested = Signal()
    web_requested = Signal()
    restart_requested = Signal()
    about_requested = Signal()
    quit_requested = Signal()
    open_dir_requested = Signal(str)     # "plugins" / "models" / "output"

    def __init__(self, title: str = "ComfyUI Echo 启动器", parent=None):
        super().__init__(parent)
        self._title = title
        self._icon = None
        self._thread = None

    @property
    def running(self) -> bool:
        return self._icon is not None

    def start(self) -> bool:
        if pystray is None or self._icon is not None:
            return False
        menu = pystray.Menu(
            pystray.MenuItem("显示主界面", self._on_show),
            pystray.MenuItem("打开 ComfyUI 网页", self._on_web),
            pystray.MenuItem("重启 ComfyUI", self._on_restart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("插件目录", lambda *_a: self.open_dir_requested.emit("plugins")),
            pystray.MenuItem("模型目录", lambda *_a: self.open_dir_requested.emit("models")),
            pystray.MenuItem("输出目录", lambda *_a: self.open_dir_requested.emit("output")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("关于", self._on_about),
            pystray.MenuItem("退出", self._on_quit),
        )
        try:
            self._icon = pystray.Icon("comfyui_echo", make_icon(), self._title, menu)
        except Exception:
            self._icon = None
            return False
        # pystray 自己开线程跑消息循环；回调里发信号即可（Qt 会自动排队到主线程）
        self._icon.run_detached()
        return True

    def set_title(self, text: str) -> None:
        if self._icon is not None:
            try:
                self._icon.title = text
            except Exception:
                pass

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

    # ---------- 菜单回调（pystray 线程） ----------
    def _on_show(self, *_a):
        self.show_requested.emit()

    def _on_web(self, *_a):
        self.web_requested.emit()

    def _on_restart(self, *_a):
        self.restart_requested.emit()

    def _on_about(self, *_a):
        self.about_requested.emit()

    def _on_quit(self, *_a):
        self.stop()
        self.quit_requested.emit()
