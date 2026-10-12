"""插件市场页：直连 ComfyUI-Manager 公开目录搜索并安装。

数据源（全部公开，无私有服务端）：
- 插件目录：Comfy-Org/ComfyUI-Manager 的 `custom-node-list.json`
  （jsdelivr / raw / ghfast 三源回退，本地缓存 6 小时）
- 按节点名反查：官方 `extension-node-map.json`（本合并版已修正解析结构）

安装走 Echo 的便携 Git，`--depth 1` 克隆到 custom_nodes，可选顺带装 requirements。
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .dialogs import MessageBox as QMessageBox

from ..core import market as MK
from ..core import paths as _paths
from ..ui import style
from .widgets import TableEmptyHint, page_header
from .workers import Worker

COLS = ["插件", "作者", "描述", "标签", "状态"]
MAX_ROWS = 400
# 边打字边过滤的去抖间隔：本地过滤很快，但没必要每个按键都重排一次表格
SEARCH_DEBOUNCE_MS = 200


class MarketPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.items = []

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        page_header(v, "插件市场", "搜索 ComfyUI-Manager 公开目录，一键装进 custom_nodes")

        # 单行顶栏：搜索框吃满剩余宽度，紧跟「安装选中」，两个复选框并排收尾。
        # 「搜索」按钮已删 —— 输入即过滤（去抖 200ms），不需要它；回车仍然可用。
        # 本页只留「安装选中」一个实心主操作，「刷新目录」降为 ghost。
        bar = QHBoxLayout()
        bar.setSpacing(6)
        btn_refresh = QPushButton("刷新目录")
        btn_refresh.setObjectName("Ghost")
        self.btn_refresh = btn_refresh
        self.edit_kw = QLineEdit()
        self.edit_kw.setPlaceholderText("搜插件名 / 作者 / 描述 / 标签")
        self.edit_kw.setToolTip("边打边过滤，输入即出结果；回车同样触发")
        btn_install = QPushButton("安装选中")
        btn_install.setObjectName("Primary")
        self.chk_node = QCheckBox("按节点名搜")
        self.chk_node.setToolTip("改成按「工作流里的节点类名」反查插件")
        self.chk_reqs = QCheckBox("顺带装依赖")
        self.chk_reqs.setToolTip("装完插件后顺带跑 pip install -r requirements.txt")
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("VersionHint")
        bar.addWidget(btn_refresh)
        bar.addWidget(self.edit_kw, 1)
        bar.addWidget(btn_install)
        bar.addWidget(self.chk_node)
        bar.addWidget(self.chk_reqs)
        bar.addSpacing(12)          # 状态计数与前面的复选框拉开一点，别黏在一起
        bar.addWidget(self.lbl_status)
        v.addLayout(bar)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for c, w in ((1, 120), (3, 180), (4, 90)):
            self.table.setColumnWidth(c, w)
        v.addWidget(self.table, 1)
        self.table_hint = TableEmptyHint(
            self.table, "插件目录为空。点右上角「刷新目录」重新拉取 ComfyUI-Manager 的公开列表")

        tip = QLabel("目录来自 ComfyUI-Manager 的公开数据；安装走内置便携 Git，"
                     "不需要系统 Git。装完请回「插件」页确认，或用「工作流」页重新分析。")
        tip.setObjectName("VersionHint")
        tip.setWordWrap(True)
        v.addWidget(tip)

        btn_refresh.clicked.connect(self.refresh_catalog)
        self.edit_kw.returnPressed.connect(lambda: self.search())
        self.chk_node.toggled.connect(lambda _on: self.search())
        btn_install.clicked.connect(self.install_selected)

        # 边打字边过滤：去抖 200ms 后自动跑一次 silent 搜索，不用点「搜索」按钮
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._live_search)
        self.edit_kw.textChanged.connect(lambda _t: self._search_timer.start())

    # ---------- 工具 ----------
    def _root(self) -> str:
        return self.ctx.root()

    def _proxy(self) -> str:
        lc = self.ctx.launch_cfg()
        return lc.get("launcher_proxy_url", "") if lc.get("launcher_proxy") else ""

    def _require(self) -> str:
        root = self._root()
        if not root or _paths.validate_root(root):
            QMessageBox.warning(self, "无可用包", "请先在「启动」页选择有效的 ComfyUI 包路径。")
            return ""
        return root

    # ---------- 目录 ----------
    def refresh_catalog(self):
        self.btn_refresh.setEnabled(False)
        self.ctx.status("正在从公开源拉取插件目录…")
        w = self.ctx.spawn(Worker(MK.refresh, self._proxy()))

        def _done(n):
            self.btn_refresh.setEnabled(True)
            if n and n > 0:
                self.ctx.status(f"插件目录已更新（{n} 个）")
                self.search()
            else:
                self.ctx.status("拉取失败（无网络/被墙）")
                QMessageBox.warning(self, "刷新失败",
                                    "无法拉取插件目录。可用国内镜像或代理后重试。")

        w.done.connect(_done)
        w.failed.connect(lambda m: (self.btn_refresh.setEnabled(True),
                                    QMessageBox.warning(self, "刷新异常", m)))

    # ---------- 搜索 ----------
    def _live_search(self):
        """输入框内容变化后自动触发（去抖 200ms）—— 边打边过滤。"""
        self.search()

    def search(self):
        """按关键字过滤。

        「按节点名搜」模式下关键字为空（或包路径没配好）时**直接清空结果、不给任何提示**：
        用户明确要求节点搜索不要提示，而且这里会被每次打字触发 —— 弹模态框会烦死人。
        """
        kw = (self.edit_kw.text() or "").strip()
        if self.chk_node.isChecked():
            root = self._root()
            if not kw or not root or _paths.validate_root(root):
                self.items = []
                self._fill()
                self.lbl_status.setText("")
                return
            pairs = MK.search_by_node(kw, _paths.dirs(root)["comfyui"])
            self.items = []
            from echo_launcher.plugin.market import MarketItem
            for node, plugin in pairs[:MAX_ROWS]:
                self.items.append(MarketItem(name=plugin, repository="",
                                             author=node, description=f"节点 {node}",
                                             dir_name=plugin))
            self.lbl_status.setText(f"节点反查 {len(pairs)} 条")
        else:
            self.items = MK.search(kw, limit=MAX_ROWS)
            self.lbl_status.setText(f"{len(self.items)} 个插件")
        self._fill()

    def _fill(self):
        root = self._root()
        installed = MK.installed_names(root) if root else set()
        self.table.setRowCount(len(self.items))
        for r, it in enumerate(self.items):
            name = it.dir_name or it.name
            vals = [it.name, it.author, (it.description or "")[:120], it.tags,
                    "已安装" if (name or "").lower() in installed else ""]
            for c, text in enumerate(vals):
                cell = QTableWidgetItem(text)
                if c == 0:
                    cell.setToolTip(it.repository or it.description or "")
                if c == 4 and text:
                    cell.setForeground(QColor(style.SUCCESS))
                elif c == 2:
                    cell.setForeground(QColor(style.MUTED))
                self.table.setItem(r, c, cell)
        self.table_hint.refresh()

    # ---------- 安装 ----------
    def install_selected(self):
        root = self._require()
        if not root:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "提示", "先在表格里选中要安装的插件")
            return
        picked = [self.items[r] for r in rows if r < len(self.items)]
        py = _paths.dirs(root)["python_exe"]
        deps = self.chk_reqs.isChecked()

        def job(report):
            out = []
            for k, item in enumerate(picked, 1):
                report(f"[{k}/{len(picked)}] 安装 {item.name}")
                res = MK.install(root, item, install_deps=deps, python_path=py)
                report(f"    -> {'成功' if res['ok'] else res['error']}")
                out.append(res)
            return out

        w = self.ctx.spawn(Worker(job, on_line=lambda s: self.ctx.status(s)))

        def _done(results):
            ok = sum(1 for r in results if r["ok"])
            self.ctx.status(f"安装完成 {ok}/{len(results)}")
            QMessageBox.information(self, "安装完成",
                                    f"成功 {ok} 个，失败 {len(results) - ok} 个。\n"
                                    "失败原因见状态栏与日志。")
            self.search()

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "安装异常", m))

    def on_show(self):
        if not self.items and MK.items():
            self.search()
