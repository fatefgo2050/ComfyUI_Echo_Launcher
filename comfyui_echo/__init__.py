"""ComfyUI Echo 启动器 —— 合并版。

来源
----
- **界面设计 / 页面结构**：来自「NOBU ComfyUI 启动器」（PySide6），
  原样落在 `comfyui_echo/ui/`，导入路径保持 `from ..core import ...` 不变。
- **功能内核**：来自「ComfyUI Echo 启动器」，原样落在 `echo_launcher/`。

分工方式
--------
`comfyui_echo/core/` 是**适配层**：把 Echo 功能层（配置 / 模式切换 / 插件 Git /
节点映射 / 进程管理 / pip）包装成 UI 期望的函数签名。因此：

    UI 页面  ──调用──▶  comfyui_echo.core（适配层）  ──调用──▶  echo_launcher（功能层）

UI 文件无需改动 import，换后端只动适配层即可。
"""
import os

__version__ = "0.1.0"
APP_TITLE = "ComfyUI Echo 启动器"

# 项目根目录（= Comfyui_Echo/）
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
