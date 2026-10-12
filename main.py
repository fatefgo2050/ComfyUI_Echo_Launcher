"""ComfyUI Echo 启动器入口（PySide6 图形界面）。

    python main.py            启动图形界面
    python main_cli.py --help 查看无界面子命令（自检 / 扫描 / 连通性 …）
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main() -> int:
    # 隐藏入口：在独立进程里做「节点映射」全量扫描，供 core/nodemap 子进程调用。
    # 必须放在 import Qt 之前 —— 子进程不该为了一次扫描去加载 Qt。
    # 大插件源码（如 1.1MB 的 Comfly.py）单次 ast.parse 会独占 GIL ~1.7 秒，
    # 放主进程会把 GUI 卡住，所以扫描整体挪到这里跑。
    if len(sys.argv) >= 3 and sys.argv[1] == "--nodemap-scan":
        import json
        from comfyui_echo.core import nodemap
        mapping, failed = nodemap._scan_nodes_parallel(sys.argv[2])
        # ensure_ascii=True（默认）→ 纯 ASCII 传输，跨进程/跨控制台编码都不怕中文节点名
        print(nodemap._SCAN_SENTINEL + json.dumps([mapping, failed]))
        return 0

    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from comfyui_echo import APP_TITLE
    from comfyui_echo.ui import style
    from comfyui_echo.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    # 注意：**不要** setApplicationDisplayName —— Windows 会把它拼到每个对话框标题后面，
    # 变成「确认删除 - ComfyUI Echo 启动器 v0.1.1」。主窗口标题自己 setWindowTitle 即可。

    # Qt 的官方中文翻译**必须显式加载**，否则 Qt 标准控件的文字全是英文 ——
    # 行编辑右键菜单（撤销 / 复制 / 粘贴…）、QMessageBox 的 Yes/No、文件对话框都算。
    # ⚠ Qt6 已把基础翻译合并进 `qt_zh_CN.qm`（Qt5 时代的 `qtbase_zh_CN` 已不存在），
    #   所以是 load(..., "qt", "_", <translations 目录>)。
    # ⚠ 必须留着这个引用（这里挂在 app 上）—— QTranslator 一旦被回收，翻译就失效。
    qt_zh = QTranslator(app)
    if qt_zh.load(QLocale(QLocale.Chinese, QLocale.China), "qt", "_",
                  QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(qt_zh)

    style.apply(app)

    # 图标复用 Echo 的资源（蓝底白字标）
    for name in ("echo_white.ico", "echo_logo.png"):
        icon_path = os.path.join(BASE_DIR, "resources", name)
        if os.path.isfile(icon_path):
            app.setWindowIcon(QIcon(icon_path))
            break

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
