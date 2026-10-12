"""模型 / LoRA 管理器页：扫描、**底模/用途分类**、查重、备注、Civitai 反查。

后端走 Echo 的 `model/*`：SQLite 四表（notes / file_hashes / civitai_matches /
civitai_prefs）+ Civitai 公开接口按 sha256 反查。

⚠️ 修掉 Echo 原实现的两处问题：
1. `ModelFile` 没有 `name` 属性，批量匹配里写 `f.name[:30]` 会直接 AttributeError。
2. 批量匹配的 `threading.Thread(...).start()` 被写了两遍，等于并发跑两轮，
   既双倍耗时又会重复打 C 站接口。这里只跑一次。

**底模 / 用途两列**（2026-10-11 新增）：探测逻辑全在 `core/modelmeta.py`
（只读文件头、不加载张量、不联网）。这一页只负责：
扫描完 → 后台跑探测（`probe_all`，实测 234 个文件 0.7 秒）→ 回填两列。
⚠ 探测**不用按钮触发**：它在 Worker 里跑，界面不卡，没必要让用户多点一下。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter,
                               QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .dialogs import MessageBox as QMessageBox

from ..core import model as MODEL
from ..core import modelmeta as MM
from ..core import paths as _paths
from ..ui import style
from .widgets import TableEmptyHint, page_header
from .workers import Worker

#: 列。⚠ 「底模」「用途」插在「模型」后面：它们是最常用来找人/筛的，
#: 放右边会被「备注」挤到看不见。固定列的宽度总和要留够「模型」列 —— `ui_min_size` 会量。
COLS = ["模型", "底模", "用途", "类型", "大小", "备注", "Civitai"]
_ALL = "全部"

#: 搜索框的「边打边过滤」去抖时长（毫秒）。和市场页 / 媒体页**同值** ——
#: 三页行为不一致最容易被当成 bug。⚠ 必须去抖：一次输入要重排整张表。
SEARCH_DEBOUNCE_MS = 200


class ModelPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.files = []          # 当前**可见**的行（过滤后的）
        self._all = []           # 扫描到的全部
        self._meta = {}          # {rel_path: 探测结果行}
        self._root = ""

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        page_header(v, "模型管理",
                    "扫描 models 目录、按底模/用途分类、查重、备注、Civitai 反查")

        # ---- 第一排：找（刷新 / 关键词 / 底模筛 / 用途筛）----
        # 工具行原来 9 个控件挤一行，再加上新的两列两筛必然放不下 ——
        # 按项目规矩拆成两排（判断标准是 tests/ui_min_size.py）。
        bar = QHBoxLayout()
        bar.setSpacing(6)
        btn_refresh = QPushButton("刷新")
        btn_refresh.setObjectName("Primary")
        self.edit_kw = QLineEdit()
        self.edit_kw.setPlaceholderText("按路径/备注/底模/用途搜索")
        self.edit_kw.setMaximumWidth(240)
        self.edit_kw.setToolTip("边打边过滤，输入即出结果；回车或「搜索」同样触发（大小写不敏感）")
        btn_search = QPushButton("搜索")
        btn_search.setObjectName("Ghost")
        self.combo_family = QComboBox()
        self.combo_family.setToolTip("只看某个底模家族（探测出来的，可手改）")
        self.combo_purpose = QComboBox()
        self.combo_purpose.setToolTip("只看某个用途（加速 / 检测 / 放大 …）")
        for cb in (self.combo_family, self.combo_purpose):
            # ⚠ 下拉里可能出现很长的值，不锁短的话 sizeHint 会把这一排撑爆
            cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(8)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("VersionHint")
        for w in (btn_refresh, self.edit_kw, btn_search):
            bar.addWidget(w)
        # 筛选下拉前面加个短标签 —— 两个框都显示「全部」时，不标一下分不清哪个是哪个
        bar.addWidget(QLabel("底模"))
        bar.addWidget(self.combo_family)
        bar.addWidget(QLabel("用途"))
        bar.addWidget(self.combo_purpose)
        bar.addStretch(1)
        bar.addWidget(self.lbl_status)
        v.addLayout(bar)

        # ---- 第二排：工具 ----
        bar2 = QHBoxLayout()
        bar2.setSpacing(6)
        btn_dup_size = QPushButton("查重(按大小)")
        btn_dup_hash = QPushButton("查重(哈希)")
        btn_match = QPushButton("Civitai 匹配")
        btn_batch = QPushButton("批量 Civitai 匹配")
        self.btn_export = QPushButton("导出 CSV")
        btn_export = self.btn_export
        btn_open = QPushButton("打开目录")
        for b in (btn_dup_size, btn_dup_hash, btn_match, btn_batch,
                  btn_export, btn_open):
            b.setObjectName("Ghost")
            bar2.addWidget(b)
        bar2.addStretch(1)
        v.addLayout(bar2)

        # ⚠ 「手改底模 / 用途」那两个框**不在这里** —— 放到表格下面的编辑区了（见下）。
        #   原来它们占工具栏第三排，和第一排的筛选下拉**同名、同宽、长得一模一样**，
        #   分不清哪个是"筛"哪个是"设"（用户 2026-10-11 指出这是重复）。
        #   挪到表格下面之后：「表格下面是编辑选中行」这层语义立得住，
        #   工具栏回到两排、控件总行数不变，重复也消失了。

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c, w in ((1, 104), (2, 88), (3, 84), (4, 84), (5, 190), (6, 96)):
            self.table.setColumnWidth(c, w)
        v.addWidget(self.table, 1)
        self.table_hint = TableEmptyHint(
            self.table, "点「刷新」扫描 models 目录；扫描后可按底模 / 用途筛选")

        # ---- 编辑区（表格下面，**一排**）：改写选中的行 ----
        # 顺序 = 底模 / 用途 / 应用到选中 ‖ 备注 / 保存备注 / 设置预览图 / 缩略图。
        # 原来拆成两排，右半边全是空的；并成一排之后按钮还能放大（用户 2026-10-11 要求）。
        # ⚠ 「底模 / 用途」不加 QLabel 前缀 —— 两个框的 placeholder 已经自带说明，
        #   加标签纯占宽度（并排时一行要装 12 个控件）。
        # ⚠ 这里用 QLineEdit + QCompleter，**不用可编辑的 QComboBox**：
        #   可编辑 combo 内部那个 QLineEdit 的 sizeHint 固定、且跟着 combo 一起被限宽，
        #   必然过不了 `ui_min_size` 的「控件宽 >= sizeHint」检查（实测被它抓了 2 处）。
        #   补全器一样能"下拉选 + 手打新值"，而且没有这个内部框。
        edit_row = QHBoxLayout()
        edit_row.setSpacing(6)
        self.edit_set_family = QLineEdit()
        self.edit_set_family.setPlaceholderText("底模，如 Illustrious / Anima")
        self.edit_set_family.setMaximumWidth(170)
        self.edit_set_family.setToolTip(
            "选中若干行（可 Ctrl/Shift 多选，或先筛选再 Ctrl+A）后，点右边的「应用到选中」写入。\n"
            "可以手打新值。写入后会被标记为「手动」，自动探测不会再覆盖。")
        self.edit_set_purpose = QLineEdit()
        self.edit_set_purpose.setPlaceholderText("用途，如 加速 / 角色")
        self.edit_set_purpose.setMaximumWidth(150)
        self.edit_set_purpose.setToolTip(self.edit_set_family.toolTip())
        btn_apply = QPushButton("应用到选中")
        btn_apply.setObjectName("GhostLg")      # 「大号 Ghost」= 和 #Primary 同高，见 style.py
        btn_apply.setToolTip("把左边两个框里的底模 / 用途写到选中的行")
        edit_row.addWidget(self.edit_set_family)
        edit_row.addWidget(self.edit_set_purpose)
        edit_row.addWidget(btn_apply)
        edit_row.addSpacing(14)                 # 两组之间留一道缝，别糊成一串

        edit_row.addWidget(QLabel("备注"))
        self.edit_note = QLineEdit()
        self.edit_note.setPlaceholderText("给选中的模型写一句备注（点右边「保存备注」入库）")
        btn_save = QPushButton("保存备注")
        btn_save.setObjectName("Primary")
        btn_save.setToolTip("把左边这句备注存进 SQLite（只对当前选中的那一个）")
        # ⚠ 这里曾经有个右下角缩略图 + 「设置预览图」按钮（原名「选图片备注」），
        #   2026-10-11 按用户要求**整块删掉**（先删按钮，再删缩略图）。
        #   相关的东西（`_load_thumb` 缓存 / `_find_sidecar_image` 同名图查找 / `_show_image`）
        #   也一并删了 —— ⚠ **`note_image` 字段还留在库里**（`save_note` 会把它原样存回），
        #   只是不再有界面去设置/显示它。哪天想恢复，`_find_sidecar_image` 的思路是：
        #   找 `<模型同名>.png`，实测 loras 里 110/110 都有；但注意预览图最大的有
        #   2496×4440（整图解码 127ms/42MB），**必须缓存**，别直接 QPixmap(path)。
        edit_row.addWidget(self.edit_note, 1)
        edit_row.addWidget(btn_save)
        v.addLayout(edit_row)

        btn_refresh.clicked.connect(self.refresh)
        # ⚠ 「搜索」/回车 **只重筛，不重扫**：关键词过滤是纯内存操作（`_apply_filters`），
        #   以前它们和「刷新」连的是同一个 `refresh()`，按一下搜索就重走一遍
        #   `MODEL.scan()`（遍历整个 models 目录）+ 探测流程 —— 既是浪费，
        #   也让「搜索」和「刷新」变成两个一模一样的按钮（用户 2026-10-11 指出重复）。
        btn_search.clicked.connect(self._apply_filters)
        self.edit_kw.returnPressed.connect(self._apply_filters)
        # 边打边过滤（和市场页 / 媒体页同一套）：停手 SEARCH_DEBOUNCE_MS 之后自动重筛一次。
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_filters)
        self.edit_kw.textChanged.connect(lambda _t: self._search_timer.start())
        for cb in (self.combo_family, self.combo_purpose):
            cb.currentIndexChanged.connect(lambda _i: self._apply_filters())
        btn_apply.clicked.connect(self.apply_to_selected)
        btn_dup_size.clicked.connect(self.find_dup_size)
        btn_dup_hash.clicked.connect(self.find_dup_hash)
        btn_match.clicked.connect(self.match_civitai)
        btn_batch.clicked.connect(self.batch_match)
        btn_export.clicked.connect(self.export_csv)
        btn_open.clicked.connect(self.open_dir)
        btn_save.clicked.connect(self.save_note)
        self.table.itemSelectionChanged.connect(self.on_select)

    # ---------- 工具 ----------
    def _models_dir(self) -> str:
        root = self.ctx.root()
        return _paths.dirs(root).get("models", "") if root else ""

    def _require(self) -> str:
        d = self._models_dir()
        if not d or not os.path.isdir(d):
            QMessageBox.information(self, "提示", "请先选择有效的 ComfyUI 包路径")
            return ""
        self._root = d
        return d

    def current(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.files):
            return None
        return self.files[row]

    def _selected_files(self) -> list:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.files[r] for r in rows if 0 <= r < len(self.files)]

    def _meta_of(self, f) -> dict:
        return self._meta.get(f.rel_path) or {}

    # ---------- 数据 ----------
    def refresh(self):
        d = self._require()
        if not d:
            return
        try:
            self._all = MODEL.scan(d)
        except Exception as e:
            QMessageBox.warning(self, "扫描失败", str(e))
            return
        self._meta = MM.load_all(d)
        self._fill_choices()
        self._apply_filters()
        self.ctx.status(f"模型扫描完成：{len(self._all)} 个，正在分析底模…")
        self._spawn_probe()

    def _spawn_probe(self):
        """后台探测底模/用途。⚠ 不用按钮触发 —— 实测 234 个文件 0.7 秒，没必要让用户点。"""
        if not self._all:
            return
        root, files = self._root, list(self._all)

        def job(report):
            return MM.probe_all(root, files, report=report)

        w = self.ctx.spawn(Worker(job, on_line=lambda s: self.ctx.status("分析 " + s)))

        def _done(n):
            self._meta = MM.load_all(root)
            self._fill_choices()
            self._apply_filters()
            if n:
                self.ctx.status(f"底模分析完成：新分析 {n} 个 / 共 {len(self._all)} 个")

        w.done.connect(_done)
        w.failed.connect(lambda m: self.ctx.status("底模分析失败：" + m.strip().splitlines()[-1][:80]))

    def _apply_filters(self):
        # 显式触发（搜索按钮 / 回车 / 筛选下拉 / 刷新…）先取消挂起的「边打边过滤」——
        # 否则"打完字立刻回车"会重筛两次。⚠ 媒体页踩过同一个坑。
        t = getattr(self, "_search_timer", None)
        if t is not None:
            t.stop()
        kw = (self.edit_kw.text() or "").strip().lower()
        fam = self.combo_family.currentText()
        pur = self.combo_purpose.currentText()
        out = []
        for f in self._all:
            m = self._meta_of(f)
            if fam and fam != _ALL and (m.get("family") or "") != fam:
                continue
            if pur and pur != _ALL and (m.get("purpose") or "") != pur:
                continue
            if kw:
                hay = " ".join((f.rel_path, f.text_note,
                                m.get("family") or "", m.get("purpose") or "",
                                m.get("arch") or "", m.get("detail") or "")).lower()
                if kw not in hay:
                    continue
            out.append(f)
        self.files = out
        self._fill()
        total = sum(f.size for f in self.files) / 1073741824
        self.lbl_status.setText(f"{len(self.files)} 个模型 / {total:.1f} GB")

    def _fill_choices(self):
        """筛选/手改的候选项（保留当前选择）。

        手改那两个框走 `QCompleter` 而不是可编辑 combo —— 见第三排那段注释。
        补全器每次重建（候选值本来就会随探测结果变），父对象给输入框本身免得被 GC。
        """
        rows = list(self._meta.values())
        fams = MM.family_options(rows)
        purs = MM.purpose_options(rows)
        for cb, vals in ((self.combo_family, fams), (self.combo_purpose, purs)):
            cur = cb.currentText()
            cb.blockSignals(True)
            cb.clear()
            cb.addItem(_ALL)
            cb.addItems(vals)
            cb.setCurrentText(cur if cur and cb.findText(cur) >= 0 else _ALL)
            cb.blockSignals(False)
        for ed, vals in ((self.edit_set_family, fams), (self.edit_set_purpose, purs)):
            c = QCompleter(vals, ed)
            c.setCaseSensitivity(Qt.CaseInsensitive)
            ed.setCompleter(c)

    def _fill(self):
        self.table.setRowCount(len(self.files))
        for r, f in enumerate(self.files):
            m = self._meta_of(f)
            tip = self._probe_tip(f, m)
            vals = [f.rel_path, m.get("family") or "", m.get("purpose") or "",
                    f.kind, f"{f.size_mb:.0f} MB", f.text_note[:60],
                    f"已匹配 #{f.civitai_id}" if f.civitai_id else ""]
            for c, text in enumerate(vals):
                it = QTableWidgetItem(text)
                # 底模 / 用途两格都挂同一份「推断依据」，免得信息只在一个格子里找得到
                it.setToolTip(tip if c in (1, 2) else (f.abs_path if c == 0 else ""))
                if c == 6 and text:
                    it.setForeground(QColor(style.SUCCESS))
                elif c in (1, 2) and text:
                    it.setForeground(QColor(style.MUTED if m.get("probe_src") == "user"
                                            else style.TEXT))
                self.table.setItem(r, c, it)
        self.table_hint.refresh()
        self.on_select()

    def _probe_tip(self, f, m) -> str:
        """把「推断依据」摊在 tooltip 里 —— 让探测结果可见、可校对。"""
        if not m:
            return "尚未探测"
        src = {"header-meta": "文件头 __metadata__", "tensor-fingerprint": "张量形状指纹",
               "torch-pickle": "torch 存档（不用 torch 读的）", "filename": "文件名/目录",
               "user": "手动指定"}.get(m.get("probe_src") or "", m.get("probe_src") or "—")
        return "\n".join(filter(None, [
            f"底模：{m.get('family') or '（未判定）'}",
            f"用途：{m.get('purpose') or '（未判定）'}",
            f"架构：{m.get('arch') or '—'}    格式：{m.get('fmt') or '—'}",
            f"rank/variant：{m.get('variant')}" if m.get("variant") else "",
            m.get("detail") or "",
            f"判定依据：{src}",
        ]))

    def on_select(self):
        f = self.current()
        if not f:
            return
        self.edit_note.setText(f.text_note)
        m = self._meta_of(f)
        if m:
            self.ctx.status(f"{f.rel_path} —— 底模 {m.get('family') or '未判定'} / "
                            f"用途 {m.get('purpose') or '未判定'} / 架构 {m.get('arch') or '—'}")

    # ---------- 手改底模 / 用途 ----------
    def apply_to_selected(self):
        files = self._selected_files()
        if not files:
            QMessageBox.information(self, "提示", "先在表格里选中若干行（可 Ctrl / Shift 多选、"
                                                 "或先筛选再 Ctrl+A）")
            return
        fam = self.edit_set_family.text().strip()
        pur = self.edit_set_purpose.text().strip()
        failed = []
        for f in files:
            # ⚠ 只有**写进库了**才更新界面：以前无论成败都改 `self._meta`，
            #   数据库写失败时表格会一直显示新值，刷新一下又变回去 —— 等于骗用户。
            if MM.set_user(self._root, f.rel_path, fam, pur):
                row = self._meta.setdefault(f.rel_path, {})
                row.update({"family": fam, "purpose": pur, "probe_src": "user"})
            else:
                failed.append(f.rel_path)
        if failed:
            QMessageBox.warning(
                self, "写入失败",
                "有 %d 个没写进库（界面不会显示它们的新值）：\n%s"
                % (len(failed), "\n".join(failed[:8])))
            self.ctx.status(f"写入失败 {len(failed)} 个 / 共 {len(files)} 个")
        else:
            self.ctx.status(f"已把 {len(files)} 行的底模/用途改为："
                            f"{fam or '(空)'} / {pur or '(空)'}")
        self._fill_choices()
        self._apply_filters()

    # ---------- 操作 ----------
    def save_note(self):
        f = self.current()
        if not f:
            return
        text = self.edit_note.text().strip()
        MODEL.set_note(self._root, f.rel_path, text, f.note_image)
        f.text_note = text
        self.ctx.status(f"已保存备注：{f.rel_path}")
        self._apply_filters()

    def open_dir(self):
        f = self.current()
        if not f:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(f.abs_path)))

    def _scope_note(self) -> str:
        """当前是不是只在"筛选后的子集"上操作。

        ⚠ 查重 / 批量匹配 / 导出都只跑**可见行**。不说清楚的话，用户筛了个关键词再点查重，
        得到「没有发现重复」会以为整个库都没重复 —— 而看不见的那些被静默跳过了。
        """
        if self._all and len(self.files) != len(self._all):
            return f"\n\n⚠ 当前有筛选：只处理可见的 {len(self.files)} / 共 {len(self._all)} 个。"
        return ""

    def export_csv(self):
        if not self._require():
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "导出模型清单", "model_inventory.csv", "CSV 文件 (*.csv)")
        if not dest:
            return
        try:
            path = MODEL.export_csv(self._root, dest, self.files)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        self.ctx.status(f"模型清单已导出：{path}")
        QMessageBox.information(self, "导出完成",
                                f"已导出 {len(self.files)} 个模型。\n\n{path}"
                                + self._scope_note())

    def find_dup_size(self):
        if not self._require():
            return
        groups = MODEL.duplicates_by_size(self._root, self.files)
        if not groups:
            QMessageBox.information(self, "查重",
                                    "没有发现同尺寸的重复文件" + self._scope_note())
            return
        lines = []
        for items in list(groups.values())[:12]:
            lines.append(f"{len(items)} 个（{items[0].size_mb:.0f} MB each）：")
            lines.append("  " + ", ".join(i.rel_path for i in items[:4]))
        QMessageBox.information(self, f"疑似重复 {len(groups)} 组",
                                "\n".join(lines)[:2500] + self._scope_note())

    def find_dup_hash(self):
        if not self._require():
            return
        if QMessageBox.question(
                self, "哈希查重",
                f"将计算 {len(self.files)} 个文件的 SHA256，大文件较慢，继续？"
                + self._scope_note()) != QMessageBox.Yes:
            return

        def job(report):
            def _rep(i, total, f):
                report(f"[{i}/{total}] {f.rel_path}")
            MODEL.hash_all(self._root, self.files, report=_rep)
            return MODEL.duplicates(self._root, self.files)

        w = self.ctx.spawn(Worker(job, on_line=lambda s: self.ctx.status(s)))

        def _done(groups):
            if not groups:
                QMessageBox.information(self, "哈希查重", "没有完全重复的文件")
            else:
                lines = [f"{sha[:12]}  {len(items)} 个：" +
                         ", ".join(i.rel_path for i in items[:3])
                         for sha, items in groups.items()]
                QMessageBox.information(self, f"重复 {len(groups)} 组",
                                        "\n".join(lines)[:2500] + self._scope_note())
            self.refresh()

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "哈希失败", m))

    def match_civitai(self):
        f = self.current()
        if not f:
            return
        self.ctx.status("正在查询 Civitai…")

        def job():
            return MODEL.match_one(self._root, f)

        w = self.ctx.spawn(Worker(job))

        def _done(res):
            if not res.get("ok"):
                QMessageBox.information(self, "Civitai",
                                        res.get("error", "匹配失败"))
                self.refresh()
                return
            info = res.get("info") or {}
            text = "\n".join(filter(None, [
                f"名称：{info.get('name', '')}",
                f"底模：{info.get('base_model', '')}",
                f"触发词：{', '.join(info.get('trained_words') or []) or '(无)'}",
                "", (info.get("description") or "")[:400],
            ]))
            self.ctx.status(f"Civitai 匹配成功：{info.get('name', '')}")
            QMessageBox.information(self, "Civitai 匹配结果", text)
            self._fill()

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "匹配失败", m))

    def batch_match(self):
        if not self._require():
            return
        files = list(self.files)
        if not files:
            QMessageBox.information(self, "提示", "没有可匹配的模型，请先刷新")
            return
        if QMessageBox.question(
                self, "批量 Civitai 匹配",
                f"将对 {len(files)} 个模型逐个计算哈希并查询 Civitai\n"
                f"（大文件较慢，公开接口有速率限制）。继续？"
                + self._scope_note()) != QMessageBox.Yes:
            return

        def job(report):
            def _rep(i, total, f):
                report(f"[{i}/{total}] {f.rel_path}")
            return MODEL.match_batch(self._root, files, report=_rep)

        w = self.ctx.spawn(Worker(job, on_line=lambda s: self.ctx.status(s)))

        def _done(res):
            self.ctx.status(f"批量完成：匹配 {res['ok']} / 跳过 {res['skip']} / 失败 {res['fail']}")
            QMessageBox.information(
                self, "批量 Civitai 匹配",
                f"匹配成功 {res['ok']} 个，跳过 {res['skip']} 个，失败 {res['fail']} 个")
            self.refresh()

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "批量匹配失败", m))

    def on_show(self):
        if not self._all and self._models_dir():
            self.refresh()
