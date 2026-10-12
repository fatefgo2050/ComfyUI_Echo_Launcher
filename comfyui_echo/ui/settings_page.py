"""设置页：只暴露 **启动器 UI 视图里没有** 的 Echo 配置键。

代理 / HF 镜像 / pip 镜像 / 注意力 / shim / 自定义参数这些都在「启动」页和「环境」页，
这里不再重复，避免两处写入互相覆盖。

本页写入的键（都不在 UI 视图映射里，所以直接写 Echo 的 Config）：
    update_repo                      自更新的 GitHub 仓库 owner/name
    github_token                     GitHub API 限额 60 -> 5000/h
    disable_ssl_verify               关掉 SSL 校验（自签证书/抓包时用）
    minimize_to_tray                 关闭窗口时最小化到托盘
    enable_duplicate_plugin_check    扫描插件时做重复检测
    enable_tray                      启用系统托盘
    notify_on_ready                  ComfyUI 就绪时弹 Windows 通知
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from .dialogs import MessageBox as QMessageBox

from ..core import config as cfgmod
from ..core import selfupdate as SU
from .widgets import card, page_header
from .workers import Worker

# ---- 间距档位（4px 网格）----------------------------------------------------
# 本页所有留白都从这里取，别再散落硬编码。依据本地设计 skill
# （high-end-visual-design）的 Macro-Whitespace 一条：**容器四周要留出气口、
# 卡片内容不能贴着卡片边**，所以这里比项目默认的 14/10/14/12 整体加厚了一档。
# 改就整档改（4px 步进），别只改一个数。
PAGE_PAD = (20, 16, 20, 16)     # 页面四周（卡片 / 页头离窗口边）
HEADER_GAP = 14                 # 页头与网格之间
GRID_GAP = 12                   # 卡片与卡片之间（横竖同档）
CARD_PAD = (20, 16, 20, 16)     # 卡片内边距（卡片内容离卡片边）
CARD_GAP = 12                   # 卡片内元素之间（标题 / 行 / 说明）
ROW_LABEL_W = 118               # 「标签」列定宽（右对齐）
ROW_GAP = 10                    # 「标签 ↔ 控件」之间
BTN_GAP = 8                     # 按钮行内按钮之间

#: 文本框自动保存的去抖时长（毫秒）。**必须去抖**：`textChanged` 是按字符触发的，
#: 每敲一个键就重写一次 launcher_config.json 太蠢。开关是离散动作，不走这个。
AUTOSAVE_MS = 500


def _card(title: str):
    """本页的分组卡：复用 `widgets.card()`（同款 `#Card` + `#CardTitle`），
    但**内边距与行距按本页的档位加厚** —— 它们是「一行两张」的窄卡，
    用 `card()` 的默认 14/10/14/12 会显得内容贴着边。

    注意：**只调本页这一层**，不动 `widgets.card()` 的默认值（那会波及另外 10 个页面）。
    """
    frame, lay = card(title)
    lay.setContentsMargins(*CARD_PAD)
    lay.setSpacing(CARD_GAP)
    return frame, lay


def _row(parent, label: str, widget, hint: str = ""):
    """一行「标签 + 控件（+ 说明）」。

    标签为空时**整列不留白**：以前空标签也占着 190px，在一行两张的窄卡里就是纯浪费，
    而且会把复选框挤到折行。
    """
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(ROW_GAP)
    if label:
        lab = QLabel(label)
        lab.setMinimumWidth(ROW_LABEL_W)
        lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(lab)
    h.addWidget(widget, 1)
    parent.addLayout(h)
    if hint:
        t = QLabel(hint)
        t.setObjectName("VersionHint")
        t.setWordWrap(True)
        parent.addWidget(t)


class SettingsPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        # 灌值 / 联动期间压住自动保存（见 reload）；建完控件、reload 完才放开。
        self._loading = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(*PAGE_PAD)
        outer.setSpacing(HEADER_GAP)
        self.fields = {}

        page_header(outer, "设置", "启动器自身的偏好（改动即时保存）；"
                                   "代理/镜像/注意力在「专家模式」页")

        # ---- 四张分组卡：**一行两张** ----
        # 配对不是随便配的，是按**卡内容高度**配的：
        #   上排「自更新 + 版本与更新」—— 两张都矮（各 1 个字段 + 一排按钮），而且本来就是
        #                                同一件事的两半（配仓库 → 看版本 → 检查更新）；
        #   下排「网络 + 界面与系统」  —— 两张都高（2 行 + 2 条说明 / 4 个开关）。
        # 这样同一行里两张卡高度接近，不会出现"一张卡底下空一截"。
        f_upd, g1 = _card("自更新（GitHub Releases）")
        self.edit_repo = QLineEdit()
        self.edit_repo.setPlaceholderText("owner/name，如 Comfy-Org/ComfyUI")
        _row(g1, "仓库 owner/name", self.edit_repo,
             "留空则无法检查更新。更新走公开 Release，不连任何私有服务器。")

        f_net, g2 = _card("网络")
        self.edit_token = QLineEdit()
        self.edit_token.setEchoMode(QLineEdit.Password)
        self.edit_token.setPlaceholderText("ghp_xxx（只存本地，不写日志）")
        _row(g2, "GitHub Token", self.edit_token,
             "未认证时 GitHub API 只有 60 次/小时，填了提到 5000 次/小时。")
        self.chk_ssl = QCheckBox("关闭 SSL 校验")
        _row(g2, "", self.chk_ssl, "仅在公司代理/自签证书环境临时开启。")

        f_sys, g3 = _card("界面与系统")
        self.chk_tray = QCheckBox("启用系统托盘")
        _row(g3, "", self.chk_tray)
        self.chk_minimize = QCheckBox("关闭窗口时最小化到托盘（而不是退出）")
        _row(g3, "", self.chk_minimize)
        self.chk_notify = QCheckBox("ComfyUI 就绪时弹 Windows 通知")
        _row(g3, "", self.chk_notify)
        self.chk_dupcheck = QCheckBox("扫描插件时做重复插件检测")
        _row(g3, "", self.chk_dupcheck)

        # 分组名原来叫「关于」—— 和侧栏那个新的「关于」页撞名，改成它实际做的事
        # （这里有 owner/name 与 Token 输入、「检查更新」的真实实现，是关于页指过来的地方）。
        f_ver, g4 = _card("版本与更新")
        self.lbl_ver = QLabel("")
        self.lbl_ver.setObjectName("VersionHint")
        _row(g4, "当前版本", self.lbl_ver)
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(0, 0, 0, 0)
        btn_bar.setSpacing(BTN_GAP)
        btn_check = QPushButton("检查更新")
        btn_check.setObjectName("Ghost")
        btn_open_releases = QPushButton("打开 Releases 页面")
        btn_open_releases.setObjectName("Ghost")
        btn_bar.addWidget(btn_check)
        btn_bar.addWidget(btn_open_releases)
        btn_bar.addStretch(1)
        g4.addLayout(btn_bar)

        # ---- 网格：一行两张，两行按上面那组配对 ----
        # 余量分配 = **卡片按内容高贴顶、余量留在页面底部**（用户 2026-10-11 选定）。
        # ⚠ 别为了"消灭底部留白"改成「两行平分整页」—— 实测那会把卡片拉成空壳
        #   （卡内约 200px 留白），比底部留白难看。设置页底部留白是正常的。
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(GRID_GAP)
        grid.setVerticalSpacing(GRID_GAP)
        grid.addWidget(f_upd, 0, 0)
        grid.addWidget(f_ver, 0, 1)
        grid.addWidget(f_net, 1, 0)
        grid.addWidget(f_sys, 1, 1)
        grid.setColumnStretch(0, 1)         # 两列等宽
        grid.setColumnStretch(1, 1)

        outer.addLayout(grid)
        outer.addStretch(1)

        btn_check.clicked.connect(self.check_update)
        btn_open_releases.clicked.connect(self.open_releases)

        # ---- 自动保存：**没有「保存」按钮了**（用户 2026-10-11 要求）----
        # 文本框改动**去抖**后落盘，开关改了立刻落盘。落盘后 status 会给一句反馈，
        # 所以不需要按钮来提示"已保存"。
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(AUTOSAVE_MS)
        self._save_timer.timeout.connect(self.save)
        for ed in (self.edit_repo, self.edit_token):
            ed.textChanged.connect(self._queue_save)
        for chk in (self.chk_ssl, self.chk_tray, self.chk_minimize,
                    self.chk_notify, self.chk_dupcheck):
            chk.toggled.connect(self.save)

        self.reload()

    # ---------- 读写 ----------
    def reload(self):
        """把配置灌进控件。**期间禁止自动保存** —— 否则灌值本身就会触发一轮写盘。"""
        self._loading = True
        try:
            self.edit_repo.setText(cfgmod.get_echo("update_repo", "") or "")
            self.edit_token.setText(cfgmod.get_echo("github_token", "") or "")
            self.chk_ssl.setChecked(bool(cfgmod.get_echo("disable_ssl_verify", False)))
            self.chk_tray.setChecked(bool(cfgmod.get_echo("enable_tray", True)))
            self.chk_minimize.setChecked(bool(cfgmod.get_echo("minimize_to_tray", False)))
            self.chk_notify.setChecked(bool(cfgmod.get_echo("notify_on_ready", True)))
            self.chk_dupcheck.setChecked(
                bool(cfgmod.get_echo("enable_duplicate_plugin_check", True)))
            self.lbl_ver.setText(f"v{SU.current_version()}")
        finally:
            self._loading = False

    def _queue_save(self, *_):
        """文本框改动 → 去抖后再落盘（见 `AUTOSAVE_MS`）。"""
        if not self._loading:
            self._save_timer.start()

    def save(self, *_):
        """写盘。签名带 `*_` 是因为它也被 `toggled(bool)` 直接连着。"""
        if self._loading:
            return
        cfgmod.set_echo("update_repo", self.edit_repo.text().strip())
        cfgmod.set_echo("github_token", self.edit_token.text().strip())
        cfgmod.set_echo("disable_ssl_verify", self.chk_ssl.isChecked())
        cfgmod.set_echo("enable_tray", self.chk_tray.isChecked())
        cfgmod.set_echo("minimize_to_tray", self.chk_minimize.isChecked())
        cfgmod.set_echo("notify_on_ready", self.chk_notify.isChecked())
        cfgmod.set_echo("enable_duplicate_plugin_check", self.chk_dupcheck.isChecked())
        self.ctx.status("设置已自动保存到 launcher_config.json")
        # 托盘开关可能需要立刻生效
        mw = self.ctx.window
        if hasattr(mw, "sync_tray"):
            mw.sync_tray()

    # ---------- 自更新 ----------
    def _proxy(self) -> str:
        lc = self.ctx.launch_cfg()
        return lc.get("launcher_proxy_url", "") if lc.get("launcher_proxy") else ""

    def check_update(self):
        repo = (self.edit_repo.text() or "").strip()
        if not repo:
            QMessageBox.information(self, "提示",
                                    "请先填写自更新仓库（owner/name）再检查更新。")
            return
        self.save()
        self.ctx.status("正在检查 GitHub Releases…")
        w = self.ctx.spawn(Worker(SU.latest_release, self._proxy()))

        def _done(rel):
            if rel is None:
                self.ctx.status("检查更新失败")
                QMessageBox.warning(self, "检查更新",
                                    "拿不到 Release 信息（仓库不存在 / 无网络 / 无 Release）。")
                return
            if not SU.newer_than(rel.tag):
                self.ctx.status(f"已是最新（{rel.tag}）")
                QMessageBox.information(self, "检查更新",
                                        f"当前 v{SU.current_version()} 已是最新（{rel.tag}）。")
                return
            asset = rel.pick()
            ans = QMessageBox.question(
                self, "发现新版本",
                f"当前 v{SU.current_version()}，最新 {rel.tag}\n\n"
                f"{rel.name}\n\n{(rel.notes or '')[:600]}\n\n"
                + (f"安装包：{asset.get('name')}\n" if asset else "（该 Release 没有 Windows 安装包）\n")
                + "\n现在下载并更新？更新后会自动重启本程序。")
            if ans != QMessageBox.Yes or asset is None:
                return
            self._download_and_apply(asset)

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "检查更新失败", m))

    def _download_and_apply(self, asset):
        dest = os.path.join(os.path.dirname(SU.current_launch_cmd()[0]),
                            asset.get("name") or "update.zip")
        self.ctx.status(f"正在下载 {asset.get('name')} …")
        w = self.ctx.spawn(Worker(SU.download_asset, asset, dest, self._proxy()))

        def _done(path):
            if not path:
                QMessageBox.warning(self, "下载失败", "下载更新包失败")
                return
            self.ctx.status("下载完成，准备覆盖更新…")
            target = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            try:
                SU.apply_archive(path, target)
                self.ctx.status("更新脚本已启动，程序即将退出")
                QMessageBox.information(self, "更新",
                                        "更新脚本已启动。\n程序将退出，完成覆盖后会自动重新启动。")
                self.ctx.window.close()
            except Exception as e:
                QMessageBox.warning(self, "更新失败", str(e))

        w.done.connect(_done)
        w.failed.connect(lambda m: QMessageBox.warning(self, "下载失败", m))

    def open_releases(self):
        repo = (self.edit_repo.text() or "").strip()
        if not repo:
            QMessageBox.information(self, "提示", "先填写仓库 owner/name")
            return
        QDesktopServices.openUrl(QUrl(f"https://github.com/{repo}/releases"))

    def on_show(self):
        self.reload()
