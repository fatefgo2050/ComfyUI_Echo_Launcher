"""跨页面复用的小部件工厂。"""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..core import _bridge as B
from ..core import brand

_LOGO_CACHE = {}

# 三种呈现形态（见 core/brand.py 的说明）
FRAME_FULL = "full"        # 白色圆角填充 + 描边 + 字标
FRAME_OUTLINE = "outline"  # 只留描边 + 字标（默认）
FRAME_NONE = "none"        # 只剩字标


def _read_logo_svg() -> str:
    with open(os.path.join(B.RESOURCE_DIR, brand.LOGO_SVG), "r", encoding="utf-8") as f:
        return f.read()


def _strip(text: str, begin: str, end: str) -> str:
    a = text.find(begin)
    b = text.find(end)
    if a < 0 or b <= a:
        return text
    return text[:a] + text[b + len(end):]


def logo_renderer(frame: str = FRAME_OUTLINE):
    """logo SVG 渲染器（进程内缓存，按形态各存一个；读不到返回 None）。

    三个形态（`core/brand.py` 有完整说明）：
      `FRAME_FULL`    —— 白色圆角填充 + 描边 + 字标。**图标场合**用（托盘 / exe）。
      `FRAME_OUTLINE` —— 摘掉白色填充、**保留那圈描边** + 字标。**内嵌场合默认**：
                         白色填充压在浅色侧栏上是一块突兀的方块，但那圈边是 logo
                         自带的、要去掉的是填充 —— 这两件事必须分开（2026-10-10 踩过：
                         一开始整块摘掉，用户问「周围那个边怎么取消了」）。
      `FRAME_NONE`    —— 连描边也摘掉，只剩字标。
    """
    key = frame
    if key not in _LOGO_CACHE:
        r = None
        try:
            from PySide6.QtCore import QByteArray
            from PySide6.QtSvg import QSvgRenderer
            text = _read_logo_svg()
            if frame != FRAME_FULL:
                text = _strip(text, brand.TILE_BG_BEGIN, brand.TILE_BG_END)
            if frame == FRAME_NONE:
                text = _strip(text, brand.TILE_BORDER_BEGIN, brand.TILE_BORDER_END)
            cand = QSvgRenderer(QByteArray(text.encode("utf-8")))
            if cand.isValid():
                cand.setAspectRatioMode(Qt.KeepAspectRatio)
                r = cand
        except Exception:
            r = None
        _LOGO_CACHE[key] = r
    return _LOGO_CACHE[key]


class LogoBadge(QWidget):
    """Echo logo：渲染 `resources/echo_logo.svg`。

    logo 是**正方形**的 —— 圆角方块里放着 ECHO 字标（见 `core/brand.py` 的说明）。
    **别把它当成"就一排字标"**：那样方块的容器就没了（用户 2026-10-10 纠正过）。
    也**别自造方标**（曾经用「蓝环 + 橙点」顶替过，被要求删掉）。

    `frame` 决定方块那部分怎么呈现，默认 `FRAME_OUTLINE`（只留描边、不要白色填充）：
      - `FRAME_OUTLINE`：内嵌在有色背景上时用（左上角品牌区 / 关于页）
      - `FRAME_FULL`   ：图标场合用（托盘）
      - `FRAME_NONE`   ：只要字标
    ⚠ **不要把「填充」和「描边」一起摘掉** —— 那圈边是 logo 自带的（2026-10-10 踩过）。

    **为什么必须用矢量而不是 `echo_logo.png`**：那张位图字标只占 210×66，缩到 40px 时
    字标只剩 33×10px 且糊成一团（用户反馈过）。矢量版按原图亚像素量测重绘，任何尺寸都清晰。
    """

    def __init__(self, size: int = 40, frame: str = FRAME_OUTLINE, parent=None):
        super().__init__(parent)
        self._frame = frame
        # 自绘控件同样会被全局 `QWidget { background: <画布色> }` 刷底 —— 用同一个
        # 「不画背景」标记（见 plain_box 的说明）。
        self.setObjectName("PlainBox")
        self.setFixedSize(size, size)

    def paintEvent(self, event):        # noqa: N802 (Qt 命名)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = logo_renderer(self._frame)
        if r is None:                   # 资源缺失时的兜底（不该发生）
            p.setPen(QColor(brand.BLUE))
            p.drawText(self.rect(), Qt.AlignCenter, "ECHO")
        else:
            r.render(p, QRectF(0.0, 0.0, float(self.width()), float(self.height())))
        p.end()


class TableEmptyHint(QLabel):
    """盖在表格视口中央的**空态提示**（表格有行时自动隐藏）。

    为什么需要它：`QTableWidget` 没有 `placeholderText`，未扫描 / 无结果时就是一大片空白，
    看不出"是没数据还是坏了"。它贴一句居中的说明（`QLabel#EmptyHint` 样式）。
    ⚠ 用 `WA_TransparentForMouseEvents`：不能挡住表格的滚动 / 选择。
    ⚠ 每次改过行数后要调 `refresh()`（本类不监听 model 变化，只在视口 resize 时重算）。
    挂载：`hint = widgets.TableEmptyHint(table, "…")`，填表后 `hint.refresh()`。
    """

    def __init__(self, table, text: str):
        super().__init__(table.viewport())
        self._table = table
        self.setObjectName("EmptyHint")
        self.setText(text)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        table.viewport().installEventFilter(self)
        self._sync()

    def eventFilter(self, obj, ev):     # noqa: N802 (Qt 命名)
        if obj is self._table.viewport() and ev.type() == QEvent.Type.Resize:
            self._sync()
        return super().eventFilter(obj, ev)

    def refresh(self):
        """填表 / 清表之后调一次：按当前行数决定显隐。"""
        self._sync()

    def _sync(self):
        self.setGeometry(self._table.viewport().rect())
        self.setVisible(self._table.rowCount() == 0)


def plain_box() -> QWidget:
    """一个**不画背景**的布局容器（把若干控件攒成一组时套的壳）。

    ⚠ **为什么不能直接用裸 `QWidget()`**：全局 `QWidget { background: <画布色> }`
    会给**每一个**裸 QWidget 刷上画布色 —— 落在白色卡片上就是一条明显的色带，
    落在侧栏上就是一块比侧栏浅的方块。2026-10-10 为此返工过三次：
      ① QTabWidget 的内容页（页签栏下压了一条色带）
      ② 品牌区那个容器（logo 底下像压了块白板）
      ③ 注意力卡「档位」行那个容器（检测CK 右边拖出一条色带）
    凡是「只为布局而存在、不该自己上色」的容器，一律用它。
    颜色由 `QWidget#PlainBox { background: transparent; }` 接管（style.py）。
    """
    w = QWidget()
    w.setObjectName("PlainBox")
    return w


def card(title: str = "", action=None):
    """卡片容器（暖米黄主题的 `QFrame#Card`）。

    `action` 传一个按钮时会挂在标题行右端（如「复制日志」）。
    返回 `(frame, 内容用 layout)`——调用方往 layout 里塞东西，再把 frame 加进页面。
    """
    frame = QFrame()
    frame.setObjectName("Card")
    v = QVBoxLayout(frame)
    v.setContentsMargins(14, 10, 14, 12)
    v.setSpacing(8)
    if title:
        lab = QLabel(title)
        lab.setObjectName("CardTitle")
        if action is None:
            v.addWidget(lab)
        else:
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(6)
            head.addWidget(lab)
            head.addStretch(1)
            head.addWidget(action)
            v.addLayout(head)
    return frame, v


def divider():
    """1px 结构分隔线（`QFrame#Divider`）。"""
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.NoFrame)
    return line


def page_header(parent, title: str, sub: str = "", center: bool = False):
    """页头：一行「页面名 + 一句说明」，右端留白。

    给**没有卡片结构**的页面用（直接铺工具条 + 表格的那种：插件 / 市场 / 模型 /
    媒体 / 内核）——这些页面原来一上来就是按钮行，没有标题读法。页头只占一行
    （约 20px），不会跟下面的表格抢高度。返回 layout，调用方一般不用再动。

    `center=True` **换成两行**：标题一行、说明一行，各自水平居中
    （「关于」页整页居中，见 `about_page.py`）。为什么不是把标题和说明拼成一行居中：
    那样两行的**视觉重心**才是对的 —— 拼一行居中时"关于"两个字会偏在中心左侧。
    ⚠ 默认是左对齐版式，**别随手改成居中**：全项目只有「关于」一页居中。
    """
    if center:
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        lab = QLabel(title)
        lab.setObjectName("PageTitle")
        lab.setAlignment(Qt.AlignHCenter)
        col.addWidget(lab)
        if sub:
            s = QLabel(sub)
            s.setObjectName("PageSub")
            s.setAlignment(Qt.AlignHCenter)
            col.addWidget(s)
        parent.addLayout(col)
        return col

    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(10)
    lab = QLabel(title)
    lab.setObjectName("PageTitle")
    h.addWidget(lab)
    if sub:
        s = QLabel(sub)
        s.setObjectName("PageSub")
        h.addWidget(s)
    h.addStretch(1)
    parent.addLayout(h)
    return h
