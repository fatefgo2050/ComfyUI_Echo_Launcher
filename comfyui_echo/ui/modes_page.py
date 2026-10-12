"""模式页：工作流 / 插件 / 模式 —— 三栏，一条链路走完。

TE 启动器的「启动模式」有两种：**默认**（加载全部插件）和**按工作流新建**
（选一份工作流，只加载它用得到的插件）。本页把这两件事合成一屏三栏：

    左：可用工作流        —— 可多选（跑多份工作流就取并集）
    中：可用插件          —— 选中的工作流用得到的插件高亮
                            绿 = 已在当前模式（本次启动会启用）
                            橙 = 工作流需要、但还没加进当前模式
    右：当前模式插件      —— 本次启动只启用这些；缺失的插件就地备注

所以「工作流识别」不再单独占一页 —— 分析出结果却要跳到另一页才能用，
就是之前「工作流和模式分开、没办法合在一起使用」的根源。

模式 = 插件启用白名单。应用时白名单内的保持启用，其余就地重命名为
`<插件名>.disabled`（ComfyUI 原生跳过该目录）；「默认模式」白名单为空 = 不干预。

规避过的坑
----------
- 中栏高亮**只以 `self._wf_needed` 为准**、右栏内容**只以 `self._checked` 为准**，
  两边都不读控件状态：中栏带搜索框，被过滤掉的行读不到，一搜索就会丢状态。
- 「是否已加入模式」的判断全是集合运算，**不要**去解析 item 文本
  （右栏文本带「（缺失…）」后缀，反解必错）。
- 工作流分析要跑 AST 扫描 + 可能联网，一律走后台 Worker，不阻塞界面；
  用 `self._seq` 令牌丢弃过期结果（快速连点/框选时）。
- 工作流树用 `setRootIsDecorated(False)`：**不画展开三角**（用户嫌「全是箭头」），
  子树永远摊开，文件夹行靠灰字当小标题看；树的选中态必须 `border-radius: 0`，
  否则圆角会露出「四个带颜色的角」。
- 工作流**多选**时不要每变一次选择就跑分析：框选会连发几十次
  `itemSelectionChanged`，所以走 `self._wf_timer` 去抖 250ms。
"""
import os

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QFileDialog, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .dialogs import MessageBox as QMessageBox

from ..core import nodemap, paths, plugins
from ..ui import style
from .workers import Worker

DEFAULT_MODE = "默认模式"
UNSAVED_LABEL = "（未保存的工作集）"
WF_DEBOUNCE_MS = 250


def _tidy_view(view):
    """长名字不要撑出横向滚动条 —— 省略号收尾就好，别让用户去拖。"""
    view.setTextElideMode(Qt.ElideRight)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)


class _WfTree(QTreeWidget):
    """工作流树：**单击 = 选中 / 再单击 = 取消**，不用按住 Ctrl。

    默认的 `ExtendedSelection` 要求用户按 Ctrl 才能多选，这里把左键单击接管成
    「切换」语义，挑几份工作流就是连着点几下。按住 **Shift** 仍然走 Qt 原生的
    范围选择（用户说过这个手感不错，保留）。
    点空白 = 全不选，等于快速重挑。
    """

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton or (ev.modifiers() & Qt.ShiftModifier):
            super().mousePressEvent(ev)
            return
        self.setFocus(Qt.MouseFocusReason)
        it = self.itemAt(ev.position().toPoint())
        if it is None:
            self.clearSelection()
        elif it.flags() & Qt.ItemIsSelectable:
            it.setSelected(not it.isSelected())
            # 同时把 currentIndex 挪过来：Qt 原生 Shift 范围选择拿 currentIndex 当锚点，
            # 不设的话锚点会回退到第一行，Shift 连选的范围就不是用户以为的那一段。
            # NoUpdate = 只挪锚点，别顺手改动当前的选择集。
            self.selectionModel().setCurrentIndex(
                self.indexFromItem(it), QItemSelectionModel.NoUpdate)
        ev.accept()


def _panel(title: str, actions=None):
    """三栏面板：`QFrame#Card` + 可变标题 + 标题行右端的迷你按钮（可多个）。

    返回 `(frame, 内容 layout, 标题 QLabel)` —— 拿得到标题才能改「（16 个）」这种计数文案。
    """
    frame = QFrame()
    frame.setObjectName("Card")
    v = QVBoxLayout(frame)
    v.setContentsMargins(12, 10, 12, 12)
    v.setSpacing(8)
    head = QHBoxLayout()
    head.setContentsMargins(0, 0, 0, 0)
    head.setSpacing(6)
    lab = QLabel(title)
    lab.setObjectName("CardTitle")
    head.addWidget(lab)
    head.addStretch(1)
    if actions:
        if not isinstance(actions, (list, tuple)):
            actions = [actions]
        for a in actions:
            head.addWidget(a)
    v.addLayout(head)
    return frame, v, lab


class ModesPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._checked = set()       # 右栏工作集 = 当前模式的插件白名单
        self._wf_needed = set()     # 选中工作流需要的插件（中栏高亮依据）
        self._all_plugins = []      # [(名字, 路径, 是否启用)]
        self._result = None         # nodemap.merge_plugin_sets() 的结果
        self._wf_files = []         # 扫描到的工作流文件全路径
        self._wf_paths = []         # 当前选中的工作流（可多个）
        self._extmap = {}
        self._seq = 0               # 分析令牌，丢弃过期结果
        self._analyzing = False     # 是否已有分析在跑（同一时刻只允许一个）
        self._pending_wf_paths = None   # 分析期间又来了新选择 -> 记下来，等它结束再跑
        self._cur_mode = DEFAULT_MODE
        self._dirty = False

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        splitter = QSplitter(Qt.Horizontal)
        p1 = self._build_wf_panel()
        p2 = self._build_plugin_panel()
        p3 = self._build_mode_panel()
        # 三栏各自的「最宽一行」不一样（左：三个目录/扫描按钮；中：标题行的添加按钮；
        # 右：安装缺失那行），用最小宽度锁住，免得 1100 宽的窗口下被切字
        for w, mn in ((p1, 272), (p2, 267), (p3, 302)):
            w.setMinimumWidth(mn)
        for i in range(3):
            splitter.setStretchFactor(i, 1)
        splitter.addWidget(p1)
        splitter.addWidget(p2)
        splitter.addWidget(p3)
        splitter.setSizes([330, 300, 330])
        v.addWidget(splitter, 1)

        # 工作流多选去抖：框选会连发几十次 itemSelectionChanged
        self._wf_timer = QTimer(self)
        self._wf_timer.setSingleShot(True)
        self._wf_timer.setInterval(WF_DEBOUNCE_MS)
        self._wf_timer.timeout.connect(self._on_wf_selection_settled)

        # ---------------- 接线 ----------------
        self.wf_search.textChanged.connect(lambda _t: self._fill_wf_tree())
        self.wf_tree.itemSelectionChanged.connect(self._wf_timer.start)
        self.btn_wf_scan.clicked.connect(self.reload_workflows)
        self.btn_wf_default.clicked.connect(self._open_wf_dir)
        self.btn_wf_external.clicked.connect(self._analyze_external)

        self.plug_search.textChanged.connect(lambda _t: self._refresh_plugin_list())
        self.plug_list.itemDoubleClicked.connect(lambda _i: self._add_selected_plugins())
        self.btn_plug_add.clicked.connect(self._add_selected_plugins)
        self.btn_plug_default.clicked.connect(self._open_plugin_dir)
        self.btn_plug_refresh.clicked.connect(self._refresh_all)
        self.btn_install_missing.clicked.connect(self._install_missing)

        self.mode_combo.currentIndexChanged.connect(self._on_mode_picked)
        self.btn_mode_new.clicked.connect(self._new_mode)
        self.btn_mode_save.clicked.connect(self._save_mode)
        self.btn_mode_rename.clicked.connect(self._rename_mode)
        self.btn_mode_del.clicked.connect(self._del_mode)
        self.btn_apply_now.clicked.connect(self._apply_now)
        self.btn_resync.clicked.connect(self._resync_from_workflow)
        self.btn_capture.clicked.connect(self._capture_current)
        self.btn_enable_all.clicked.connect(self._enable_all)
        self.mode_list.itemDoubleClicked.connect(lambda _i: self._remove_selected())
        self.btn_mode_remove.clicked.connect(self._remove_selected)
        self.btn_mode_clear.clicked.connect(self._remove_all)
        self.chk_apply.toggled.connect(self._apply_toggled)

        self.reload()

    # ------------------------------------------------------------------
    # 三栏构建
    # ------------------------------------------------------------------
    def _build_wf_panel(self):
        self.btn_wf_external = QPushButton("分析外部")
        self.btn_wf_external.setObjectName("CardAction")
        self.btn_wf_external.setToolTip("分析搜索范围之外的一份工作流 JSON")
        frame, wv, self.lbl_wf_count = _panel("可用工作流", [self.btn_wf_external])

        self.wf_search = QLineEdit()
        self.wf_search.setPlaceholderText("搜索工作流…")
        wv.addWidget(self.wf_search)

        self.wf_tree = _WfTree()
        self.wf_tree.setHeaderHidden(True)
        self.wf_tree.setUniformRowHeights(True)
        self.wf_tree.setRootIsDecorated(False)      # 不画展开三角
        self.wf_tree.setItemsExpandable(False)      # 子树永远摊开
        self.wf_tree.setExpandsOnDoubleClick(False)
        self.wf_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.wf_tree.setToolTip("单击 = 选中，再单击 = 取消（不用按 Ctrl）；"
                                "按住 Shift 连选一段；点空白 = 全不选。\n"
                                "选多份工作流时，白名单取它们所需的并集。")
        _tidy_view(self.wf_tree)
        wv.addWidget(self.wf_tree, 1)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.btn_wf_default = QPushButton("默认目录")
        self.btn_wf_default.setObjectName("Mini")
        self.btn_wf_default.setToolTip("打开 ComfyUI 自带的工作流目录"
                                       "（user/default/workflows，本来就在搜索范围里）")
        self.btn_wf_adddir = QPushButton("自定义目录")
        self.btn_wf_adddir.setObjectName("Mini")
        # 单击 = 直接选目录并**替换**：旧目录立刻移出搜索范围，它的工作流随即消失。
        # 没有下拉菜单、也没有右键菜单 —— 用户明确不要「展开所选目录的记录」这类入口，
        # 「自定义目录」就只剩「一键替换」这一种操作。
        self.btn_wf_adddir.clicked.connect(self._choose_wf_dir)
        self._sync_wf_dir_tip()
        self.btn_wf_scan = QPushButton("扫描工作流")
        self.btn_wf_scan.setObjectName("Mini")
        # 底部工具条：三栏**统一成左对齐按钮簇**，同一基线、同一节奏（2026-10-12）。
        # 原来左栏把「扫描工作流」甩到最右，中/右栏却是左侧簇 —— 三栏形状不一致。
        # 按钮总宽不变（都是 3 个 Mini + 2×6px 间距），不增加这一栏的宽度预算。
        bar.addWidget(self.btn_wf_default)
        bar.addWidget(self.btn_wf_adddir)
        bar.addWidget(self.btn_wf_scan)
        bar.addStretch(1)
        wv.addLayout(bar)
        return frame

    def _build_plugin_panel(self):
        # 「添加选中插件」挪到标题行：底部按用户要求只留「默认目录 / 全部刷新」
        self.btn_plug_add = QPushButton("添加选中插件 →")
        self.btn_plug_add.setObjectName("CardAction")
        self.btn_plug_add.setToolTip("把中栏选中的插件加进右栏的当前模式（也可双击单个插件）")
        frame, pv, self.lbl_plug_count = _panel("可用插件", [self.btn_plug_add])

        self.plug_search = QLineEdit()
        self.plug_search.setPlaceholderText("搜索插件…")
        pv.addWidget(self.plug_search)

        self.plug_list = QListWidget()
        self.plug_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.plug_list.setToolTip("双击 = 加入当前模式；绿色粗体 = 已在当前模式，"
                                  "橙色粗体 = 选中工作流需要但未加入")
        _tidy_view(self.plug_list)
        pv.addWidget(self.plug_list, 1)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.btn_plug_default = QPushButton("默认目录")
        self.btn_plug_default.setObjectName("Mini")
        self.btn_plug_default.setToolTip("打开 custom_nodes（插件）目录")
        self.btn_plug_refresh = QPushButton("全部刷新")
        self.btn_plug_refresh.setObjectName("Mini")
        self.btn_plug_refresh.setToolTip("重新扫描插件目录 + 重新扫描工作流 + 重新分析选中的工作流")
        bar.addWidget(self.btn_plug_default)
        bar.addWidget(self.btn_plug_refresh)
        bar.addStretch(1)
        pv.addLayout(bar)
        return frame

    def _build_mode_panel(self):
        self.btn_capture = QPushButton("捕获当前")
        self.btn_capture.setObjectName("CardAction")
        self.btn_capture.setToolTip("捕获当前启用：把磁盘上此刻启用中的插件原样填进右栏"
                                    "（不落盘，需再点「保存模式」）")
        self.btn_enable_all = QPushButton("全部启用")
        self.btn_enable_all.setObjectName("CardAction")
        self.btn_enable_all.setToolTip("恢复所有插件为启用状态（等于退出模式、加载全部插件）")
        frame, mv, self.lbl_mode_count = _panel(
            "模式管理", [self.btn_capture, self.btn_enable_all])

        # 当前模式：下拉（紧凑，不占地方）
        row = QHBoxLayout()
        row.setSpacing(6)
        lab = QLabel("当前模式:")
        lab.setObjectName("VersionHint")
        row.addWidget(lab)
        self.mode_combo = QComboBox()
        self.mode_combo.setMinimumWidth(110)
        row.addWidget(self.mode_combo, 1)
        mv.addLayout(row)

        self.btn_mode_new = QPushButton("新建模式")
        self.btn_mode_save = QPushButton("保存模式")
        self.btn_mode_rename = QPushButton("重命名模式")
        self.btn_mode_del = QPushButton("删除模式")
        self.btn_apply_now = QPushButton("应用模式")
        for b in (self.btn_mode_new, self.btn_mode_save, self.btn_mode_rename,
                  self.btn_mode_del, self.btn_apply_now):
            b.setObjectName("Mini")
            b.setMinimumHeight(24)
        # 「保存模式」是主操作：走 MiniPrimary（墨色实心，与 #Primary 同一套主操作语言），
        # **不加大 padding** —— 加大会把这一行邻居的字挤掉。
        self.btn_mode_save.setObjectName("MiniPrimary")
        # 「删除模式」是破坏性动作：同尺寸 ghost + 危险色文字（padding 与 #Mini 完全一致，
        # 不会把这一行挤宽 —— 这一栏的按钮宽度是算着塞进 ~300px 的）
        self.btn_mode_del.setObjectName("GhostDanger")
        self.btn_apply_now.setToolTip("把右栏这份白名单立刻应用到磁盘（不等下次启动）")

        # 五个按钮按「使用频次 + 语义」分两排（两排的按钮总宽度与原来**完全一致**，
        # 不会把这一栏挤宽）：上排 = 白名单落地（保存 = 主 / 应用 = 次），紧挨「当前模式」
        # 下拉、最常用；下排 = 模式增删改（新建 / 重命名 / 删除，删除走 GhostDanger）。
        # ★2026-10-12：原来是「新建 / 保存」+「重命名 / 删除 / 应用」，保存与应用被拆开、
        #   且删除夹在应用旁边，主次与分组都不清。
        for pair in ((self.btn_mode_save, self.btn_apply_now),
                     (self.btn_mode_new, self.btn_mode_rename, self.btn_mode_del)):
            rowi = QHBoxLayout()
            rowi.setSpacing(6)
            for b in pair:
                rowi.addWidget(b)
            rowi.addStretch(1)
            mv.addLayout(rowi)

        # 启动行为 + 缺失插件补装（缺失明细就在下面的列表里，按钮放同一屏）
        row_mid = QHBoxLayout()
        row_mid.setSpacing(6)
        self.chk_apply = QCheckBox("启动时自动应用该模式")
        self.btn_install_missing = QPushButton("一键安装缺失")
        self.btn_install_missing.setObjectName("Mini")
        self.btn_install_missing.setEnabled(False)
        row_mid.addWidget(self.chk_apply)
        row_mid.addStretch(1)
        row_mid.addWidget(self.btn_install_missing)
        mv.addLayout(row_mid)

        row3 = QHBoxLayout()
        row3.setSpacing(6)
        self.lbl_source = QLabel("")
        self.lbl_source.setObjectName("VersionHint")
        self.btn_resync = QPushButton("↻ 重新同步")
        self.btn_resync.setObjectName("CardAction")
        self.btn_resync.setEnabled(False)
        self.btn_resync.setToolTip("重新分析该模式来源的工作流，把白名单更新为它此刻所需的插件")
        row3.addWidget(self.lbl_source, 1)
        row3.addWidget(self.btn_resync)
        mv.addLayout(row3)

        self.mode_list = QListWidget()
        self.mode_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.mode_list.setToolTip("本次启动只会启用这里列出的插件。双击 = 移出。")
        _tidy_view(self.mode_list)
        mv.addWidget(self.mode_list, 1)

        row4 = QHBoxLayout()
        row4.setSpacing(6)
        self.btn_mode_remove = QPushButton("< 移出选中插件")
        self.btn_mode_clear = QPushButton("< 移出所有插件")
        for b in (self.btn_mode_remove, self.btn_mode_clear):
            b.setObjectName("Mini")
        row4.addWidget(self.btn_mode_remove)
        row4.addWidget(self.btn_mode_clear)
        row4.addStretch(1)
        mv.addLayout(row4)
        return frame

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------
    def _require_root(self):
        root = self.ctx.root()
        if not root or paths.validate_root(root):
            QMessageBox.warning(self, "无可用包",
                                "请先在「启动」页选择有效的 ComfyUI 包路径。")
            return ""
        return root

    def _git_proxy(self):
        lc = self.ctx.launch_cfg()
        return lc.get("launcher_proxy_url", "") if lc.get("launcher_proxy") else ""

    def _mode_data(self, name: str) -> dict:
        return (self.ctx.cfg.get("modes", {}).get(name) or {})

    def _combo_mode(self) -> str:
        """下拉里当前选中的**真实**模式名；停在下拉里那个合成项时返回空串。"""
        t = self.mode_combo.currentText()
        return "" if t == UNSAVED_LABEL else t

    def _suggest_name(self) -> str:
        if not self._wf_paths:
            return "工作流模式"
        base = os.path.splitext(os.path.basename(self._wf_paths[0]))[0].strip()
        if len(self._wf_paths) > 1:
            base = f"{base} 等 {len(self._wf_paths)} 份"
        return base or "工作流模式"

    def _bound_wf(self) -> str:
        """白名单与某份工作流的所需插件**完全一致**时才把两者绑定。

        多选时（同时跑好几份工作流）不绑定 —— 一份模式挂不了多份来源。
        """
        if len(self._wf_paths) == 1 and set(self._wf_needed) == set(self._checked):
            return self._wf_paths[0]
        return self._mode_data(self._cur_mode).get("workflow") or ""

    # ------------------------------------------------------------------
    # 模式列表 / 下拉
    # ------------------------------------------------------------------
    def reload(self, keep_row=True):
        cfg = self.ctx.cfg
        if not cfg.get("modes"):
            cfg.setdefault("modes", {})[DEFAULT_MODE] = {"plugins": []}
            self.ctx.save_cfg()
        modes = list(cfg["modes"].keys())
        if self._cur_mode not in modes:
            self._cur_mode = DEFAULT_MODE if DEFAULT_MODE in modes else modes[0]
        self._checked = set(self._mode_data(self._cur_mode).get("plugins", []))
        self._dirty = False
        self.chk_apply.blockSignals(True)
        self.chk_apply.setChecked(bool(cfg.get("apply_mode_on_launch")))
        self.chk_apply.blockSignals(False)
        self._sync_combo()
        self._refresh_source()
        self._refresh_plugin_list()
        self._refresh_mode_list()

    def _sync_combo(self):
        modes = list(self.ctx.cfg.get("modes", {}).keys())
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        self.mode_combo.addItems(modes)
        if self._dirty:
            self.mode_combo.addItem(UNSAVED_LABEL)
            self.mode_combo.setCurrentIndex(self.mode_combo.count() - 1)
        else:
            idx = modes.index(self._cur_mode) if self._cur_mode in modes else 0
            self.mode_combo.setCurrentIndex(idx)
        self.mode_combo.blockSignals(False)

    def _on_mode_picked(self, idx):
        name = self.mode_combo.itemText(idx) if idx >= 0 else ""
        if not name or name == UNSAVED_LABEL:
            return
        self._cur_mode = name
        self._checked = set(self._mode_data(name).get("plugins", []))
        self._dirty = False
        self._sync_combo()
        self._refresh_source()
        self._refresh_plugin_list()
        self._refresh_mode_list()

    def _mark_dirty(self):
        saved = set(self._mode_data(self._cur_mode).get("plugins", []))
        self._dirty = self._checked != saved
        self._sync_combo()
        self._refresh_source()

    def _refresh_source(self):
        wf = self._mode_data(self._cur_mode).get("workflow") or ""
        if wf and os.path.isfile(wf):
            self.lbl_source.setText(f"来源：{os.path.basename(wf)}")
            self.btn_resync.setEnabled(True)
        elif wf:
            self.lbl_source.setText(f"来源已不在磁盘：{os.path.basename(wf)}")
            self.btn_resync.setEnabled(False)
        elif self._dirty:
            self.lbl_source.setText("工作集尚未保存")
            self.btn_resync.setEnabled(False)
        else:
            self.lbl_source.setText("")
            self.btn_resync.setEnabled(False)

    def _refresh_mode_list(self):
        """右栏 = 本次启动会启用的插件清单（含缺失备注）。"""
        self.mode_list.clear()
        installed = {n for n, _p, _e in self._all_plugins}
        absent = 0
        for name in sorted(self._checked):
            it = QListWidgetItem(name)
            it.setData(Qt.UserRole, name)
            if name not in installed:
                it.setText(f"{name}　（本机未安装）")
                it.setForeground(QColor(style.WARNING))
                absent += 1
            self.mode_list.addItem(it)
        # 工作流需要、但本机根本没装的 → 就地备注缺失（不可选，只作提示）
        miss = []
        for m in ((self._result or {}).get("missing") or []):
            nm = m.get("plugin") if isinstance(m, dict) else str(m)
            if nm and nm not in miss:
                miss.append(nm)
        for nm in miss:
            it = QListWidgetItem(f"{nm}　（缺失 · 该工作流需要）")
            it.setForeground(QColor(style.WARNING))
            it.setFlags(Qt.NoItemFlags)
            self.mode_list.addItem(it)
        if not self.mode_list.count():
            it = QListWidgetItem("（空白名单 = 加载全部插件、不干预）")
            it.setForeground(QColor(style.MUTED))
            it.setFlags(Qt.NoItemFlags)
            self.mode_list.addItem(it)
        n = len(self._checked)
        self.lbl_mode_count.setText(f"当前模式插件（{n} 个）")
        self.lbl_mode_count.setToolTip(
            f"本次启动只会启用这里列出的 {n} 个插件。\n"
            f"{f'其中 {absent} 个本机没装；' if absent else ''}"
            f"{f'另有 {len(miss)} 个所选工作流需要的插件缺失；' if miss else ''}\n"
            "带「缺失」的行是工作流需要、但本机还没装的插件 —— "
            "可以用右边的「一键安装缺失」补上。")

    # ------------------------------------------------------------------
    # 中栏：可用插件
    # ------------------------------------------------------------------
    def _refresh_plugin_list(self):
        root = self.ctx.root()
        self.plug_list.clear()
        self._all_plugins = plugins.list_plugin_dirs(root) if root else []
        if not root:
            self.plug_list.addItem("（请先在「启动」页选择包路径）")
            self.lbl_plug_count.setText("可用插件")
            return
        self.lbl_plug_count.setText(f"可用插件（{len(self._all_plugins)} 个）")
        kw = self.plug_search.text().strip().lower()
        shown = 0
        for name, _p, enabled in self._all_plugins:
            if kw and kw not in name.lower():
                continue
            it = QListWidgetItem(name)
            it.setData(Qt.UserRole, name)
            tips = ["已禁用" if not enabled else "启用中"]
            if name in self._checked:
                it.setForeground(QColor(style.SUCCESS))
                f = it.font()
                f.setBold(True)
                it.setFont(f)
                tips.append("已在当前模式")
            elif name in self._wf_needed:
                it.setForeground(QColor(style.WARNING))
                f = it.font()
                f.setBold(True)
                it.setFont(f)
                tips.append("选中工作流需要，但未加入当前模式")
            elif not enabled:
                it.setForeground(QColor(style.MUTED))
            it.setToolTip("　".join(tips))
            self.plug_list.addItem(it)
            shown += 1
        if kw and not shown:
            it = QListWidgetItem("（没有匹配的插件）")
            it.setFlags(Qt.NoItemFlags)
            self.plug_list.addItem(it)

    # ------------------------------------------------------------------
    # 左栏：工作流
    # ------------------------------------------------------------------
    def reload_workflows(self):
        root = self.ctx.root()
        self._wf_files = []
        if root:
            self._wf_files = nodemap.list_workflow_files(
                root, self.ctx.cfg.get("workflow_folders", []))
        self._fill_wf_tree()
        self._sync_wf_selection()
        if root:
            self.ctx.status(f"发现 {len(self._wf_files)} 个工作流文件")

    def _sync_wf_selection(self):
        """搜索范围变了以后，把已经消失的工作流从选择里摘掉。

        只认 `self._wf_files`（搜索范围），**不看树的选中状态** —— 树可能正被搜索词
        过滤，这时「没选中」并不代表来源失效，照树判断会把用户的选择误清掉。
        """
        keep = [p for p in self._wf_paths if p in self._wf_files]
        if keep == self._wf_paths:
            return
        self._wf_paths = keep
        if keep:
            self._analyze_files(keep)
            return
        self._seq += 1                  # 选中的工作流全没了 -> 作废在跑的分析
        self._pending_wf_paths = None
        self._wf_needed = set()
        self._result = None
        self._refresh_plugin_list()
        self._refresh_mode_list()

    def _default_wf_dir(self) -> str:
        """ComfyUI 自带的工作流目录（归一化后的绝对路径，比较用）。"""
        root = self.ctx.root()
        if not root:
            return ""
        try:
            return os.path.normcase(os.path.abspath(paths.dirs(root)["workflows"]))
        except Exception:
            return ""

    def _fill_wf_tree(self):
        """按所在文件夹分组列出工作流；保留当前选中项（搜索/刷新时不丢选择）。

        分组顺序：**ComfyUI 默认目录的组永远在最上面**（用户要求），
        其余自定义目录的组再按文件夹名排。所以分组键取 `(优先级, 文件夹名)`，
        `sorted()` 出来的就是「默认目录在前 + 组内按名排」。
        """
        kw = self.wf_search.text().strip().lower()
        base = self._default_wf_dir()

        def _rank(path: str) -> int:
            """0 = ComfyUI 默认目录；1 = 自定义目录。"""
            if not base:
                return 1
            n = os.path.normcase(os.path.abspath(path))
            return 0 if (n == base or n.startswith(base + os.sep)) else 1

        groups = {}
        for f in self._wf_files:
            if kw and kw not in os.path.basename(f).lower():
                continue
            folder = os.path.basename(os.path.dirname(f)) or "（根目录）"
            groups.setdefault((_rank(f), folder), []).append(f)
        self.wf_tree.blockSignals(True)
        self.wf_tree.clear()
        picked = []
        for key in sorted(groups):
            folder = key[1]
            top = QTreeWidgetItem([f"[{folder}]"])
            top.setFlags(Qt.ItemIsEnabled)      # 文件夹只作分组，不可选中
            for p in sorted(groups[key]):
                it = QTreeWidgetItem([os.path.basename(p)])
                it.setData(0, Qt.UserRole, p)
                it.setToolTip(0, p)
                top.addChild(it)
                if p in self._wf_paths:
                    it.setSelected(True)
                    picked.append(it)
            self.wf_tree.addTopLevelItem(top)
        self.wf_tree.expandAll()
        if picked:
            self.wf_tree.setCurrentItem(picked[0])
        self.wf_tree.blockSignals(False)
        self.lbl_wf_count.setText(f"可用工作流（{len(self._wf_files)} 个）")

    def _selected_wf_paths(self) -> list:
        out = []
        for it in self.wf_tree.selectedItems():
            p = it.data(0, Qt.UserRole)
            if p and p not in out:
                out.append(p)
        return out

    def _on_wf_selection_settled(self):
        """去抖后跑：选择没实质变化就不重复分析。"""
        paths = self._selected_wf_paths()
        if paths == self._wf_paths:
            return
        if not paths:
            self._seq += 1              # 作废正在跑的分析，别让它的结果回填到已清空的界面
            self._pending_wf_paths = None
            self._wf_paths = []
            self._wf_needed = set()
            self._result = None
            self._refresh_plugin_list()
            self._refresh_mode_list()
            return
        self._analyze_files(paths)

    def _open_wf_dir(self):
        root = self.ctx.root()
        if root:
            d = paths.dirs(root)["workflows"]
            if os.path.isdir(d):
                QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def _open_plugin_dir(self):
        root = self.ctx.root()
        if root:
            d = paths.dirs(root)["custom_nodes"]
            if os.path.isdir(d):
                QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    # ------------------------------------------------------------------
    # 自定义工作流目录（workflow_folders）
    # ------------------------------------------------------------------
    def _wf_dir_list(self) -> list:
        return [os.path.normpath(x)
                for x in (self.ctx.cfg.get("workflow_folders") or []) if x]

    def _set_wf_dirs(self, folders: list, msg: str):
        """写回 workflow_folders 并立刻重扫 —— 必须重扫，否则左栏还挂着上一批工作流。"""
        self.ctx.cfg["workflow_folders"] = list(folders)
        self.ctx.save_cfg()
        self.reload_workflows()
        self._sync_wf_dir_tip()
        self.ctx.status(msg)

    def _choose_wf_dir(self):
        """单击「自定义目录」= 选一个文件夹并**替换**现有配置。

        旧目录会立刻移出搜索范围、它的工作流随即从左栏消失 —— 这就是用户要的
        「换目录 = 旧目录缓存自动清理」，不用再手动逐个移除。
        """
        self._pick_wf_dir()

    def _sync_wf_dir_tip(self):
        n = len(self._wf_dir_list())
        tip = ("单击：选择自定义工作流目录 —— 直接替换现有的，"
               "旧目录立刻移出搜索范围、它的工作流随即消失。")
        tip += (f"\n当前已配置 {n} 个自定义目录。" if n
                else "\n当前没有自定义目录（只扫 ComfyUI 默认目录）。")
        self.btn_wf_adddir.setToolTip(tip)

    def _pick_wf_dir(self):
        """选一个文件夹作为**唯一**的自定义目录（替换语义，不追加、不保留旧目录）。"""
        d = QFileDialog.getExistingDirectory(self, "选择工作流文件夹")
        if not d:
            return
        d = os.path.normpath(d)
        nd = os.path.normcase(os.path.abspath(d))
        base = self._default_wf_dir()
        if base and (nd == base or nd.startswith(base + os.sep)):
            self.ctx.status("这就是 ComfyUI 自带的工作流目录，本来就在搜索范围里")
            return
        self._set_wf_dirs([d], f"自定义目录已设为 {d}（旧目录已移出搜索范围）")

    def _refresh_all(self):
        """中栏「全部刷新」：插件 + 工作流 + 当前选中的分析，全部重来一遍。"""
        self._refresh_plugin_list()
        self.reload_workflows()
        paths = self._selected_wf_paths()
        if paths:
            self._analyze_files(paths)
            self.ctx.status(f"已刷新：插件 {len(self._all_plugins)} 个 / "
                            f"工作流 {len(self._wf_files)} 个，正在重新分析选中的 "
                            f"{len(paths)} 份工作流…")
        else:
            self.ctx.status(f"已刷新：插件 {len(self._all_plugins)} 个 / "
                            f"工作流 {len(self._wf_files)} 个")

    # ------------------------------------------------------------------
    # 分析
    # ------------------------------------------------------------------
    def _analyze_external(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择工作流 JSON", "",
                                           "Workflow JSON (*.json)")
        if f:
            self._wf_paths = [f]
            self._analyze_files([f])

    def _summary_text(self, res, paths) -> str:
        bits = [f"{res['nodes']} 节点", f"需 {len(res['plugins'])} 插件"]
        if res["missing"]:
            bits.append(f"缺 {len(res['missing'])}")
        if res["unknown"]:
            bits.append(f"内置/未识别 {len(res['unknown'])}")
        if len(paths) == 1:
            tail = os.path.basename(paths[0])
        else:
            tail = (f"{len(paths)} 份工作流（"
                    f"{'、'.join(os.path.basename(p) for p in paths[:2])}…）")
        return " · ".join(bits) + f"　（{tail}）"

    def _analyze_files(self, wf_paths):
        root = self._require_root()
        wf_paths = [p for p in wf_paths if p]
        if not root or not wf_paths:
            return
        self._wf_paths = list(wf_paths)
        # 同一时刻只允许一个分析在跑：快速连点/框选时如果各起一个，会并发触发多次
        # 全量节点扫描，把界面拖死。这里只记下最新请求，等当前这次结束后再跑一次。
        if self._analyzing:
            self._pending_wf_paths = list(wf_paths)
            return
        self._analyzing = True
        self._seq += 1
        seq = self._seq
        head = os.path.basename(wf_paths[0]) if len(wf_paths) == 1 \
            else f"{len(wf_paths)} 份工作流"
        self.ctx.status(f"分析中：{head}（首次扫描插件约 10 秒）")

        def job():
            ext = nodemap.ensure_extmap()
            outs = [nodemap.workflow_plugin_set(root, p, ext) for p in wf_paths]
            return nodemap.merge_plugin_sets(outs)

        w = self.ctx.spawn(Worker(job))

        def _next():
            """本次分析收尾：如果期间又选了别的，接着跑最新那次。"""
            self._analyzing = False
            pending = self._pending_wf_paths
            self._pending_wf_paths = None
            if pending:
                self._analyze_files(pending)

        def _done(res):
            if seq == self._seq:        # 用户已经换了选择，丢弃
                self._result = res
                self._wf_needed = set(res["plugins"])
                self.ctx.status(self._summary_text(res, wf_paths))
                self.btn_install_missing.setEnabled(bool(res["missing_urls"]))
                self.btn_install_missing.setText(
                    f"一键安装缺失({len(res['missing_urls'])})"
                    if res["missing_urls"] else "一键安装缺失")
                self._autofill(res)
                self._refresh_plugin_list()
                self._refresh_mode_list()
            _next()

        def _failed(m):
            if seq == self._seq:
                self.ctx.status(f"分析失败：{m}")
                QMessageBox.warning(self, "分析失败", m)
            _next()

        w.done.connect(_done)
        w.failed.connect(_failed)

    def _analyze_file(self, wf_path):
        """单份文件的入口（外部文件 / 装完缺失插件后重新分析用）。"""
        self._analyze_files([wf_path])

    def _autofill(self, res):
        """选中工作流后，直接把「它需要的插件」填进右栏（未保存状态）。"""
        plugins_ = set(res["plugins"])
        if not plugins_:
            return
        # 与某个已存模式的白名单完全一致时，直接切到那个模式，不标记未保存
        for name, data in (self.ctx.cfg.get("modes") or {}).items():
            if set(data.get("plugins") or []) == plugins_:
                self._cur_mode = name
                self._checked = plugins_
                self._dirty = False
                self._sync_combo()
                self._refresh_source()
                self.ctx.status(f"所选工作流所需的插件与模式「{name}」一致，已自动切过去")
                return
        self._checked = plugins_
        self._mark_dirty()

    def _install_missing(self):
        root = self._require_root()
        if not root or not self._result:
            return
        urls = list(self._result["missing_urls"])
        if not urls:
            return

        def job(report):
            ok_names = []
            for k, url in enumerate(urls, 1):
                report(f"[{k}/{len(urls)}] 克隆 {url}")
                ok, msg, name = plugins.install(url, root, self._git_proxy())
                report(f"    -> {msg}")
                if ok:
                    ok_names.append(name)
            return ok_names

        self.btn_install_missing.setEnabled(False)
        w = self.ctx.spawn(Worker(job, on_line=lambda s: self.ctx.status(s)))

        def _done(names):
            self.ctx.status(f"缺失插件安装完成 {len(names)}/{len(urls)}")
            # 装完立刻重分析：新装的插件这时才会出现在白名单候选里
            if self._wf_paths:
                self._analyze_files(self._wf_paths)

        w.done.connect(_done)

    # ------------------------------------------------------------------
    # 右栏：把中栏选中的插件加进来 / 移出去
    # ------------------------------------------------------------------
    def _add_selected_plugins(self):
        names = [it.data(Qt.UserRole) for it in self.plug_list.selectedItems()]
        names = {n for n in names if n}
        if not names:
            self.ctx.status("先在中栏选中要加入的插件（可多选，或双击单个）")
            return
        before = len(self._checked)
        self._checked |= names
        self._mark_dirty()
        self._refresh_plugin_list()
        self._refresh_mode_list()
        self.ctx.status(f"已加入 {len(self._checked) - before} 个插件"
                        f"（共 {len(self._checked)} 个，记得点「保存模式」）")

    def _remove_selected(self):
        names = {it.data(Qt.UserRole) for it in self.mode_list.selectedItems()}
        names.discard(None)
        if not names:
            self.ctx.status("先在右栏选中要移出的插件")
            return
        self._checked -= names
        self._mark_dirty()
        self._refresh_plugin_list()
        self._refresh_mode_list()
        self.ctx.status(f"已移出 {len(names)} 个插件（记得点「保存模式」）")

    def _remove_all(self):
        if not self._checked:
            return
        if QMessageBox.question(
                self, "移出全部",
                f"把 {len(self._checked)} 个插件都移出当前模式？") != QMessageBox.Yes:
            return
        n = len(self._checked)
        self._checked = set()
        self._mark_dirty()
        self._refresh_plugin_list()
        self._refresh_mode_list()
        self.ctx.status(f"已移出全部 {n} 个插件（记得点「保存模式」）")

    def _capture_current(self):
        root = self.ctx.root()
        if not root:
            QMessageBox.information(self, "提示", "请先在「启动」页选择包路径")
            return
        enabled = plugins.capture_enabled(root)
        self._checked = set(enabled)
        self._mark_dirty()
        self._refresh_plugin_list()
        self._refresh_mode_list()
        self.ctx.status(f"已把当前启用中的 {len(enabled)} 个插件填进右栏（记得点「保存模式」）")

    # ------------------------------------------------------------------
    # 模式的新建 / 保存 / 重命名 / 删除
    # ------------------------------------------------------------------
    def _new_mode(self):
        name, ok = QInputDialog.getText(self, "新建模式", "新模式名称：")
        name = (name or "").strip()
        if not ok or not name:
            return
        modes = self.ctx.cfg.setdefault("modes", {})
        if name in modes:
            QMessageBox.information(self, "已存在", f"模式「{name}」已存在")
            return
        modes[name] = {"plugins": []}
        self.ctx.save_cfg()
        self._cur_mode = name
        self._checked = set()
        self._dirty = False
        self._sync_combo()
        self._refresh_source()
        self._refresh_plugin_list()
        self._refresh_mode_list()
        self.ctx.status(f"已新建空模式「{name}」，从中栏挑插件加进右栏")

    def _save_mode(self):
        name = self._combo_mode()
        if not name:
            # 下拉停在合成项 → 让用户起个名字
            name, ok = QInputDialog.getText(self, "保存模式", "新模式名称：",
                                            text=self._suggest_name())
            name = (name or "").strip()
            if not ok or not name:
                return
            if name in (self.ctx.cfg.get("modes") or {}):
                if QMessageBox.question(
                        self, "已存在",
                        f"模式「{name}」已存在，用当前工作集覆盖它？") != QMessageBox.Yes:
                    return
        self._cur_mode = name
        self._write_mode(name, sorted(self._checked), self._bound_wf())
        self._dirty = False
        self.ctx.save_cfg()
        self._sync_combo()
        self._refresh_source()
        self.ctx.status(f"模式「{name}」已保存（{len(self._checked)} 个插件）")

    def _rename_mode(self):
        name = self._combo_mode()
        if not name:
            self.ctx.status("请先在「当前模式」下拉里选中一个模式")
            return
        if name == DEFAULT_MODE:
            QMessageBox.information(self, "保留", "默认模式不建议改名")
            return
        new, ok = QInputDialog.getText(self, "重命名模式", "新名称：", text=name)
        new = (new or "").strip()
        if not ok or not new or new == name:
            return
        modes = self.ctx.cfg.get("modes") or {}
        if new in modes:
            QMessageBox.information(self, "已存在", f"模式「{new}」已存在")
            return
        modes[new] = modes.pop(name)
        if self.ctx.cfg.get("last_mode") == name:
            self.ctx.cfg["last_mode"] = new
        self._cur_mode = new
        self.ctx.save_cfg()
        self._sync_combo()
        self._refresh_source()
        self.ctx.status(f"模式「{name}」已重命名为「{new}」")

    def _del_mode(self):
        name = self._combo_mode()
        if not name:
            self.ctx.status("请先在「当前模式」下拉里选中一个模式")
            return
        if name == DEFAULT_MODE:
            QMessageBox.information(self, "保留",
                                    "默认模式不可删除（白名单为空 = 加载全部插件）")
            return
        if QMessageBox.question(
                self, "确认删除",
                f"删除模式「{name}」？") != QMessageBox.Yes:
            return
        del self.ctx.cfg["modes"][name]
        if self.ctx.cfg.get("last_mode") == name:
            self.ctx.cfg["last_mode"] = DEFAULT_MODE
        self.ctx.save_cfg()
        self._cur_mode = DEFAULT_MODE
        self.reload()
        self.ctx.status(f"模式「{name}」已删除")

    def _write_mode(self, name, plugin_names, wf_path=""):
        mode = self.ctx.cfg.setdefault("modes", {}).setdefault(name, {})
        mode["plugins"] = sorted(plugin_names)
        if wf_path:
            mode["workflow"] = wf_path
        elif "workflow" in mode and not wf_path:
            # 白名单已经跟来源工作流脱钩了，别再挂着一份过期的绑定
            del mode["workflow"]

    def _resync_from_workflow(self):
        name = self._cur_mode
        wf = self._mode_data(name).get("workflow") or ""
        root = self._require_root()
        if not root or not wf:
            return
        self.ctx.status(f"正在按「{os.path.basename(wf)}」重新同步模式「{name}」…")
        self.btn_resync.setEnabled(False)

        def job():
            ext = nodemap.ensure_extmap()
            return nodemap.workflow_plugin_set(root, wf, ext)

        w = self.ctx.spawn(Worker(job))

        def _done(res):
            self.btn_resync.setEnabled(True)
            old = set(self._checked)
            new = set(res["plugins"])
            self._checked = new
            self._result = res
            self._wf_needed = new
            self._wf_paths = [wf]
            self._write_mode(name, res["plugins"], wf)
            self.ctx.save_cfg()
            self._dirty = False
            self._sync_combo()
            self._refresh_source()
            self._refresh_plugin_list()
            self._refresh_mode_list()
            added = len(new - old)
            removed = len(old - new)
            msg = f"模式「{name}」已同步：{len(new)} 个插件"
            if added or removed:
                msg += f"（新增 {added} / 移除 {removed}）"
            if res["missing"]:
                msg += f"；仍缺 {len(res['missing'])} 个所需插件"
            self.ctx.status(msg)

        w.done.connect(_done)
        w.failed.connect(lambda m: (self.btn_resync.setEnabled(True),
                                    QMessageBox.warning(self, "同步失败", m)))

    # ------------------------------------------------------------------
    # 应用
    # ------------------------------------------------------------------
    def _apply_toggled(self, on):
        self.ctx.cfg["apply_mode_on_launch"] = on
        name = self._combo_mode() or self._cur_mode
        if on and name:
            self.ctx.cfg["last_mode"] = name
        self.ctx.save_cfg()

    def _apply_now(self):
        name = self._combo_mode() or self._cur_mode
        root = self.ctx.root()
        if not root:
            QMessageBox.information(self, "提示", "请先在「启动」页选择包路径")
            return
        picked = sorted(self._checked)
        if not picked:
            QMessageBox.information(
                self, "空白名单",
                f"模式「{name}」白名单为空（= 加载全部插件）。")
            return
        self._write_mode(name, picked, self._bound_wf())
        self.ctx.cfg["last_mode"] = name
        self.ctx.save_cfg()
        self._dirty = False
        self._sync_combo()
        self._refresh_source()
        self.ctx.status(f"正在应用模式「{name}」（{len(picked)} 个插件）...")

        w = self.ctx.spawn(Worker(plugins.apply_mode, root, picked))

        def _done(res):
            moved, errors = res
            for e in errors:
                self.ctx.status(e)
            self.ctx.status(f"模式「{name}」已应用：调整 {moved} 个插件目录"
                            + (f"，{len(errors)} 个失败" if errors else ""))
            self._refresh_plugin_list()

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "应用失败", m))

    def _enable_all(self):
        root = self.ctx.root()
        if not root:
            QMessageBox.information(self, "提示", "请先在「启动」页选择包路径")
            return
        w = self.ctx.spawn(Worker(plugins.enable_all, root))

        def _done(errors):
            for e in errors:
                self.ctx.status(e)
            self.ctx.status("已恢复全部插件为启用状态（等于加载全部插件）" if not errors
                            else f"恢复完成，{len(errors)} 个失败")
            self._refresh_plugin_list()

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "操作失败", m))

    # ------------------------------------------------------------------
    def on_show(self):
        self.reload_workflows()
        self._refresh_plugin_list()
