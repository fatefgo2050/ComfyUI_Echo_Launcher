"""最小窗口 1100x700 下逐控件体检：找出「实际宽度 < sizeHint 宽度」的控件。

    .venv/Scripts/python.exe tests/ui_min_size.py

**改布局/改按钮文案后必须重跑这个**（1100x700 是窗口最小尺寸，是内容排得最紧的工况）。
输出里每页一行 `[OK]` / `[!!]`，`[!!]` 会列出具体是哪个 objectName、什么文案、差多少。

⚠ 绝对不要加 QT_QPA_PLATFORM=offscreen —— 本机 offscreen 平台字体解析失败，
   QFontMetrics/sizeHint 会基于兜底字体重算，量出来的宽度和真实平台不是一个数，
   照着 offscreen 的数字改布局等于白改。
"""
from __future__ import annotations

import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSpinBox,
                               QWidget)

from comfyui_echo.ui.widgets import LogoBadge

TYPES = (QPushButton, QCheckBox, QComboBox, QLineEdit, QSpinBox)
TOL = 1


def _in_scroll(w) -> bool:
    p = w.parent()
    while p is not None:
        if isinstance(p, QScrollArea):
            return True
        p = p.parent()
    return False


def main() -> int:
    from comfyui_echo import APP_TITLE
    from comfyui_echo.ui import style
    from comfyui_echo.ui.main_window import MainWindow, PAGES

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    style.apply(app)

    win = MainWindow()
    win.resize(1100, 700)
    win.show()
    for _ in range(25):
        app.processEvents()
        time.sleep(0.1)

    total = 0
    for name, key in PAGES:
        win.switch(key)
        page = win.pages[key]
        for _ in range(12):
            app.processEvents()
            time.sleep(0.05)
        bad = []
        for t in TYPES:
            for w in page.findChildren(t):
                if not w.isVisible() or _in_scroll(w):
                    continue
                text = w.text() if hasattr(w, "text") else ""
                if not str(text).strip():
                    continue
                need, got = w.sizeHint().width(), w.width()
                if got + TOL < need:
                    bad.append((w.objectName() or t.__name__,
                                str(text).strip()[:24], got, need))
        total += len(bad)
        flag = "!!" if bad else "OK"
        print(f"[{flag}] {name} ({key}) — {len(bad)} 处压缩")
        for obj, text, got, need in bad:
            print(f"      {obj:<16} {text:<26} width={got} < sizeHint={need}")

    print("---- 页头 / 品牌区（QLabel）----")
    wanted = {"PageTitle", "PageSub", "AppSubtitle"}
    for key in ("home", "version", "settings", "plugins", "modes"):
        page = win.pages.get(key)
        if not page:
            continue
        for lab in page.findChildren(QLabel):
            if lab.objectName() not in wanted or not lab.isVisible():
                continue
            need, got = lab.sizeHint().width(), lab.width()
            got = got - 2 * lab.contentsMargins().left()
            mark = "  " if got >= need else "!!"
            print(f"  {mark} [{key}] #{lab.objectName():<12} "
                  f"{lab.text()[:26]:<28} avail={got} sizeHint={need}")
    for lab in win.findChildren(QLabel):
        if lab.objectName() in ("AppTitle", "AppSubtitle") and lab.isVisible():
            need, got = lab.sizeHint().width(), lab.width()
            mark = "  " if got >= need else "!!"
            print(f"  {mark} [chrome] #{lab.objectName():<12} "
                  f"{lab.text()[:26]:<28} avail={got} sizeHint={need}")

    # 品牌区字标：宽度必须放得进侧栏（侧栏 178px 宽、左右各 12px 内缩）
    side = win.findChild(QWidget, "Sidebar")
    marks = side.findChildren(LogoBadge) if side else []
    if side and marks:
        m = marks[0]
        avail = side.width() - 24
        mark = "  " if m.width() <= avail else "!!"
        print(f"  {mark} [chrome] LogoBadge  {m.width()}x{m.height()}  "
              f"侧栏可用宽 {avail}")
    else:
        print("  !! 没找到 LogoBadge / 侧栏")

    # ---- 侧栏的「纵向预算」----
    # 侧栏（178px 固定宽）不可滚动：11 个导航项 + 4 个分区小标题 + 品牌区，
    # 装不下就会被压扁、按钮文字截断。**加页面 / 加分区后看这一行。**
    win.resize(1100, 700)
    for _ in range(12):
        app.processEvents()
        time.sleep(0.05)
    sd = win.findChild(QWidget, "Sidebar")
    need = sd.layout().sizeHint().height() if sd is not None and sd.layout() else 0
    print("---- 侧栏纵向预算 ----")
    print(f"  [1100x700] 侧栏高 {sd.height()}  导航内容需要 {need}px  "
          f"（剩 {sd.height() - need}px）")
    assert need <= sd.height(), (
        f"侧栏装不下了：内容需要 {need}px > 侧栏高 {sd.height()}px —— "
        "缩字号/减少分区间距，或把页面并进现有分区")

    # ---- 「关于」页：可滚动卡片栈，查「被裁」与「横向裁切」----
    # 这一页是**可滚动卡片栈**（2026-10-12 起正文精简为 2 节，见 about_page 文件头）。内容可能长过视口，
    # 所以「最底控件越出视口」是正常的，要防的是另外两件事：
    #   ① 文字被裁（标签拿到的高度不够它折行后的需要）；
    #   ② 出现横向滚动条（说明卡片或值列被压窄了）。
    win.switch("about")          # 窗口在上面已经 resize 到 1100×700 了
    for _ in range(12):
        app.processEvents()
        time.sleep(0.05)
    ap = win.pages["about"]
    # ⚠ **不要用 `layout().sizeHint()` 判预算**：那一页全是 `wordWrap` 的 QLabel，
    #   Qt 给它们算的 sizeHint 宽度本身就是废数（实测整框只报 206px），累加出来的整页
    #   hint 会虚高 40% 以上，属于误报。要查就查**结果**。
    from PySide6.QtCore import QPoint as _QP
    from PySide6.QtWidgets import QLabel as _QL, QScrollArea as _QSA
    print("---- 「关于」页（可滚动卡片栈，查裁切/横向溢出）----")
    scroll = ap.findChild(_QSA, "PlainScroll")
    assert scroll is not None, "「关于」页没有 #PlainScroll 滚动区"
    hmax = scroll.horizontalScrollBar().maximum()
    vmax = scroll.verticalScrollBar().maximum()
    print(f"  横向滚动量 {hmax}  纵向滚动量 {vmax}")
    assert hmax == 0, "「关于」页出现横向滚动条 —— 有控件被压得比内容还窄"
    clipped = []
    for lab in ap.findChildren(_QL):
        if not lab.isVisible() or not lab.wordWrap():
            continue
        need = lab.heightForWidth(lab.width())
        if lab.height() + 1 < need:
            clipped.append((lab.objectName(), lab.text()[:22], lab.height(), need))
    for obj, txt, got, need in clipped:
        print(f"      !! {obj} 被裁：高 {got} < 需要 {need}  {txt}")
    assert not clipped, "「关于」页有文字被裁掉 —— 查卡片 margins/spacing 是否被压"
    # 首屏（1100×700、未滚动）必须看得见品牌行 —— 它是这一页的身份标识。
    brand = ap.findChild(_QL, "AboutTitle")
    assert brand is not None, "「关于」页没有品牌行（#AboutTitle）"
    on_screen = not brand.visibleRegion().isEmpty()
    print(f"  品牌行底边 {brand.mapTo(ap, _QP(0, 0)).y() + brand.height()}  "
          f"首屏可见 {on_screen}")
    assert on_screen, "「关于」页首屏看不到品牌行 —— 页头/品牌行被挤下去了"

    # ---- 环境页左栏的「纵向预算」（全项目最紧的一处）----
    # 左栏虽然是可滚动区，但**最小窗口下不许出现滚动条**：预算只剩十几个像素。
    # 2026-10-10 把按钮间距从 6 提到 10/12 时，就是先在这里量出「改完 628 > 视口 626」，
    # 才发现必须拿卡片内边距去换。**往环境页左栏加控件/加行距之前，先看这一段。**
    # ⚠ 这一段依赖真实平台的字体度量，所以放在本文件（本文件不用 offscreen），
    #   别搬去 ui_smoke —— 那边是 offscreen，sizeHint 会基于兜底字体重算。
    def _env_budget(w, h):
        win.resize(w, h)
        for _ in range(12):
            app.processEvents()
            time.sleep(0.05)
        win.switch("env")
        for _ in range(12):
            app.processEvents()
            time.sleep(0.05)
        sa = win.pages["env"].findChild(QScrollArea)
        return sa, sa.widget(), sa.viewport().height(), sa.verticalScrollBar().maximum()

    print("---- 环境页左栏纵向预算 ----")
    for _w, _h in ((1100, 700), (1400, 900)):
        sa, inner, vp, mx = _env_budget(_w, _h)
        content = inner.sizeHint().height()
        slack = vp - content
        print(f"  [{_w}x{_h}] 内容 {content}  视口 {vp}  余量 {slack}  滚动量 {mx}")
        assert mx == 0, (f"{_w}x{_h} 下环境页左栏出现 {mx}px 滚动量"
                         f"（内容 {content} > 视口 {vp}）——"
                         "先腾空间（卡片内边距）或调小 env_page 里的 BTN_* 常量")
        if _h >= 900:
            # 余量要分摊到卡间距，不许在末尾堆成一片空白（用户 2026-10-10 的诉求）。
            lv = inner.layout()
            cards = [lv.itemAt(i).widget() for i in range(lv.count())
                     if lv.itemAt(i).widget() is not None]
            tail = vp - (cards[-1].y() + cards[-1].height())
            print(f"           末卡下方留白 {tail}（总余量 {slack}，占 {tail / slack:.0%}）")
            assert tail < slack * 0.5, (
                f"环境页左栏的余量堆在末尾了（末卡下方留白 {tail}px / 总余量 {slack}px）"
                "—— 应把余量分摊到卡间距，见 env_page.EnvPage.__init__ 的注释")

    print(f"\n合计压缩控件：{total}")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
