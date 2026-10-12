纯本地运行的 **ComfyUI 管理面板**：在一台机器上管理多套 ComfyUI 环境，
把 启动 / 插件 / 工作流 / 模型 / 媒体 / 内核版本 收进一个窗口。

**首个版本 v0.1.0**，适用于 Windows 10 / 11（64 位）。

## 安装

1. 下载下方 Assets 里的 `ComfyUI_Echo_Launcher_v0.1.0_windows-x64.zip`
2. 解压到任意目录
3. 双击 `ComfyUI Echo启动器.exe` —— 免安装，无需 Python

> ⚠ 解压后 `ComfyUI Echo启动器.exe` 与 `resources/` 文件夹**必须保持同级**，别只把 exe 单独拷走。

## 本版功能

- **多环境管理**：多套 ComfyUI 根目录与各自的 Python 解释器
- **按工作流切插件**：`.disabled` 就地重命名，不改 ComfyUI 源码
- **启停运维**：端口占用清理、进程树终止、控制台实时输出
- **插件市场 / 内核版本**：GitHub 直连，安装、更新、切换提交
- **模型与媒体**：查重、Civitai 反查、缩略图浏览、图片分割对比
- **环境救援**：插件目录 + 依赖清单双备份，一键还原
- **注意力后端**：默认 / SAGE / FLASH / CK INT8，四档互斥

## 其他

- 从源码运行 / 自行打包：见 [README](https://github.com/fatefgo2050/ComfyUI_Echo_Launcher#从源码运行)
- 全部数据留在本机，不依赖任何私有服务端（详见 README）
