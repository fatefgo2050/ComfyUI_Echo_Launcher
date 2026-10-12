"""救援还原点对话框（Echo 的「救援模式」）。

一个还原点 = 插件目录的文件级备份 + `pip freeze` 环境快照。
出问题时可以一键回滚，可选连依赖环境一起还原。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog,
                               QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from .dialogs import MessageBox as QMessageBox

from ..core import rescue as rescuemod
from .workers import Worker

COLS = ["还原点", "创建时间", "包含插件", "备注"]


class RescueDialog(QDialog):
    def __init__(self, parent, ctx):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("救援还原点")
        self.resize(760, 460)

        v = QVBoxLayout(self)

        tip = QLabel("还原点包含：插件目录的文件级备份 + 环境快照（pip freeze）。\n"
                     "更新/切版本前会自动建一个；出问题可在这里一键回滚。")
        tip.setObjectName("VersionHint")
        tip.setWordWrap(True)
        v.addWidget(tip)

        bar = QHBoxLayout()
        btn_new = QPushButton("新建还原点（当前全部启用插件）")
        btn_new.setObjectName("Primary")
        btn_restore = QPushButton("还原选中")
        btn_restore.setObjectName("Ghost")
        btn_del = QPushButton("删除选中")
        btn_del.setObjectName("GhostDanger")
        btn_refresh = QPushButton("刷新")
        btn_refresh.setObjectName("Ghost")
        self.chk_env = QCheckBox("同时还原 Python 依赖环境")
        self.chk_env.setChecked(True)
        for w in (btn_new, btn_restore, btn_del, btn_refresh):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.chk_env)
        v.addLayout(bar)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        v.addWidget(self.table, 1)

        self.hint = QLabel("")
        self.hint.setObjectName("VersionHint")
        v.addWidget(self.hint)

        foot = QHBoxLayout()
        foot.addStretch(1)
        btn_close = QPushButton("关闭")
        foot.addWidget(btn_close)
        v.addLayout(foot)

        btn_new.clicked.connect(self._create)
        btn_restore.clicked.connect(self._restore)
        btn_del.clicked.connect(self._delete)
        btn_refresh.clicked.connect(self.reload)
        btn_close.clicked.connect(self.accept)

        self.reload()

    # ---------- 列表 ----------
    def reload(self):
        points = rescuemod.list_points()
        self.table.setRowCount(len(points))
        for r, p in enumerate(points):
            names = "、".join(p.plugins[:3]) or "(无)"
            if len(p.plugins) > 3:
                names += f" 等 {len(p.plugins)} 个"
            import time as _t
            when = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(p.created_at))
            for c, text in enumerate([p.id, when, names, p.note or ""]):
                it = QTableWidgetItem(text)
                if c == 0:
                    it.setData(Qt.UserRole, p.id)
                self.table.setItem(r, c, it)
        self.hint.setText(f"共 {len(points)} 个还原点；备份实体在 data/rescue/<还原点 ID>/")

    def _selected_ids(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        out = []
        for r in rows:
            it = self.table.item(r, 0)
            if it:
                out.append(it.data(Qt.UserRole) or it.text())
        return out

    # ---------- 操作 ----------
    def _create(self):
        root = self.ctx.root()
        if not root:
            QMessageBox.information(self, "提示", "请先在「启动」页选择包路径")
            return
        self.hint.setText("正在创建还原点（备份插件目录 + 导出环境快照）...")
        w = self.ctx.spawn(Worker(rescuemod.create, root, None, "手动创建"))

        def _done(point):
            if point is None:
                QMessageBox.warning(self, "失败", "创建还原点失败，详见控制台/日志")
            else:
                self.ctx.status(f"还原点 {point.id} 已创建（{len(point.plugins)} 个插件）")
            self.reload()

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "创建失败", m))

    def _restore(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "先在表格里选中要还原的还原点")
            return
        rid = ids[0]
        env = self.chk_env.isChecked()
        ans = QMessageBox.question(
            self, "确认还原",
            f"将还原到 {rid}。\n\n"
            "会用备份覆盖同名插件目录（当前内容被覆盖，不可撤销）。\n"
            + ("并会按快照重装 Python 依赖。\n" if env else "")
            + "\n确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        self.hint.setText(f"正在还原 {rid} ...")
        w = self.ctx.spawn(Worker(rescuemod.restore, rid, env))

        def _done(errors):
            for e in errors:
                self.ctx.status(e)
            QMessageBox.information(
                self, "完成",
                "已还原" if not errors else f"还原完成，但有 {len(errors)} 个错误（见状态栏/日志）")
            self.ctx.status(f"还原 {rid} 完成")
            self.reload()

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "还原失败", m))

    def _delete(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "先选中要删除的还原点")
            return
        if QMessageBox.question(self, "确认删除",
                                f"删除 {len(ids)} 个还原点及其备份文件？") != QMessageBox.Yes:
            return
        for rid in ids:
            rescuemod.delete(rid)
        self.ctx.status(f"已删除 {len(ids)} 个还原点")
        self.reload()
