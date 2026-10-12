"""主窗口：自绘标题栏（无边框）+ 侧栏导航 + 页面栈 + 全局上下文 + 系统托盘。

标题栏说明（2026-10-10 用户要求去掉系统标题栏）：
- `FramelessWindowHint` 去边框，左上角自绘 logo + 软件名 + 版本，右上角自绘
  最小化 / 最大化 / 关闭 三枚按钮；
- 拖动、双击最大化、贴边分屏（Aero Snap）、边缘缩放**全部走 Windows 原生
  WM_NCHITTEST**（见 `nativeEvent`）——返回 HTCAPTION / HT* 让系统接管，
  体验与系统标题栏完全一致，这是自绘标题栏里坑最少的一种做法；
- 「关闭」走 close()，托盘最小化等行为在 closeEvent 里，不受影响。
"""
import ctypes
import os

from PySide6.QtCore import Qt, QEvent, QPoint, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

try:            # Windows 专属：无边框标题栏的 hit-test 需要
    import ctypes.wintypes as _wt
except ImportError:
    _wt = None


class _MARGINS(ctypes.Structure):
    """DWM 的 MARGINS（`ctypes.wintypes` 里没有这个结构体）。"""
    _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]

from .. import APP_TITLE, __version__
from ..core import config as cfgmod
from ..core import paths as _paths
from ..core import tray as traymod
from ..core.process import ComfyRunner
from .about_page import AboutPage
from .env_page import EnvPage
from .expert_page import ExpertPage
from .home_page import HomePage
from .market_page import MarketPage
from .media_page import MediaPage
from .model_page import ModelPage
from .modes_page import ModesPage
from .plugins_page import PluginsPage
from .settings_page import SettingsPage
from .version_page import VersionPage
from .widgets import LogoBadge, plain_box

PAGES = [
    ("启动", "home"),
    ("专家模式", "expert"),
    ("ComfyUI 内核", "version"),
    ("插件", "plugins"),
    ("模式", "modes"),
    ("环境", "env"),
    ("媒体", "media"),
    ("模型", "model"),
    ("市场", "market"),
    ("设置", "settings"),
    ("关于", "about"),
]

# ---- 侧栏功能分区 -----------------------------------------------------------
# 11 个平铺入口按使用阶段归成 4 组：
#   运行 → 把 ComfyUI 跑起来 + 高级参数 + 看内核版本（唯一入口 + 专家模式 + 内核升级）
#   扩展 → 围绕 custom_nodes 的装/找/切      （插件 → 市场 → 模式）
#   素材 → 喂进去的模型 / 跑出来的产物       （模型 → 媒体）
#   系统 → 启动器自身与解释器环境           （环境 → 设置 → 关于）
# 「工作流」不再是独立页面：它和「模式」合成了同一页（见 modes_page 文件头），
# 否则「分析出需要哪些插件」和「按它启动」之间永远隔着一页。
# 「ComfyUI 内核」原先叫「版本管理」、而且弹的是**独立对话框**；用户要求直接内嵌成页面，
# 所以它现在和别的页一样走 PAGES + switch()，侧栏标签改成更准确的「ComfyUI 内核」。
# 「专家模式」对齐 TE 的「专家设置」，2026-10-09 按用户要求做成独立页面归入运行区。
# 「关于」2026-10-10 同样从独立对话框（原 about_dialog.py）改成一级页面，归入系统区；
#   侧栏底部原来那个 `#NavAux`「关于」入口已删除（见 _build_sidebar）。
# 注意：PAGES 保持平铺不变，switch() 与测试都依赖它，这里只改显示分组。
NAV_GROUPS = [
    ("运行", ["home", "expert", "version"]),
    ("扩展", ["plugins", "market", "modes"]),
    ("素材", ["model", "media"]),
    ("系统", ["env", "settings", "about"]),
]

PAGE_LABELS = {key: name for name, key in PAGES}

PAGE_HINTS = {
    "home": "选包路径、调启动参数、看实时日志",
    "expert": "ComfyUI 高级启动参数（精度 / 显存 / 注意力等）",
    "version": "查看并切换 ComfyUI 内核版本（内核升级）",
    "plugins": "已装插件的启用 / 更新 / 依赖",
    "market": "搜索并安装新插件",
    "modes": "按工作流生成启动模式：只加载这份工作流要用的插件",
    "model": "模型清单、备注、Civitai 匹配",
    "media": "浏览 output 里的图片和视频",
    "env": "pip 包管理与重型轮子",
    "settings": "启动器自身设置与更新",
    "about": "软件介绍 · 版本更新（功能用途与更新记录）",
}


class AppContext:
    """页面共享状态。"""

    def __init__(self, window):
        self.window = window
        self.cfg = cfgmod.load()
        self.runner = ComfyRunner(window)
        self._workers = []
        # CK（comfy-kitchen INT8 注意力）实测结果，跨页共享：
        # 在「环境」页点「检测 CK」测出来后，「启动」页的 CK 前置实测就不用再测一遍。
        self.ck_ok = None
        self.ck_text = ""

    def save_cfg(self):
        cfgmod.save(self.cfg)

    def root(self) -> str:
        return cfgmod.current_root(self.cfg)

    def launch_cfg(self) -> dict:
        return self.cfg.setdefault("launch", {})

    def spawn(self, worker):
        """持有 worker 引用防止被 GC；结束后自动清理。"""
        self._workers.append(worker)
        worker.finished.connect(lambda: self._cleanup(worker))
        worker.start()
        return worker

    def _cleanup(self, worker):
        if worker in self._workers:
            self._workers.remove(worker)

    def status(self, text):
        self.window.statusBar().showMessage(text, 8000)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} v{__version__}")
        self.resize(1280, 840)
        # 最小尺寸跟着内容走：再窄下去「启动参数」的复选框与控制台按钮行
        # 会被压到文字截断（Qt 的按钮不会自动省略号，只会直接切掉）。
        self.setMinimumSize(1100, 700)
        # 无边框：标题栏由 _build_titlebar() 自绘（用户要求：左上角 logo+软件名，
        # 右上角保留缩放/关闭，去掉系统那条标题栏）
        self.setWindowFlag(Qt.FramelessWindowHint)
        self._dwm_chrome_done = False       # DWM 圆角 / 投影只设一次（见 showEvent）

        self.ctx = AppContext(self)
        self.tray = None

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())

        # 右侧内容：顶部留 DRAG_H 的安全区给右上角那三个按钮（不画横条，
        # 只是让内容别顶到按钮底下），其余照旧铺满
        wrap = QWidget()
        wv = QVBoxLayout(wrap)
        wv.setContentsMargins(0, self.DRAG_H, 0, 0)
        wv.setSpacing(0)
        self.stack = QStackedWidget()
        self.pages = {
            "home": HomePage(self.ctx),
            "expert": ExpertPage(self.ctx),
            "version": VersionPage(self.ctx),
            "plugins": PluginsPage(self.ctx),
            "modes": ModesPage(self.ctx),
            "env": EnvPage(self.ctx),
            "media": MediaPage(self.ctx),
            "model": ModelPage(self.ctx),
            "market": MarketPage(self.ctx),
            "settings": SettingsPage(self.ctx),
            "about": AboutPage(self.ctx),
        }
        for _name, key in PAGES:
            self.stack.addWidget(self.pages[key])
        wv.addWidget(self.stack)
        layout.addWidget(wrap, 1)
        self.setCentralWidget(central)

        # 右上角三枚窗口按钮：浮在内容之上，不占布局、没有标题栏横条
        self._title_btns = self._build_title_buttons(central)

        self._wire_runner()
        self.statusBar().showMessage("就绪")
        self.switch("home")
        self.sync_tray()
        # 预热「节点映射」缓存：首次全量扫描约 8 秒（走子进程，不卡界面）。
        # 提前在后台跑掉，用户到「模式」页点工作流时就是缓存命中、结果秒出。
        self._warm_nodemap()

    def _warm_nodemap(self):
        """后台预热节点映射缓存（只在缓存过期时才会真扫）。"""
        root = self.ctx.root()
        if not root:
            return
        from ..core import nodemap
        from .workers import Worker
        self.ctx.spawn(Worker(lambda: nodemap.build_local_map(root)))

    # ---------- 右上角窗口按钮 / 左上角品牌区 ----------
    DRAG_H = 30          # 顶部可拖动安全区（内容顶部为按钮让出的空白，不是标题栏）

    def _build_brand(self):
        """左上角：logo（方形圆角方块 + ECHO 字标）+ 软件名 / 版本。

        logo 走 `widgets.LogoBadge(40)`（渲染 `resources/echo_logo.svg`，2026-10-10 依原图
        矢量重绘）。**注意它是方形 logo**（方块 + 里边的字标），不要只画那排字标、
        也不要用 `resources/echo_logo.png` 缩放（位图缩到 40px 会糊，用户反馈过），
        更不要自造方标（曾经画过「蓝环 + 橙点」顶替，被要求删掉）。

        用**默认的 `FRAME_OUTLINE`**：摘掉 logo 的**白色填充**、保留它自带的**那圈描边**
        （用户在 2026-10-10 的两轮反馈里定下来的 —— 先是"白色方块显得极不协调"要拿掉填充，
        接着又问"我那个logo不是周围带着一个边的吗，怎么取消了"，所以边必须留着）。
        主题也因此换成了暖骨白 + 纯白卡片 + 蓝调强调（见 `style.py`）。
        **图标位置（托盘）用 `FRAME_FULL`**，见 `core/tray.py`。
        """
        box = plain_box()        # ⛔ 别用裸 QWidget：会在侧栏上显出一块浅色方块
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(LogoBadge(40))
        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel("ComfyUI Echo")
        name.setObjectName("AppTitle")
        sub = QLabel(f"启动器 v{__version__}")
        sub.setObjectName("AppSubtitle")
        col.addWidget(name)
        col.addWidget(sub)
        h.addLayout(col)
        h.addStretch(1)
        return box

    def _build_title_buttons(self, parent):
        """右上角三枚按钮：浮层，不进布局、没有横条背景。"""
        bar = QWidget(parent)
        bar.setFixedSize(40 * 3 + 8, self.DRAG_H)
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 4, 0)
        h.setSpacing(0)

        def _btn(text, slot, name):
            b = QPushButton(text)
            b.setObjectName(name)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(40, 26)
            b.clicked.connect(slot)
            h.addWidget(b)
            return b

        self.btn_title_min = _btn("–", self.showMinimized, "TitleBtn")
        self.btn_title_max = _btn("□", self._toggle_maximized, "TitleBtn")
        self.btn_title_close = _btn("✕", self.close, "TitleClose")
        bar.raise_()
        return bar

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 浮层按钮跟着窗口右上角走（不进布局，所以要自己挪）
        bar = getattr(self, "_title_btns", None)
        if bar is not None:
            bar.move(self.width() - bar.width(), 0)
            bar.raise_()

    def _toggle_maximized(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            self.btn_title_max.setText("❐" if self.isMaximized() else "□")
        super().changeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        # 无边框窗口在 Win11 上默认是**方角**、而且**完全没有投影**。三件事按这个顺序做
        # （顺序有讲究，别调换）：
        #   ① DwmExtendFrameIntoClientArea(0,0,0,1)：把「1px 的底边」当作边框交给 DWM，
        #      DWM 才会认为这个窗口有框，从而画出**系统柔和投影**。
        #      实测：加完窗口外 3px 处变暗 Δ≈-50，向 90px 外平滑衰减到 0。
        #   ② DWMWA_BORDER_COLOR = DWMWA_COLOR_NONE(0xFFFFFFFE)：不加这条，① 会在窗口最
        #      底下留一条 ~2px 的灰色系统描边（实测 #a8a8a8，白底窗口上很显眼）。
        #   ③ DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND：Win11 圆角。
        #
        # ★绝对不要再给窗口类加 CS_DROPSHADOW（曾经这么干过）。它是**按「窗口类」**生效的，
        #   而 Qt 所有带图标的顶层窗口共用同一个类 —— 实测主窗口与 MessageBox 的
        #   GetClassNameW 都是 `Qt6112QWindowIcon`、GCL_STYLE 都是 0x20008（含 CS_DROPSHADOW）。
        #   加一次就会把那条 3px 硬边阴影**传染给每一个对话框**（用户 2026-10-10 反馈
        #   「有些小窗口带有阴影」就是这个）。对话框本身是带标题栏的普通窗口，
        #   DWM 本来就会给它系统柔和投影，不需要我们再插手。
        if not self._dwm_chrome_done and os.name == "nt" and _wt is not None:
            try:
                hwnd = _wt.HWND(int(self.winId()))
                dwm = ctypes.windll.dwmapi
                margins = _MARGINS(0, 0, 0, 1)
                dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
                for attr, val in ((34, -2),      # DWMWA_BORDER_COLOR = DWMWA_COLOR_NONE
                                  (33, 2)):      # DWMWA_WINDOW_CORNER_PREFERENCE = ROUND
                    v = ctypes.c_int(val)
                    dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v),
                                              ctypes.sizeof(v))
            except Exception:
                pass
            self._dwm_chrome_done = True

    def nativeEvent(self, eventType, message):
        """无边框窗口的原生命中测试：标题栏区域可拖动/双击最大化/贴边分屏，
        四边 6px 热区可拉伸。返回 HT* 让 Windows 接管，Qt 侧零拖动代码。"""
        if _wt is not None and eventType == "windows_generic_MSG":
            msg = _wt.MSG.from_address(int(message))
            if msg.message == 0x0084:                       # WM_NCHITTEST
                # lParam 是物理像素坐标，除以 DPR 再映射到窗口逻辑坐标
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                dpr = self.devicePixelRatioF() or 1.0
                pos = self.mapFromGlobal(QPoint(round(x / dpr), round(y / dpr)))
                w, h, m = self.width(), self.height(), 6
                if not self.isMaximized():
                    left, right = pos.x() <= m, pos.x() >= w - m
                    top, bottom = pos.y() <= m, pos.y() >= h - m
                    if top and left:
                        return True, 13                     # HTTOPLEFT
                    if top and right:
                        return True, 14                     # HTTOPRIGHT
                    if bottom and left:
                        return True, 16                     # HTBOTTOMLEFT
                    if bottom and right:
                        return True, 17                     # HTBOTTOMRIGHT
                    if left:
                        return True, 10                     # HTLEFT
                    if right:
                        return True, 11                     # HTRIGHT
                    if top:
                        return True, 12                     # HTTOP
                    if bottom:
                        return True, 15                     # HTBOTTOM
                if pos.y() <= self.DRAG_H:
                    # 按钮必须保持可点，其余（含左上角品牌区）都算可拖动区
                    if not isinstance(self.childAt(pos), QAbstractButton):
                        return True, 2                      # HTCAPTION
        return super().nativeEvent(eventType, message)

    # ---------- 侧栏 ----------
    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(178)
        v = QVBoxLayout(side)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(2)

        # 左上角：大 logo + 软件名 + 版本（去掉系统标题栏后，这里是窗口的「头」）
        v.addWidget(self._build_brand())
        v.addSpacing(10)

        self._nav_btns = {}
        for caption, keys in NAV_GROUPS:
            cap = QLabel(caption)
            cap.setObjectName("NavCaption")
            v.addWidget(cap)
            for key in keys:
                btn = QPushButton(PAGE_LABELS[key])
                btn.setCheckable(True)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setToolTip(PAGE_HINTS.get(key, ""))
                btn.clicked.connect(lambda _c, k=key: self.switch(k))
                v.addWidget(btn)
                self._nav_btns[key] = btn
            v.addSpacing(10)

        v.addStretch(1)
        # 侧栏底部**不再有「关于」按钮**：它 2026-10-10 已改成「系统」分区里的一级页面
        # （`about_page.py`），底部工具区随之取消，`#NavAux` 样式也已删除。
        # `switch()` 里那条 `key not in self.pages` 护栏保留 —— 它是防御性的，
        # 别当死代码删掉（侧栏键与页面键必须一一对应）。
        return side

    def switch(self, key):
        if key not in self.pages:      # 护栏：侧栏键与页面键一一对应，不认识的直接忽略
            return
        for k, btn in self._nav_btns.items():
            if btn.isCheckable():
                btn.setChecked(k == key)
        self.stack.setCurrentWidget(self.pages[key])
        page = self.pages[key]
        if hasattr(page, "on_show"):
            page.on_show()

    # ---------- 运行器联动 ----------
    def _wire_runner(self):
        self.ctx.runner.server_ready.connect(self._on_ready)

    def _on_ready(self, url):
        if not cfgmod.get_echo("notify_on_ready", True):
            return
        try:
            traymod.notify("ComfyUI 已就绪", url)
        except Exception:
            pass
        if self.tray is not None:
            self.tray.set_title(f"{APP_TITLE} · 运行中")

    # ---------- 托盘 ----------
    def sync_tray(self):
        """按设置启停托盘。设置页保存后会调用它。"""
        want = bool(cfgmod.get_echo("enable_tray", True))
        if want and self.tray is None:
            if not traymod.available():
                return
            self.tray = traymod.TrayController(APP_TITLE, self)
            self.tray.show_requested.connect(self._tray_show)
            self.tray.web_requested.connect(self._tray_web)
            self.tray.restart_requested.connect(self._tray_restart)
            self.tray.about_requested.connect(self._tray_about)
            self.tray.quit_requested.connect(self._tray_quit)
            self.tray.open_dir_requested.connect(self._tray_open_dir)
            self.tray.start()
        elif not want and self.tray is not None:
            self.tray.stop()
            self.tray = None

    def _tray_show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_about(self):
        """托盘的「关于」：**弹主窗口并切到「关于」页**，不再开对话框
        （用户 2026-10-10：关于的二级窗口取消，改成一级页面）。"""
        self._tray_show()
        self.switch("about")

    def _tray_web(self):
        port = int(self.ctx.launch_cfg().get("port") or 0) or 8188
        QDesktopServices.openUrl(QUrl(f"http://127.0.0.1:{port}"))

    def _tray_restart(self):
        self.ctx.runner.stop()
        self.pages["home"].on_start()

    def _tray_quit(self):
        self._quitting = True
        self.close()

    def _tray_open_dir(self, key):
        root = self.ctx.root()
        if not root:
            return
        mapping = {"plugins": "custom_nodes", "models": "models", "output": "outputs"}
        d = _paths.dirs(root).get(mapping.get(key, ""), "")
        if d and os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    # ---------- 关闭行为 ----------
    def closeEvent(self, event):
        if getattr(self, "_quitting", False) or \
                not cfgmod.get_echo("minimize_to_tray", False):
            try:
                self.ctx.runner.stop()
            except Exception:
                pass
            if self.tray is not None:
                self.tray.stop()
                self.tray = None
            event.accept()
            return
        # 最小化到托盘
        self.hide()
        if self.tray is None:
            self.sync_tray()
        event.ignore()
