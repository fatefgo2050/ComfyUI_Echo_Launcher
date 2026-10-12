@echo off
rem 打包 ComfyUI Echo 启动器为发布版（Nuitka onefile + resources 外置）
rem 用法：build_release.bat            正式版（无控制台）
rem       build_release.bat --console  诊断版（保留控制台，出错看这里）
"%~dp0.venv\Scripts\python.exe" "%~dp0build_release.py" %*
if errorlevel 1 pause
