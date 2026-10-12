# ComfyUI Echo 启动器 v0.1.0

纯本地运行的 ComfyUI 管理面板：在一台机器上管理多套 ComfyUI 环境，把**启动 / 插件 / 工作流 / 模型 /
媒体 / 内核版本**这些日常运维收进一个窗口。

## 下载

| 文件 | 说明 |
|---|---|
| `ComfyUI_Echo_Launcher_v0.1.0_windows-x64.zip` | **免安装**：解压后双击 `ComfyUI Echo启动器.exe` 即可，**无需安装 Python** |
| Source code (zip / tar.gz) | GitHub 自动生成的源码包（也可直接 clone 本仓库） |

- 平台：**Windows 10 / 11（64 位）**。
- 解压后 `ComfyUI Echo启动器.exe` 与 `resources/` **必须保持同级**，不要只把 exe 单独拷走。

## 免安装包内容

```
ComfyUI Echo启动器.exe     单文件主程序
resources/                 图标 + 便携 Git 等随包资源（exe 靠它定位资源，必须同级）
```

## 从源码运行（开发者）

```bat
:: 需要 Python 3.13（64 位）
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

打包发布版：`.venv\Scripts\python.exe build_release.py`（产物在 `dist/`）。

## 功能一览

- **多路径管理**：多套 ComfyUI 根目录与各自的 Python 解释器
- **模式与插件启停**：按工作流切换插件集，`.disabled` 就地重命名，不改 ComfyUI 源码
- **启动与停止**：端口占用清理、子进程树终止、控制台实时输出
- **插件市场与内核版本**：GitHub 直连，安装 / 检查更新 / 切换提交
- **模型与媒体**：模型查重与 Civitai 反查、缩略图浏览、图片分割对比
- **环境救援**：插件目录 + 依赖清单双备份，一键还原
- **注意力后端**：默认 / SageAttention / FlashAttention / CK INT8 实测，四档互斥

## 数据与隐私

不依赖任何私有服务端：插件索引直连 ComfyUI-Manager 的公开 JSON，模型信息走 HuggingFace / Civitai
公开接口；全部数据留在本机，不上传运行信息。

## 校验

- 目标平台：Windows x64（PyInstaller onefile 构建）。
- 构建自本仓库 `main` 的 v0.1.0 标签源码。
