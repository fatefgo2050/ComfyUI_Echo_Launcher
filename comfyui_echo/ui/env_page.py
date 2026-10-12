"""Python 环境维护页：**左边功能区，右边输出日志**。

布局对齐 TE 启动器的「ComfyUI维护」页：左栏按功能分组，右栏是一整块输出日志。

    当前 Python 环境   路径（只读）+ 打开目录
    镜像源             PyPI 镜像下拉
    库管理            库名称 / 库版本 + 查找 / 安装 / 删除 / 列出已安装库
                        + 安装 whl 轮子 / 装依赖 / Python 命令行
    备份与还原         备份当前环境 / 对比两次环境 / 还原环境信息
    特殊安装           安装 Torch / Triton / SageAttention / xFormers / llama-cpp

- 包管理走 Echo 的 `env.pipwrap`（`core/pipenv.py`），重型轮子走 `core/wheels.py`。
- **「注意力」（档位 / 检测 CK / 强制接管 shim）已搬到「专家模式」页**
  （`expert_page.py`，2026-10-09 按用户要求），配置键不变，CK 实测结果仍存
  `ctx.ck_ok/ck_text` 供「启动」页复用。
- 耗时操作全在 Worker 里跑，输出实时打进右栏日志。
"""
from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from .dialogs import MessageBox as QMessageBox

from ..core import core_version as CV
from ..core import paths as _paths
from ..core import pipenv
from ..core import wheels as W
from ..core.config import PYPI_MIRRORS
from .workers import Worker

LABEL_W = 66          # 左栏表单的标签列宽（和启动页保持一致的观感）

# ---- 间距常量（依据本地 skill `minimalist-ui`：4px 网格 + 靠「邻近性」表达分组）----
# 同一个分组里，「按钮」和「字段」是两类东西：按钮是一排可点的动作，字段是要填的输入。
# 原来的问题是**两者共用同一个 6px 行距**，于是按钮簇看起来像表单又多了一行；
# 而按钮彼此之间也只有 6px，1px 描边 + 6px 圆角挤在一起，像一条粘住的胶带。
# 现在按「谁跟谁是一组」分三档（数字都落在 4px 网格上）：
BTN_GAP = 10          # ★同一行按钮之间（查找/安装/删除/列出已安装库…）
BTN_WRAP = 4          # 换行后按钮行的**额外**上边距 → 行距 6+4=10 = BTN_GAP，换行仍是一个匀齐的矩阵
BTN_LEAD = 6          # 动作簇与上方字段/标题的**额外**上边距 → 12，把「动作区」从表单里划出来
ROW_GAP = 6           # 卡片内「行」的基础行距（字段行之间）。
#   ⚠ 这一档**不在 4px 网格上**，是纵向预算卡出来的：1100×700 下左栏内容 608px / 视口 626px，
#     **只剩 18px 余量**；改成 8 会多出 24px，立刻撑出滚动条（实测）。
#     所以「让按钮松快」只能花在 BTN_* 上，字段行距保持不动。
#   📏 每次改动后跑 `tests/ui_min_size.py` 末段「环境页左栏纵向预算」看这四个数。


def _card(title: str, actions=None):
    """左栏的一个功能分组：`QFrame#Card` + 标题（可选：标题行右端的迷你按钮）。"""
    frame = QFrame()
    frame.setObjectName("Card")
    v = QVBoxLayout(frame)
    # 内边距收成上下对称的 8px（原来上 10 / 下 12）。省下的这 20px 让给 BTN_* ——
    # 卡片内边距是「空」，按钮间距是「内容」，预算只能有一个去处时给内容。
    v.setContentsMargins(12, 8, 12, 8)
    v.setSpacing(ROW_GAP)
    head = QHBoxLayout()
    head.setContentsMargins(0, 0, 0, 0)
    head.setSpacing(6)
    lab = QLabel(title)
    lab.setObjectName("CardTitle")
    head.addWidget(lab)
    head.addStretch(1)
    for a in (actions or []):
        head.addWidget(a)
    v.addLayout(head)
    return frame, v


def _row(parent, label: str, widget, extra=None):
    """标签 + 控件（占满余宽）+ 可选尾随控件。"""
    h = QHBoxLayout()
    h.setSpacing(8)
    lab = QLabel(label)
    lab.setFixedWidth(LABEL_W)
    lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    h.addWidget(lab)
    h.addWidget(widget, 1)
    if extra is not None:
        h.addWidget(extra)
    parent.addLayout(h)
    return h


def _btn_bar(parent, *buttons, gap_top=0):
    """一行按钮（左对齐）。

    `gap_top` 是**额外的上边距**，用来表达层次（默认 0 = 不额外加）：
      - `BTN_LEAD`：分组里的**第一行**按钮。和上方字段/标题拉开到 12px，
        让「动作簇」从表单里浮出来 —— 依据 skill 的邻近性原则。
      - `BTN_WRAP`：**紧跟上一行按钮**的那行。拉到 10px，与行内间隙 `BTN_GAP` 一致，
        换行之后仍是一个上下匀齐的按钮矩阵，而不是两行粘在一起。
    """
    h = QHBoxLayout()
    h.setSpacing(BTN_GAP)
    if gap_top:
        h.setContentsMargins(0, gap_top, 0, 0)
    for b in buttons:
        h.addWidget(b)
    h.addStretch(1)
    parent.addLayout(h)
    return h


def _mini(text, tip=""):
    b = QPushButton(text)
    b.setObjectName("Mini")
    if tip:
        b.setToolTip(tip)
    return b


class EnvPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._probe_result = None
        self._installed = []

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 10)
        outer.setSpacing(10)

        # ---- 左：功能区（可滚动：窗口矮的时候不至于把下面的按钮挤没）----
        left = QScrollArea()
        left.setObjectName("PlainScroll")
        left.setWidgetResizable(True)
        left.setFrameShape(QFrame.NoFrame)
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        lv = QVBoxLayout(inner)
        lv.setContentsMargins(0, 0, 6, 0)
        lv.setSpacing(8)          # 卡与卡之间的**最小**间距（余量另外分摊，见下）
        # ★余量按权重分摊到卡间距，而不是在末尾留一大片空白
        #   （用户 2026-10-10：「注意力卡移除后把空出的空白用一下」）。
        #   ⚠ 这里只管卡与卡之间的留白 —— **按钮的松紧请改上面的 BTN_* 常量**，
        #     用户明确说过要调的是「功能区**里边**的按钮间距」，不是这一层。
        for build in (self._group_python, self._group_mirror, self._group_packages,
                      self._group_backup, self._group_heavy):
            build(lv)
        cards = [c for c in (lv.itemAt(i).widget() for i in range(lv.count()))
                 if c is not None]
        while lv.count():
            lv.takeAt(0)
        for i, c in enumerate(cards):
            if i:
                lv.addStretch(1)
            lv.addWidget(c)
        lv.addStretch(2)             # 末尾权重略大，别让末卡浮在半空
        left.setWidget(inner)
        left.setMinimumWidth(430)
        outer.addWidget(left, 0)

        # ---- 右：输出日志 ----
        self.btn_env_info = _mini(
            "获取环境信息",
            "把 Python / 显卡 / ComfyUI / torch / SageAttention 等版本打一份到日志")
        self.btn_clear_log = _mini("清空日志")
        card, rv = _card("输出日志", [self.btn_env_info, self.btn_clear_log])
        self.log = QPlainTextEdit()
        self.log.setObjectName("Console")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setPlaceholderText(
            "按左侧「查找 / 安装 / 列出已安装库」等操作，输出会实时打在这里")
        rv.addWidget(self.log, 1)
        outer.addWidget(card, 1)

        self.btn_env_info.clicked.connect(self.show_env_info)
        self.btn_clear_log.clicked.connect(self.log.clear)

    # ------------------------------------------------------------------
    # 左栏分组
    # ------------------------------------------------------------------
    def _group_python(self, parent):
        card, v = _card("当前 Python 环境")
        self.edit_py = QLineEdit()
        self.edit_py.setReadOnly(True)
        self.edit_py.setPlaceholderText("（未配置 ComfyUI 包路径）")
        btn_open = _mini("打开目录", "在资源管理器里打开 python_embeded")
        btn_open.clicked.connect(self._open_py)
        _row(v, "路径", self.edit_py, extra=btn_open)
        parent.addWidget(card)

    def _group_mirror(self, parent):
        card, v = _card("镜像源")
        self.mirror = QComboBox()
        self.mirror.addItems(list(PYPI_MIRRORS.keys()))
        self.mirror.setCurrentText(self.ctx.launch_cfg().get("pypi_mirror", "阿里云"))
        self.mirror.setToolTip("pip 安装/查询走的索引源")
        self.mirror.currentTextChanged.connect(self._mirror_changed)
        _row(v, "选择镜像源", self.mirror)
        parent.addWidget(card)

    def _group_packages(self, parent):
        card, v = _card("库管理")
        self.cmb_pkg = QComboBox()
        self.cmb_pkg.setEditable(True)
        self.cmb_pkg.setInsertPolicy(QComboBox.NoInsert)
        self.cmb_pkg.lineEdit().setPlaceholderText("请先查找库，或直接输入包名")
        self.cmb_ver = QComboBox()
        self.cmb_ver.setEditable(True)
        self.cmb_ver.setInsertPolicy(QComboBox.NoInsert)
        self.cmb_ver.lineEdit().setPlaceholderText("留空 = 最新版")
        _row(v, "库名称", self.cmb_pkg)
        _row(v, "库版本", self.cmb_ver)

        b_find = _mini("查找", "查这个包有哪些版本（走当前镜像源）")
        b_install = _mini("安装", "安装「库名称 == 库版本」；版本留空则装最新")
        b_del = _mini("删除", "卸载「库名称」")
        b_list = _mini("列出已安装库", "读取当前环境已装的包，填进「库名称」下拉")
        _btn_bar(v, b_find, b_install, b_del, b_list, gap_top=BTN_LEAD)

        b_whl = _mini("安装 whl 轮子", "选一个本地 .whl 文件安装")
        b_req = _mini("装依赖", "选一个 requirements.txt 安装里面的依赖")
        b_cmd = _mini("Python 命令行", "在当前解释器里跑一段 python 代码")
        _btn_bar(v, b_whl, b_req, b_cmd, gap_top=BTN_WRAP)

        b_find.clicked.connect(self.find_versions)
        b_install.clicked.connect(self.install_spec)
        b_del.clicked.connect(self.uninstall_pkg)
        b_list.clicked.connect(self.refresh)
        b_whl.clicked.connect(self.install_whl)
        b_req.clicked.connect(self.install_requirements)
        b_cmd.clicked.connect(self.python_console)
        parent.addWidget(card)

    def _group_backup(self, parent):
        card, v = _card("备份与还原")
        b_backup = _mini("备份当前环境", "导出 pip freeze 快照（requirements_*.txt）")
        b_diff = _mini("对比两次环境", "选两份快照，列出差异")
        b_restore = _mini("还原环境信息", "用一份快照把依赖装回来")
        _btn_bar(v, b_backup, b_diff, b_restore, gap_top=BTN_LEAD)
        b_backup.clicked.connect(self.backup_env)
        b_diff.clicked.connect(self.diff_env)
        b_restore.clicked.connect(self.restore_env)
        parent.addWidget(card)

    def _group_heavy(self, parent):
        card, v = _card("特殊安装")
        self.cuda_tag = QComboBox()
        self.cuda_tag.addItems(W.CUDA_TAGS)
        self.cuda_tag.setCurrentText("cu128")
        self.cuda_tag.setToolTip("torch 用哪个 CUDA 轮子")
        self.edit_torch_ver = QLineEdit()
        self.edit_torch_ver.setPlaceholderText("留空 = 最新版")
        _row(v, "CUDA 标签", self.cuda_tag)
        _row(v, "torch 版本", self.edit_torch_ver)

        b_torch = _mini("安装 Torch", "torch / torchvision / torchaudio（可能十几分钟）")
        b_triton = _mini("安装 Triton", "pip install triton-windows")
        b_sage = _mini("安装 SageAttention", "按当前 torch 版本挑预编译轮子")
        b_xform = _mini("安装 xFormers", "按当前 torch 版本挑预编译轮子")
        b_llama = _mini("安装 llama-cpp", "pip install llama-cpp-python（无预编译轮子时较慢）")
        _btn_bar(v, b_torch, b_triton, b_sage, gap_top=BTN_LEAD)
        _btn_bar(v, b_xform, b_llama, gap_top=BTN_WRAP)

        b_torch.clicked.connect(self.install_torch)
        b_triton.clicked.connect(lambda: self.install_pkg("triton-windows", "Triton"))
        b_sage.clicked.connect(lambda: self.install_kind("sage", "SageAttention"))
        b_xform.clicked.connect(lambda: self.install_kind("xformers", "xFormers"))
        b_llama.clicked.connect(lambda: self.install_pkg("llama-cpp-python", "llama-cpp"))

        tip = QLabel("轮子源：pytorch 官方索引与公开 Release；Triton 走 PyPI。")
        tip.setObjectName("VersionHint")
        tip.setWordWrap(True)
        tip.setToolTip("torch 走 pytorch 官方索引；SageAttention 走 woct0rdho/SageAttention、"
                       "xFormers 走 Wildminder/AI-windows-whl 的 GitHub Release；"
                       "Triton 走 PyPI 的 triton-windows。")
        v.addWidget(tip)
        parent.addWidget(card)

    # ------------------------------------------------------------------
    # 通用
    # ------------------------------------------------------------------
    def _log(self, text: str):
        self.log.appendPlainText(text)

    def _mirror_changed(self, text):
        self.ctx.launch_cfg()["pypi_mirror"] = text
        self.ctx.save_cfg()

    def _require_root(self) -> str:
        root = self.ctx.root()
        if not root or _paths.validate_root(root):
            QMessageBox.warning(self, "无可用包", "请先在「启动」页选择有效的 ComfyUI 包路径。")
            return ""
        return root

    def _python(self) -> str:
        root = self.ctx.root()
        return _paths.dirs(root)["python_exe"] if root else ""

    def _open_py(self):
        py = self._python()
        if py:
            d = os.path.dirname(py)
            if os.path.isdir(d):
                QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    # ------------------------------------------------------------------
    # 环境信息
    # ------------------------------------------------------------------
    def show_env_info(self):
        """把当前环境信息打一份到右栏（对齐 TE「ComfyUI维护」页那份输出）。

        收集过程要起子进程（读包版本）和 git（读内核 HEAD），一律丢 Worker。
        """
        py = self._python()
        root = self.ctx.root()
        self._log("─" * 46)
        self._log(f"当前 Python 路径：{py or '（未配置 ComfyUI 包路径）'}")
        if not root or _paths.validate_root(root):
            self._log("请先在「启动」页选择有效的 ComfyUI 包路径，再获取环境信息。")
            return
        self._log("正在收集环境信息…")
        w = self.ctx.spawn(Worker(self._collect_env_info, root, py))

        def _done(lines):
            for ln in lines:
                self._log(ln)
            self.ctx.status("环境信息已输出到日志")

        w.done.connect(_done)
        w.failed.connect(lambda m: self._log("收集失败：" + m))

    def _collect_env_info(self, root: str, py: str) -> list:
        """在后台线程里把各版本读齐（返回要写进日志的行）。"""
        lines = []
        gpu = _paths.gpu_name()
        lines.append(f"显卡型号：GPU 0: {gpu or '未知'}")

        vers = W.versions_of(py) if py else {}
        pyver = vers.get("__python__") or _paths.python_version(root) or "未知"
        lines.append(f"Python 版本：{pyver}")

        cv_ver = _paths.comfyui_version(root)
        cur = CV.current(root) or {}
        if cur:
            tail = f" · v{cv_ver}" if cv_ver and cv_ver != "未知" else ""
            lines.append(f"ComfyUI 版本：HEAD {cur.get('short', '')} "
                         f"[{cur.get('date', '')}]{tail}")
        else:
            lines.append(f"ComfyUI 版本：v{cv_ver}（不是 git 仓库，读不到 HEAD）")

        for pkg, label in (("torch", "torch"), ("torchvision", "torchvision"),
                           ("torchaudio", "torchaudio"),
                           ("sageattention", "SageAttention"),
                           ("triton-windows", "triton-windows"),
                           ("xformers", "xFormers"),
                           ("llama-cpp-python", "llama-cpp-python")):
            v = vers.get(pkg, "")
            lines.append(f"{label} 版本：{v or '（未安装）'}")
        return lines

    def on_show(self):
        py = self._python()
        self.edit_py.setText(py or "")
        self.edit_py.setCursorPosition(0)      # 否则显示的是路径尾部（光标在末尾）
        self.edit_py.setToolTip(py or "")

    # ------------------------------------------------------------------
    # 库管理
    # ------------------------------------------------------------------
    def refresh(self):
        """列出已安装库 -> 填进「库名称」下拉。"""
        root = self._require_root()
        if not root:
            return
        self.ctx.status("正在读取 pip 包列表...")
        self._log(">>> pip list")
        w = self.ctx.spawn(Worker(pipenv.list_packages, root, self.mirror.currentText()))

        def _done(pkgs):
            self._installed = pkgs or []
            self.cmb_pkg.clear()
            for p in self._installed:
                self.cmb_pkg.addItem(p.get("name", ""), p.get("version", ""))
                # 逐条打进日志（只报个总数的话，日志里等于什么都没说）
                self._log(f"    {p.get('name', '')}=={p.get('version', '')}")
            self._log(f">>> 已安装 {len(self._installed)} 个包")
            self.ctx.status(f"共 {len(self._installed)} 个包")

        w.done.connect(_done)
        w.failed.connect(lambda m: (self._log(">>> 读取失败：" + m),
                                    QMessageBox.warning(self, "读取失败", m)))

    def _selected_pkg(self) -> str:
        return (self.cmb_pkg.currentText() or "").strip()

    def find_versions(self):
        """查这个包有哪些版本（`pip index versions`，走当前镜像源）。"""
        root = self._require_root()
        name = self._selected_pkg()
        if not root or not name:
            QMessageBox.information(self, "提示", "先在「库名称」里填/选一个包名")
            return
        self._log(f">>> pip index versions {name}")
        w = self.ctx.spawn(Worker(pipenv.run_pip, root,
                                  ["index", "versions", name],
                                  self.mirror.currentText(), "", None))

        def _done(res):
            code, out = res
            text = out or ""
            for line in text.splitlines():
                self._log(line)
            vers = []
            for line in text.splitlines():
                if "Available versions:" in line:
                    vers = [v.strip() for v in
                            line.split("Available versions:")[1].split(",") if v.strip()]
            if vers:
                self.cmb_ver.clear()
                self.cmb_ver.addItems(vers)
                self.cmb_ver.setCurrentIndex(0)
                self.ctx.status(f"{name}: {len(vers)} 个版本")
            else:
                self.ctx.status(f"没查到 {name} 的版本（包名对不对？）")

        w.done.connect(_done)
        w.failed.connect(lambda m: self._log(">>> 查找失败：" + m))

    def install_spec(self):
        name = self._selected_pkg()
        if not name:
            QMessageBox.information(self, "提示", "先在「库名称」里填/选一个包名")
            return
        ver = (self.cmb_ver.currentText() or "").strip()
        spec = f"{name}=={ver}" if ver else name
        self._run_pip(f"安装 {spec}", ["install", spec])

    def uninstall_pkg(self):
        name = self._selected_pkg()
        if not name:
            QMessageBox.information(self, "提示", "先在「库名称」里填/选一个包名")
            return
        if QMessageBox.question(self, "确认卸载", f"卸载：{name} ?") != QMessageBox.Yes:
            return
        self._run_pip(f"卸载 {name}", ["uninstall", "-y", name])

    def install_whl(self):
        root = self._require_root()
        if not root:
            return
        f, _ = QFileDialog.getOpenFileName(self, "选择 wheel 文件", "",
                                           "Wheel (*.whl);;所有文件 (*)")
        if f:
            self._run_pip(f"安装 {os.path.basename(f)}", ["install", f])

    def install_requirements(self):
        root = self._require_root()
        if not root:
            return
        f, _ = QFileDialog.getOpenFileName(self, "选择 requirements.txt", "",
                                           "Text (*.txt);;所有文件 (*)")
        if not f:
            return
        self._log(f">>> pip install -r {f}")
        w = self.ctx.spawn(Worker(pipenv.install_requirements, root, f,
                                  self.mirror.currentText(), "",
                                  lambda s: self._log(s)))

        def _done(code):
            self._log(">>> 完成" if code == 0 else f">>> 失败（代码 {code}）")
            self.refresh()

        w.done.connect(_done)

    def python_console(self):
        """在当前解释器里跑一段 python 代码，输出打到右栏。"""
        py = self._python()
        if not py:
            QMessageBox.information(self, "提示", "请先在「启动」页选择有效的 ComfyUI 包路径")
            return
        code, ok = QInputDialog.getMultiLineText(
            self, "Python 命令行", f"{py}\n\n输入要执行的 python 代码：",
            "import sys; print(sys.version)")
        if not ok or not code.strip():
            return
        self._log(f">>> python -c {code.strip()}")
        w = self.ctx.spawn(Worker(self._run_python, py, code))

        def _done(res):
            code_rc, out = res
            if out:
                self._log(out)
            self._log(">>> 完成" if code_rc == 0 else f">>> 退出码 {code_rc}")

        w.done.connect(_done)
        w.failed.connect(lambda m: self._log(">>> 执行异常：" + m))

    def _run_python(self, py: str, code: str):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            p = subprocess.run([py, "-c", code], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=300,
                               creationflags=0x08000000, env=env)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except Exception as e:
            return 1, str(e)

    # ------------------------------------------------------------------
    # 备份与还原
    # ------------------------------------------------------------------
    def backup_env(self):
        root = self._require_root()
        if not root:
            return
        out, _ = QFileDialog.getSaveFileName(self, "保存环境快照",
                                             "requirements_freeze.txt", "Text (*.txt)")
        if not out:
            return
        ok, path = pipenv.freeze(root, out, self.mirror.currentText())
        self._log(f">>> 快照{'已保存: ' + path if ok else '导出失败'}")
        self.ctx.status("快照已保存: " + path if ok else "导出失败")

    def restore_env(self):
        root = self._require_root()
        if not root:
            return
        f, _ = QFileDialog.getOpenFileName(self, "选择要还原的快照", "",
                                           "Text (*.txt);;所有文件 (*)")
        if not f:
            return
        if QMessageBox.question(
                self, "确认还原",
                f"按快照「{os.path.basename(f)}」安装依赖？\n\n"
                "只做「装上缺的」，不会卸载快照里没有的包。") != QMessageBox.Yes:
            return
        self._log(f">>> pip install -r {f}")
        w = self.ctx.spawn(Worker(pipenv.install_requirements, root, f,
                                  self.mirror.currentText(), "",
                                  lambda s: self._log(s)))

        def _done(code):
            self._log(">>> 还原完成" if code == 0 else f">>> 还原失败（代码 {code}）")
            self.refresh()

        w.done.connect(_done)

    def diff_env(self):
        """选两份快照，列出差异（只比包名，忽略版本细节以外的行）。"""
        a, _ = QFileDialog.getOpenFileName(self, "选择第一份快照（旧）", "",
                                           "Text (*.txt);;所有文件 (*)")
        if not a:
            return
        b, _ = QFileDialog.getOpenFileName(self, "选择第二份快照（新）", "",
                                           "Text (*.txt);;所有文件 (*)")
        if not b:
            return
        try:
            with open(a, "r", encoding="utf-8", errors="replace") as f:
                la = {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}
            with open(b, "r", encoding="utf-8", errors="replace") as f:
                lb = {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}
        except Exception as e:
            self._log(f">>> 读取快照失败：{e}")
            return
        only_a = sorted(la - lb)
        only_b = sorted(lb - la)
        self._log(f">>> 对比 {os.path.basename(a)} → {os.path.basename(b)}")
        if not only_a and not only_b:
            self._log("    两份快照完全一致")
        for ln in only_a:
            self._log(f"    - {ln}    （只在旧快照里）")
        for ln in only_b:
            self._log(f"    + {ln}    （只在新快照里）")
        self.ctx.status(f"差异：旧独有 {len(only_a)} / 新独有 {len(only_b)}")

    # ------------------------------------------------------------------
    # 特殊安装
    # ------------------------------------------------------------------
    def install_torch(self):
        py = self._python()
        if not py:
            QMessageBox.information(self, "提示", "请先在「启动」页选择有效的 ComfyUI 包路径")
            return
        tag = self.cuda_tag.currentText()
        ver = self.edit_torch_ver.text().strip()
        self._log(f">>> 安装 torch（{tag}{' ' + ver if ver else ''}）… 可能需要十几分钟")
        # 用 *_stream 版：Echo 原版是 capture_output，要等十几分钟才一次性吐结果
        w = self.ctx.spawn(Worker(W.install_torch_stream, py, tag, ver,
                                  on_line=lambda s: self._log(s)))

        def _done(res):
            ok, _msg = res
            self._log(f">>> torch 安装{'成功' if ok else '失败'}")
            self.ctx.status(f"torch 安装{'成功' if ok else '失败'}")

        w.done.connect(_done)
        w.failed.connect(lambda m: self._log(">>> 安装异常：" + m))

    def install_pkg(self, name: str, label: str):
        """普通 pip 包（Triton / llama-cpp）。"""
        self._run_pip(f"安装 {label}（{name}）", ["install", name])

    def install_kind(self, kind: str, label: str):
        """按 torch 版本挑预编译轮子（sage / xformers）。"""
        py = self._python()
        if not py:
            QMessageBox.information(self, "提示", "请先在「启动」页选择有效的 ComfyUI 包路径")
            return
        torch_ver = (self._probe_result.torch_version if self._probe_result else "")
        urls = W.candidate_urls(kind, torch_ver)
        if not urls:
            self._log(f">>> 未找到 {label} 的可用 wheel（先「探测环境」拿到 torch 版本）")
            return
        url = urls[0]
        self._log(f">>> 安装 {label}: {url}")
        # 同上：走流式版，实时看进度（原版要等装完才吐结果）
        w = self.ctx.spawn(Worker(W.install_url_stream, py, url,
                                  on_line=lambda s: self._log(s)))

        def _done(res):
            ok, _msg = res
            self._log(f">>> {label} 安装{'成功' if ok else '失败'}")
            self.ctx.status(f"{label} 安装{'成功' if ok else '失败'}")

        w.done.connect(_done)
        w.failed.connect(lambda m: self._log(">>> 安装异常：" + m))

    # ------------------------------------------------------------------
    # pip 通用
    # ------------------------------------------------------------------
    def _run_pip(self, title: str, args: list):
        root = self._require_root()
        if not root:
            return
        self._log(f">>> pip {' '.join(args)}")
        # 注意别把 title 当第二个位置参数传进来——_pip_job 的第二个形参是 report，
        # 传了 title 就会被当成进度回调（bug 曾经把标题字符串塞给 report）
        w = self.ctx.spawn(Worker(self._pip_job, args))

        def _done(code):
            self._log(">>> 完成" if code == 0 else f">>> 失败（代码 {code}）")
            self.ctx.status(f"{title}：{'完成' if code == 0 else '失败'}")
            self.refresh()

        w.done.connect(_done)

    def _pip_job(self, args, report=None):
        root = self._require_root()
        if not root:
            return None
        code, _out = pipenv.run_pip(root, list(args), self.mirror.currentText(),
                                    on_line=lambda s: self._log(s))
        return code
