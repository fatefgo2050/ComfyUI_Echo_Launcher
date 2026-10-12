"""界面冒烟测试（离屏运行，不需要显示器）。

    .venv/Scripts/python.exe tests/ui_smoke.py

构建主窗口 -> 逐个切换侧栏 10 个页面 -> 触发各页 on_show -> 退出。
用来抓 import 错误、Qt 控件拼装错误、页面切换异常。
"""
from __future__ import annotations

import os
import sys
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main() -> int:
    from PySide6.QtWidgets import QApplication, QPushButton
    from comfyui_echo import APP_TITLE, __version__
    from comfyui_echo.ui import style
    from comfyui_echo.ui.main_window import MainWindow, PAGES, PAGE_LABELS

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    style.apply(app)

    print(f"[1] 构建主窗口（{APP_TITLE} v{__version__}）")
    win = MainWindow()
    win.show()
    app.processEvents()
    print(f"    OK  页面数 {len(PAGES)}")

    print("[2] 逐页切换")
    for name, key in PAGES:
        win.switch(key)
        app.processEvents()
        page = win.pages[key]
        assert page is not None
        print(f"    OK  {name} ({key})")

    print("[3] 启动页关键控件自检")
    home = win.pages["home"]
    # 注意力档位 / 检测 CK / 接管开关已搬到「专家模式」页（见 [7]），启动页不再有它们；
    # 「实验性开关」折叠区也已升级成独立的「专家模式」页，启动页不再保留第二入口
    for attr in ("path_combo", "chk_fp16", "chk_cudamalloc", "chk_reserve",
                 "spin_port", "combo_gpu", "edit_extra", "edit_env", "console",
                 "btn_start", "btn_stop"):
        assert hasattr(home, attr), attr
    assert not hasattr(home, "btn_expert"), "实验性开关已搬去专家模式页"
    assert not hasattr(home, "expert_checks"), "实验性开关已搬去专家模式页"
    assert home.param_tabs.count() == 3, home.param_tabs.count()
    # 每个页签的内容页都必须是 ParamPage：全局 `QWidget { background: #f5f0e5 }` 会把
    # 普通 QWidget 刷成画布色，在卡片上显出一条硬边色带（2026-10-10 修过一次）。
    # 谁新加页签忘了挂 objectName，这里就会拦下来。
    for i in range(home.param_tabs.count()):
        page_w = home.param_tabs.widget(i)
        assert page_w.objectName() == "ParamPage", \
            f"页签 {home.param_tabs.tabText(i)!r} 的内容页没挂 objectName='ParamPage'"
    # 「指定 GPU」改成显卡名下拉（原来是数字序号框）
    assert home.combo_gpu.count() >= 1, "GPU 下拉至少要有「自动（默认）」"
    assert home.combo_gpu.itemData(0) == -1, home.combo_gpu.itemData(0)
    print(f"    OK  启动参数 {home.param_tabs.count()} 签（常用 / 网络 / 高级），"
          f"内容页均已挂 ParamPage")

    print("[4] 配置往返（UI 视图 <-> Echo 配置）")
    from comfyui_echo.core import config as cfgmod
    view = cfgmod.load()
    assert "launch" in view and "paths" in view and "modes" in view
    print(f"    OK  路径 {len(view['paths'])} 个 / 模式 {list(view['modes'])}")

    print("[5] 模式页控件（三栏：工作流 / 插件 / 当前模式插件）")
    from PySide6.QtWidgets import QSplitter
    mp = win.pages["modes"]
    for attr in (
            # 左栏：可用工作流
            "wf_tree", "wf_search", "btn_wf_scan", "btn_wf_default",
            "btn_wf_adddir", "btn_wf_external", "lbl_wf_count",
            # 中栏：可用插件
            "plug_list", "plug_search", "btn_plug_add", "btn_plug_default",
            "btn_plug_refresh", "lbl_plug_count",
            # 右栏：当前模式插件
            "mode_combo", "mode_list", "btn_mode_new", "btn_mode_save",
            "btn_mode_rename", "btn_mode_del", "btn_mode_remove",
            "btn_mode_clear", "btn_capture", "btn_enable_all", "btn_resync",
            "lbl_source", "lbl_mode_count", "chk_apply", "btn_apply_now",
            "btn_install_missing"):
        assert hasattr(mp, attr), attr
    assert "workflow" not in win.pages, "工作流页应已并入模式页"
    sp = mp.findChild(QSplitter)
    assert sp is not None and sp.count() == 3, "模式页应是一屏三栏"
    # 左侧底部三个按钮：默认目录 / 自定义目录 / 扫描工作流
    assert mp.btn_wf_scan.text() == "扫描工作流"
    assert mp.btn_wf_adddir.text() == "自定义目录"
    assert mp.btn_wf_default.text() == "默认目录"
    # 工作流要能多选（跑多份工作流取并集）
    from PySide6.QtWidgets import QAbstractItemView
    assert mp.wf_tree.selectionMode() == QAbstractItemView.ExtendedSelection
    # 树上不该有展开三角
    assert not mp.wf_tree.rootIsDecorated(), "工作流树不要画展开箭头"
    print(f"    OK  三栏 = 工作流 / 插件 / 模式（当前模式：{mp.mode_combo.currentText()}）")

    print("[6] 插件页控件")
    pp = win.pages["plugins"]
    for attr in ("table", "btn_scan", "btn_check_all", "btn_install", "edit_url",
                 "chk_reqs", "chk_only_upd", "hint",
                 "btn_update_sel", "btn_toggle_sel", "btn_uninstall_sel"):
        assert hasattr(pp, attr), attr
    # 「操作」列已挪到顶栏 -> 表格只剩 6 列；没选中行时三个操作按钮必须禁用
    assert pp.table.columnCount() == 6, pp.table.columnCount()
    assert not any(b.isEnabled() for b in
                   (pp.btn_update_sel, pp.btn_toggle_sel, pp.btn_uninstall_sel))
    # 详情条（插件名 / 远程地址的「可选中复制」）：两个**只读**文本框 + 两个复制按钮。
    # ⚠ 别改回「让表格单元格可拖选」——那要给格子里塞子控件，会把 itemSelectionChanged
    #   （点一行 → 顶栏按钮生效）和整行标红（对 item setForeground）一起弄坏。
    for attr in ("detail_name", "detail_url", "btn_copy_name", "btn_copy_url"):
        assert hasattr(pp, attr), "plugins." + attr
    for ed in (pp.detail_name, pp.detail_url):
        assert ed.isReadOnly(), "详情条文本框必须只读"
        assert ed.text() == "", "未选中行时详情条应为空"
    assert not pp.btn_copy_name.isEnabled() and not pp.btn_copy_url.isEnabled(), \
        "未选中行时两个复制按钮必须禁用"
    assert "QLineEdit#CopyLine" in style.QSS, "详情条的 #CopyLine 没有 QSS 规则"
    # 功能验证：文本 -> 剪贴板（这是这个功能存在的全部意义，别只测控件在不在）
    from PySide6.QtGui import QGuiApplication
    pp.detail_name.setText("ComfyUI-Demo-Node")
    pp._copy_text(pp.detail_name, "插件名")
    assert QGuiApplication.clipboard().text() == "ComfyUI-Demo-Node", "复制插件名没进剪贴板"
    pp.detail_name.setText("")
    print(f"    OK  6 列 + 顶栏操作按钮（未选中时禁用）；详情条可复制（剪贴板往返正常）")
    print(f"    OK  提示：{pp.hint.text()[:30]}...")

    print("[7] 环境页控件")
    for attr in ("edit_py", "mirror", "cmb_pkg", "cmb_ver", "cuda_tag", "log",
                 "btn_env_info", "btn_clear_log"):
        assert hasattr(win.pages["env"], attr), attr
    # 注意力 UI 已按要求从「环境」页搬到「专家模式」页
    assert not hasattr(win.pages["env"], "combo_attn"), "注意力已搬去专家模式页"
    assert not hasattr(win.pages["home"], "combo_attn"), "启动页不该有注意力控件"
    assert not hasattr(win.pages["home"], "chk_shim"), "启动页不该有接管开关"
    print("    OK  左功能区 / 右日志；注意力已搬到专家模式页")

    print("[7.5] 专家模式页控件")
    ep = win.pages["expert"]
    # 复选框组（TE「通用设置」对齐）
    for attr in ("checks", "combos", "btn_reset", "combo_attn", "btn_ck",
                 "chk_shim", "lbl_attn", "lbl_shim"):
        assert hasattr(ep, attr), attr
    assert len(ep.checks) == 17, len(ep.checks)
    assert set(ep.combos) == {"verbose", "unet_precision", "vae_precision",
                              "text_enc_precision", "global_precision",
                              "vram_mode", "cache_mode"}, set(ep.combos)
    assert ep.combo_attn.itemText(0) == "默认", ep.combo_attn.itemText(0)
    # 档位下拉文本读回（配置里默认档）
    print(f"    OK  复选 {len(ep.checks)} 个 + 下拉 {len(ep.combos)} 个；"
          f"档位下拉 = {[ep.combo_attn.itemText(i) for i in range(ep.combo_attn.count())]}")
    print(f"    OK  档位说明 = {ep.lbl_attn.text()[:36]}...")

    print("[8] 新增页面控件")
    for attr in ("grid", "preview", "meta", "slider", "edit_dir", "edit_kw",
                 "combo_group", "combo_tag", "edit_tags", "btn_import"):
        assert hasattr(win.pages["media"], attr), "media." + attr
    assert win.pages["media"].combo_group.count() == 4
    assert win.pages["media"].combo_group.itemText(0) == "默认", \
        "分组下拉第一项该是「默认」（2026-10-11 由「不分组」改名）"
    # 三页搜索都该是「边打边过滤」，且去抖时长一致（200ms）—— 不一致最容易被当成 bug
    for _p in ("media", "market", "model"):
        _t = getattr(win.pages[_p], "_search_timer", None)
        assert _t is not None, _p + " 页没有边打边过滤的去抖定时器"
        assert _t.isSingleShot() and _t.interval() == 200, \
            "%s 页搜索去抖该是 200ms 单次，实际 %d" % (_p, _t.interval())
    # 工具行第一排要有「目录…（设定）」「打开（打开当前目录）」「说明」三个不同职责的按钮
    _labels = {b.text() for b in win.pages["media"].findChildren(QPushButton)}
    assert "目录..." in _labels, "媒体页缺少「目录...」按钮"
    assert "打开" in _labels, "媒体页缺少「打开」按钮（打开当前输出目录）"
    assert "说明" in _labels, "媒体页缺少「说明」按钮（拖拽导入的说明）"

    # 媒体页：**分组标题必须独占一整行**。它以前只占一个缩略图格子（150px），
    # 文字被挤成好几行 —— 看起来就是「图片的位置显示了一坨文字」。
    # ⚠ 这条要单独测：_render 是被 currentTextChanged 信号调起来的，
    #   里面抛异常不会传回主线程（只会打 stderr），页面悄悄坏掉而测试照样通过。
    from comfyui_echo.ui.media_page import ROLE_GROUP, MediaGridDelegate
    mp_ = win.pages["media"]
    win.switch("media")
    if hasattr(mp_, "refresh"):
        mp_.refresh()
    for _ in range(60):
        app.processEvents()
        time.sleep(0.1)
        if mp_.grid.count():
            break
    if mp_.grid.count():
        assert mp_.grid.itemDelegate().__class__ is MediaGridDelegate, \
            "媒体网格没挂 MediaGridDelegate（分组标题就跨不了整行）"
        idx = mp_.combo_group.findText("按日期")
        if idx >= 0:
            mp_.combo_group.setCurrentIndex(idx)
            for _ in range(20):
                app.processEvents()
                time.sleep(0.05)
            vw = mp_.grid.viewport().width()
            heads = [mp_.grid.item(r) for r in range(mp_.grid.count())
                     if mp_.grid.item(r).data(ROLE_GROUP)]
            assert heads, "「按日期」分组后一个标题项都没有（_render 可能抛异常了）"
            wide = 0
            for h in heads:
                rect = mp_.grid.visualItemRect(h)
                if rect.isEmpty():
                    continue
                assert rect.width() > vw * 0.8, (
                    f"媒体页分组标题没独占一整行（{rect.width()} vs 视口 {vw}）——"
                    "它只占一个缩略图格子，文字会被挤成好几行")
                wide += 1
            print(f"    OK  媒体分组标题独占整行（{wide}/{len(heads)} 个标题，"
                  f"视口宽 {vw}）")
    else:
        print("    （媒体目录没扫到文件，跳过分组标题检查）")
    for attr in ("table", "edit_note", "edit_kw", "btn_export",
                 "combo_family", "combo_purpose", "edit_set_family",
                 "edit_set_purpose"):
        assert hasattr(win.pages["model"], attr), "model." + attr
    # 底模 / 用途两列必须真的在表里（探测结果要有地方显示）
    from comfyui_echo.ui.model_page import COLS as _MCOLS
    assert "底模" in _MCOLS and "用途" in _MCOLS, _MCOLS
    assert win.pages["model"].table.columnCount() == len(_MCOLS) == 7, \
        win.pages["model"].table.columnCount()
    for attr in ("table", "edit_kw", "chk_node", "chk_reqs"):
        assert hasattr(win.pages["market"], attr), "market." + attr
    # 市场页：输入即过滤（去抖 200ms），不用点「搜索」
    mp = win.pages["market"]
    win.switch("market")
    for _ in range(60):
        app.processEvents()
        time.sleep(0.05)
        if mp.table.rowCount():
            break
    if mp.table.rowCount():
        before = mp.table.rowCount()
        mp.edit_kw.setText("manager")
        deadline = time.time() + 5
        while time.time() < deadline and mp.table.rowCount() == before:
            app.processEvents()
            time.sleep(0.05)
        after = mp.table.rowCount()
        assert 0 < after < before, (before, after)
        mp.edit_kw.clear()
        app.processEvents()
        print(f"    OK  市场页输入即过滤：{before} -> {after} 行")
    else:
        print("    （市场目录未缓存，跳过输入即过滤检查）")
    for attr in ("edit_repo", "edit_token", "chk_tray", "chk_minimize", "lbl_ver"):
        assert hasattr(win.pages["settings"], attr), "settings." + attr
    print("    OK  媒体 / 模型 / 市场 / 设置 四页控件齐全")
    print(f"    OK  设置页显示版本：{win.pages['settings'].lbl_ver.text()}")

    # 「设置」页是**自动保存**（用户 2026-10-11 要求取消「保存」按钮）：
    # 文本框改动去抖后落盘、开关改了立刻落盘。
    from comfyui_echo.ui import settings_page as _SP
    sp = win.pages["settings"]
    for attr in ("chk_ssl", "chk_notify", "chk_dupcheck", "_save_timer", "_loading"):
        assert hasattr(sp, attr), "settings." + attr
    assert not any(b.text() == "保存" for b in sp.findChildren(QPushButton)), \
        "「设置」页不该再有「保存」按钮（已改成自动保存）"
    # 接线检查：文本框改动要排进去抖定时器。
    # ⚠ **只验证"排上了"，然后停掉，不真的落盘** —— 落盘会改到操作者本机的
    #   launcher_config.json（这个测试不该有这种副作用）。
    sp._queue_save()
    assert sp._save_timer.isActive(), "文本框改动没排上自动保存"
    assert sp._save_timer.interval() == _SP.AUTOSAVE_MS, sp._save_timer.interval()
    sp._save_timer.stop()
    print(f"    OK  自动保存已接线（文本框去抖 {_SP.AUTOSAVE_MS}ms，无「保存」按钮）")

    print("[9] 救援对话框可构建")
    from comfyui_echo.ui.rescue_dialog import RescueDialog
    dlg = RescueDialog(win, win.ctx)
    app.processEvents()
    print(f"    OK  还原点 {dlg.table.rowCount()} 个")
    dlg.close()

    print("[10] 「关于」/ 「ComfyUI 内核」都是**主窗口里的页面**")
    # 2026-10-10 用户要求：「关于」的二级窗口取消、改成一级页面（原先 10 个页 + 一个
    # 侧栏底部弹窗）。所以这里不再建 AnyDialog，而是断言页面本身存在、正文可用，
    # 并且**旧对话框模块必须已经删掉**（留着就会有人接回去）。
    # 正文是若干个 QLabel（版式见 about_page 文件头），不再是单个 QTextEdit。
    from PySide6.QtWidgets import QLabel
    assert "about" in [k for _n, k in PAGES], "「关于」没进 PAGES"
    assert not os.path.exists(os.path.join(BASE_DIR, "comfyui_echo", "ui",
                                           "about_dialog.py")), \
        "about_dialog.py 还在：应该用 about_page.py（一级页面）"
    ap = win.pages["about"]
    win.switch("about")
    app.processEvents()
    body = "\n".join(lab.text() for lab in ap.findChildren(QLabel)
                     if lab.objectName() in ("AboutText", "AboutNote", "AboutKey",
                                             "AboutVal", "CardTitle"))
    for kw in ("软件介绍", "版本更新"):
        assert kw in body, f"「关于」页正文缺少「{kw}」"
    # 定义行（#AboutKey + #AboutVal）必须真的接上：这两条 QSS 曾经长期零引用，
    # 别哪天又被谁删了。
    assert ap.findChildren(QLabel, "AboutKey") and ap.findChildren(QLabel, "AboutVal"), \
        "「关于」页的定义行没接上 #AboutKey / #AboutVal"
    # 条目标记「●」必须有样式：v8 只设了 objectName="Bullet"、QSS 里却一条规则都没有，
    # 圆点与正文同色 → 整页看不出条目标记。守住这条修复。
    assert "QLabel#Bullet" in style.QSS, "「关于」页的 #Bullet 没有 QSS 规则"
    # 正文里不许再出现其它项目的来源说明（用户 2026-10-10 定的口径）
    for kw in ("NOBU", "tkinter", "TE模式", "原版", "复刻"):
        assert kw not in body, f"「关于」页正文不该再提「{kw}」"
    # 版式要点：正文必须**允许折行**（第一版把源码硬折行当成分段，句子半截断开）
    texts = [lab for lab in ap.findChildren(QLabel) if lab.objectName() == "AboutText"]
    assert texts and all(lab.wordWrap() for lab in texts), "「关于」页正文段落必须开 wordWrap"
    # 侧栏底部那个弹窗入口已经取消：全窗口不许再有 #NavAux 按钮
    assert not win.findChildren(QPushButton, "NavAux"), "侧栏底部还有 #NavAux 入口"
    print("    OK  「关于」页可构建、正文已精简分行；侧栏底部弹窗入口已移除")

    # 「ComfyUI 内核」也早就是**主窗口里的一个页面**（不再弹独立对话框）
    from comfyui_echo.ui.version_page import COLS
    vp = win.pages["version"]
    win.switch("version")           # 触发 on_show（首次异步加载）
    deadline = time.time() + 30
    while time.time() < deadline and vp.cmb_channel.count() == 0:
        app.processEvents()
        time.sleep(0.05)
    # 内核页只管 ComfyUI 内核：表格只有「提交 / 提交日期 / 说明」，
    # 不允许再出现插件列（插件更新在「插件」页）。
    assert PAGE_LABELS["version"] == "ComfyUI 内核", PAGE_LABELS["version"]
    assert COLS == ["提交", "提交日期", "说明"], COLS
    assert vp.table.columnCount() == 3
    assert not hasattr(vp, "chk_only_upd"), "插件版的「仅显示有更新」不该还在"
    assert vp.cmb_channel.count() >= 1
    # 镜像下拉：「官方」+ 至少一个加速镜像；切换要能落盘
    assert vp.cmb_mirror.count() >= 2, "镜像下拉选项太少"
    assert "官方" in vp.cmb_mirror.itemText(0)
    old_mirror = cfgmod.get_echo("github_mirror", "")
    vp.cmb_mirror.setCurrentIndex(1)
    app.processEvents()
    assert cfgmod.get_echo("github_mirror", "") == vp.cmb_mirror.itemText(1), "镜像切换没落盘"
    cfgmod.set_echo("github_mirror", old_mirror)
    print(f"    OK  ComfyUI 内核页（{vp.cmb_channel.count()} 个通道，"
          f"{vp.table.rowCount()} 条版本，{vp.cmb_mirror.count()} 个镜像）")

    print("[11] 托盘控制器")
    from comfyui_echo.core import tray as traymod
    print(f"    OK  pystray 可用={traymod.available()}；"
          f"当前托盘实例={'已启动' if win.tray else '未启动'}")

    print("[12] 窗口投影：不许再给窗口类加 CS_DROPSHADOW")
    # 这条只能查源码：CS_DROPSHADOW 是**按窗口类**生效的，而 Qt 所有带图标的顶层窗口共用
    # 同一个类（主窗口和每个对话框都是 Qt6xxxQWindowIcon）。一旦为了给主窗口要投影而加它，
    # 那条 3px 硬边阴影会传染给**每一个对话框**（2026-10-10 实际踩过，
    # 用户反馈「有些小窗口带有阴影」）。主窗口的投影走 DWM，见 main_window.showEvent。
    # 冒烟测试本身跑在 offscreen 平台下（拿不到真 HWND），所以只能做源码级断言。
    mw_src = open(os.path.join(BASE_DIR, "comfyui_echo", "ui", "main_window.py"),
                  encoding="utf-8").read()
    assert "SetClassLongPtrW" not in mw_src, \
        "main_window.py 里又出现 SetClassLongPtrW（CS_DROPSHADOW 会污染所有对话框）"
    assert "DwmExtendFrameIntoClientArea" in mw_src, \
        "主窗口的 DWM 柔和投影丢了（见 main_window.showEvent）"
    assert "(34, -2)" in mw_src and "(33, 2)" in mw_src, \
        "DWM 边框色 / 圆角属性设置丢了（见 main_window.showEvent）"
    print("    OK  主窗口投影走 DWM；未给窗口类加 CS_DROPSHADOW")

    print("[13] 品牌 logo（矢量 SVG，方形，三种形态）")
    from comfyui_echo.core import _bridge as _B
    from comfyui_echo.core import brand as _brand
    import comfyui_echo.ui.widgets as _w
    svg_path = os.path.join(_B.RESOURCE_DIR, _brand.LOGO_SVG)
    assert os.path.isfile(svg_path), f"logo SVG 丢了：{svg_path}"
    r_full = _w.logo_renderer(_w.FRAME_FULL)
    r_out = _w.logo_renderer(_w.FRAME_OUTLINE)
    r_none = _w.logo_renderer(_w.FRAME_NONE)
    for tag, r in (("full", r_full), ("outline", r_out), ("none", r_none)):
        assert r is not None, f"logo {tag} 形态渲染器无效"
    badge = _w.LogoBadge(40)
    assert badge.width() == badge.height() == 40, \
        f"logo 必须是正方形：{badge.width()}x{badge.height()}"
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter

    def _alpha_at(rend, x, y):
        im = QImage(256, 256, QImage.Format_ARGB32)
        im.fill(Qt.transparent)
        p = QPainter(im)
        p.setRenderHint(QPainter.Antialiasing, True)
        rend.render(p, QRectF(0, 0, 256, 256))
        p.end()
        return im.pixelColor(x, y).alpha()

    # 三形态必须真的不同：填充 / 描边 / 字标是三层独立的东西。
    # ⚠「描边」和「填充」**不能一起摘掉** —— 那圈边是 logo 自带的，
    #   2026-10-10 一开始整块摘掉，用户就问「周围那个边怎么取消了」。
    assert _alpha_at(r_full, 40, 40) > 200, "full 形态应该有白色填充"
    assert _alpha_at(r_out, 40, 40) == 0, "outline 形态不该有白色填充"
    assert _alpha_at(r_out, 128, 2) > 0, "outline 形态必须保留那圈描边"
    assert _alpha_at(r_out, 128, 128) > 200, "outline 形态仍应画出字标"
    assert _alpha_at(r_none, 128, 2) == 0, "none 形态不该有描边"
    assert _alpha_at(r_none, 128, 128) > 200, "none 形态仍应画出字标"
    # 控件本身必须透明：全局 `QWidget { background: #f7f6f3 }` 会给它刷上一层比侧栏浅的
    # 实心方块，看起来就像「logo 底下压了一块白板」（用户 2026-10-10 反馈过）。
    # 「不画背景的容器」统一用 widgets.plain_box()（objectName="PlainBox"）。
    assert badge.objectName() == "PlainBox", \
        "LogoBadge 必须用 PlainBox 标记，否则全局底色会给它刷底"
    assert "QWidget#PlainBox { background: transparent; }" in style.QSS, \
        "style.py 里缺 QWidget#PlainBox { background: transparent; }"
    # 两个旧实现都已按用户要求删除，别再冒出来：
    #   LogoMark     —— 我自己造的「蓝环 + 橙点」方标
    #   LogoWordmark —— 只有一排字标、丢了方块容器
    for gone in ("LogoMark", "LogoWordmark", "wordmark_renderer"):
        assert not hasattr(_w, gone), f"{gone} 应已删除（见 core/brand.py 的说明）"
    print(f"    OK  logo 三形态均可渲染；LogoBadge(40) = {badge.width()}x{badge.height()}")

    print("[14] 配色对比度（WCAG AA）")
    # 换主题最容易翻车的地方就是文字对比度：面色一变浅，原来的灰字就不够看了。
    # 这里把关键「前景/背景」配对按 WCAG 公式核一遍，低于门槛直接失败。
    from comfyui_echo.ui import style as _st

    def _lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def _lum(h):
        h = h.lstrip("#")
        return (0.2126 * _lin(int(h[0:2], 16)) + 0.7152 * _lin(int(h[2:4], 16))
                + 0.0722 * _lin(int(h[4:6], 16)))

    def _cr(a, b):
        la, lb = _lum(a), _lum(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    checks = [
        ("正文/页面", _st.TEXT, _st.CANVAS, 4.5),
        ("正文/卡片", _st.TEXT, _st.SURFACE, 4.5),
        ("次要/侧栏", _st.TEXT_2, _st.SIDEBAR, 4.5),
        ("次要/卡片", _st.TEXT_2, _st.SURFACE, 4.5),
        ("元信息/卡片", _st.TEXT_3, _st.SURFACE, 3.0),
        ("强调/卡片", _st.ACCENT, _st.SURFACE, 4.5),
        ("强调/淡蓝块", _st.ACCENT, _st.ACCENT_SOFT, 4.5),
        ("白字/主按钮", "#ffffff", _st.ACCENT, 4.5),
        ("白字/危险钮", "#fff4f3", _st.DANGER, 4.5),
        ("成功/卡片", _st.SUCCESS, _st.SURFACE, 4.5),
        ("警告/卡片", _st.WARNING, _st.SURFACE, 4.5),
        ("危险/卡片", _st.DANGER, _st.SURFACE, 4.5),
    ]
    low = [(n, round(_cr(a, b), 2)) for n, a, b, need in checks if _cr(a, b) < need]
    assert not low, f"对比度不达标的配对：{low}"
    print(f"    OK  {len(checks)} 组配对全部达标（最低 "
          f"{min(round(_cr(a, b), 2) for _, a, b, _ in checks)}:1）")

    print("[15] 复选框：胶囊开关（QProxyStyle 自绘）")
    # QSS 的 ::indicator 做不出「滑块左右移动」（image 只能居中贴），所以指示器改由
    # style.SwitchStyle 自绘。两条硬约束（实测出来的，破了会静默退回方块外观）：
    #   ① QSS 里不能有任何 QCheckBox::indicator 规则 —— 否则绘制权归 QStyleSheetStyle；
    #   ② 尺寸要从 pixelMetric(PM_IndicatorWidth/Height) 走。
    # ⚠ 这里**刻意不做运行时实例化**：装着完整主窗口时再 QStyleFactory.create 一个新
    #   style，Qt 会直接硬崩（连 traceback 都来不及打，只剩 SIGTERM），排查成本很高。
    #   所以这几条都做成静态断言 —— 足够守住那两条约束。
    from PySide6.QtWidgets import QProxyStyle
    import re as _re
    assert not _re.findall(r'^\s*QCheckBox::indicator[^{]*\{', style.QSS, _re.M), \
        "style.QSS 里又出现了 QCheckBox::indicator 规则：自绘开关会被顶掉"
    assert style.SwitchStyle.pixelMetric is not QProxyStyle.pixelMetric, \
        "SwitchStyle 没覆盖 pixelMetric（指示器尺寸会退回 Fusion 的 14px）"
    assert style.SwitchStyle.drawPrimitive is not QProxyStyle.drawPrimitive, \
        "SwitchStyle 没覆盖 drawPrimitive（画不出胶囊开关）"
    # 所有入口都必须走 style.apply(app)：漏掉的那处复选框会静默退回方块
    # ⚠ 旧写法用拼接拼出来，否则本文件自己会被这条断言命中（自引用）。
    old_form = "setStyleSheet(style." + "QSS)"
    for rel in ("main.py", "tests/ui_smoke.py", "tests/ui_screenshot.py",
                "tests/ui_min_size.py"):
        src = open(os.path.join(BASE_DIR, rel), encoding="utf-8").read()
        assert "style.apply(app)" in src, f"{rel} 没走 style.apply(app)"
        assert old_form not in src, f"{rel} 还在自己装样式（应改用 style.apply(app)）"
    print(f"    OK  指示器 {style.SwitchStyle.W}x{style.SwitchStyle.H}"
          f"（胶囊轨道 + 圆形滑块）；QSS 未越权接管；4 个入口都走 apply(app)")

    print("[16] 卡片内不许有「未标记的裸容器」")
    # 全局 `QWidget { background: #f7f6f3 }` 会给**每一个**裸 QWidget 刷上画布色：
    # 落在白色卡片上是一条色带、落在侧栏上是一块比侧栏浅的方块。
    # 2026-10-10 为此返工过三次（页签内容页 / 品牌区容器 / 注意力卡档位行），
    # 所以加这条**结构化**断言：卡片里凡是「挂了 layout 的容器」，objectName 不能为空
    # （要么用 widgets.plain_box()，要么是具名控件）。这样以后不用再靠肉眼发现问题。
    from PySide6.QtWidgets import QWidget as _QWidget
    bad = []
    for _name, _key in PAGES:
        win.switch(_key)
        for _ in range(6):
            app.processEvents()
        _page = win.pages[_key]
        for _w in _page.findChildren(_QWidget):
            if _w.layout() is None or _w.objectName() or not _w.isVisible():
                continue
            _p = _w.parentWidget()
            while _p is not None and _p.objectName() != "Card":
                _p = _p.parentWidget()
            if _p is not None:
                bad.append(f"{_name}/{type(_w).__name__}"
                           f"@{_w.mapTo(_page, _w.rect().topLeft()).x()},"
                           f"{_w.mapTo(_page, _w.rect().topLeft()).y()}")
    assert not bad, ("卡片里出现未标记的裸容器（会被全局底色刷成色带）："
                     f"{bad} —— 请改用 widgets.plain_box()")
    print("    OK  卡片内的布局容器都有标记（plain_box 或具名卡片）")

    print("\n界面冒烟通过")
    win.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
