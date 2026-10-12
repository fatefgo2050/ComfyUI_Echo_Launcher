"""ComfyUI 内核：**只**管 ComfyUI 内核（本体仓库）的版本查看与升级。

对齐 TE 启动器「版本管理 → ComfyUI 更新」页的语义，刻意不掺和插件：
插件的更新 / 启停 / 回滚在「插件」页里，两边分开，各自说清楚自己的事。

它现在是**主窗口里的一个页面**（原先弹独立对话框，用户要求直接内嵌到主界面），
侧栏标签叫「ComfyUI 内核」。

界面四块
--------
    路径信息   ComfyUI 路径 / Python 路径
    当前版本   describe 串 + HEAD 短 sha + 提交时间 + 分支（游离 HEAD 会写明）
    通道       开发版（master 最新） / 正式版（最新发布标签）→ 决定下面列哪段历史
    提交列表   提交 / 提交日期 / 说明（当前版本那行标注）+ 右侧「更新日志」
    更新       更新到选中版本 = checkout --force（游离 HEAD，不动 master）

⚠️ 首次进页必须异步加载：`CV.current()` 要连开 4 个 git 子进程（本机实测 1.65 s），
整块 `_load` 约 2.3 s，直接放 `on_show` 会把界面卡住 —— 所以 `on_show` 只起 Worker，
读到的快照再由 `_apply_snapshot()` 回填（回填过程不再碰 git）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox,
                               QHBoxLayout, QHeaderView, QLabel,
                               QPlainTextEdit, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from .dialogs import MessageBox as QMessageBox

from ..core import config as cfgmod
from ..core import core_version as CV
from ..ui import style
from .widgets import page_header
from .workers import Worker

COLS = ["提交", "提交日期", "说明"]
# 「当前版本」那行要放两行文字（sha + 标记），得单独加高——TE 也是这么干的
CUR_ROW_H = 46


def _select_combo(combo, text: str):
    """把下拉选中到指定文案；找不到就回到第 0 项（静默，不触发信号）。"""
    combo.blockSignals(True)
    idx = combo.findText(text or "")
    combo.setCurrentIndex(idx if idx >= 0 else 0)
    combo.blockSignals(False)


class VersionPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

        self.rows = []            # CV.commits() 的结果
        self.cur = {}             # CV.current() 的结果
        self._log_cache = {}      # sha -> 更新日志正文（避免反复起 git 进程）
        self._pick_seq = 0        # 选中行的竞态令牌
        self._loaded = False      # 首次进页才做完整加载

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)

        # ---- 页头 ----
        page_header(v, "ComfyUI 内核", "只管本体仓库的版本查看与切换，插件的更新在「插件」页")

        # ---- 路径信息 ----
        self.lbl_path = QLabel("")
        self.lbl_path.setObjectName("PathLine")
        self.lbl_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_py = QLabel("")
        self.lbl_py.setObjectName("PathLine")
        self.lbl_py.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_path)
        v.addWidget(self.lbl_py)

        # ---- 当前版本 ----
        self.lbl_cur = QLabel("")
        self.lbl_cur.setObjectName("CurrentVer")
        v.addWidget(self.lbl_cur)

        # ---- 工具行 ----
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.btn_cur = QPushButton("刷新当前版本")
        self.btn_cur.setObjectName("Ghost")
        self.btn_cur.setToolTip("重新读取本地仓库的 HEAD（不联网）")
        self.btn_list = QPushButton("刷新版本列表")
        self.btn_list.setObjectName("Ghost")
        self.btn_list.setToolTip("从 GitHub 拉取最新提交与标签，然后重列下面的版本")
        bar.addWidget(self.btn_cur)
        bar.addWidget(self.btn_list)
        bar.addSpacing(10)
        cap = QLabel("通道")
        cap.setObjectName("VersionHint")
        bar.addWidget(cap)
        self.cmb_channel = QComboBox()
        self.cmb_channel.setMinimumWidth(200)
        self.cmb_channel.setToolTip("决定下面列出哪一段历史")
        bar.addWidget(self.cmb_channel)
        bar.addSpacing(6)
        cap_m = QLabel("镜像")
        cap_m.setObjectName("VersionHint")
        bar.addWidget(cap_m)
        self.cmb_mirror = QComboBox()
        self.cmb_mirror.setMinimumWidth(170)
        self.cmb_mirror.setToolTip(
            "国内直连 GitHub 慢 / 不通时换一个加速镜像。\n"
            "只影响「刷新版本列表」的 fetch：用 git 的 url.insteadOf 透明改写远端地址，\n"
            "仓库的 origin 不动、origin/master 照常更新。")
        self.cmb_mirror.addItems(CV.mirror_options())
        _select_combo(self.cmb_mirror, CV.github_mirror())
        bar.addWidget(self.cmb_mirror)
        bar.addStretch(1)
        self.btn_update = QPushButton("更新到选中版本")
        self.btn_update.setObjectName("Primary")
        self.btn_update.setEnabled(False)
        bar.addWidget(self.btn_update)
        v.addLayout(bar)

        # ---- 提交列表 + 更新日志 ----
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setColumnWidth(0, 104)
        self.table.setColumnWidth(1, 138)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        ll.addWidget(self.table)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        cap2 = QLabel("更新日志")
        cap2.setObjectName("CardTitle")
        rl.addWidget(cap2)
        self.log = QPlainTextEdit()
        self.log.setObjectName("LogView")
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("选一条提交，这里显示它的完整说明与改动文件")
        rl.addWidget(self.log, 1)

        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([620, 380])
        v.addWidget(split, 1)

        # ---- 底部提示 / 状态 ----
        # 「120 条提交 · origin/master」这类计数放这里：工具栏再塞它会在 1100 宽窗口下被切字
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.lbl_state = QLabel("")
        self.lbl_state.setObjectName("VersionHint")
        foot.addWidget(self.lbl_state, 1)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("VersionHint")
        foot.addWidget(self.lbl_count)
        v.addLayout(foot)
        tip = QLabel("更新到选中版本 = git checkout --force（把工作区切到那条提交，不动 master 分支）。"
                     "ComfyUI 内核文件被插件改动过的话，这一步会一并还原。"
                     "「刷新版本列表」要联网，国内直连慢的话换上面的「镜像」。")
        tip.setObjectName("VersionHint")
        tip.setWordWrap(True)
        v.addWidget(tip)

        self.btn_cur.clicked.connect(self.refresh_current)
        self.btn_list.clicked.connect(self.fetch_and_reload)
        self.cmb_channel.currentIndexChanged.connect(lambda _i: self._reload_list())
        self.cmb_mirror.currentTextChanged.connect(self._on_mirror_changed)
        self.table.itemSelectionChanged.connect(self._on_pick)
        self.btn_update.clicked.connect(self.update_to_selected)

    # ---------- 进页 ----------
    def on_show(self):
        """切到本页：首次进来做完整加载（读 git 走后台，不卡界面）。"""
        _select_combo(self.cmb_mirror, CV.github_mirror())   # 别处改过镜像也要跟上
        if self._loaded:
            return
        self._loaded = True
        self._load_async()

    def _on_mirror_changed(self, name: str):
        """切换 GitHub 镜像：写进配置，下次「刷新版本列表」的 fetch 生效。"""
        cfgmod.set_echo("github_mirror", name)
        self.ctx.status(f"内核镜像已设为「{name}」——点「刷新版本列表」即用新镜像")

    # ---------- 基础 ----------
    def _root(self) -> str:
        root = self.ctx.root()
        d, err = CV.is_ready(root) if root else ("", "请先在「启动」页选择有效的 ComfyUI 包路径")
        if not d:
            self.lbl_path.setText("")
            self.lbl_py.setText("")
            self.lbl_cur.setText(err)
            self.lbl_cur.setStyleSheet(f"color: {style.DANGER}; font-weight: 600;")
            self.btn_cur.setEnabled(False)
            self.btn_list.setEnabled(False)
            self.btn_update.setEnabled(False)
            return ""
        self.lbl_cur.setStyleSheet("")
        return root

    def _load_async(self):
        """整块加载丢后台：`CV.current()` 要开 4 个 git 子进程（实测 1.65 s）。"""
        root = self._root()
        if not root:
            return
        from ..core import paths as _paths
        d, py = _paths.layout(root)
        self.lbl_path.setText(f"ComfyUI 路径：{d}")
        self.lbl_py.setText(f"Python 路径 ：{py or '（未找到 python.exe）'}")
        self.btn_cur.setEnabled(False)
        self.btn_list.setEnabled(False)
        self._state("正在读取内核版本…")

        def job():
            return (CV.current(root) or {},
                    CV.commits(root, CV.DEFAULT_REF, limit=120),
                    CV.latest_tag(root))

        w = self.ctx.spawn(Worker(job))

        def _done(res):
            self.btn_cur.setEnabled(True)
            self.btn_list.setEnabled(True)
            self._state("")
            self._apply_snapshot(*res)

        def _failed(m):
            self.btn_cur.setEnabled(True)
            self.btn_list.setEnabled(True)
            self._state(f"读取失败：{m.splitlines()[-1][:160]}")

        w.done.connect(_done)
        w.failed.connect(_failed)

    def _apply_snapshot(self, cur: dict, rows: list, tag: str):
        """把后台读到的快照填进界面 —— 全程只用已算好的数据，不再调 git。"""
        self.cur = cur or {}
        self._apply_current()
        self._sync_channel_combo(tag)
        self.rows = rows or []
        self._fill()
        self.lbl_count.setText(f"{len(self.rows)} 条提交 · {self._current_ref()}")

    def _state(self, text: str):
        self.lbl_state.setText(text)

    # ---------- 当前版本 ----------
    def refresh_current(self):
        root = self._root()
        if not root:
            return
        self.cur = CV.current(root) or {}
        self._apply_current()

    def _apply_current(self):
        """把 self.cur 渲染到「当前版本」那行（纯 UI，不调 git）。"""
        if not self.cur:
            self.lbl_cur.setText("读不到当前版本（仓库可能损坏）")
            return
        c = self.cur
        where = f"分支 {c['branch']}" if not c["detached"] else "游离 HEAD（不在任何分支上）"
        self.lbl_cur.setText(
            f"当前版本：{c['describe'] or c['short']}    "
            f"HEAD: {c['short']} [{c['date']}]    ·    {where}")
        self._refresh_mark()

    # ---------- 版本列表 ----------
    def fetch_and_reload(self):
        """联网拉取最新提交/标签，再重列。"""
        root = self._root()
        if not root:
            return
        self.btn_list.setEnabled(False)
        self.btn_cur.setEnabled(False)
        self._state("正在从 GitHub 拉取最新提交与标签…")

        def job():
            ok, msg = CV.fetch(root, "master")
            return ok, msg

        w = self.ctx.spawn(Worker(job))

        def _done(res):
            ok, msg = res
            self.btn_list.setEnabled(True)
            self.btn_cur.setEnabled(True)
            self._state(("已拉取最新版本列表" if ok else f"拉取失败：{str(msg)[:160]}"))
            self.refresh_current()
            self._reload_list()

        w.done.connect(_done)
        w.failed.connect(lambda m: (self.btn_list.setEnabled(True),
                                    self.btn_cur.setEnabled(True),
                                    self._state(f"拉取异常：{m.splitlines()[-1][:160]}")))

    def _sync_channel_combo(self, tag: str):
        """通道下拉：首次填充；以后只在出现新「正式版」标签时补一项。

        开发版 = origin/master；正式版 = 最新发布标签。
        """
        if self.cmb_channel.count() == 0:
            self.cmb_channel.blockSignals(True)
            self.cmb_channel.addItem("开发版（master 最新）", CV.DEFAULT_REF)
            if tag:
                self.cmb_channel.addItem(f"正式版（{tag}）", tag)
            self.cmb_channel.blockSignals(False)
        elif tag and "正式版" not in self.cmb_channel.itemText(
                self.cmb_channel.count() - 1):
            self.cmb_channel.blockSignals(True)
            self.cmb_channel.addItem(f"正式版（{tag}）", tag)
            self.cmb_channel.blockSignals(False)

    def _current_ref(self) -> str:
        data = self.cmb_channel.currentData()
        return data or CV.DEFAULT_REF

    def _reload_list(self):
        root = self._root()
        if not root:
            return
        need_tag = (self.cmb_channel.count() == 0 or "正式版" not in
                    self.cmb_channel.itemText(self.cmb_channel.count() - 1))
        self._sync_channel_combo(CV.latest_tag(root) if need_tag else "")
        ref = self._current_ref()
        self.rows = CV.commits(root, ref, limit=120)
        self._fill()
        self.lbl_count.setText(f"{len(self.rows)} 条提交 · {ref}")

    def _fill(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        cur_sha = (self.cur or {}).get("sha", "")
        for r, item in enumerate(self.rows):
            is_cur = bool(cur_sha) and cur_sha.startswith(item["short"])
            # 标记单独占一行（与 TE 一致）：塞在同一行会被列宽截成省略号
            sha_txt = item["short"] + ("\n（当前版本）" if is_cur else "")
            for c, text in enumerate([sha_txt, item["date"], item["subject"]]):
                it = QTableWidgetItem(text)
                if c == 0:
                    f = QFont(it.font())
                    f.setFamily("Cascadia Mono")
                    it.setFont(f)
                    it.setForeground(QColor(style.ACCENT if is_cur else style.TEXT_2))
                    if is_cur:
                        f2 = QFont(it.font())
                        f2.setBold(True)
                        it.setFont(f2)
                if c == 2:
                    it.setForeground(QColor(style.TEXT))
                self.table.setItem(r, c, it)
            self.table.setRowHeight(
                r, CUR_ROW_H if is_cur else self.table.verticalHeader().defaultSectionSize())
        self.table.blockSignals(False)
        self._refresh_mark()
        self.btn_update.setEnabled(bool(self.rows))

    def _refresh_mark(self):
        """当前版本被外部改动（比如刚更新完）后，重标「← 当前版本」。"""
        if not self.rows:
            return
        cur_sha = (self.cur or {}).get("sha", "")
        for r, item in enumerate(self.rows):
            it = self.table.item(r, 0)
            if it is None:
                continue
            is_cur = bool(cur_sha) and cur_sha.startswith(item["short"])
            txt = item["short"] + ("\n（当前版本）" if is_cur else "")
            if it.text() != txt:
                it.setText(txt)
            it.setForeground(QColor(style.ACCENT if is_cur else style.TEXT_2))
            f = QFont(it.font())
            f.setFamily("Cascadia Mono")
            f.setBold(is_cur)
            it.setFont(f)

    def _selected_sha(self) -> str:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows or rows[0] >= len(self.rows):
            return ""
        return self.rows[rows[0]]["short"]

    # ---------- 更新日志 ----------
    def _on_pick(self):
        sha = self._selected_sha()
        if not sha:
            return
        if sha in self._log_cache:
            self.log.setPlainText(self._log_cache[sha])
            return
        root = self.ctx.root()
        self._pick_seq += 1
        seq = self._pick_seq
        self.log.setPlainText("读取更新日志…")

        w = self.ctx.spawn(Worker(CV.show, root, sha))

        def _done(text):
            if seq != self._pick_seq:      # 用户已经点走了，丢弃过期结果
                return
            self._log_cache[sha] = text or "（这条提交没有说明）"
            self.log.setPlainText(self._log_cache[sha])

        w.done.connect(_done)
        w.failed.connect(lambda m: seq == self._pick_seq
                         and self.log.setPlainText(f"读取失败：{m.splitlines()[-1]}"))

    # ---------- 更新 ----------
    def update_to_selected(self):
        root = self._root()
        sha = self._selected_sha()
        if not root or not sha:
            QMessageBox.information(self, "提示", "先在下面选中要切换到的版本")
            return
        cur_short = self.cur.get("short", "")
        if sha == cur_short:
            QMessageBox.information(self, "提示", "选中的就是当前版本，不用更新")
            return
        changes = CV.local_changes(root)
        extra = ""
        if changes:
            shown = "\n".join("  " + c for c in changes[:6])
            more = f"\n  …还有 {len(changes) - 6} 项" if len(changes) > 6 else ""
            extra = (f"\n\n⚠ 工作区有 {len(changes)} 处本地改动，"
                     f"checkout --force 会把它们还原：\n{shown}{more}")
        if QMessageBox.question(
                self, "确认更新",
                f"把 ComfyUI 内核从 {cur_short or '当前版本'} 切到 {sha}？\n\n"
                f"方式：git checkout --force（游离 HEAD，不动 master 分支）{extra}"
        ) != QMessageBox.Yes:
            return

        self.btn_update.setEnabled(False)
        self.btn_list.setEnabled(False)
        self._state(f"正在切换到 {sha} …")

        w = self.ctx.spawn(Worker(CV.update_to, root, sha))

        def _done(res):
            ok, msg = res
            self.btn_update.setEnabled(True)
            self.btn_list.setEnabled(True)
            if ok:
                self._log_cache.clear()
                self.refresh_current()
                self._reload_list()
                self._state(f"已切换到 {sha}")
                QMessageBox.information(self, "完成", f"ComfyUI 内核已切换到 {sha}。")
            else:
                self._state("切换失败")
                QMessageBox.warning(self, "更新失败", str(msg)[:600])

        w.done.connect(_done)
        w.failed.connect(lambda m: (self.btn_update.setEnabled(True),
                                    self.btn_list.setEnabled(True),
                                    self._state("切换异常"),
                                    QMessageBox.warning(self, "更新异常", m[:600])))
