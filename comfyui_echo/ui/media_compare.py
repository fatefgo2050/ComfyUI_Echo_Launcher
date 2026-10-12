"""媒体对比对话框：图片分割对比 / 视频同步播放。

**「分割对比」是什么意思**（`chk_split`，两张图时默认开）：把 A、B 叠在**同一张画布**上，
顶部那根滑条就是分界线 —— 线的左边显示 A、右边显示 B，拖动它扫过全图。
两张图往往只差局部（换采样器 / 换 LoRA 权重），并排看根本对不齐、也看不出动了哪；
叠起来看是**像素级**的。关掉它就退回并排。

⚠ 两处是 2026-10-11 修出来的（用户反馈「上下布局显示不全」）：
- 原来的「上下布局」开关是个**死开关** —— `_relayout()` 从来不读 `chk_vert`，两个画布
  一直是竖着堆的，勾不勾都一样（所以看着像"没勾却是上下"）。**已删除**，
  非分割模式固定**左右并排**（竖图上下堆必然溢出）。
- 原来非分割模式直接 `setPixmap(原图)`，而源图可能上千像素 —— 标签装不下就**被裁**
  （640×1664 的竖图塞进约 490px 高的标签只剩上半截）。现在统一走 `_fit()` 按标签尺寸等比缩放，
  并在 `resizeEvent` 里重新贴合。
  ⚠ 这一步是**必须**的：改成左右并排后标签宽约 490，同样装不下大图。

**载入尺寸按画布自适应**（`_need_size`）：源图长边取「画布较长边」，所以任何窗口尺寸下都是
**降采样**（不放大 = 不糊）。窗口拉大到超过已载入尺寸时，`resizeEvent` 会去抖重载一次更清晰的
（`_upgrade_if_needed`）。所以**别改回固定尺寸** —— 固定 1024 在 1600×1200 的窗口下画布已有
约 1156px，就又成放大了。

**分界线有两种拖法**：顶部那根 `QSlider`（细调），或者**直接在图上按住左键拖**
（`eventFilter` + `_scrub`，只在分割模式下生效，鼠标移上去会变左右分栏光标）。
换算见 `_split_from_pos`：QLabel 是按图片自身尺寸居中画的，两侧有留白，得先扣掉再算比例。

视频：两侧共用一个时间轴，`seek_frame` 同步取帧（10 fps，够看清差异）；逐帧解码尺寸封顶
`VIDEO_MAX`（每秒 20 次解码，开销随尺寸线性涨，运动对比用不着跟静态图一样锐）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QSlider, QVBoxLayout)

from ..core import media as M
from ..ui import style

#: 载入源图的尺寸**按当前画布自适应**（见 `_need_size`）：任何窗口尺寸下都保证
#: 「源图长边 ≥ 画布长边」，即永远是**降采样**（不放大 = 不糊），又不为用不到的分辨率白占内存。
#: 下面两个是夹逼上下限，防止构造期量到 320×240 这种没布局完的尺寸、或超宽屏把内存吃爆。
MIN_LOAD = 640
MAX_LOAD = 4096
#: 视频逐帧解码的**上限**：10fps × 两侧 = 每秒 20 次解码，开销随尺寸线性涨，
#: 而看运动差异用不着跟静态对比一样锐 —— 超过这个就封顶。
VIDEO_MAX = 1024
#: 拖窗口时不要每帧都重载源图，停手 RESIZE_RELOAD_MS 之后再决定要不要升清晰度。
RESIZE_RELOAD_MS = 300
FPS = 10


def _pixmap(data: bytes) -> QPixmap:
    pm = QPixmap()
    if data:
        pm.loadFromData(data)
    return pm


class CompareDialog(QDialog):
    def __init__(self, parent, a, b):
        super().__init__(parent)
        self.a, self.b = a, b
        # 载入/布局状态：必须先于任何 resizeEvent 存在（下面 resize() 就可能触发）
        self.pm_a = QPixmap()
        self.pm_b = QPixmap()
        self._loaded = False        # 首次 showEvent 才载入（那时画布尺寸才量得准）
        self._loaded_at = 0         # 上一次是按下多大的源图载的
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(RESIZE_RELOAD_MS)
        self._reload_timer.timeout.connect(self._upgrade_if_needed)

        self.setWindowTitle("对比")
        self.resize(1000, 720)
        self.playing = False
        self._t = 0.0
        self._dur = 0.0

        v = QVBoxLayout(self)

        bar = QHBoxLayout()
        bar.addWidget(QLabel(f"A: {a.name}    B: {b.name}"))
        self.chk_split = QCheckBox("分割对比")
        self.chk_split.setChecked(a.kind == "image" and b.kind == "image")
        self.chk_split.toggled.connect(self._relayout)
        bar.addWidget(self.chk_split)
        # ⚠ 这里原来有个「上下布局」开关 —— 它是**死开关**（`_relayout` 从来不读它，
        #   两个画布一直是竖着堆的）。已按用户要求删除，非分割模式固定左右并排。

        both_video = a.kind == "video" and b.kind == "video"
        if both_video:
            self.btn_play = QPushButton("播放")
            self.btn_play.setObjectName("Primary")
            self.btn_play.clicked.connect(self.toggle_play)
            bar.addWidget(self.btn_play)
            self.chk_split.setChecked(False)
            self.chk_split.setEnabled(False)
        self.split = QSlider(Qt.Horizontal)
        self.split.setRange(0, 100)
        self.split.setValue(50)
        self.split.setMaximumWidth(260)
        self.split.valueChanged.connect(lambda _v: self._paint())
        bar.addWidget(self.split, 1)
        v.addLayout(bar)

        self.canvas_a = QLabel()
        self.canvas_b = QLabel()
        for c in (self.canvas_a, self.canvas_b):
            c.setAlignment(Qt.AlignCenter)
            c.setMinimumSize(320, 240)
            # ⚠ sizePolicy 必须是 Ignored：QLabel 的 sizeHint 会跟着刚塞进去的 pixmap 变，
            #   和 resizeEvent 里的「重新贴合」互相反馈会抖 / 递归。Ignored 之后画布尺寸
            #   只由外层布局决定，图片再大也撑不动对话框。
            c.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        # 非分割模式 = **左右并排**（竖图上下堆必然溢出，见文件头）
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(self.canvas_a, 1)
        row.addWidget(self.canvas_b, 1)
        v.addLayout(row, 1)
        # 分割模式下可以直接在画布上按住左键拖分界线（见 eventFilter / _scrub）
        self.canvas_a.installEventFilter(self)

        # ⚠ 这里**不载入**：构造期布局还没跑，画布尺寸还是最小尺寸（320×240），
        #   按它载进来的源图会偏小、真显示时就放大发糊。真正的载入放在 `showEvent`。
        self._relayout()

        if both_video:
            self._dur = min(float(M.probe(a.path).get("duration") or 0),
                            float(M.probe(b.path).get("duration") or 0))
            self.timer = QTimer(self)
            self.timer.setInterval(int(1000 / FPS))
            self.timer.timeout.connect(self._tick)

    # ---------- 载入 ----------
    def _need_size(self) -> int:
        """当前画布需要多大的源图：取画布的**较长边**（夹在 MIN_LOAD / MAX_LOAD 之间）。

        判据只有一条：**源图长边 ≥ 画布长边** 就是降采样（清晰）；小于就是放大（糊）。
        """
        w = max(self.canvas_a.width(), self.canvas_b.width())
        h = max(self.canvas_a.height(), self.canvas_b.height())
        return min(MAX_LOAD, max(MIN_LOAD, w, h))

    def _load(self):
        """按 `_need_size()` 载入（图片走全图，视频取第 0 帧）。"""
        size = self._need_size()
        self._loaded_at = size
        vsize = min(size, VIDEO_MAX)
        if self.a.kind == "image":
            self.pm_a = _pixmap(M.full_pixmap_bytes(self.a, max_size=size))
        else:
            self.pm_a = _pixmap(M.seek_pixmap_bytes(self.a, 0.0, max_size=vsize))
        if self.b.kind == "image":
            self.pm_b = _pixmap(M.full_pixmap_bytes(self.b, max_size=size))
        else:
            self.pm_b = _pixmap(M.seek_pixmap_bytes(self.b, 0.0, max_size=vsize))

    def showEvent(self, event):
        """首次真正显示时才载入 —— 这时布局已经跑过，画布的最终尺寸才量得准。"""
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self._load()
            self._relayout()

    def _upgrade_if_needed(self):
        """画布变大到超过已载入的源图尺寸 -> 重载一次更清晰的。

        ⚠ 不重载就是「放大」= 糊：窗口从 1000×720 拉到 1600×1200，画布从 676 涨到约 1156，
          按 676 载的源图就得放大 1.7 倍。
        """
        if not self._loaded:
            return
        if self._need_size() > self._loaded_at:
            self._load()
            self._relayout()

    def _relayout(self):
        use_split = self.chk_split.isChecked()
        self.canvas_b.setVisible(not use_split)
        self.split.setVisible(use_split)
        # 分割模式下**在图上直接拖**分界线（鼠标移上去就变左右分栏光标，给个提示）
        self.canvas_a.setCursor(Qt.SplitHCursor if use_split else Qt.ArrowCursor)
        self.canvas_a.setToolTip("按住左键左右拖动 = 直接拖分界线（等同上方滑条）"
                                 if use_split else "")
        if not use_split:
            self._fit(self.canvas_a, self.pm_a)
            self._fit(self.canvas_b, self.pm_b)
        self._paint()

    # ---------- 在图上拖分界线 ----------
    def eventFilter(self, obj, event):          # noqa: N802 (Qt 命名)
        """把画布上的左键拖动接成分界线（只在分割模式下生效）。"""
        if obj is self.canvas_a and self.chk_split.isChecked():
            t = event.type()
            if t == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._scrub(event.position().x())
                return True
            if t == QEvent.MouseMove and (event.buttons() & Qt.LeftButton):
                self._scrub(event.position().x())
                return True
        return super().eventFilter(obj, event)

    def _scrub(self, x: float):
        """把画布上的横坐标换成滑条百分比并写回去。"""
        v = self._split_from_pos(x)
        if v is not None:
            self.split.setValue(v)

    def _split_from_pos(self, x: float):
        """画布横坐标 -> 滑条百分比（0–100）；没图时返回 None。

        ⚠ 不能直接拿 `x / 画布宽`：QLabel 是**按图片自己的尺寸居中画的**（两边有留白），
        得先扣掉左边那段留白再换算，否则靠近边缘时对不上手。
        这里的 pixmap 本来就等于画布缩放后的尺寸（见 `_paint`），所以直接用它的宽度。
        """
        pm = self.canvas_a.pixmap()
        if pm is None or pm.isNull() or pm.width() <= 0:
            return None
        left = (self.canvas_a.width() - pm.width()) / 2.0
        frac = (x - left) / pm.width()
        return max(0, min(100, round(frac * 100)))

    def _fit(self, label, pm):
        """把源图**按标签尺寸等比缩放**之后再放进去。

        ⚠ 别直接 `setPixmap(pm)`：源图是「按画布尺寸载入」的（可能上千像素），
        标签装不下就**被裁掉** —— 640×1664 的竖图塞进约 490px 高的标签只剩上半截
        （「打开默认布局显示不全」的直接原因）。
        """
        if pm is None or pm.isNull():
            label.setPixmap(QPixmap())
            return
        label.setPixmap(pm.scaled(label.size(), Qt.KeepAspectRatio,
                                  Qt.SmoothTransformation))

    def resizeEvent(self, event):
        """窗口尺寸变了：① 重贴合；② 停手后决定要不要换更清晰的源图。"""
        super().resizeEvent(event)
        # 构造期 resize(1000, 720) 可能早于画布创建，这里必须能容忍
        if getattr(self, "canvas_a", None) is None:
            return
        self._relayout()                 # 先用手上这张重贴合，不闪
        self._reload_timer.start()       # 去抖：拖窗口时别每帧都重载源图

    def _paint(self):
        if not self.chk_split.isChecked():
            return
        if self.pm_a.isNull() or self.pm_b.isNull():
            return
        w = max(1, self.pm_a.width())
        h = max(1, min(self.pm_a.height(), self.pm_b.height()))
        canvas = QPixmap(w, h)
        canvas.fill(QColor(style.SURFACE))
        p = QPainter(canvas)
        p.drawPixmap(0, 0, self.pm_a.scaled(w, h, Qt.KeepAspectRatio,
                                            Qt.SmoothTransformation))
        x = int(w * self.split.value() / 100)
        if x < w:
            p.drawPixmap(x, 0, self.pm_b.scaled(w, h, Qt.KeepAspectRatio,
                                                Qt.SmoothTransformation),
                         x, 0, w - x, h)
        pen = QPen(QColor(style.ACCENT))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawLine(x, 0, x, h)
        p.end()
        self.canvas_a.setPixmap(canvas.scaled(self.canvas_a.size(),
                                              Qt.KeepAspectRatio,
                                              Qt.SmoothTransformation))

    # ---------- 视频同步 ----------
    def toggle_play(self):
        self.playing = not self.playing
        self.btn_play.setText("停止" if self.playing else "播放")
        if self.playing:
            self._t = 0.0
            self.timer.start()
        else:
            self.timer.stop()

    def _tick(self):
        self._t += 1.0 / FPS
        if self._dur and self._t > self._dur:
            self.playing = False
            self.timer.stop()
            self.btn_play.setText("播放")
            return
        vsize = min(self._loaded_at or MIN_LOAD, VIDEO_MAX)
        pa = _pixmap(M.seek_pixmap_bytes(self.a, self._t, max_size=vsize))
        pb = _pixmap(M.seek_pixmap_bytes(self.b, self._t, max_size=vsize))
        if not pa.isNull():
            self.canvas_a.setPixmap(pa.scaled(self.canvas_a.size(),
                                              Qt.KeepAspectRatio,
                                              Qt.SmoothTransformation))
        if not pb.isNull():
            self.canvas_b.setPixmap(pb.scaled(self.canvas_b.size(),
                                              Qt.KeepAspectRatio,
                                              Qt.SmoothTransformation))

    def closeEvent(self, event):
        try:
            if getattr(self, "timer", None):
                self.timer.stop()
        except Exception:
            pass
        super().closeEvent(event)
