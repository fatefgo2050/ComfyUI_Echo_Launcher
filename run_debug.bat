@echo off
rem ComfyUI Echo 启动器 —— 带控制台启动（出错时看这里）
"%~dp0.venv\Scripts\python.exe" "%~dp0main.py"
if errorlevel 1 pause
