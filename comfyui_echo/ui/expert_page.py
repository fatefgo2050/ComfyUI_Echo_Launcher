"""专家模式页：ComfyUI 的高级启动参数（对齐「专家设置」）。

布局（一屏两栏，均可滚动）：
    顶部   红色警告条 + 「恢复默认」
    左栏   通用设置（17 个复选框） + 注意力（从「环境」页整体搬来）
    右栏   精度控制（UNet / VAE / Text Encoder / 全局） + 显存与缓存（显存模式 /
           缓存策略 / 日志级别）

约定：
- 所有参数写入 `launch_cfg()["expert"]`（键集见 `core/config.DEFAULT_LAUNCH`），
  拼装在 `core/process.LaunchOptions.build_args()`，改动后调 `_sync_home_preview()`
  让「启动」页的命令预览实时跟上。
- 每个控件的 tooltip 都是对应的命令行开关（对照
  `D:\\ComfyUI\\ComfyUI\\comfy\\cli_args.py` 核对过；fp8 系列是下划线）。
- 注意力档位 / 检测 CK / 强制接管 shim 原在「环境」页，2026-10-09 按用户要求
  搬到本页；配置键不变（attention_mode / sage_attention / attention_shim_enabled），
  CK 实测结果仍存 `ctx.ck_ok/ck_text`，「启动」页启动前的实测直接复用。
- 本页没有日志区：操作反馈走 `ctx.status()` 与卡内 hint 标签。
"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..core import attention as attnmod
from ..core import paths as _paths
from ..core.config import DEFAULT_LAUNCH, VERBOSE_LEVELS
from .dialogs import MessageBox as QMessageBox
from .widgets import plain_box
from .workers import Worker

WARNING_TEXT = ("⚠ 仅建议使用默认设置，除非你明确了解每项参数的含义，"
                "否则请勿随意更改，以免导致软件无法正常运行。")

# 复选框：（配置键, 显示文案, 命令行开关 tooltip）——顺序对齐 TE 的「通用设置」
CHECKS = [
    ("enable_manager", "启用 ComfyUI-Manager", "--enable-manager"),
    ("disable_xformers", "禁用 xformers", "--disable-xformers"),
    ("disable_metadata", "不保存提示词等元数据到图像", "--disable-metadata"),
    ("dont_print_server", "不打印服务器日志", "--dont-print-server"),
    ("compress_response", "启用响应压缩", "--enable-compress-response-body"),
    ("cors_listen", "启用 CORS，监听 0.0.0.0（局域网）",
     "--listen 0.0.0.0 --enable-cors-header *"),
    ("disable_smart_memory", "禁用智能显存管理", "--disable-smart-memory"),
    ("force_upcast_attention", "强制注意力上采样（强制 FP32）", "--force-upcast-attention"),
    ("dont_upcast_attention", "禁用注意力上采样", "--dont-upcast-attention"),
    ("deterministic", "启用确定性算法（速度变慢，结果可能更精确）", "--deterministic"),
    ("mmap_torch_files", "mmap 加载 ckpt/pt", "--mmap-torch-files"),
    ("disable_mmap", "禁用 safetensors 的 mmap", "--disable-mmap"),
    ("async_offload", "异步权重加载",
     "--async-offload（N 卡默认已启用，此项用于显式指定流数）"),
    ("force_non_blocking", "强制非阻塞操作", "--force-non-blocking"),
    ("quick_test", "CI 快速测试", "--quick-test-for-ci"),
    ("log_stdout", "日志输出到 stdout", "--log-stdout"),
    ("channels_last", "强制 channels-last 内存格式", "--force-channels-last"),
]

# 下拉：（配置键, 显示标签, 选项, tooltip）
# 选项顺序固定为 **(显示文本, 存储值)**，addItem(text, data) 就按这个顺序吃 —— 别写反，
# 反了会把存储值当显示文本（verbose 第一项会显示成空白、把「默认」存成等级名）。
# 存储值即 build_args 里那张映射表的键；「默认」= 不加这项参数。
COMBOS = [
    ("verbose", "日志级别",
     [("默认", ""), ("DEBUG", "DEBUG"), ("DETAIL", "DETAIL"), ("INFO", "INFO"),
      ("WARNING", "WARNING"), ("ERROR", "ERROR"), ("CRITICAL", "CRITICAL")],
     "--verbose [DEBUG/INFO/WARNING/ERROR/CRITICAL]"),
    ("unet_precision", "精度控制 · UNet",
     [("默认", "默认"), ("fp16", "fp16"), ("fp32", "fp32"), ("bf16", "bf16"),
      ("fp64", "fp64"), ("fp8_e4m3fn", "fp8_e4m3fn"), ("fp8_e5m2", "fp8_e5m2"),
      ("fp8_e8m0fnu", "fp8_e8m0fnu")],
     "--fp16-unet / --fp32-unet / --bf16-unet / --fp64-unet /\n"
     "--fp8_e4m3fn-unet / --fp8_e5m2-unet / --fp8_e8m0fnu-unet"),
    ("vae_precision", "精度控制 · VAE",
     [("默认", "默认"), ("fp16", "fp16"), ("fp32", "fp32"), ("bf16", "bf16")],
     "--fp16-vae / --fp32-vae / --bf16-vae"),
    ("text_enc_precision", "精度控制 · Text Encoder",
     [("默认", "默认"), ("fp16", "fp16"), ("fp32", "fp32"), ("bf16", "bf16"),
      ("fp8_e4m3fn", "fp8_e4m3fn"), ("fp8_e5m2", "fp8_e5m2")],
     "--fp16-text-enc / --fp32-text-enc / --bf16-text-enc /\n"
     "--fp8_e4m3fn-text-enc / --fp8_e5m2-text-enc"),
    ("global_precision", "全局精度",
     [("默认", "默认"), ("fp16", "fp16"), ("fp32", "fp32")],
     "--force-fp16 / --force-fp32"),
    ("vram_mode", "显存管理模式",
     [("默认", "默认"), ("gpu-only", "gpu-only"), ("highvram", "highvram"),
      ("lowvram", "lowvram"), ("novram", "novram"), ("cpu", "cpu")],
     "--gpu-only / --highvram / --lowvram / --novram / --cpu\n"
     "（normalvram = 默认，不加参数）"),
    ("cache_mode", "缓存策略",
     [("默认", "默认"), ("classic", "classic"), ("none", "none")],
     "--cache-classic（旧式激进缓存） / --cache-none（每次重算节点，最省内存）"),
]


def _card(title: str, actions=None):
    """功能分组卡片：`QFrame#Card` + 标题（可选：标题行右端迷你按钮）。"""
    frame = QFrame()
    frame.setObjectName("Card")
    v = QVBoxLayout(frame)
    v.setContentsMargins(12, 8, 12, 10)
    v.setSpacing(6)
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


def _row(parent, label: str, widget):
    """标签 + 控件（标签右对齐，定宽列）。宽度要容得下「精度控制 · Text Encoder」。"""
    h = QHBoxLayout()
    h.setSpacing(8)
    lab = QLabel(label)
    lab.setFixedWidth(150)
    lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    h.addWidget(lab)
    h.addWidget(widget, 1)
    parent.addLayout(h)


def _mini(text, tip=""):
    b = QPushButton(text)
    b.setObjectName("Mini")
    if tip:
        b.setToolTip(tip)
    return b


def _scroll_column():
    area = QScrollArea()
    area.setObjectName("PlainScroll")
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    inner = QWidget()
    v = QVBoxLayout(inner)
    v.setContentsMargins(0, 0, 6, 0)
    v.setSpacing(8)
    area.setMinimumWidth(430)
    return area, inner, v


class ExpertPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        # ---- 顶部：红色警告**框** + 恢复默认 ----
        # ★2026-10-12 用户要求：警告文字用带底 / 描边的框圈住（原来是一行裸红字，太素）；
        #   「恢复默认」从迷你按钮放大成标准主按钮（这一页只有它一个动作）。
        #   框的竖向 padding 取 9px，与 #Primary 按钮等高，二者在同一行里齐平。
        head = QHBoxLayout()
        head.setSpacing(10)
        warn_box = QFrame()
        warn_box.setObjectName("WarnBox")
        wb = QHBoxLayout(warn_box)
        wb.setContentsMargins(12, 9, 12, 9)
        wb.setSpacing(8)
        warn = QLabel(WARNING_TEXT)
        warn.setObjectName("WarnText")        # 颜色 / 字重走 QSS（见 style.py 的 #WarnText）
        warn.setWordWrap(True)
        wb.addWidget(warn, 1)
        head.addWidget(warn_box, 1)
        self.btn_reset = QPushButton("恢复默认")
        self.btn_reset.setObjectName("Primary")   # 放大：与其它页的主操作同一语言
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.setToolTip("把所有专家参数恢复为默认值")
        head.addWidget(self.btn_reset)
        outer.addLayout(head)

        # ---- 两栏 ----
        cols = QHBoxLayout()
        cols.setSpacing(10)
        outer.addLayout(cols, 1)

        self.checks = {}     # 配置键 -> QCheckBox
        self.combos = {}     # 配置键 -> QComboBox

        # 左栏：通用设置（17 个开关；长文案必须单列，两列会裁字）
        left, linner, lv = _scroll_column()
        self._card_checks(lv)
        lv.addStretch(1)
        left.setWidget(linner)
        cols.addWidget(left, 1)

        # 右栏：精度 + 显存与日志 + 注意力
        # ★2026-10-12：注意力卡原在左栏，导致「左高右矮、右下大片空白」。
        #   移到右栏后两栏高度接近（左 17 项 / 右 7 下拉 + 注意力卡）。
        right, rinner, rv = _scroll_column()
        self._card_precision(rv)
        self._card_storage(rv)
        self._card_attn(rv)
        rv.addStretch(1)
        right.setWidget(rinner)
        cols.addWidget(right, 1)

        # ---- 接线 ----
        self.btn_reset.clicked.connect(self._reset_defaults)
        # 信号只在这里连一次（on_show 里的 _load_values 只刷控件、不连信号，
        # 否则每切一次页就多连一层，改一个复选框会触发 N 遍保存）
        for c in self.checks.values():
            c.toggled.connect(self._save)
        for combo in self.combos.values():
            combo.currentIndexChanged.connect(self._save)
        self._load_values()

    # ------------------------------------------------------------------
    # 卡片构建
    # ------------------------------------------------------------------
    def _card_checks(self, parent):
        card, v = _card("通用设置")
        # 单列（TE 同款）：两列会把「启用确定性算法（…）」这类长文案挤到裁字
        for i, (key, label, tip) in enumerate(CHECKS):
            c = QCheckBox(label)
            c.setToolTip(tip)
            self.checks[key] = c
            v.addWidget(c)
        parent.addWidget(card)

    def _card_precision(self, parent):
        card, v = _card("精度控制")
        for key, label, items, tip in COMBOS[:4]:
            combo = QComboBox()
            for text, data in items:
                combo.addItem(text, data)
            combo.setToolTip(tip)
            self.combos[key] = combo
            _row(v, label, combo)
        parent.addWidget(card)

    def _card_storage(self, parent):
        card, v = _card("显存与日志")
        for key, label, items, tip in COMBOS[4:]:
            combo = QComboBox()
            for text, data in items:
                combo.addItem(text, data)
            combo.setToolTip(tip)
            self.combos[key] = combo
            _row(v, label, combo)
        parent.addWidget(card)

    def _card_attn(self, parent):
        """注意力卡（从「环境」页整体搬来，配置键不变）。"""
        card, v = _card("注意力")
        row_w = plain_box()      # ⛔ 别用裸 QWidget：会被全局底色刷出一条色带
        rh = QHBoxLayout(row_w)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(6)
        self.combo_attn = QComboBox()
        self.combo_attn.addItems(list(attnmod.ATTENTION_MODES))
        self.combo_attn.setMinimumWidth(160)
        self.combo_attn.setMaximumWidth(220)
        self.btn_ck = _mini("检测 CK",
                            "实测 comfy-kitchen 的 INT8 注意力是否可用（CK 档的前置条件）")
        rh.addWidget(self.combo_attn)
        rh.addWidget(self.btn_ck)
        rh.addStretch(1)
        _row(v, "档位", row_w)

        self.lbl_attn = QLabel("")
        self.lbl_attn.setObjectName("VersionHint")
        self.lbl_attn.setWordWrap(True)
        v.addWidget(self.lbl_attn)

        self.chk_shim = QCheckBox("强制接管（装切换器 shim，重启后生效）")
        self.chk_shim.setToolTip(
            "有些便携包把注意力后端写死在解释器里，启动参数不生效；\n"
            "开启后会在 python_embeded\\Lib\\site-packages\\sitecustomize.py 装入 shim，\n"
            "在 ComfyUI 读参数之前改写 sys.argv，强制注入所选后端。")
        v.addWidget(self.chk_shim)

        self.lbl_shim = QLabel("")
        self.lbl_shim.setObjectName("VersionHint")
        self.lbl_shim.setWordWrap(True)
        v.addWidget(self.lbl_shim)

        self.btn_ck.clicked.connect(self.detect_ck)
        self.combo_attn.currentTextChanged.connect(self._on_attn_changed)
        self.chk_shim.toggled.connect(self._on_shim_toggled)
        parent.addWidget(card)

    # ------------------------------------------------------------------
    # 配置读写
    # ------------------------------------------------------------------
    def on_show(self):
        self._load_values()

    def _load_values(self):
        """从配置读回所有控件（blockSignals，避免又触发保存写回去）。"""
        lc = self.ctx.launch_cfg()
        ex = lc.get("expert") or {}
        for key, c in self.checks.items():
            c.blockSignals(True)
            c.setChecked(bool(ex.get(key, False)))
            c.blockSignals(False)
        for key, combo in self.combos.items():
            default = "" if key == "verbose" else "默认"
            val = ex.get(key, default)
            # verbose 只认合法等级名，非法值（旧配置里的「默认」）一律按「默认」显示
            if key == "verbose" and val:
                val = val if val in VERBOSE_LEVELS else ""
            combo.blockSignals(True)
            idx = combo.findData(val)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        # 注意力档位 / 接管开关读回（含旧配置的 sage_attention 兼容）
        mode = attnmod.normalize_mode(lc.get("attention_mode"))
        if mode == "默认" and lc.get("sage_attention"):
            mode = "SAGE"
        for w in (self.combo_attn, self.chk_shim):
            w.blockSignals(True)
        self.combo_attn.setCurrentText(mode)
        self.chk_shim.setChecked(bool(lc.get("attention_shim_enabled", False)))
        for w in (self.combo_attn, self.chk_shim):
            w.blockSignals(False)
        self._refresh_attn_label()

    def _save(self, *_a):
        lc = self.ctx.launch_cfg()
        ex = lc.setdefault("expert", {})
        for key, c in self.checks.items():
            ex[key] = c.isChecked()
        for key, combo in self.combos.items():
            ex[key] = self._combo_value(combo)
        self.ctx.save_cfg()
        self._sync_home_preview()

    def _combo_value(self, combo) -> str:
        """下拉当前项对应的存储值。

        ⚠ 别写成 `currentData() or currentText()`：第一项「默认」的 data 是空字符串，
        Qt 回读时给 None，那样兜底会把显示文本「默认」当成值存进去
        —— 曾经拼出 `--verbose 默认`，ComfyUI 启动直接 parser.error 退出（代码 2）。
        """
        data = combo.itemData(combo.currentIndex())
        return data if isinstance(data, str) else ""

    def _reset_defaults(self):
        if QMessageBox.question(self, "恢复默认",
                                "把所有专家参数恢复为默认值？") != QMessageBox.Yes:
            return
        lc = self.ctx.launch_cfg()
        lc["expert"] = copy.deepcopy(DEFAULT_LAUNCH["expert"])
        self.ctx.save_cfg()
        self._load_values()
        self._sync_home_preview()
        self.ctx.status("专家参数已恢复默认")

    def _sync_home_preview(self):
        """改完参数后，让「启动」页的启动命令预览跟着变。"""
        try:
            home = self.ctx.window.pages.get("home")
            if home is not None and hasattr(home, "_refresh_preview"):
                home._refresh_preview()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 注意力（从「环境」页搬来；无日志区，反馈走 status 与 hint 标签）
    # ------------------------------------------------------------------
    def _attn_root(self):
        """便携根目录（shim 要装的那一层）。"""
        root = self.ctx.root()
        if not root:
            return ""
        return attnmod.portable_root(_paths.dirs(root)["comfyui"])

    def _on_attn_changed(self, _text=""):
        mode = attnmod.normalize_mode(self.combo_attn.currentText())
        lc = self.ctx.launch_cfg()
        lc["attention_mode"] = mode
        lc["sage_attention"] = (mode == "SAGE")
        self.ctx.save_cfg()
        self._sync_home_preview()
        self._refresh_attn_label()
        # 已开启接管时，切档立即改写 shim 标记
        if self.chk_shim.isChecked():
            self._arm_shim_async(quiet=True)

    def _on_shim_toggled(self, on):
        lc = self.ctx.launch_cfg()
        lc["attention_shim_enabled"] = on
        self.ctx.save_cfg()
        root = self._attn_root()
        if not root:
            self.ctx.status("无法定位便携根目录，切换器接管不可用")
            self._refresh_attn_label()
            return
        if on:
            self._arm_shim_async()
        else:
            self.ctx.status("正在卸载切换器接管...")
            w = self.ctx.spawn(Worker(attnmod.uninstall_shim, root))
            w.done.connect(self._on_shim_result)
        self._refresh_attn_label()

    def _arm_shim_async(self, quiet=False):
        root = self._attn_root()
        if not root:
            return
        mode = attnmod.normalize_mode(self.combo_attn.currentText())
        if not quiet:
            self.ctx.status(f"正在安装切换器接管（{mode}）...")
        w = self.ctx.spawn(Worker(attnmod.apply_shim, root, mode))
        w.done.connect(self._on_shim_result)

    def _on_shim_result(self, res):
        ok, msg = res
        self.ctx.status(("切换器：" if ok else "切换器失败：") + str(msg))
        self._refresh_attn_label()

    def _refresh_attn_label(self):
        mode = attnmod.normalize_mode(self.combo_attn.currentText())
        text = attnmod.describe(mode)
        if mode == "CK":
            if self.ctx.ck_ok is True:
                text += f"　✓ 已实测可用（{self.ctx.ck_text}）"
            elif self.ctx.ck_ok is False:
                text += f"　✗ {self.ctx.ck_text}"
            else:
                text += "　（启动时会自动实测可用性）"
        self.lbl_attn.setText(text)
        self.lbl_attn.setToolTip(
            "不勾 SageAttention 时用 PyTorch 原生注意力" if mode == "默认" else text)

        root = self._attn_root()
        status = "未配置 ComfyUI 路径"
        if root:
            st = attnmod.state(root)
            if st["installed"]:
                status = "shim 已装 · " + (st["mode"].upper() or "休眠（不接管）")
            else:
                status = "shim 未安装（当前只靠启动参数）"
            if st["last"]:
                parts = [p for p in st["last"].split("\t") if p]
                brief = parts[0][:16] + (("  " + parts[1]) if len(parts) > 1 else "")
                status += "\n上次：" + brief
                self.lbl_shim.setToolTip("完整记录：" + st["last"].replace("\t", "  "))
        self.lbl_shim.setText(status)

    def detect_ck(self):
        root = self.ctx.root()
        if not root:
            QMessageBox.information(self, "提示", "请先在「启动」页选择有效的 ComfyUI 包路径")
            return
        self.btn_ck.setEnabled(False)
        self.lbl_attn.setText("正在实测 comfy-kitchen 的 INT8 注意力…")
        w = self.ctx.spawn(Worker(self._probe_ck_job, root))

        def _done(res):
            self.btn_ck.setEnabled(True)
            self.ctx.ck_ok, self.ctx.ck_text = res
            self._refresh_attn_label()
            self.ctx.status(f"CK 实测：{'可用' if res[0] else '不可用'} — {res[1]}")

        w.done.connect(_done)
        w.failed.connect(lambda m: (self.btn_ck.setEnabled(True),
                                    self.ctx.status("CK 检测异常：" + m)))

    def _probe_ck_job(self, root):
        d = _paths.dirs(root)
        return attnmod.probe_ck(d["python_exe"], d["comfyui"])
