"""插件管理页。"""
import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (QColor, QDesktopServices, QGuiApplication,
                           QKeySequence, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .dialogs import MessageBox as QMessageBox

from ..core import paths, pipenv, plugins, rescue as rescuemod
from ..ui import style
from .rescue_dialog import RescueDialog
from .widgets import TableEmptyHint, page_header, plain_box
from .workers import Worker

COLS = ["插件", "状态", "分支", "本地提交", "可更新", "远程地址"]


class PluginsPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._infos = []
        self._filter_updatable = False

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        # 页头：本页一上来就是按钮行，没有标题读法，补一行（15px 页面名 + 一句说明）
        page_header(v, "插件管理", "扫描 custom_nodes、检查更新、启停与卸载（破坏性操作会先自动建还原点）")

        # 顶栏两行：第一行「操作」（原表格里的「操作」列按钮挪上来，作用于选中行），
        # 第二行「从 Git 安装」。表格里不再放按钮，省下的宽度给「插件」列。
        # 按钮层级：整页只留「扫描插件」一个实心主操作，其余一律 ghost；
        # 「卸载」是破坏性动作，给 ghost + 危险色文字（平时不抢戏，hover 才红起来）。
        bar1 = QHBoxLayout()
        bar1.setSpacing(6)
        self.btn_scan = QPushButton("扫描插件")
        self.btn_scan.setObjectName("Primary")
        self.btn_check_all = QPushButton("全部检查更新")
        self.btn_check_all.setObjectName("Ghost")
        self.btn_update_sel = QPushButton("更新选中")
        self.btn_update_sel.setObjectName("Ghost")
        self.btn_update_sel.setToolTip("更新表格里选中的那个插件（更新前自动建还原点）")
        self.btn_toggle_sel = QPushButton("启用/禁用选中")
        self.btn_toggle_sel.setObjectName("Ghost")
        self.btn_toggle_sel.setToolTip("启用 / 禁用选中的插件（就地重命名为 <插件名>.disabled）")
        self.btn_uninstall_sel = QPushButton("卸载选中")
        self.btn_uninstall_sel.setObjectName("GhostDanger")
        self.btn_uninstall_sel.setToolTip("删除选中插件的整个目录（会先自动建还原点，可回滚）")
        for b in (self.btn_update_sel, self.btn_toggle_sel, self.btn_uninstall_sel):
            b.setEnabled(False)
        # 标签会在「启用选中 / 禁用选中」之间切换，锁个最小宽度免得按钮忽宽忽窄
        self.btn_toggle_sel.setMinimumWidth(112)
        btn_open = QPushButton("插件目录")
        btn_open.setObjectName("Ghost")
        btn_rescue = QPushButton("还原点...")
        btn_rescue.setObjectName("Ghost")
        btn_rescue.setToolTip("更新/切版本前自动建还原点；出问题可一键回滚（含依赖环境）")
        bar1.addWidget(self.btn_scan)
        bar1.addWidget(self.btn_check_all)
        bar1.addWidget(self.btn_update_sel)
        bar1.addWidget(self.btn_toggle_sel)
        bar1.addWidget(self.btn_uninstall_sel)
        bar1.addStretch(1)
        bar1.addWidget(btn_open)
        bar1.addWidget(btn_rescue)
        v.addLayout(bar1)

        bar2 = QHBoxLayout()
        bar2.setSpacing(6)
        self.edit_url = QLineEdit()
        self.edit_url.setPlaceholderText("粘贴插件 Git 地址，如 https://github.com/xxx/yyy")
        self.edit_url.setMinimumWidth(180)
        self.btn_install = QPushButton("从 Git 安装")
        self.btn_install.setObjectName("Ghost")
        self.chk_reqs = QCheckBox("装依赖(requirements)")
        self.chk_reqs.setChecked(True)
        self.chk_only_upd = QCheckBox("仅显示可更新")
        bar2.addWidget(self.edit_url, 1)
        bar2.addWidget(self.btn_install)
        bar2.addWidget(self.chk_reqs)
        bar2.addWidget(self.chk_only_upd)
        v.addLayout(bar2)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        # 「插件」列按内容自适应：以前固定宽度会把长名字截成「ComfyUI-…」，认不出是哪个插件
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 64)     # 状态
        self.table.setColumnWidth(2, 72)     # 分支
        self.table.setColumnWidth(3, 92)     # 本地提交
        self.table.setColumnWidth(4, 76)     # 可更新
        v.addWidget(self.table, 1)
        self.table_hint = TableEmptyHint(
            self.table, "点「扫描插件」读取 custom_nodes；也可用「从 Git 安装」添加新插件")

        # ---- 详情条：选中一行 → 把「插件名 / 远程地址」摊成可拖选、可一键复制的两行 ----
        # ⚠ 为什么不直接让表格单元格可拖选：QTableWidget 的格子只能「选行」或「进编辑器」，
        #   而编辑器/子控件会**吃掉鼠标事件** —— 会把这两件事一起弄坏：
        #     ① 点一行 → 顶栏按钮生效（靠 itemSelectionChanged）；
        #     ② 有更新的**整行标红**（对 item setForeground 画的，被子控件盖住就没了）。
        #   所以独立成一条详情条，表格的交互一点不动。
        # ⚠ 用只读 QLineEdit 而不是 QLabel：长地址能拖选、能 Ctrl+A / Ctrl+C，
        #   超出宽度时会**自己横向滚动**（QLabel 只会被裁掉，右边那段选不全、也复制不到）。
        self.detail_name = QLineEdit()
        self.detail_url = QLineEdit()
        for ed, ph, tip in (
                (self.detail_name, "（未选中插件）",
                 "插件名 —— 可拖选复制；表格有焦点时 Ctrl+C 同效"),
                (self.detail_url, "（未选中插件，或该插件非 Git 安装）",
                 "远程地址 —— 可拖选复制；表格有焦点时 Ctrl+Shift+C 同效")):
            ed.setObjectName("CopyLine")
            ed.setReadOnly(True)
            ed.setPlaceholderText(ph)
            ed.setToolTip(tip)
        self.btn_copy_name = QPushButton("复制名称")
        self.btn_copy_url = QPushButton("复制地址")
        for b in (self.btn_copy_name, self.btn_copy_url):
            b.setObjectName("Ghost")
            b.setEnabled(False)
        self.btn_copy_name.setToolTip("复制插件名到剪贴板（表格里 Ctrl+C 同效）")
        self.btn_copy_url.setToolTip("复制远程地址到剪贴板（表格里 Ctrl+Shift+C 同效）")

        detail = plain_box()        # ⛔ 别用裸 QWidget：会被全局底色刷出色带
        dv = QVBoxLayout(detail)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(6)
        for ed, btn in ((self.detail_name, self.btn_copy_name),
                        (self.detail_url, self.btn_copy_url)):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addWidget(ed, 1)
            row.addWidget(btn)
            dv.addLayout(row)
        v.addWidget(detail)

        self.hint = QLabel("提示：禁用的插件会就地重命名为 <插件名>.disabled；"
                           "选中一行可在上方复制名称与 Git 地址。更新前自动建还原点。")
        self.hint.setObjectName("VersionHint")
        v.addWidget(self.hint)

        self.btn_scan.clicked.connect(self.rescan)
        self.btn_check_all.clicked.connect(self.check_all)
        self.btn_install.clicked.connect(self.install)
        self.btn_update_sel.clicked.connect(self._update_selected)
        self.btn_toggle_sel.clicked.connect(self._toggle_selected)
        self.btn_uninstall_sel.clicked.connect(self._uninstall_selected)
        self.table.itemSelectionChanged.connect(self._on_sel_changed)
        self.chk_only_upd.toggled.connect(self._toggle_filter)
        btn_open.clicked.connect(self._open_dir)
        btn_rescue.clicked.connect(self._open_rescue)

        # 复制：两个按钮 + 表格上的快捷键。
        # ⚠ 快捷键用 `WidgetShortcut` 挂在表格上 —— 只有在**表格有焦点**时才触发，
        #   否则会和详情条里「拖选一段再 Ctrl+C」抢剪贴板。
        self.btn_copy_name.clicked.connect(
            lambda: self._copy_text(self.detail_name, "插件名"))
        self.btn_copy_url.clicked.connect(
            lambda: self._copy_text(self.detail_url, "远程地址"))
        for seq, attr, what in ((QKeySequence.StandardKey.Copy, "detail_name", "插件名"),
                                ("Ctrl+Shift+C", "detail_url", "远程地址")):
            sc = QShortcut(QKeySequence(seq), self.table)
            sc.setContext(Qt.WidgetShortcut)
            sc.activated.connect(
                (lambda a=attr, w=what: self._copy_text(getattr(self, a), w)))

    # ---------- 复制 ----------
    def _copy_text(self, edit, what: str):
        """把详情条里的文本放进剪贴板（空文本时只提示，不用空串覆盖剪贴板）。"""
        text = edit.text()
        if not text:
            self.ctx.status(f"没有可复制的{what}")
            return
        QGuiApplication.clipboard().setText(text)
        self.ctx.status(f"已复制{what}：{text}")

    # ---------- 工具 ----------
    def _git_proxy(self):
        lc = self.ctx.launch_cfg()
        if lc.get("launcher_proxy"):
            return lc.get("launcher_proxy_url", "")
        return ""

    def _open_dir(self):
        root = self.ctx.root()
        if root:
            d = paths.dirs(root)["custom_nodes"]
            if os.path.isdir(d):
                QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def _open_rescue(self):
        RescueDialog(self, self.ctx).exec()

    def _toggle_filter(self, on):
        self._filter_updatable = on
        self._fill_table()

    def _require_root(self):
        from ..core import paths as _p
        root = self.ctx.root()
        if not root or _p.validate_root(root):
            QMessageBox.warning(self, "无可用包", "请先在「启动」页选择有效的 ComfyUI 包路径。")
            return ""
        return root

    # ---------- 扫描 ----------
    def rescan(self):
        root = self._require_root()
        if not root:
            return
        self.ctx.status("正在扫描插件...")
        w = self.ctx.spawn(Worker(plugins.scan, root, self._git_proxy()))
        w.done.connect(self._on_scanned)
        w.failed.connect(lambda m: QMessageBox.warning(self, "扫描失败", m))

    def _on_scanned(self, infos):
        self._infos = infos
        self._fill_table()
        self.ctx.status(f"插件扫描完成，共 {len(infos)} 个")

    def _fill_table(self):
        rows = [i for i in self._infos
                if not self._filter_updatable or (i.update_count or 0) > 0]
        self.table.setRowCount(len(rows))
        for r, info in enumerate(rows):
            upd = (info.update_count or 0) > 0
            for c, text in enumerate(info.as_row()):
                item = QTableWidgetItem(text)
                if upd:
                    # 有更新 -> **整行**染红（用户要求整行变红，不只是那一格数字），
                    # 一眼就能扫出哪几个插件要更新
                    item.setForeground(QColor(style.DANGER))
                elif c == 1 and not info.enabled:
                    item.setForeground(QColor(style.MUTED))
                self.table.setItem(r, c, item)
            self.table.item(r, 0).setData(Qt.UserRole, info)
        self.table_hint.refresh()
        self._on_sel_changed()      # 重填后选中态没了，按钮跟着回到禁用

    # ---------- 选中行 -> 顶部按钮 ----------
    def _selected_info(self):
        """表格里当前选中的插件条目；没选中返回 None。"""
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            return None
        it = self.table.item(rows[0], 0)
        return it.data(Qt.UserRole) if it else None

    def _on_sel_changed(self):
        info = self._selected_info()
        self.btn_update_sel.setEnabled(bool(info and info.is_git))
        self.btn_toggle_sel.setEnabled(bool(info))
        self.btn_uninstall_sel.setEnabled(bool(info))
        # 详情条跟着选中行走：列表里选谁，下面就显示谁、就能复制谁
        url = (info.url if info else "") or ""
        self.detail_name.setText(info.name if info else "")
        self.detail_url.setText(url)
        for ed in (self.detail_name, self.detail_url):
            ed.setCursorPosition(0)         # 长文本从头上显示，别停在尾巴上
        self.btn_copy_name.setEnabled(bool(info))
        self.btn_copy_url.setEnabled(bool(url))
        if not info:
            self.btn_toggle_sel.setText("启用/禁用选中")
        else:
            self.btn_toggle_sel.setText("禁用选中" if info.enabled else "启用选中")

    def _update_selected(self):
        info = self._selected_info()
        if info:
            self._update_by_info(info)

    def _toggle_selected(self):
        info = self._selected_info()
        if info:
            self._toggle_by_info(info)

    def _uninstall_selected(self):
        """卸载选中插件：确认 -> 建还原点 -> 删目录（破坏性操作，必须先备份）。"""
        root = self._require_root()
        info = self._selected_info()
        if not root or not info:
            return
        if QMessageBox.question(
                self, "确认卸载",
                f"卸载插件「{info.name}」？\n\n"
                "会删除它的整个目录。删除前会自动建一个还原点，出问题可以回滚。") \
                != QMessageBox.Yes:
            return
        self.ctx.status(f"正在卸载 {info.name} ...")

        def job(report):
            report(f"创建还原点（{info.name}）...")
            point = rescuemod.create(root, [info.name], note=f"卸载 {info.name} 前")
            report(f"还原点：{point.id if point else '创建失败（继续卸载）'}")
            ok, msg = plugins.uninstall(root, info.name)
            report(msg)
            return ok, msg

        w = self.ctx.spawn(Worker(job, on_line=lambda s: self.ctx.status(s)))

        def _done(res):
            ok, msg = res
            self.ctx.status(f"{info.name}: {msg}")
            if not ok:
                QMessageBox.warning(self, "卸载失败", msg)
            self.rescan()

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "卸载异常", m))

    def _find_row(self, info):
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.data(Qt.UserRole) is info:
                return r
        return -1

    def _update_by_info(self, info):
        root = self._require_root()
        if not root:
            return
        self.ctx.status(f"正在更新 {info.name} ...")

        def job(report):
            # Echo 的做法：更新前先建还原点，出问题可回滚
            report(f"创建还原点（{info.name}）...")
            point = rescuemod.create(root, [info.name], note=f"更新 {info.name} 前")
            report(f"还原点：{point.id if point else '创建失败（继续更新）'}")
            ok, msg = plugins.update(info, self._git_proxy())
            report(msg)
            return ok, msg

        w = self.ctx.spawn(Worker(job, on_line=lambda s: self.ctx.status(s)))

        def _done(res):
            ok, msg = res
            self.ctx.status(f"{info.name}: {msg}")
            if ok:
                plugins.check_update(info, self._git_proxy())
            self._fill_table()

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "更新异常", m))

    def _toggle_by_info(self, info):
        root = self._require_root()
        if not root:
            return
        ok, msg = plugins.set_enabled(root, info.name, enable=not info.enabled)
        self.ctx.status(msg)
        if not ok:
            QMessageBox.warning(self, "操作失败", msg)
        else:
            self.rescan()

    # ---------- 更新检查 ----------
    def check_all(self):
        root = self._require_root()
        if not root:
            return
        git_infos = [i for i in self._infos if i.is_git]
        if not git_infos:
            self.ctx.status("没有 git 插件可检查")
            return
        self.ctx.status(f"正在检查 {len(git_infos)} 个插件更新...")

        def job(report):
            for k, info in enumerate(git_infos, 1):
                report(f"[{k}/{len(git_infos)}] {info.name}")
                plugins.check_update(info, self._git_proxy())
            return git_infos

        w = self.ctx.spawn(Worker(job, on_line=lambda s: self.ctx.status("检查更新: " + s)))
        w.done.connect(lambda _r: (self._fill_table(),
                                   self.ctx.status("更新检查完成")))

    # ---------- 安装 / 操作 ----------
    def install(self):
        root = self._require_root()
        if not root:
            return
        url = self.edit_url.text().strip()
        if not url:
            QMessageBox.information(self, "提示", "请先粘贴插件 Git 地址。")
            return

        def job(report):
            report(f"克隆 {url} ...")
            ok, msg, name = plugins.install(url, root, self._git_proxy())
            report(msg)
            if ok and self.chk_reqs.isChecked():
                req = os.path.join(paths.dirs(root)["custom_nodes"], name, "requirements.txt")
                if os.path.isfile(req):
                    report("检测到 requirements.txt，正在安装依赖...")
                    code, out = pipenv.run_pip(
                        root, ["install", "-r", req],
                        self.ctx.launch_cfg().get("pypi_mirror", ""),
                        self._git_proxy(), on_line=report)
                    report("依赖安装完成" if code == 0 else f"依赖安装失败(代码 {code})")
            return ok, msg, name

        self.btn_install.setEnabled(False)
        w = self.ctx.spawn(Worker(job, on_line=lambda s: self.ctx.status(s)))

        def _done(res):
            self.btn_install.setEnabled(True)
            ok, msg, name = res
            if ok:
                self.edit_url.clear()
                self.rescan()
            else:
                QMessageBox.warning(self, "安装失败", msg)

        w.done.connect(_done)
        w.failed.connect(lambda m: (self.btn_install.setEnabled(True),
                                    QMessageBox.warning(self, "安装异常", m)))

    def on_show(self):
        # 首次进入且已有有效包路径时自动扫一次（Echo 启动时也会自动刷新插件）
        if not self._infos:
            root = self.ctx.root()
            if root and not paths.validate_root(root):
                self.rescan()
