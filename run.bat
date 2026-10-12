@echo off
rem ComfyUI Echo 启动器 —— 静默启动（无控制台窗口）
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
