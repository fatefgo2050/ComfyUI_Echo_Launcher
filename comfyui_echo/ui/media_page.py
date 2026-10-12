"""媒体浏览器页：分组 / 标签 / 拖拽导入 / 缩略图 / 参数 / 对比。

后端全部走 Echo 的 `media/*`，新增的标签与导入逻辑在 `core/media.py`：
- 分组：默认 / 按日期 / 按类型 / 按目录
- 标签：保存在 `data/media_tags.json`，不污染输出文件元数据
- 拖拽导入：拖入文件或目录，默认复制到当前输出目录；同名自动加后缀，不覆盖
"""
from __future__ import annotations

import os

from PySide6.QtCore import (QSize, Qt, QThread, QTimer, QUrl,
                            Signal)
from PySide6.QtGui import (QDesktopServices, QDragEnterEvent, QDropEvent,
                           QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QSizePolicy, QSlider,
                               QSplitter, QStyledItemDelegate, QVBoxLayout,
                               QWidget, QListView)

from .dialogs import MessageBox as QMessageBox

from ..core import media as M
from ..core import paths as _paths
from .media_compare import CompareDialog
from .widgets import page_header
from .workers import Worker

THUMB = 150
#: 分组标题占用的一整行高度（跨行后只需单行高度，见 `_render` 里的说明）。
GROUP_H = 28
#: 拖拽导入的说明。原来是一整行提示文字压在工具行下面，2026-10-11 改成「说明」按钮：
#: 悬停 tooltip 显示原文、点击弹对话框 —— 省下一整行高度还给缩略图网格。
DROP_HINT = ("也可以把图片 / 视频 / 目录直接拖到左侧网格导入。\n\n"
             "默认是复制：原文件不动，副本进输出目录；遇到同名文件不覆盖。")

#: 两个输入框的宽度上限。三种排法都试过之后定在这里：不设上限 → 为了「占满」会被拉成
#: 868px 的巨框（用户：「占满了也不太适合」）；太窄 → 右边又空一片（用户：「太随意」）。
#: 420 够放约 60 字符的路径、220 够放约 25 字符的关键词 —— 正好"够用又不过长"。
DIR_EDIT_MAX = 420
KW_EDIT_MAX = 220

#: 搜索框的「边打边过滤」去抖时长（毫秒）。和市场页 `market_page.SEARCH_DEBOUNCE_MS` 对齐 ——
#: 两页行为不一致最容易被当成 bug。⚠ 必须去抖：一次输入触发一次完整 `refresh()`（含重扫目录）。
SEARCH_DEBOUNCE_MS = 200
MAX_RENDER = 300
ROLE_MEDIA = Qt.UserRole + 1
ROLE_GROUP = Qt.UserRole + 2


class DropListWidget(QListWidget):
    """接收文件/目录拖拽，交给 MediaPage 处理。"""

    paths_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


def _kw_hit(item, tags, kw: str) -> bool:
    """关键词是否命中：**文件名 / 标签 / positive 提示词**，任一命中就算（都按小写子串比）。

    ⚠ 标签一定要查（2026-10-11 补）：以前只查文件名和 positive，用户打自己存的标签名
    一条都搜不出来 —— 而标签是这一页唯一"自己填进去"的信息，搜不到最反直觉。
    用**子串**而不是相等，是为了让 `真实` 也能命中 `#真实`（存标签时带了井号）。
    ⚠ 顺序按"便宜的在前"：`load_meta()` 要解 PNG 文本块，最贵，所以放最后。
    """
    if kw in item.name.lower():
        return True
    if any(kw in str(t).lower() for t in (tags or [])):
        return True
    return kw in str(M.load_meta(item).get("positive", "")).lower()


class MediaGridDelegate(QStyledItemDelegate):
    """媒体网格的**尺寸策略**（只改 sizeHint，绘制仍交给默认实现）。

    为什么要它：分组标题必须**独占一整行**，否则它就只占一个缩略图格子 ——
    文字被挤在那 `THUMB` 宽的格子里折成好几行，看起来就是「图片的位置显示了一坨文字」
    （2026-10-11 用户反馈）。

    ⚠ **`QListView` 没有「跨列」这种 API**：`setFirstColumnSpanned` 在 `QTreeView` 上、
    `setSpan` 在 `QTableView` 上，ListView 两个都没有（当初写错这个，直接把 `_render`
    抛异常、整页渲染不出来）。IconMode 可行的做法是：
    **不设 `gridSize`**，让布局改用每个 item 的 sizeHint —— 标题返回「视口宽 × 单行高」，
    宽度不小于视口就自然独占一行；缩略图仍返回固定的格子尺寸，网格照样均匀。
    所以这里把原来 `setGridSize(...)` 的那对数字搬进了 sizeHint。
    """

    GRID = QSize(THUMB + 16, THUMB + 48)        # 缩略图格子（原 setGridSize 的值）

    def sizeHint(self, option, index):          # noqa: N802 (Qt 命名)
        if index.data(ROLE_GROUP):
            view = option.widget
            w = view.viewport().width() if view is not None else THUMB * 4
            return QSize(max(w, self.GRID.width()), GROUP_H)
        return QSize(self.GRID)


class ThumbWorker(QThread):
    ready = Signal(int, bytes)        # 可见媒体序号, PNG 字节
    progress = Signal(int, int)
    finished_all = Signal()

    def __init__(self, items, size=THUMB, parent=None):
        super().__init__(parent)
        self.items = list(items)
        self.size = size
        self._stop = False

    def run(self):
        total = len(self.items)
        for i, item in enumerate(self.items):
            if self._stop:
                break
            try:
                data = M.thumb_pixmap_bytes(item, self.size)
            except Exception:
                data = b""
            if data:
                self.ready.emit(i, data)
            self.progress.emit(i + 1, total)
        self.finished_all.emit()

    def stop(self):
        self._stop = True


def _pixmap(data: bytes) -> QPixmap:
    pm = QPixmap()
    if data:
        pm.loadFromData(data)
    return pm


#: 右侧预览源图的尺寸区间。实际按预览区的**较长边**算（见 `_preview_size`），
#: 保证「源 ≥ 显示」= 降采样（清晰）；下限免得面板还小的时候载得太糊，上限免得白解码一张巨图。
PREVIEW_MIN = 480
PREVIEW_MAX = 1024


class FitLabel(QLabel):
    """按**自身尺寸等比缩放**的图片标签。

    ⚠ 别直接 `setPixmap(原图)`，也别 `scaledToHeight(固定值)`：QLabel 自己**不缩放**，
    图比标签高就被**裁掉** —— 右栏预览原来写死 `scaledToHeight(300)`，而预览区高只有约 208px，
    于是上下各切一截（「右边的图片显示不全」，2026-10-11 用户反馈）。
    ⚠ `sizePolicy` 必须是 Ignored：否则标签的 sizeHint 会跟着刚塞进去的图变，
    和 `resizeEvent` 里的重贴合**互相反馈**（右栏宽度会跳）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._src = QPixmap()
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

    def set_source(self, pm):
        """存入原图并立刻贴合当前尺寸（`pm` 为空则清掉）。"""
        self._src = pm if pm is not None else QPixmap()
        self._refit()

    def source(self) -> QPixmap:
        return self._src

    def _refit(self):
        if self._src.isNull():
            return
        self.setPixmap(self._src.scaled(self.size(), Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation))

    def resizeEvent(self, event):       # noqa: N802 (Qt 命名)
        super().resizeEvent(event)
        self._refit()


class MediaPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.items = []
        self._visible_items = []
        self._worker = None
        self._cur = None
        self._dur = 0.0
        self._tags_for_visible = {}
        # 扫描代次：连点「刷新」/ 边打边搜会连着发起好几次扫描，只认最后一次的结果。
        self._scan_seq = 0

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)

        page_header(v, "媒体浏览", "浏览 output 目录、打标签、拖拽导入、对比两帧")

        # 工具行：只留「刷新」实心；其余 ghost；「删除」是破坏性动作 → ghost + 危险色
        # （原来是一颗实心红按钮钉在工具行里，太重，抢了整个页面的注意力）。
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.edit_dir = QLineEdit()
        self.edit_dir.setPlaceholderText("输出目录（默认取当前包的 output）")
        self.edit_dir.setMaximumWidth(DIR_EDIT_MAX)
        btn_dir = QPushButton("目录...")
        btn_dir.setObjectName("Ghost")
        btn_dir.setToolTip("**设定**输出目录（选一个新的）")
        btn_open = QPushButton("打开")
        btn_open.setObjectName("Ghost")
        btn_open.setToolTip("在资源管理器里**打开**当前这个目录")
        btn_refresh = QPushButton("刷新")
        btn_refresh.setObjectName("Primary")
        btn_help = QPushButton("说明")
        btn_help.setObjectName("Ghost")
        btn_help.setToolTip(DROP_HINT)
        btn_help.clicked.connect(self.show_help)

        self.edit_kw = QLineEdit()
        self.edit_kw.setPlaceholderText("文件名 / 提示词 / 标签")
        self.edit_kw.setMaximumWidth(KW_EDIT_MAX)
        self.edit_kw.setToolTip("边打边过滤，输入即出结果；回车或「搜索」同样触发（大小写不敏感）")
        self.combo_group = QComboBox()
        self.combo_group.addItems(M.GROUP_MODES)
        self.combo_group.setToolTip("选择媒体网格的分组方式")
        self.combo_tag = QComboBox()
        self.combo_tag.addItem("全部标签")
        self.combo_tag.setToolTip("只显示带有该标签的媒体")
        btn_search = QPushButton("搜索")
        btn_search.setObjectName("Ghost")
        btn_cmp = QPushButton("对比选中(2)")
        btn_cmp.setObjectName("Ghost")
        self.btn_import = QPushButton("导入文件...")
        btn_import = self.btn_import
        btn_import.setObjectName("Ghost")
        btn_del = QPushButton("删除")
        btn_del.setObjectName("GhostDanger")
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("VersionHint")
        # 工具行两排，**每排 = 「左簇（输入 / 筛选）+ 右簇（动作）」贴两边**，
        # 中间一条弹性间隙 —— 间隙是"有意分出来的组缝"，不是漏出来的空白。
        # 三种排法都试过、前两种都被打回（2026-10-11）：
        #   纯靠左 + 输入框上限 → 右边一大片空（「太随意，右边有空的」）；
        #   输入框无限拉长去"占满" → 868px 巨框（「占满了也不太适合」）。
        # 现在是第三版：两簇各贴一边、右缘对齐；输入框只在 `*_EDIT_MAX` 内拉伸，不会变巨框。
        # ⚠ 别把两排合成一排：1100 最小窗口下 10 个控件放不下，目录框会被压到 90px。
        bar.addWidget(self.edit_dir, 1)
        bar.addWidget(btn_dir)
        bar.addWidget(btn_open)
        bar.addStretch(1)
        bar.addWidget(btn_refresh)
        bar.addWidget(btn_help)
        bar.addWidget(self.lbl_count)
        v.addLayout(bar)

        bar2 = QHBoxLayout()
        bar2.setSpacing(6)
        bar2.addWidget(self.edit_kw, 1)
        bar2.addWidget(btn_search)
        bar2.addWidget(self.combo_group)
        bar2.addWidget(self.combo_tag)
        bar2.addStretch(1)
        bar2.addWidget(btn_cmp)
        bar2.addWidget(btn_import)
        bar2.addWidget(btn_del)
        v.addLayout(bar2)

        split = QSplitter()
        self.grid = DropListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        # ⚠ **不要设 `gridSize`**：设了就是「所有格子等宽」的固定网格，分组标题没办法
        # 独占一整行（详因见 MediaGridDelegate 的说明）。格子尺寸改由代理的 sizeHint 给。
        self.grid.setItemDelegate(MediaGridDelegate(self.grid))
        self.grid.setIconSize(QSize(THUMB, THUMB))
        self.grid.setMovement(QListWidget.Static)
        self.grid.setSpacing(6)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setUniformItemSizes(False)
        self.grid.setWordWrap(True)
        split.addWidget(self.grid)

        right = QFrame()
        right.setMinimumWidth(350)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(12, 10, 12, 10)
        rv.setSpacing(8)
        self.preview = FitLabel(
            "未选择任何媒体\n\n"
            "点左侧缩略图预览 · 看它的生成参数\n"
            "按住 Ctrl 点选两个可进入对比")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(200)
        self.preview.setObjectName("VersionHint")
        rv.addWidget(self.preview, 1)
        self.meta = QPlainTextEdit()
        self.meta.setReadOnly(True)
        self.meta.setObjectName("Console")
        self.meta.setPlaceholderText(
            "选中媒体后，这里显示它的生成参数（提示词 / 采样器 / 模型 / 尺寸 …）")
        rv.addWidget(self.meta, 2)

        tag_row = QHBoxLayout()
        self.edit_tags = QLineEdit()
        self.edit_tags.setPlaceholderText("标签，用逗号分隔，如 人物,精选")
        btn_tags = QPushButton("保存标签")
        btn_tags.setObjectName("Ghost")
        tag_row.addWidget(self.edit_tags, 1)
        tag_row.addWidget(btn_tags)
        rv.addLayout(tag_row)

        self.video_bar = QWidget()
        hb = QHBoxLayout(self.video_bar)
        hb.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.valueChanged.connect(self._on_seek)
        self.btn_frame = QPushButton("保存帧")
        self.btn_frame.setObjectName("Ghost")
        hb.addWidget(self.slider, 1)
        hb.addWidget(self.btn_frame)
        self.video_bar.setVisible(False)
        rv.addWidget(self.video_bar)
        split.addWidget(right)
        split.setSizes([650, 390])
        v.addWidget(split, 1)

        btn_dir.clicked.connect(self.pick_dir)
        btn_refresh.clicked.connect(self.refresh)
        btn_open.clicked.connect(self.open_dir)
        btn_search.clicked.connect(self.refresh)
        self.edit_kw.returnPressed.connect(self.refresh)
        # 边打边过滤（和市场页同一套）：停手 SEARCH_DEBOUNCE_MS 之后自动刷一次。
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._live_search)
        self.edit_kw.textChanged.connect(lambda _t: self._search_timer.start())
        self.combo_group.currentTextChanged.connect(self._render)
        self.combo_tag.currentTextChanged.connect(self.refresh)
        btn_cmp.clicked.connect(self.compare)
        btn_import.clicked.connect(self.import_dialog)
        btn_del.clicked.connect(self.delete_sel)
        btn_tags.clicked.connect(self.save_tags)
        self.btn_frame.clicked.connect(self.save_frame)
        self.grid.itemSelectionChanged.connect(self._on_sel_changed)
        self.grid.paths_dropped.connect(self.import_dropped)

    # ---------- 数据 ----------
    def _default_dir(self) -> str:
        root = self.ctx.root()
        return _paths.dirs(root).get("outputs", "") if root else ""

    def show_help(self):
        """「说明」按钮 —— 拖拽导入怎么用。

        这段文字原来是一整行 QLabel 压在工具行下面；2026-10-11 改成按钮：
        省下一整行高度还给缩略图网格，原文一个字不丢（按钮 tooltip 里也是全文）。
        """
        QMessageBox.information(self, "说明", DROP_HINT)

    def open_dir(self):
        """用资源管理器**打开**输入框里那个目录。

        和 `pick_dir()`（「目录…」按钮，**设定**一个新目录）是两件事，别合并：
        这个不动配置，只是把当前目录打开看看。
        """
        d = (self.edit_dir.text() or "").strip()
        if not d or not os.path.isdir(d):
            QMessageBox.information(self, "提示", "当前目录不存在，先用「目录…」设一个有效的输出目录")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录",
                                             self.edit_dir.text() or self._default_dir())
        if d:
            self.edit_dir.setText(d)
            self.refresh()

    def _refresh_tags(self):
        current = self.combo_tag.currentText()
        self.combo_tag.blockSignals(True)
        self.combo_tag.clear()
        self.combo_tag.addItem("全部标签")
        self.combo_tag.addItems(M.all_tags())
        self.combo_tag.setCurrentText(current if current in M.all_tags() else "全部标签")
        self.combo_tag.blockSignals(False)

    def _live_search(self):
        """关键词框内容变化后自动触发（去抖 `SEARCH_DEBOUNCE_MS`）—— 边打边过滤。

        ⚠ **不能直接走 `refresh()` 里"目录无效就弹框"那条路**：这个入口是**打字**触发的，
        目录无效时每敲一次停顿就弹一次模态框。目录无效就静静返回，等用户点「目录…」设好。
        """
        d = (self.edit_dir.text() or "").strip()
        if not d or not os.path.isdir(d):
            return
        self.refresh()

    def refresh(self):
        """扫描输出目录 + 过滤 —— **放进 Worker，不在 UI 线程做**。

        ⚠ 2026-10-11 修的真卡点：以前这里在 UI 线程直接跑 `M.scan([d])`（os.walk 整个
        output 目录），开了关键字筛选还要逐文件 `load_meta()`；而它同时是「刷新」按钮、
        搜索框回车、标签下拉的**直接回调** —— 目录一大或开着筛选，界面就真的冻住
        （白屏 +「无响应」）。现在扫描在 Worker 里，界面立刻返回，状态栏报进度。
        """
        # 显式刷新（刷新 / 搜索按钮 / 回车 / 选目录 / 标签下拉…）先取消挂起的「边打边过滤」——
        # 否则"打完字立刻按回车"会扫两次（回车一次 + 去抖到点再一次），纯浪费。
        t = getattr(self, "_search_timer", None)
        if t is not None:
            t.stop()
        d = (self.edit_dir.text() or "").strip() or self._default_dir()
        self.edit_dir.setText(d)
        if not d or not os.path.isdir(d):
            QMessageBox.information(self, "提示", "请先选择有效的输出目录")
            return
        self._stop_worker()
        self.grid.clear()
        self._cur = None
        self.video_bar.setVisible(False)

        kw = (self.edit_kw.text() or "").strip().lower()
        tag = self.combo_tag.currentText()
        self._scan_seq += 1
        seq = self._scan_seq
        self.ctx.status(f"正在扫描 {d} ...")

        def job():
            items = M.scan([d])
            if not kw and tag == "全部标签":
                return items
            out = []
            for item in items:
                tags = M.tags_for(item.path)
                if tag != "全部标签" and tag not in tags:
                    continue
                if kw and not _kw_hit(item, tags, kw):
                    continue
                out.append(item)
            return out

        w = self.ctx.spawn(Worker(job))

        def _done(items):
            # 这期间又发起了新的刷新 -> 这次的结果作废（否则慢的那次会覆盖快的）
            if seq != self._scan_seq:
                return
            self.items = items
            self._refresh_tags()
            self._render()

        w.done.connect(_done)
        w.failed.connect(
            lambda m: self.ctx.status(f"扫描失败：{m.strip().splitlines()[-1][:80]}"))

    def _render(self):
        self._stop_worker()
        self.grid.clear()
        self._visible_items = []
        groups = M.group_items(self.items, self.combo_group.currentText())
        for group_name, group_items in groups:
            if group_name:
                header = QListWidgetItem(f"▾ {group_name}  ({len(group_items)})")
                header.setData(ROLE_GROUP, True)
                header.setFlags(Qt.NoItemFlags)      # 标题不可选、不可拖
                header.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                self.grid.addItem(header)
                # 让它**独占一整行**的是 MediaGridDelegate.sizeHint（它读 ROLE_GROUP）——
                # ⚠ 别在里试 `setFirstItemColumnSpanned`，那是 QTreeView 的 API，
                #   QListView 上会直接 AttributeError 把 _render 打断。
            for item in group_items:
                self._visible_items.append(item)
                tags = M.tags_for(item.path)
                text = item.name if not tags else f"{item.name}\n[{', '.join(tags)}]"
                cell = QListWidgetItem(text)
                cell.setToolTip(item.path)
                cell.setTextAlignment(Qt.AlignHCenter)
                cell.setData(ROLE_MEDIA, item)
                self.grid.addItem(cell)
        self.lbl_count.setText(f"{len(self.items)} 个文件 / {len(groups)} 组")
        if not self._visible_items:
            self.ctx.status("没有符合条件的媒体文件")
            return

        self._worker = ThumbWorker(self._visible_items[:MAX_RENDER], THUMB, self)
        self._worker.ready.connect(self._set_icon)
        self._worker.progress.connect(
            lambda done, total: self.ctx.status(f"生成缩略图 {done}/{total}"))
        self._worker.finished_all.connect(
            lambda: self.ctx.status(f"缩略图完成，共 {len(self.items)} 个文件"))
        self._worker.start()

    def _stop_worker(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        self._worker = None

    def _set_icon(self, media_index: int, data: bytes):
        if media_index >= len(self._visible_items):
            return
        target = self._visible_items[media_index]
        for row in range(self.grid.count()):
            it = self.grid.item(row)
            if it.data(ROLE_MEDIA) is target:
                it.setIcon(_pixmap(data))
                return

    # ---------- 标签 ----------
    def _selected_media(self):
        out = []
        for it in self.grid.selectedItems():
            item = it.data(ROLE_MEDIA)
            if item is not None:
                out.append(item)
        return out

    def save_tags(self):
        selected = self._selected_media()
        if not selected:
            QMessageBox.information(self, "提示", "先选中一个或多个媒体文件")
            return
        tags = [x.strip() for x in self.edit_tags.text().replace("，", ",").split(",") if x.strip()]
        for item in selected:
            M.set_tags(item.path, tags)
        self.ctx.status(f"已给 {len(selected)} 个文件保存标签：{', '.join(tags) or '(清空)'}")
        self.refresh()

    # ---------- 拖拽导入 ----------
    def import_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要导入的媒体", "",
            "媒体文件 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.mp4 *.webm *.mov *.avi *.mkv)")
        if paths:
            self._confirm_import(paths)

    def import_dropped(self, paths):
        files = M.collect_paths(paths)
        if not files:
            QMessageBox.information(self, "导入", "拖入的内容里没有发现支持的图片/视频文件")
            return
        self._confirm_import(files)

    def _confirm_import(self, files):
        dest = (self.edit_dir.text() or "").strip() or self._default_dir()
        if not dest:
            QMessageBox.information(self, "导入", "请先配置输出目录")
            return
        answer = QMessageBox.question(
            self, "确认导入",
            f"发现 {len(files)} 个媒体文件。\n\n"
            f"目标：{dest}\n\n"
            "选择“是”复制（保留原文件），选择“否”移动（导入后删除原文件）。",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes)
        if answer == QMessageBox.Cancel:
            return
        move = answer == QMessageBox.No

        def job(report):
            return M.import_files(dest, files, move=move,
                                  report=lambda i, total, name:
                                  report(f"[{i}/{total}] {name}"))

        w = self.ctx.spawn(Worker(job, on_line=lambda s: self.ctx.status(s)))

        def _done(res):
            self.ctx.status(f"导入完成：成功 {res['ok']} / 失败 {res['fail']}")
            if res["fail"]:
                QMessageBox.warning(self, "部分失败",
                                    f"成功 {res['ok']} 个，失败 {res['fail']} 个，详见日志。")
            self.refresh()

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "导入失败", m))

    # ---------- 选择 / 预览 ----------
    def _on_sel_changed(self):
        selected = self._selected_media()
        if not selected:
            return
        item = selected[0]
        self._show(item)
        self.edit_tags.setText(", ".join(M.tags_for(item.path)))

    def _preview_size(self) -> int:
        """预览源图该载多大：取预览区的**较长边**（夹在 PREVIEW_MIN / PREVIEW_MAX 之间）。

        判据和对比对话框一样：**源 ≥ 显示** 才是降采样（清晰）。
        """
        return min(PREVIEW_MAX, max(self.preview.width(), self.preview.height(),
                                    PREVIEW_MIN))

    def _show(self, item):
        self._cur = item
        meta = M.load_meta(item)
        pm = _pixmap(M.full_pixmap_bytes(item, max_size=self._preview_size()))
        if not pm.isNull():
            self.preview.set_source(pm)
        else:
            self.preview.setText("(无法预览)")
        lines = M.meta_lines(item, meta)
        tags = M.tags_for(item.path)
        if tags:
            lines.insert(2, "标签：" + ", ".join(tags))
        self.meta.setPlainText("\n".join(lines))

        if item.kind == "video":
            dur = float(M.probe(item.path).get("duration") or 0)
            self._dur = dur
            if dur > 0:
                self.slider.blockSignals(True)
                self.slider.setValue(0)
                self.slider.blockSignals(False)
                self.video_bar.setVisible(True)
            else:
                self.video_bar.setVisible(False)
        else:
            self.video_bar.setVisible(False)

    def _cur_time(self) -> float:
        return float(self.slider.value()) / 1000.0 * self._dur

    def _on_seek(self, _value):
        item = self._cur
        if item is None or item.kind != "video":
            return
        pm = _pixmap(M.seek_pixmap_bytes(item, self._cur_time(),
                                         max_size=self._preview_size()))
        if not pm.isNull():
            self.preview.set_source(pm)

    def save_frame(self):
        item = self._cur
        if item is None or item.kind != "video":
            return
        t = self._cur_time()
        base = os.path.splitext(item.name)[0]
        dest, _ = QFileDialog.getSaveFileName(
            self, "保存帧为 PNG", f"{base}_{t:.1f}s.png", "PNG (*.png)")
        if not dest:
            return
        if M.save_frame(item, t, dest):
            self.ctx.status(f"已保存帧：{dest}")
        else:
            QMessageBox.warning(self, "失败", "无法解码该帧")

    # ---------- 操作 ----------
    def delete_sel(self):
        selected = self._selected_media()
        if not selected:
            return
        if QMessageBox.question(self, "删除", f"删除选中的 {len(selected)} 个文件？") \
                != QMessageBox.Yes:
            return
        for item in selected:
            M.delete(item)
        self.refresh()

    def compare(self):
        selected = self._selected_media()
        if len(selected) != 2:
            QMessageBox.information(self, "提示", "请按住 Ctrl 选中两个文件")
            return
        CompareDialog(self, selected[0], selected[1]).exec()

    def on_show(self):
        if not self.edit_dir.text().strip():
            self.edit_dir.setText(self._default_dir())
