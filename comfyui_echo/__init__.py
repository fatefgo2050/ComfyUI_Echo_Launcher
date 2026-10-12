"""ComfyUI Echo 启动器。

分层
----
- `comfyui_echo/ui/`    界面与页面结构（PySide6）
- `comfyui_echo/core/`  适配层：把功能内核包装成 UI 期望的函数签名
- `echo_launcher/`      功能内核：配置 / 模式 / Git / 节点映射 / 进程 / pip / 媒体 / 模型

    UI 页面  ──调用──▶  comfyui_echo.core（适配层）  ──调用──▶  echo_launcher（功能内核）

UI 文件不改 import；换后端只动适配层即可。
"""
import os

__version__ = "0.1.0"
APP_TITLE = "ComfyUI Echo 启动器"

# 项目根目录（= Comfyui_Echo/）
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
