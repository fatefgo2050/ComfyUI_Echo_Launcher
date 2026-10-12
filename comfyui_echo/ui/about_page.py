"""「关于」页 —— 卡片栈版式（v10：正文精简为「软件介绍 + 版本更新」两节）。

## 版式（沿 v9，模板 = `settings_page.py`）

内容从顶部开始排、卡片贴左边界，末尾 `addStretch(1)` 把余量留在页面底部。**全页一律左对齐。**

**这一页没有 `page_header`**：页面顶部**直接就是品牌块**（`_brand_row()`，钉在滚动区外常显）。
品牌块本身就是这一页的身份（理由见 `交付文档.md` 的「关于」页一节）：软件名（`#AboutTitle` 18px）
是页面上最大的字，「关于」标题已被侧栏高亮承担，不再重复。

## 2 节（2026-10-12 用户要求精简）

    品牌块（页头，非卡片）  LogoBadge(72) + 名称 + 版本徽标 + 副标题
    软件介绍          1 段定位 + 若干条「● 能力」
    版本更新          当前版本定义行 + 「本次更新」条目 + 升级指引（整页唯一的动作按钮）

历史：v1 撑满大卡 → v2 居中悬浮面板 → v3 贴左网格 → v4 整页居中 → v5 单一居中大框 →
v6/v7 手绘稿「大框 + 多节、正文居中」→ v8 照搬旧窗口 → **v9（卡片栈，作废「居中特例」）** →
v9.1 去掉 `page_header`（「品牌即页头」）→ **v10（现在，正文精简为两节）**。
⚠ 不要把版式改回居中。

## 关键实现（踩过的坑，别改）

- **小节标题一律用 `widgets.card(title=...)` 生成的 `#CardTitle`**，不要再自己造标题 QLabel。
- **定义行用 `#AboutKey` + `#AboutVal`**（名称列 `setFixedWidth(KEY_W)`，值列吃余量）。
- **条目标记「●」走 `#Bullet`**（`QLabel#Bullet` 必须在 `style.QSS` 里有规则，`ui_smoke` 守着）。
- **卡片不设固定宽**：宽度由 `QScrollArea(widgetResizable=True)` 传给卡片、再传给段落，
  `wordWrap` 的 QLabel 才拿得到真实宽度。
  ⚠ `wordWrap` 的 QLabel，`sizeHint()` 宽度是**废数** → 绝不能用布局去算宽度。
- **容器一律 `plain_box()`**（否则被全局 `QWidget{background}` 刷出色带）；
  滚动区用 `QScrollArea#PlainScroll`（横向滚动条恒关）。
- 正文一律左对齐；文案里**不许出现其它启动器 / 工具链的名字**（`ui_smoke` [10] 守着）。
- **增删节只改 `SECTIONS` 这张表**，版式不用动。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from comfyui_echo import APP_TITLE, __version__
from ..core import selfupdate as SU
from . import style
from .dialogs import MessageBox as QMessageBox
from .widgets import LogoBadge, card, plain_box

BRAND_LOGO = 72         # 品牌块 logo 边长（原窗口 84；用户要求「适当缩小」，取 72）
TAGLINE = "本地 ComfyUI 管理面板"
KEY_W = 84              # 定义行「名称」列定宽（4 个汉字 @12px ≈ 48px，留余量）
DOT_W = 10              # 「●」标记列宽
CARD_GAP = 12           # 卡片之间的间距

INTRO = [
    "纯本地运行的 ComfyUI 管理面板：在一台机器上管理多套 ComfyUI 环境，"
    "把启动、插件、工作流、模型、媒体与内核版本这些日常运维收进一个窗口。",
]

#: 软件功能（精简到最常打交道的几类；更细的说明见各页面内的提示）
CAPABILITIES = [
    "多路径管理：多套 ComfyUI 根目录与各自的 Python 解释器",
    "模式与插件启停：按工作流切换插件集，`.disabled` 就地重命名，不改 ComfyUI 源码",
    "启动与停止：端口占用清理、子进程树终止、控制台实时输出",
    "插件市场与内核版本：`GitHub` 直连，安装 / 检查更新 / 切换提交",
    "模型与媒体：模型查重与 Civitai 反查、缩略图浏览、图片分割对比",
    "环境救援：插件目录 + 依赖清单双备份，一键还原",
]

#: 版本更新记录：(版本号, [面向用户的条目...])，**新的版本在最前**。
#: 渲染时：版本号 == `__version__` 的那条标成「本次更新：」，其余标成「vX.Y.Z 更新：」。
CHANGELOG = [
    ("v0.1.0", [
        "专家模式：注意力设置移到右栏，两栏高度更均衡；顶部提示改为警示框，「恢复默认」按钮放大",
        "空状态引导：控制台 / 输出日志 / 媒体预览，以及插件、模型、市场三张空表都给出操作提示",
        "模式页：右栏按钮按用途重排（保存 / 应用在上，新建 / 重命名 / 删除在下），三栏底部工具条统一",
        "配色：次要主按钮（保存模式 / 恢复默认）统一成墨色，与主操作一致",
    ]),
]

UPDATE_TEXT = ("升级走 `GitHub Releases`：在「设置」页填写仓库 owner/name 后，"
               "即可检查并安装新版本。")


def _esc(text: str) -> str:
    """HTML 转义（Qt 富文本用，只认这三个字符）。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _rich(text: str) -> str:
    """转义 + 把 `` `行内代码` `` 变成着色 span（正文里只有这一种行内标记）。

    颜色取自 `style` 的色板常量（规范 Pale Blue 的底 + 文字色），别硬编码十六进制。
    """
    out = _esc(text)
    while "`" in out:
        out = out.replace("`", "<span style='color:%s; background-color:%s;'>"
                               % (style.ACCENT, style.ACCENT_SOFT), 1)
        if "`" in out:
            out = out.replace("`", "</span>", 1)
    return out


def _para(text: str, object_name: str = "AboutText") -> QLabel:
    """正文段落（**左对齐**）。含行内代码的走富文本。"""
    lab = QLabel()
    lab.setObjectName(object_name)
    lab.setWordWrap(True)
    if "`" in text:
        lab.setTextFormat(Qt.RichText)
        lab.setText(_rich(text))
    else:
        lab.setText(text)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lab


def _bullet(parent, text: str):
    """「● + 文字」条目。两列布局 → **悬挂缩进**，换行后仍与文字左缘对齐。"""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    dot = QLabel("●")
    dot.setObjectName("Bullet")
    dot.setFixedWidth(DOT_W)
    dot.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
    row.addWidget(dot)
    row.addWidget(_para(text), 1)
    parent.addLayout(row)


def _defrow(parent, name: str, value: str):
    """定义行：名称列定宽（`#AboutKey`）+ 值列吃余量（`#AboutVal`，可换行）。"""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)
    key = QLabel(name)
    key.setObjectName("AboutKey")
    key.setFixedWidth(KEY_W)
    key.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    row.addWidget(key)
    row.addWidget(_para(value, "AboutVal"), 1)
    parent.addLayout(row)


# --------------------------------------------------------------------------- #
# 各节的正文构造器：签名都是 (layout) -> None
def _sec_intro(lay):
    for t in INTRO:
        lay.addWidget(_para(t))
    for t in CAPABILITIES:
        _bullet(lay, t)


def _sec_update(lay):
    """「版本更新」：当前版本 + 本次更新条目 + 升级指引。

    整页**唯一的动作按钮**挂在这里（一屏最多一个实心主操作，这一页没有主操作，所以用 `#Ghost`）。
    """
    _defrow(lay, "当前版本", f"v{__version__}")
    for ver, items in CHANGELOG:
        head = QLabel("本次更新：" if ver == f"v{__version__}" else f"{ver} 更新：")
        head.setObjectName("AboutNote")
        lay.addWidget(head)
        for t in items:
            _bullet(lay, t)

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)
    row.addWidget(_para(UPDATE_TEXT), 1)
    btn = QPushButton("打开 Releases")
    btn.setObjectName("Ghost")
    btn.setToolTip("在浏览器里打开自更新仓库的 Releases 页")
    btn.clicked.connect(_open_releases)
    row.addWidget(btn, 0, Qt.AlignTop)
    lay.addLayout(row)


#: (节标题, 正文构造器)。**增删节只改这张表**，版式不用动。
SECTIONS = [
    ("软件介绍", _sec_intro),
    ("版本更新", _sec_update),
]


def _brand_row() -> QWidget:
    """品牌块 —— **本页的页头**（不是卡片）：logo + 名称 + [版本徽标] + 副标题，左对齐。

    它钉在滚动区**外面**，滚到哪一屏都在；这一页因此不需要 `widgets.page_header()`
    —— 「关于」这个标题由它承担（见文件头）。"""
    box = plain_box()
    h = QHBoxLayout(box)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(14)
    h.addWidget(LogoBadge(BRAND_LOGO), 0, Qt.AlignTop)

    col = QVBoxLayout()
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(5)
    # ⚠ APP_TITLE 本身已含「启动器」，别再加一遍
    name = QLabel(APP_TITLE)
    name.setObjectName("AboutTitle")
    name.setTextInteractionFlags(Qt.TextSelectableByMouse)
    col.addWidget(name)

    line = QHBoxLayout()
    line.setSpacing(8)
    ver = QLabel(f"v{__version__}")
    ver.setObjectName("Pill")
    line.addWidget(ver, 0, Qt.AlignVCenter)
    tag = QLabel(TAGLINE)
    tag.setObjectName("AboutNote")
    line.addWidget(tag, 0, Qt.AlignVCenter)
    line.addStretch(1)
    col.addLayout(line)
    col.addStretch(1)
    h.addLayout(col, 1)
    return box


def _open_releases():
    """打开自更新仓库的 Releases 页（`SU.repo()` 没配就提示去「设置」页填）。"""
    repo = SU.repo()
    if not repo:
        QMessageBox.information(None, "提示",
                                "还没配置自更新仓库，可在「设置」页填写 owner/name。")
        return
    QDesktopServices.openUrl(QUrl(f"https://github.com/{repo}/releases"))


class AboutPage(QWidget):
    """「关于」页。版式 = 卡片栈（见文件头）。"""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        # 页头 = 品牌块（**不是卡片，也没有单独的「关于」标题行**，见文件头）。
        # 钉在滚动区外，滚到哪一屏都在。
        v.addWidget(_brand_row())

        # 正文：两节，最小窗口（1100×700）下仍可能超过视口 → 允许纵向滚动。
        scroll = QScrollArea()
        scroll.setObjectName("PlainScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        inner = plain_box()         # ⛔ 别用裸 QWidget：会被全局底色刷出色带
        lv = QVBoxLayout(inner)
        lv.setContentsMargins(0, 0, 8, 0)       # 右侧留出滚动条的位置
        lv.setSpacing(CARD_GAP)

        for title, body in SECTIONS:
            frame, lay = card(title)
            body(lay)
            lv.addWidget(frame)
        lv.addStretch(1)                        # 余量留在页面底部（模板规矩）

        scroll.setWidget(inner)
        v.addWidget(scroll, 1)
