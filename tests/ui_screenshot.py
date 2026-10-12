"""把 `PAGES` 里的每个页面各截一张图，用于人工确认界面渲染正常。

    .venv/Scripts/python.exe tests/ui_screenshot.py [输出目录]

默认写到 tools/shots/。**不要**加 `QT_QPA_PLATFORM=offscreen`（本机字体解析失败、出图豆腐块）。
"""
from __future__ import annotations

import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE_DIR, "tools", "shots")
    os.makedirs(out_dir, exist_ok=True)

    from PySide6.QtWidgets import QApplication
    from comfyui_echo import APP_TITLE
    from comfyui_echo.ui import style
    from comfyui_echo.ui.main_window import MainWindow, PAGES

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    style.apply(app)

    win = MainWindow()
    win.resize(1400, 900)
    win.show()

    # 给版本读取（nvidia-smi / python --version）与页面 on_show 留出时间
    for _ in range(30):
        app.processEvents()
        time.sleep(0.1)

    saved = []
    for name, key in PAGES:
        win.switch(key)
        page = win.pages[key]
        # 媒体页要真的扫一遍（异步生成缩略图）才看得出东西
        if key == "media" and hasattr(page, "refresh"):
            page.refresh()
        # 模式页选中第一份工作流：否则三栏是空的，看不出中栏高亮 / 右栏自动填充
        if key == "modes":
            from PySide6.QtCore import Qt
            for i in range(page.wf_tree.topLevelItemCount()):
                top = page.wf_tree.topLevelItem(i)
                if top.childCount():
                    page.wf_tree.setCurrentItem(top.child(0))
                    break
            deadline = time.time() + 60
            while time.time() < deadline and page._result is None:
                app.processEvents()
                time.sleep(0.1)
        # 「ComfyUI 内核」页首次进页是异步加载（读 git 约 2 秒），等表格出来再截图
        if key == "version":
            deadline = time.time() + 30
            while time.time() < deadline and page.table.rowCount() == 0:
                app.processEvents()
                time.sleep(0.1)
            if page.table.rowCount() > 1:
                page.table.selectRow(1)     # 选一条非当前版本，顺带看「更新日志」
                for _ in range(20):
                    app.processEvents()
                    time.sleep(0.1)
        for _ in range(10):
            app.processEvents()
            time.sleep(0.05)
        if key == "media":
            deadline = time.time() + 40
            while time.time() < deadline:
                app.processEvents()
                time.sleep(0.1)
                if page.grid.count() and page.grid.item(0) and \
                        not page.grid.item(0).icon().isNull():
                    break
        path = os.path.join(out_dir, f"{key}.png")
        win.grab().save(path)
        saved.append(path)
        print("saved:", path)

    # 「关于」现在是「系统」分区里的一级页面（原 about_dialog 对话框已删），
    # 它已经在上面按 PAGES 遍历时截过图了，这里不再单独截对话框。
    win.close()
    print(f"共 {len(saved)} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
