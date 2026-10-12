"""启动页：包路径 / 启动参数 / 控制台。

功能来自 Echo：
- 启动参数由 `comfyui_echo.core.process.LaunchOptions` 拼装（注意力走 Echo 四档）
- 启停在 `ComfyRunner` 内（Echo 的 ComfyProcess：psutil 进程树终止 + PYTHONUNBUFFERED）
- 启动前流程完全对齐 Echo：CK 档前置实测 -> 端口占用处理 -> 应用模式 -> 装 shim -> 拉起
"""
import os
import re
import time

from PySide6.QtCore import Qt, QEvent, QTimer, QUrl
from PySide6.QtGui import (QColor, QDesktopServices, QFontMetrics,
                           QSyntaxHighlighter, QTextCharFormat)
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox,
                               QComboBox, QDialog, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMenu,
                               QPlainTextEdit, QPushButton, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

from .dialogs import MessageBox as QMessageBox

from ..core import attention as attnmod
from ..core import paths, plugins, process as procmod
from ..core.process import LaunchOptions
from ..ui import style
from .widgets import card as _card, plain_box
from .workers import Worker

# 「复制报错与警告」用的行特征：覆盖 Python/ComfyUI/插件的常见报错措辞
ISSUE_RX = re.compile(
    r"(?i)(traceback|error|exception|failed|failure|cannot|not found"
    r"|no module named|importerror|modulenotfound|conflict|mismatch"
    r"|警告|错误|失败|无法|不兼容|版本冲突)")


class ConsoleHighlighter(QSyntaxHighlighter):
    RULES = [
        (QRegularExpression(r"(?i)^.*(traceback|error|exception|失败).*$"),
         QColor(style.DANGER)),
        (QRegularExpression(r"(?i)^.*(warning|警告).*$"), QColor(style.WARNING)),
        (QRegularExpression(r"^To see the GUI go to.*$"), QColor(style.SUCCESS)),
    ]

    def highlightBlock(self, text):
        for rx, color in self.RULES:
            m = rx.match(text)
            if m.hasMatch():
                fmt = QTextCharFormat()
                fmt.setForeground(color)
                self.setFormat(0, len(text), fmt)
                return


class PathDialog(QDialog):
    """多包路径管理。"""

    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("管理 ComfyUI 包路径")
        self.resize(560, 380)
        v = QVBoxLayout(self)
        self.listw = QListWidget()
        v.addWidget(self.listw, 1)

        h = QHBoxLayout()
        self.remark_edit = QLineEdit()
        self.remark_edit.setPlaceholderText("备注名（可留空，默认取目录名）")
        btn_add = QPushButton("添加目录...")
        btn_del = QPushButton("移除选中")
        h.addWidget(self.remark_edit, 1)
        h.addWidget(btn_add)
        h.addWidget(btn_del)
        v.addLayout(h)

        btn_ok = QPushButton("确定")
        btn_ok.setObjectName("Primary")
        h2 = QHBoxLayout()
        h2.addStretch(1)
        h2.addWidget(btn_ok)
        v.addLayout(h2)

        btn_add.clicked.connect(self._add)
        btn_del.clicked.connect(self._del)
        btn_ok.clicked.connect(self.accept)
        self._reload()

    def _reload(self):
        self.listw.clear()
        for item in self.cfg.get("paths", []):
            self.listw.addItem(f"{item.get('remark') or '未命名'}    {item.get('root')}")

    def _add(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择 ComfyUI 目录（含 main.py 的目录，或便携包根目录）")
        if not d:
            return
        err = paths.validate_root(d)
        if err:
            QMessageBox.warning(self, "无效路径", f"该目录不是有效的 ComfyUI 包：\n{err}")
            return
        self.cfg.setdefault("paths", []).append(
            {"remark": self.remark_edit.text().strip() or os.path.basename(d), "root": d})
        self.remark_edit.clear()
        self._reload()

    def _del(self):
        row = self.listw.currentRow()
        if row >= 0:
            del self.cfg["paths"][row]
            self._reload()


# 四个页签共用一个标签列宽。
# 每个页签各自一个 QFormLayout，标签列是「按本页签最宽标签」自动算的 ——
# 「档位」只要 24px、「GPU 序号」要 72px，切页签时字段起始位置就会左右跳。
_LABEL_W = None


def _label_width() -> int:
    """标签列宽 = 最宽的那个标签 + 一点余量（用真实字体量，不写死数字）。"""
    global _LABEL_W
    if _LABEL_W is None:
        fm = QFontMetrics(QApplication.font())
        _LABEL_W = max(fm.horizontalAdvance(t) for t in
                       ("端口", "GPU 序号", "档位", "代理地址",
                        "自定义参数", "环境变量")) + 6
    return _LABEL_W


def _label(text: str) -> QLabel:
    """定宽右对齐的标签。

    定宽是为了让四个页签的字段列对齐；复选框行用 `_label("")` 占住标签位，
    这样复选框的左边缘和输入框在同一条线上（不然它从最左边开始，和标签对不齐）。
    """
    w = QLabel(text)
    w.setFixedWidth(_label_width())
    w.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return w


def _form(parent):
    """页签内的表单：标签右对齐、字段列吃掉余宽、长行不折。

    长控件（复选框、说明文字）一律用单参 `addRow(widget)` 整行占满 ——
    塞进 `addRow(左, 右)` 的标签位会把整列撑宽，右侧字段全被挤扁。
    """
    form = QFormLayout(parent)
    # 上边留 10px：页签的内容页紧贴页签栏，第一行（常用页的「fp16 累加加速」、
    # 网络页的「HuggingFace 国内镜像」）原来几乎和页签文字粘在一起。
    form.setContentsMargins(0, 10, 0, 2)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.DontWrapRows)
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(8)
    return form


def _divider():
    f = QFrame()
    f.setObjectName("Divider")
    f.setFixedHeight(1)
    return f


def _flat_spin(rng, special_text, tooltip):
    """无箭头微调框：键盘直输，去掉老式上下三角。

    左对齐（原来是右对齐）：这里的值多半是「默认 (8188)」「不指定」这种文字，
    右对齐会把它们推到框的最右边，离左边的「端口 / GPU 序号」标签老远，看着断成两截。
    限宽：端口/GPU 序号是短数字，撑满整行会显得空且难对齐。
    """
    s = QSpinBox()
    s.setRange(*rng)
    if special_text:
        s.setSpecialValueText(special_text)
    s.setButtonSymbols(QAbstractSpinBox.NoButtons)
    s.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    s.setMaximumWidth(180)
    s.setToolTip(tooltip)
    return s


def _probe_version(root):
    """在后台线程里做版本探测（内部是两次子进程调用），返回 (root, 文本)。"""
    gpu = paths.gpu_name()
    text = (f"ComfyUI {paths.comfyui_version(root)}    |    "
            f"{paths.python_version(root)}    |    {gpu or '未检测到 N 卡'}")
    return root, text


class HomePage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._mode_busy = False
        # CK 实测结果存在 ctx 上（「环境」页的「检测 CK」也会写它），跨页共享
        self._ver_cache = {}        # root -> 版本信息文本（避免每次切页重探测）
        self._ver_worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        # ---- 顶部：路径与版本 ----
        top, tv = _card("COMFYUI 包")
        h = QHBoxLayout()
        h.setSpacing(6)
        self.path_combo = QComboBox()
        self.path_combo.setMinimumWidth(420)
        btn_manage = QPushButton("管理路径...")
        btn_scan = QPushButton("自动扫描")
        btn_refresh = QPushButton("刷新")
        # 这三个都是次要动作：统一 ghost 形态（透明底+描边），别和内容抢
        for b in (btn_manage, btn_scan, btn_refresh):
            b.setObjectName("Ghost")
        for w in (self.path_combo, btn_manage, btn_scan, btn_refresh):
            h.addWidget(w)
        h.addStretch(1)
        tv.addLayout(h)
        # 版本信息：有数据时显示成三枚 pastel pill（信息层级比一行灰字清楚），
        # 未选择/检测中/失败这类提示态仍走 ver_label 灰字
        self.ver_label = QLabel("版本信息：-")
        self.ver_label.setObjectName("VersionHint")
        tv.addWidget(self.ver_label)
        self._pill_row = plain_box()   # 别用裸 QWidget：会在卡片上显出一条色带
        ph = QHBoxLayout(self._pill_row)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.setSpacing(6)
        self._pill_row.hide()
        tv.addWidget(self._pill_row)
        root.addWidget(top)

        self.path_combo.currentIndexChanged.connect(self._on_path_changed)
        btn_manage.clicked.connect(self._manage_paths)
        btn_scan.clicked.connect(self._auto_scan)
        btn_refresh.clicked.connect(self._force_refresh_version)

        # ---- 中部：参数 + 控制台 ----
        mid = QHBoxLayout()
        mid.setSpacing(10)

        param, pv = _card("启动参数")
        pv.setSpacing(10)

        # 参数一列排到底太密。按使用频次分四个页签，默认只看到「常用」那 5 行，
        # 其余（注意力 / 网络 / 高级）点一下才展开。
        self.param_tabs = QTabWidget()
        self.param_tabs.setObjectName("ParamTabs")

        # 每个页签配一个 QFormLayout；变量名统一叫 form，下面 addRow 不用改缩进
        tab = QWidget()
        tab.setObjectName("ParamPage")   # 让 style.py 把它刷成透明（否则会显出一条画布色带）
        form = _form(tab)

        # ---- 页签 1：常用（加速开关 + 端口与设备）----
        # 标签只留人话，命令行开关放 tooltip：整行文字过长会把卡片顶宽，
        # 挤掉右侧控制台的位置（小窗口下文字直接被裁掉）。
        self.chk_fp16 = QCheckBox("fp16 累加加速")
        self.chk_fp16.setToolTip("--fast fp16_accumulation")
        self.chk_cudamalloc = QCheckBox("CUDA 内存分配优化")
        self.chk_cudamalloc.setToolTip("--cuda-malloc")
        self.chk_reserve = QCheckBox("预留显存")
        self.chk_reserve.setToolTip("--reserve-vram")
        for c in (self.chk_fp16, self.chk_cudamalloc, self.chk_reserve):
            form.addRow(_label(""), c)
        form.addRow(_divider())
        self.spin_port = _flat_spin(
            (0, 65535), "默认 (8188)",
            "直接输入端口号；0 = 用 ComfyUI 默认的 8188")
        form.addRow(_label("端口"), self.spin_port)
        # 用 nvidia-smi 探测到的**显卡名**做下拉（TE 的做法）—— 用户不用去猜
        # 「0 号卡到底是哪块」，直接看到型号。显卡列表由顶部版本探测在后台填。
        self.combo_gpu = QComboBox()
        self.combo_gpu.setMinimumWidth(200)
        # 显卡名很长，sizeHint 会把参数卡片顶宽（体检脚本常年报它被压窄）：
        # 框宽按 22 字符算，展开的下拉列表里仍显示完整型号
        self.combo_gpu.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo_gpu.setMinimumContentsLength(22)
        self.combo_gpu.setToolTip("指定 ComfyUI 用哪块显卡；"
                                  "「自动（默认）」= 不加 --cuda-device，交给 ComfyUI 自己挑")
        self._gpu_dev = -1
        self._reload_gpu_list()
        form.addRow(_label("指定 GPU"), self.combo_gpu)
        self.param_tabs.addTab(tab, "常用")

        # ---- 页签 2：网络（镜像与代理）----
        # 注意：原来的「注意力」页签已整体搬到「环境」页（用户要求），
        # 这里只剩 常用 / 网络 / 高级 三签；启动命令预览仍会反映注意力档位。
        tab = QWidget()
        tab.setObjectName("ParamPage")   # 让 style.py 把它刷成透明（否则会显出一条画布色带）
        form = _form(tab)
        self.chk_hf = QCheckBox("HuggingFace 国内镜像")
        self.chk_hf.setToolTip("把模型下载源换成 hf-mirror.com（国内可直连）")
        self.chk_comfy_proxy = QCheckBox("ComfyUI 进程走代理")
        self.chk_comfy_proxy.setToolTip("给 ComfyUI 进程注入 HTTP_PROXY / HTTPS_PROXY")
        self.chk_launcher_proxy = QCheckBox("启动器 git/pip 走代理")
        self.chk_launcher_proxy.setToolTip("插件 git 克隆与 pip 安装走下面的代理地址")
        self.edit_proxy = QLineEdit()
        self.edit_proxy.setPlaceholderText("http://127.0.0.1:7890")
        for c in (self.chk_hf, self.chk_comfy_proxy, self.chk_launcher_proxy):
            form.addRow(_label(""), c)
        form.addRow(_label("代理地址"), self.edit_proxy)
        self.param_tabs.addTab(tab, "网络")

        # ---- 页签 4：高级（自定义参数 / 环境变量 / 实验性开关）----
        tab = QWidget()
        tab.setObjectName("ParamPage")   # 让 style.py 把它刷成透明（否则会显出一条画布色带）
        form = _form(tab)
        self.edit_extra = QLineEdit()
        self.edit_extra.setPlaceholderText("追加命令行参数，如 --lowvram --preview-method auto")
        form.addRow(_label("自定义参数"), self.edit_extra)
        self.edit_env = QPlainTextEdit()
        self.edit_env.setPlaceholderText("每行一个环境变量：KEY=VALUE\n# 开头为注释")
        self.edit_env.setFixedHeight(52)
        form.addRow(_label("环境变量"), self.edit_env)
        # 「实验性开关」折叠区已升级成独立的「专家模式」页面（运行区第二项），
        # expert 配置由那边统一读写，这里不再保留第二入口（两处写同一份配置必打架）。
        self.param_tabs.addTab(tab, "高级")

        pv.addWidget(self.param_tabs)
        pv.addStretch(1)

        # ---- 左列下半：启动命令预览（跟着参数实时变，省得每次启动才看日志）----
        self.btn_copy_cmd = QPushButton("复制命令")
        self.btn_copy_cmd.setObjectName("CardAction")
        self.btn_copy_cmd.setCursor(Qt.PointingHandCursor)
        self.btn_copy_cmd.setToolTip("复制这行命令，可以直接粘到终端里手跑")
        self.btn_copy_cmd.clicked.connect(self._copy_cmd)
        prev_card, prevv = _card("启动命令", action=self.btn_copy_cmd)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("Preview")
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(92)          # 约 4 行，够放满参数的命令
        self.preview.setPlaceholderText("选好包路径后，这里会显示将要执行的命令")
        prevv.addWidget(self.preview)
        lbl_prev = QLabel("改动任何参数都会立刻反映到上面这行命令。")
        lbl_prev.setObjectName("VersionHint")
        prevv.addWidget(lbl_prev)

        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(param, 0)
        left.addWidget(prev_card, 0)
        left.addStretch(1)
        mid.addLayout(left, 5)

        # 控制台。按钮排一行，全部同款：复制日志 / 打开网页 / 打开目录 / 清空。
        con, cv = _card("控制台")
        ch = QHBoxLayout()
        ch.setSpacing(6)
        self.btn_start = QPushButton("启动")
        self.btn_start.setObjectName("Primary")
        # 停止是「平时禁用的危险动作」：ghost 底 + 危险色字，实心红太重会抢启动的戏
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("GhostDanger")
        self.btn_stop.setEnabled(False)
        self.btn_web = QPushButton("打开网页")
        self.btn_web.setObjectName("Ghost")
        self.btn_web.setToolTip("在浏览器里打开 ComfyUI 页面")
        # 三个目录按钮并成一个菜单：原先 7 个按钮挤在一行，文字全被截断
        self.btn_dirs = QPushButton("打开目录")
        self.btn_dirs.setObjectName("Ghost")
        self.btn_dirs.setToolTip("输出 / 模型 / 插件 / ComfyUI 根目录")
        dir_menu = QMenu(self.btn_dirs)
        for _text, _key in (("输出目录", "outputs"), ("模型目录", "models"),
                            ("插件目录", "custom_nodes")):
            act = dir_menu.addAction(_text)
            act.triggered.connect(lambda _c=False, k=_key: self._open_dir(k))
        dir_menu.addSeparator()
        act = dir_menu.addAction("ComfyUI 根目录")
        act.triggered.connect(lambda _c=False: self._open_dir("comfyui"))
        self.btn_dirs.setMenu(dir_menu)
        # 一键复制日志：自动挑出报错/警告并带上环境信息，直接贴给 AI 排查插件问题。
        self.btn_copy = QPushButton("复制日志")
        self.btn_copy.setObjectName("Ghost")
        self.btn_copy.setToolTip(
            "复制控制台日志到剪贴板，直接贴给 AI 排查插件问题。\n"
            "会自动挑出报错与警告，并带上环境信息（版本 / 显卡 / 启动命令）。")
        self.btn_copy.clicked.connect(self._copy_log)
        # 预留下「已复制 ✓」所需宽度：回执换字时按钮不会变宽，右侧的「清空」不会跟着跳。
        self.btn_copy.setText("已复制 ✓")
        _copy_w = self.btn_copy.sizeHint().width()
        self.btn_copy.setText("复制日志")
        self.btn_copy.setMinimumWidth(max(_copy_w, self.btn_copy.sizeHint().width()))
        btn_clear = QPushButton("清空")
        btn_clear.setObjectName("Ghost")
        btn_clear.setToolTip("清空控制台输出（不影响 ComfyUI 本身）")
        for w in (self.btn_start, self.btn_stop, self.btn_web,
                  self.btn_dirs, self.btn_copy, btn_clear):
            ch.addWidget(w)
        ch.addStretch(1)
        cv.addLayout(ch)
        self.console = QPlainTextEdit()
        self.console.setObjectName("Console")
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(6000)
        # 空状态提示做成居中的浮层（placeholder 是左上角一行灰字，太空旷）；
        # 首条日志到达即隐藏，「清空」后重新显示
        self.console.setPlaceholderText("")
        self.console_hint = QLabel(
            "点「启动」后，ComfyUI 日志会实时出现在这里\n\n"
            "Ctrl+A 全选 · 「复制日志」把报错整理成可直接贴给 AI 的摘要",
            self.console)
        self.console_hint.setObjectName("ConsoleHint")
        self.console_hint.setAlignment(Qt.AlignCenter)
        self.console_hint.setWordWrap(True)
        self.console.installEventFilter(self)
        self._hl = ConsoleHighlighter(self.console.document())
        cv.addWidget(self.console, 1)
        mid.addWidget(con, 6)
        root.addLayout(mid, 1)

        self.btn_start.clicked.connect(self.on_start)
        self.btn_stop.clicked.connect(self.ctx.runner.stop)
        self.btn_web.clicked.connect(self._open_web)
        btn_clear.clicked.connect(self._clear_console)

        r = self.ctx.runner
        r.output.connect(self._on_output)
        r.state_changed.connect(self._on_state)
        r.server_ready.connect(self._on_ready)
        r.stopped.connect(lambda c: self.ctx.status(f"ComfyUI 已退出 (代码 {c})"))

        self._load_cfg()
        self._sync_path_combo()
        self._refresh_preview()

    def _reload_gpu_list(self):
        """按 `paths.gpu_list()` 重建「指定 GPU」下拉（下标即 CUDA 设备号）。

        显卡要问 nvidia-smi（约 200 ms 的子进程），由顶部的版本探测在后台跑；
        探测完成后 `_on_version_ready` 会再调一次这里，所以建造时可能只剩「自动（默认）」。
        配置的序号若在列表里找不到，会补一项占位 —— 免得被悄悄改回「自动」。
        """
        self.combo_gpu.blockSignals(True)
        self.combo_gpu.clear()
        self.combo_gpu.addItem("自动（默认）", -1)
        for i, name in enumerate(paths.gpu_list()):
            self.combo_gpu.addItem(f"GPU {i} · {name}", i)
        want = getattr(self, "_gpu_dev", -1)
        idx = self.combo_gpu.findData(want)
        if idx < 0 and want >= 0:
            self.combo_gpu.addItem(f"GPU {want}", want)
            idx = self.combo_gpu.count() - 1
        self.combo_gpu.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_gpu.blockSignals(False)

    # ---------- 配置同步 ----------
    def _load_cfg(self):
        lc = self.ctx.launch_cfg()
        self.chk_fp16.setChecked(lc.get("fp16_accumulation", True))
        self.chk_cudamalloc.setChecked(lc.get("cuda_malloc", True))
        self.chk_reserve.setChecked(lc.get("reserve_vram", False))

        # 注意力档位 / 接管开关现在归「环境」页管，这里不再读写它们
        # （否则在启动页点一下保存就会把环境页刚设好的档位覆盖掉）。

        self.spin_port.setValue(int(lc.get("port") or 0))
        self._gpu_dev = int(lc.get("cuda_device", -1))
        self._reload_gpu_list()
        self.chk_hf.setChecked(lc.get("hf_mirror", False))
        self.chk_comfy_proxy.setChecked(lc.get("comfyui_proxy", False))
        self.chk_launcher_proxy.setChecked(lc.get("launcher_proxy", False))
        self.edit_proxy.setText(lc.get("launcher_proxy_url", "http://127.0.0.1:7890"))
        self.edit_extra.setText(lc.get("extra_args", ""))
        self.edit_env.setPlainText(lc.get("env_vars", ""))
        for w in (self.chk_fp16, self.chk_cudamalloc, self.chk_reserve,
                  self.chk_hf, self.chk_comfy_proxy, self.chk_launcher_proxy):
            w.toggled.connect(self._save_cfg)
        self.spin_port.valueChanged.connect(self._save_cfg)
        self.combo_gpu.currentIndexChanged.connect(self._save_cfg)
        self.edit_extra.editingFinished.connect(self._save_cfg)
        self.edit_env.textChanged.connect(self._save_cfg)
        self.edit_proxy.editingFinished.connect(self._save_cfg)

    def _save_cfg(self, *_a):
        lc = self.ctx.launch_cfg()
        lc["fp16_accumulation"] = self.chk_fp16.isChecked()
        lc["cuda_malloc"] = self.chk_cudamalloc.isChecked()
        lc["reserve_vram"] = self.chk_reserve.isChecked()
        # 注意力档位 / attention_shim_enabled 归「环境」页管，这里**不要**动，
        # 否则在启动页改任何参数都会把环境页设的档位覆盖回旧值。
        lc["port"] = self.spin_port.value()
        dev = self.combo_gpu.currentData()
        lc["cuda_device"] = int(dev) if dev is not None else -1
        self._gpu_dev = lc["cuda_device"]
        lc["hf_mirror"] = self.chk_hf.isChecked()
        lc["comfyui_proxy"] = self.chk_comfy_proxy.isChecked()
        lc["launcher_proxy"] = self.chk_launcher_proxy.isChecked()
        lc["launcher_proxy_url"] = self.edit_proxy.text().strip() or "http://127.0.0.1:7890"
        lc["extra_args"] = self.edit_extra.text().strip()
        lc["env_vars"] = self.edit_env.toPlainText()
        # expert 子配置归「专家模式」页管，这里不碰（否则会互相覆盖）
        self.ctx.save_cfg()
        self._refresh_preview()

    def _refresh_preview(self):
        """把当前参数拼成命令行实时显示（纯字符串计算，不启任何进程）。"""
        root = self.ctx.root()
        try:
            if not root:
                self.preview.setPlainText("（先在上面选一个 ComfyUI 包路径）")
                return
            opt = LaunchOptions(self.ctx.launch_cfg())
            self.preview.setPlainText(self.ctx.runner.preview(root, opt))
        except Exception as e:            # 参数拼装异常不该把整个界面拖崩
            self.preview.setPlainText(f"（预览失败：{e}）")

    # ---------- CK 实测（注意力档位 UI 已搬到「环境」页，这里只留启动前的实测）----------
    def _probe_ck_job(self, root):
        d = paths.dirs(root)
        return attnmod.probe_ck(d["python_exe"], d["comfyui"])

    # ---------- 路径 ----------
    def _sync_path_combo(self):
        cfg = self.ctx.cfg
        self.path_combo.blockSignals(True)
        self.path_combo.clear()
        for item in cfg.get("paths", []):
            self.path_combo.addItem(f"{item.get('remark') or '未命名'}  ·  {item.get('root')}")
        i = cfg.get("current_path_index", 0)
        if self.path_combo.count() and 0 <= i < self.path_combo.count():
            self.path_combo.setCurrentIndex(i)
        self.path_combo.blockSignals(False)
        self._on_path_changed(self.path_combo.currentIndex())

    def _on_path_changed(self, idx):
        if idx < 0:
            self._apply_ver("版本信息：尚未选择包路径（点击「管理路径」或「自动扫描」）")
            return
        # 切页时 on_show 也会走到这里，索引没变就别再写一次配置文件
        if self.ctx.cfg.get("current_path_index") != idx:
            self.ctx.cfg["current_path_index"] = idx
            self.ctx.save_cfg()
        self._refresh_version()
        self._refresh_preview()

    def _force_refresh_version(self):
        """「刷新」按钮：清掉探测缓存后重新实测解释器与显卡（后台跑，不卡界面）。"""
        paths.clear_probe_cache()
        self._ver_cache.clear()
        self._refresh_version(force=True)

    def _refresh_version(self, force: bool = False):
        """填顶部版本信息。

        「ComfyUI 版本」只是读一个文件，但解释器版本要起子进程、显卡要问
        nvidia-smi（实测 160 + 200 ms，冷启动更久）。同步跑会让切页明显卡顿，
        所以：命中缓存立刻回填；否则先显示「检测中…」，丢后台线程算完再回填。
        """
        root = self.ctx.root()
        if not root:
            self._apply_ver("版本信息：尚未选择包路径（点击「管理路径」或「自动扫描」）")
            return
        cached = self._ver_cache.get(root)
        if cached and not force:
            self._apply_ver("版本信息：" + cached)
            return
        if self._ver_worker is not None and self._ver_worker.isRunning():
            return
        self._apply_ver("版本信息：检测中…")
        w = self.ctx.spawn(Worker(_probe_version, root))
        self._ver_worker = w
        w.done.connect(self._on_version_ready)
        w.failed.connect(self._on_version_failed)

    def _apply_ver(self, full_text: str):
        """版本信息的统一展示入口。

        数据态（含 `    |    ` 分隔的多段信息，如 ComfyUI 版本 | Python | 显卡）
        渲染成三枚 pastel pill；提示态（未选择 / 检测中 / 失败）回落到一行灰字。
        """
        raw = full_text.split("：", 1)[-1] if "：" in full_text else full_text
        parts = ([p.strip() for p in raw.split("    |    ") if p.strip()]
                 if "    |    " in raw else [])
        lay = self._pill_row.layout()
        while lay.count():                      # 先清掉旧 pill
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if len(parts) >= 2:
            for p in parts:
                lab = QLabel(p.replace("NVIDIA GeForce ", ""))
                lab.setObjectName("Pill")
                lay.addWidget(lab)
            lay.addStretch(1)
            self._pill_row.show()
            self.ver_label.hide()
        else:
            self._pill_row.hide()
            self.ver_label.setText(full_text)
            self.ver_label.show()

    def _on_version_ready(self, res):
        root, text = res
        self._ver_cache[root] = text
        self._ver_worker = None
        # 探测顺带把显卡列表缓存填上了 —— 刷新「指定 GPU」下拉，让它显示显卡名
        self._reload_gpu_list()
        if self.ctx.root() == root:      # 期间可能已经切到别的包路径
            self._apply_ver("版本信息：" + text)

    def _on_version_failed(self, msg):
        self._ver_worker = None
        root = self.ctx.root()
        if root:
            # 失败也写进缓存，否则每次切回本页都会重试一遍
            self._ver_cache[root] = "检测失败（可点「刷新」重试）"
            self._apply_ver("版本信息：检测失败（可点「刷新」重试）")

    def _manage_paths(self):
        dlg = PathDialog(self, self.ctx.cfg)
        if dlg.exec():
            if self.ctx.cfg.get("current_path_index", 0) >= len(self.ctx.cfg.get("paths", [])):
                self.ctx.cfg["current_path_index"] = 0
            self.ctx.save_cfg()
            self._sync_path_combo()

    def _auto_scan(self):
        self.ctx.status("正在扫描各盘符...")
        w = self.ctx.spawn(Worker(paths.auto_discover))

        def _done(found):
            cfg = self.ctx.cfg
            have = {p.get("root") for p in cfg.get("paths", [])}
            added = 0
            for item in found:
                if item["root"] not in have:
                    cfg.setdefault("paths", []).append(item)
                    added += 1
            self.ctx.save_cfg()
            self._sync_path_combo()
            self.ctx.status(f"扫描完成，新增 {added} 个包路径")

        w.done.connect(_done)

    def _open_dir(self, key):
        root = self.ctx.root()
        if not root:
            return
        d = paths.dirs(root).get(key)
        if d and os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def _open_web(self):
        port = self.spin_port.value() or 8188
        QDesktopServices.openUrl(f"http://127.0.0.1:{port}")

    # ---------- 控制台 ----------
    def _on_output(self, line, level):
        self._console_hint_hide()
        self.console.appendPlainText(line)

    def _on_ready(self, url):
        self.ctx.status(f"ComfyUI 已就绪：{url}")
        self._append_sys(f">>> 服务就绪: {url}")
        lc = self.ctx.launch_cfg()
        if lc.get("auto_browser", True):
            QDesktopServices.openUrl(url)

    def _append_sys(self, text):
        self._console_hint_hide()
        self.console.appendPlainText(text)

    def _console_hint_hide(self):
        if self.console_hint.isVisible():
            self.console_hint.hide()

    def _clear_console(self):
        self.console.clear()
        self.console_hint.show()
        self._center_console_hint()

    def eventFilter(self, obj, ev):
        # 控制台尺寸变化时，让空状态提示保持居中
        if obj is self.console and ev.type() == QEvent.Type.Resize:
            self._center_console_hint()
        return super().eventFilter(obj, ev)

    def _center_console_hint(self):
        self.console_hint.setGeometry(self.console.rect())

    # ---------- 日志复制（贴给 AI 排查插件问题）----------
    def _log_header(self, kind: str, lines: int) -> str:
        """给复制出来的日志加一段自述，贴到对话里 AI 不用再问环境。"""
        root = self.ctx.root()
        cmd = self.preview.toPlainText().strip()
        when = time.strftime("%Y-%m-%d %H:%M:%S")
        ver = self._ver_cache.get(root) or "（版本信息尚未探测完）"
        return (
            f"# ComfyUI Echo 启动器日志 · {kind}\n"
            f"# 时间：{when}\n"
            f"# 包路径：{root or '（未选择）'}\n"
            f"# 环境：{ver}\n"
            f"# 启动命令：{cmd or '（尚未生成）'}\n"
            f"# 本段 {lines} 行\n"
            + "-" * 46 + "\n")

    @staticmethod
    def _extract_issues(lines, context: int = 2, cap: int = 400):
        """只挑报错/警告行并带上前后各 context 行，块与块之间插省略标记。"""
        hit = set()
        for i, ln in enumerate(lines):
            if ISSUE_RX.search(ln):
                hit.update(range(max(0, i - context),
                                 min(len(lines), i + context + 1)))
        if not hit:
            return []
        picked, prev = [], None
        for i in sorted(hit):
            if prev is not None and i - prev > 1:
                picked.append("    ...（此处省略若干无关行）")
            picked.append(lines[i])
            prev = i
            if len(picked) >= cap:
                picked.append(f"    ...（已达 {cap} 行上限，完整日志可在控制台 Ctrl+A 复制）")
                break
        return picked

    def _copy_log(self):
        """复制控制台日志：自动挑出报错/警告 + 环境信息头，直接贴给 AI 排查插件。

        想拿完整日志，直接在控制台里 Ctrl+A / Ctrl+C 即可（这里刻意只给摘要，
        贴过去省 token 也更容易让 AI 定位到真正的问题点）。
        """
        text = self.console.toPlainText()
        if not text.strip():
            self.ctx.status("控制台还没有日志可复制")
            return
        lines = text.splitlines()
        body = self._extract_issues(lines)
        if body:
            kind = "报错与警告摘要"
        else:
            body, kind = lines[-200:], "未发现报错/警告，改为最近 200 行"
        payload = self._log_header(f"{kind}（共 {len(lines)} 行日志）", len(body)) \
            + "\n".join(body)
        QApplication.clipboard().setText(payload)
        self._flash_copy(f"已复制「{kind}」{len(body)} 行 / {len(payload)} 字符",
                         self.btn_copy, "复制日志")

    def _copy_cmd(self):
        cmd = self.preview.toPlainText().strip()
        if not cmd:
            self.ctx.status("还没有可复制的启动命令")
            return
        QApplication.clipboard().setText(cmd)
        self._flash_copy("启动命令已复制", self.btn_copy_cmd, "复制命令")

    def _flash_copy(self, msg: str, btn, back: str):
        """按钮上给个短暂回执，不用去瞟状态栏。"""
        self.ctx.status(msg)
        btn.setText("已复制 ✓")
        QTimer.singleShot(1500, lambda b=btn, t=back: b.setText(t))

    def _on_state(self, running):
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    # ---------- 启动 ----------
    def on_start(self):
        if self.ctx.runner.is_running:
            return
        root = self.ctx.root()
        err = paths.validate_root(root)
        if err:
            QMessageBox.warning(self, "无法启动", f"包路径无效：{err}\n请先在「管理路径」中选择有效目录。")
            return
        self._save_cfg()

        # CK 档前置实测（Echo 行为）：comfy-kitchen 没编译 INT8 时传
        # --use-ck-attention 会让 ComfyUI 直接 exit(-1) 崩掉，必须先确认。
        # 实测结果存在 ctx 上：在「环境」页点过「检测 CK」的话这里直接复用，不重复测。
        lc = self.ctx.launch_cfg()
        if attnmod.normalize_mode(lc.get("attention_mode")) == "CK" and self.ctx.ck_ok is not True:
            self._append_sys(">>> CK 档：先实测 comfy-kitchen 的 INT8 注意力可用性...")
            self.btn_start.setEnabled(False)
            w = self.ctx.spawn(Worker(self._probe_ck_job, root))
            w.done.connect(self._on_ck_probed_then_start)
            w.failed.connect(lambda m: self._start_fail(m))
            return

        self._pre_launch(root)

    def _on_ck_probed_then_start(self, res):
        ok, txt = res
        self.ctx.ck_ok, self.ctx.ck_text = ok, txt
        if not ok:
            self._append_sys(f"[CK 不可用] {txt}")
            self.ctx.status("CK 档不可用，已取消启动")
            QMessageBox.critical(
                self, "CK 不可用，已取消启动",
                f"实测 comfy-kitchen 的 INT8 注意力不可用：\n\n{txt}\n\n"
                "此时传 --use-ck-attention 会让 ComfyUI 直接退出（exit -1），所以没有启动。\n\n"
                "请改用 SAGE / FLASH 档，或先修复 comfy-kitchen。")
            self._on_state(False)
            return
        self._append_sys(f">>> CK 可用：{txt}")
        self._pre_launch(self.ctx.root())

    def _pre_launch(self, root):
        """端口占用处理 -> 应用模式 -> 装 shim -> 真正拉起。"""
        lc = self.ctx.launch_cfg()
        port = int(lc.get("port") or 0) or 8188
        if procmod.port_in_use(port):
            ans = QMessageBox.question(
                self, "端口被占用",
                f"端口 {port} 已被占用。\n\n"
                "「是」= 结束占用该端口的进程\n「否」= 自动改用空闲端口",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans == QMessageBox.Yes:
                killed = procmod.kill_port(port)
                self._append_sys(f">>> 已结束占用端口 {port} 的进程 {killed} 个")
            if procmod.port_in_use(port):
                newp = procmod.find_free_port(port)
                self.spin_port.setValue(newp)
                self._append_sys(f">>> 端口 {port} 仍被占用，改用 {newp}")
        self._save_cfg()

        cfg = self.ctx.cfg
        wl, mode = [], ""
        if cfg.get("apply_mode_on_launch"):
            mode = cfg.get("last_mode", "")
            wl = (cfg.get("modes", {}).get(mode, {}) or {}).get("plugins", []) or []
        if wl:
            self._append_sys(f">>> 应用模式「{mode}」: 调整插件启用集合...")
            self.btn_start.setEnabled(False)
            w = self.ctx.spawn(Worker(plugins.apply_mode, root, wl))
            w.done.connect(lambda res: self._after_mode(res, root))
            w.failed.connect(lambda m: self._start_fail(m))
            return
        self._arm_shim_then_start(root)

    def _after_mode(self, res, root):
        moved, errors = res
        self._append_sys(f">>> 模式应用完成（调整 {moved} 个插件目录"
                         + (f"，{len(errors)} 个失败" if errors else "") + "）")
        for e in errors:
            self._append_sys("    " + e)
        self._arm_shim_then_start(root)

    def _arm_shim_then_start(self, root):
        lc = self.ctx.launch_cfg()
        if lc.get("attention_shim_enabled"):
            proot = self._attn_root()
            mode = attnmod.normalize_mode(lc.get("attention_mode"))
            if proot:
                self._append_sys(f">>> 启动前更新切换器接管（{mode}）...")
                w = self.ctx.spawn(Worker(attnmod.apply_shim, proot, mode))
                w.done.connect(lambda res: self._shim_then_start(res, root))
                w.failed.connect(lambda m: self._shim_then_start((False, m), root))
                return
            self._append_sys(">>> 无法定位便携根目录，跳过切换器接管")
        self._do_start(root)

    def _shim_then_start(self, res, root):
        ok, msg = res
        self._append_sys((">>> 切换器：" if ok else ">>> 切换器失败：") + str(msg))
        self._do_start(root)

    def _do_start(self, root):
        opt = LaunchOptions(self.ctx.launch_cfg())
        self._append_sys(f">>> 启动 ComfyUI: {paths.dirs(root)['comfyui']}")
        self._append_sys(">>> " + self.ctx.runner.preview(root, opt))
        msg = self.ctx.runner.start(root, opt)
        if msg:
            self._start_fail(msg)

    def _start_fail(self, msg):
        self._append_sys(f"[启动失败] {msg}")
        self.ctx.status("启动失败：" + msg)
        self._on_state(False)

    def on_show(self):
        self._sync_path_combo()
        # 注意力档位现在在「环境」页改，切回来时把启动命令预览刷一遍
        self._refresh_preview()
