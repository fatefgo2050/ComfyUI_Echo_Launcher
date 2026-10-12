"""功能级联调（离屏）：真实 ComfyUI 路径下跑一遍各页的数据链路。

    .venv/Scripts/python.exe tests/e2e_check.py

会实际扫描插件、分析工作流（走缓存）、列 pip 包，但**不启动 ComfyUI**。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main() -> int:
    from PySide6.QtWidgets import QApplication
    from comfyui_echo.ui import style
    from comfyui_echo.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    style.apply(app)
    win = MainWindow()
    win.show()

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.05)

    root = win.ctx.root()
    print("当前包路径:", root or "(未配置)")
    if not root:
        print("请先配置路径后再跑本测试")
        return 1

    print("\n[1] 启动页版本信息")
    print("   ", win.pages["home"].ver_label.text())

    print("\n[2] 插件页自动扫描（等待最多 40 秒）")
    win.switch("plugins")
    pp = win.pages["plugins"]
    for _ in range(400):
        pump(0.1)
        if pp.table.rowCount() > 0:
            break
    print(f"    表格行数 {pp.table.rowCount()}")
    for r in range(min(3, pp.table.rowCount())):
        cells = [pp.table.item(r, c).text() if pp.table.item(r, c) else ""
                 for c in range(6)]
        print("    ", " | ".join(cells))

    print("\n[3] 模式页（一屏三栏：工作流 / 插件 / 当前模式插件）")
    win.switch("modes")
    mp = win.pages["modes"]
    pump(0.5)
    print(f"    工作流 {len(mp._wf_files)} 个；可用插件 {len(mp._all_plugins)} 个")
    print(f"    当前模式 {mp.mode_combo.currentText()!r} · 白名单 {len(mp._checked)} 个插件")

    print("\n[4] 选中工作流 → 中栏高亮 + 右栏自动填充（只分析，不落盘）")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from comfyui_echo.ui import style

    wf_items = []
    for i in range(mp.wf_tree.topLevelItemCount()):
        top = mp.wf_tree.topLevelItem(i)
        for j in range(top.childCount()):
            c = top.child(j)
            if c.data(0, Qt.UserRole):
                wf_items.append(c)
    if wf_items:
        # setSelected 会触发 itemSelectionChanged → 去抖 250ms → _analyze_files（无模态框）
        wf_items[0].setSelected(True)
        for _ in range(600):      # 首次要建本地节点映射，给足时间
            pump(0.1)
            if mp._result is not None:
                break
        print(f"    分析结果：{mp._summary_text(mp._result, mp._wf_paths)}")
        res = mp._result or {}
        print(f"    工作流需要 {len(mp._wf_needed)} 个已装插件 -> "
              f"{sorted(mp._wf_needed)[:5]}")
        print(f"    缺失 {len(res.get('missing', []))} / 未识别 {len(res.get('unknown', []))}")
        print(f"    右栏（本次启动会启用）{mp.mode_list.count()} 行，"
              f"标题：{mp.lbl_mode_count.text()}")
        for r in range(mp.mode_list.count()):
            print("      |", mp.mode_list.item(r).text())
        succ = QColor(style.SUCCESS).name()
        hl = sum(1 for i in range(mp.plug_list.count())
                 if mp.plug_list.item(i).foreground().color().name() == succ)
        print(f"    中栏绿色高亮 {hl} 个（应 = 右栏里已安装的插件数）")
        assert hl > 0, "选中工作流后中栏应该有高亮"

        print("\n[4b] 工作流多选 → 白名单取并集")
        if len(wf_items) > 1:
            first_needed = set(mp._wf_needed)
            wf_items[1].setSelected(True)     # 追加选择第二份
            for _ in range(600):
                pump(0.1)
                if len(mp._wf_paths) == 2 and mp._result is not None:
                    break
            need2 = set(mp._wf_needed)
            print(f"    选中 {mp._wf_paths and len(mp._wf_paths)} 份工作流 → "
                  f"需 {len(need2)} 个插件（第 1 份单独要 {len(first_needed)} 个）")
            print(f"    并集是否覆盖第一份：{first_needed <= need2}")
            print(f"    摘要：{mp._summary_text(mp._result, mp._wf_paths)}")
            assert first_needed <= need2, "多选时白名单必须是并集"
        else:
            print("    （只有一份工作流，跳过）")
    else:
        print("    （没有工作流文件，跳过）")

    print("\n[4c] 中栏「全部刷新」必须真的刷新（插件 + 工作流 + 重新分析）")
    old_res = mp._result
    n_plug, n_wf = len(mp._all_plugins), len(mp._wf_files)
    mp.btn_plug_refresh.click()
    for _ in range(900):
        pump(0.1)
        if mp._result is not None and mp._result is not old_res:
            break
    pump(0.3)
    print(f"    插件 {n_plug} → {len(mp._all_plugins)}；"
          f"工作流 {n_wf} → {len(mp._wf_files)}；"
          f"分析结果对象已更换 = {mp._result is not old_res}")
    assert mp._all_plugins and mp._wf_files, "全部刷新没有重新扫描"
    if mp._wf_paths:
        assert mp._result is not old_res, "全部刷新没有重新分析选中的工作流"

    print("\n[5] 环境页 pip 列表（等待最多 60 秒）")
    win.switch("env")
    ep = win.pages["env"]
    ep.refresh()
    for _ in range(600):
        pump(0.1)
        if ep.cmb_pkg.count() > 0:
            break
    print(f"    已装包 {ep.cmb_pkg.count()} 个")
    for i in range(min(3, ep.cmb_pkg.count())):
        print("    ", ep.cmb_pkg.itemText(i), ep.cmb_pkg.itemData(i))

    print("\n联调结束")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
