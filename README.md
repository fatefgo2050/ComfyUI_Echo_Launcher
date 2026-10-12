# ComfyUI Echo 启动器（Comfyui_Echo）

把两个复刻 TE 启动器的项目合并成一个：

| 来源 | 贡献 |
|---|---|
| `D:\WorkBuddy_Workspace\启动器`（NOBU ComfyUI 启动器，PySide6） | **界面设计 / 页面结构** |
| `D:\WorkBuddy_Workspace\Echo`（ComfyUI Echo 启动器，tkinter） | **功能内核** |

合并方式：UI 页面原样保留（连 `from ..core import ...` 的导入路径都没改），
在中间加一层**适配层** `comfyui_echo/core/`，把 Echo 的功能包装成 UI 期望的函数签名。

```
UI 页面 ──调用──▶ comfyui_echo/core（适配层） ──调用──▶ echo_launcher（Echo 功能层）
```

这样换后端只动适配层；以后要补 Qt 版媒体浏览器 / 模型管理 / 设置等也只改 UI 层。

## 当前页面（侧栏 11 页）

```
运行                扩展              素材          系统
  启动                插件              模型          环境
  专家模式            市场              媒体          设置
  ComfyUI 内核        模式                            关于
```

| 页面 | 主要功能 |
|---|---|
| 启动 | 多包路径、启动参数、**注意力四档（默认/SAGE/FLASH/CK）+ shim 接管**、**「指定 GPU」按显卡名下拉**（不是填数字）、控制台 |
| 专家模式 | ComfyUI 高级启动参数（精度 / 显存 / 注意力四档 / 强制接管 shim）集中在一页；「注意力」档位与 CK 实测结果跨页共享 |
| ComfyUI 内核 | **只做 ComfyUI 内核升级**：当前版本 / 提交列表 / 更新日志 / 更新到选中版本；**GitHub 镜像**（官方 / gh-proxy.com / ghproxy.net / ghfast.top，只改 fetch 的实际连址，不动 origin） |
| 插件 | 扫描 / 检查更新（**有更新的插件整行标红**）/ 顶栏「更新 · 启用禁用 · 卸载」作用于选中行 / **自动建还原点** / 救援还原点 / 选中行后在下方详情条**可拖选或一键复制插件名与 Git 地址**（表格焦点下 `Ctrl+C` 复制名称、`Ctrl+Shift+C` 复制地址） |
| 模式 | **一屏三栏**：左＝可用工作流（**单击选中 / 再单击取消**，Shift 连选一段，多选取所需插件并集；**ComfyUI 默认目录的组排最上**，自定义目录单击即替换）、中＝可用插件（选中工作流用得到的自动高亮）、右＝当前模式插件（本次启动只启用这些，缺失的就地备注）；白名单模式、立即应用 / 全部启用 |
| 环境 | **左功能区 + 右输出日志**（对齐 TE「ComfyUI维护」）：Python 环境 / 镜像源 / 库管理（查找·安装·删除·列出）/ 备份与还原 / 特殊安装（torch·Triton·SageAttention·xFormers·llama-cpp）/ **注意力档位（从启动页搬来）**；日志区带「**获取环境信息**」（一键列出 Python/显卡/内核/torch 等版本）与「清空日志」 |
| 媒体 | 缩略图网格 + **按日期/类型/目录分组** + **标签筛选/保存** + **拖拽导入** + 真分割对比 + 视频帧定位；**搜索覆盖文件名 / 提示词 / 标签，边打边过滤（去抖 200ms）** |
| 模型 | 234 模型 / 185 GB、**按底模/用途自动分类（只读文件头）+ 两列两筛 + 批量手改 + 搜索边打边过滤**、查重、文字备注、**Civitai 按 sha256 反查**、**批量导出 CSV（含底模/用途/架构）** |
| 市场 | ComfyUI-Manager 公开目录 6000+ 插件、**输入即过滤**（边打边搜，不用点按钮）、**按节点名反查**（原版 VIP） |
| 设置 | GitHub 自更新仓库 / Token / 托盘 / 通知 / 关闭最小化；**改动即时保存，没有「保存」按钮** |
| 关于 | 软件介绍 + 版本更新（功能用途与更新记录；**原独立对话框，2026-10-10 改成一级页面**）|

「ComfyUI 内核」（原「版本管理」）和「关于」**都是主窗口里的一级页面、不再是弹窗**
（用户明确不要为新功能另开窗口）；侧栏底部那个「关于」按钮已取消，
**版本号只显示在「关于」页一处**。

> 「工作流」原本是独立一页，2026-10-09 与「模式」合并 —— TE 启动器的两种启动模式
> （默认加载全部插件 / 按工作流只加载所需插件）本来就是一回事，
> 分析出「需要哪些插件」却要跳到另一页才能用，等于没打通。
> 合并后布局对齐 TE 的三栏：**选中左边的工作流 → 中栏高亮它用得到的插件 → 右栏就是
> 本次启动会启用的清单**（缺失插件在右栏就地备注）。左栏工作流可以**多选**，
> 白名单取它们所需的并集；树不画展开三角，文件夹行当小标题用。

系统级：
- **系统托盘**：主菜单项（显示 / 网页 / 重启 / 各目录 / 退出），就绪时弹 Windows 通知
- **关闭窗口**默认最小化到托盘（在设置里可关）

---

## 运行

```bat
run.bat            静默启动（无控制台）
run_debug.bat      带控制台启动（出错看这里）
```

首次使用：进「启动」页 → 「管理路径...」或「自动扫描」选择 ComfyUI 目录。
两种布局都认：

- **便携包根目录**：`<root>\python_embeded\python.exe` + `<root>\ComfyUI\main.py`
- **ComfyUI 本体目录**：`<root>\main.py` + `comfy\`（或 `comfyui_version.py` / `nodes.py`）

命令行子命令（不需要 Qt）：

```bat
.venv\Scripts\python.exe main_cli.py selftest         核心链路自检（10 项）
.venv\Scripts\python.exe main_cli.py scan             扫描当前包的插件
.venv\Scripts\python.exe main_cli.py git-info         便携 Git 可用性
.venv\Scripts\python.exe main_cli.py net-check        公开数据源连通性
.venv\Scripts\python.exe main_cli.py attention-check  实测 comfy-kitchen INT8 注意力
.venv\Scripts\python.exe main_cli.py paths            冻结态关键路径
```

测试（全部应保持通过）：

```bat
.venv\Scripts\python.exe tests\ui_smoke.py           11 页 + 救援对话框 + 托盘（离屏）
.venv\Scripts\python.exe tests\e2e_check.py          真实 ComfyUI 上 4 页数据链路
.venv\Scripts\python.exe tests\e2e_new_pages.py      媒体 / 模型 / 市场 / 版本 / 重型轮子（真实数据）
.venv\Scripts\python.exe tests\process_check.py      启停链路（临时假包，不动真 ComfyUI）
.venv\Scripts\python.exe tests\ui_screenshot.py      11 页各截一张图到 tools\shots\
```

---

## 目录结构

```
Comfyui_Echo/
├─ main.py                     入口：QApplication + Fusion + 暖米黄 QSS
├─ main_cli.py                 无界面子命令
├─ comfyui_echo/
│  ├─ __init__.py              APP_TITLE / __version__
│  ├─ core/                    ★ 适配层（每次合并/补功能时新写）
│  │   ├─ _bridge.py           Echo 单例 + 便携 Git 代理注入
│  │   ├─ config.py            配置双向映射 + get_echo/set_echo（Echo 独有键）
│  │   ├─ paths.py             便携包 / ComfyUI 目录
│  │   ├─ process.py           LaunchOptions + ComfyRunner（Qt 信号）
│  │   ├─ plugins.py           插件扫描 / 更新 / 安装 / 启停 / 模式应用
│  │   ├─ nodemap.py           节点映射 + 工作流识别
│  │   ├─ pipenv.py            pip 维护
│  │   ├─ attention.py         注意力四档 + shim 接管
│  │   ├─ rescue.py            救援还原点
│  │   ├─ media.py             媒体库（缩略图 + 元数据 + 视频帧）
│  │   ├─ model.py             模型 / LoRA + Civitai
│  │   ├─ wheels.py            重型轮子（修掉 wheels.probe 的 SyntaxError）
│  │   ├─ selfupdate.py        GitHub Releases 自更新
│  │   ├─ market.py            插件市场搜索安装
│  │   ├─ version.py           Git 插件版本管理（切 commit / 切分支）
│  │   └─ tray.py              托盘（Qt 信号派发）+ 通知
│  └─ ui/                      页面层
│      ├─ main_window.py        11 页侧栏 + 托盘
│      ├─ widgets.py            跨页面小部件（卡片 / 分隔线）
│      ├─ dialogs.py            统一弹窗（无图标）
│      ├─ home_page.py          启动参数 + 控制台
│      ├─ plugins_page.py       插件 + 救援
│      ├─ modes_page.py         模式（含工作流识别 → 生成启动模式）
│      ├─ env_page.py           环境（包管理 + 重型轮子 标签页）
│      ├─ media_page.py         媒体浏览器（分组 / 标签 / 拖拽导入）
│      ├─ media_compare.py      对比对话框（真正的分割对比 / 视频同步播放）
│      ├─ model_page.py         模型管理 + CSV 导出
│      ├─ market_page.py        插件市场
│      ├─ settings_page.py      设置
│      ├─ version_page.py       ComfyUI 内核（版本查看 / 切换）
│      ├─ about_page.py         关于（2026-10-10 从对话框改成一级页面）
│      ├─ rescue_dialog.py      救援还原点对话框
│      ├─ style.py              暖米黄 QSS
│      └─ workers.py            通用 Worker
├─ echo_launcher/              Echo 功能层（原样保留，**ui/ 是 tkinter 不参与运行**）
├─ resources/                  便携 Git + 图标 + 导航图
├─ data/                       运行时数据
├─ logs/                       日志
├─ tests/  tools/shots/
├─ dist/                  打包产物（exe + resources，见「打包」）
└─ requirements.txt  run.bat  run_debug.bat  build_release.py  build_release.bat
```

---

## 页面 ↔ 适配层 ↔ Echo 对照

| 页面 | 适配层 | 落到 Echo |
|---|---|---|
| 启动 | `core/process.py` `core/attention.py` `core/paths.py` | `core.process` `core.attention` `core.attn_shim` |
| 插件 | `core/plugins.py` `core/rescue.py` | `core.mode` `plugin.gitops` `core.rescue` |
| 模式（含工作流） | `core/plugins.py` `core/nodemap.py` | `core.mode.apply_mode` `plugin.scanner` `plugin.node_map` `plugin.workflow` |
| 环境 | `core/pipenv.py` `core/wheels.py` | `env.pipwrap` `env.wheels` |
| 媒体 | `core/media.py` | `media.browser` `media.meta` `media.video` |
| 模型 | `core/model.py` `core/modelmeta.py` | `model.manager` `model.civitai` |
| 市场 | `core/market.py` `core/nodemap.py` | `plugin.market` `plugin.node_map` |
| 设置 | `core/config.py` `core/selfupdate.py` | `core.config.Config` `update.selfupdate` |
| ComfyUI 内核 | `core/core_version.py` | `plugin.gitops`（只操作 ComfyUI 本体仓库）+ `net.mirrors.GITHUB_MIRRORS` |
| 托盘 / 通知 | `core/tray.py` | pystray + win11toast |

---

## 已接入的功能（来自 Echo，全部走公开源）

- **TE 模式**（`.disabled` 就地改名）
- **注意力后端四档**（默认/SAGE/FLASH/CK）+ **CK 前置实测** + **解释器级 shim 接管**
- **救援还原点**（更新前自动建点，可一键回滚 + 还原依赖环境）
- **便携 Git**（无需系统 git 即可 clone/fetch/切版本）
- **多源回退 + 私有域名拦截**（`BLOCKED_HOSTS`）
- **日志总线**（Qt 定时器抽干，错误/警告高亮）
- **媒体浏览器**：缩略图网格、按日期/类型/目录分组、标签筛选/保存、文件/目录拖拽导入、生成参数、图片**真正分割对比**、视频帧定位
- **模型管理器**：扫描 / 查重 / 文字+图片备注 / **Civitai 按 sha256 反查** / 批量匹配 / **Excel/WPS 友好的 UTF-8 BOM CSV 导出**
- **插件市场**：ComfyUI-Manager 公开目录搜索 + **按节点名反查**（原 VIP 功能）
- **Git 插件版本管理**：检查更新 / 切 commit / 切分支 / 单个/批量更新（自动建还原点）
- **重型轮子一键装**：torch / SageAttention / xFormers（按 CUDA 自动挑 wheel）
- **GitHub Releases 自更新**
- **系统托盘 + Windows 通知**

## 没接入 Qt 界面的剩余功能

| 功能 | 后端模块 | 状态 |
|---|---|---|
| ~~媒体浏览器~~ | `media/*` | ✅ 已搬（comfyui_echo/ui/media_page.py） |
| ~~模型管理器~~ | `model/*` | ✅ 已搬 |
| ~~版本管理~~ | `plugin/gitops` | ✅ 已搬（现为「ComfyUI 内核」页） |
| ~~重型轮子~~ | `env/wheels` | ✅ 已搬（环境页第二个标签） |
| ~~托盘 + 通知~~ | `ui/tray` | ✅ 已搬 |
| ~~自更新~~ | `update/selfupdate` | ✅ 已搬（设置页） |
| ~~插件市场搜索~~ | `plugin/market` | ✅ 已搬 |
| ~~媒体拖拽导入~~ | `core/media.py` | ✅ 已搬（默认复制，同名不覆盖） |
| ~~媒体分组 / 标签~~ | `core/media.py` | ✅ 已搬（日期/类型/目录 + JSON 标签） |
| ~~模型批量导出 CSV~~ | `core/model.py` | ✅ 已搬（UTF-8 BOM） |

---

## 合并过程中修掉的问题

1. **Echo 官方节点映射表解析错误（功能一直没生效）**
   `NodeMap.resolve()` 按「节点名→插件」查，官方文件真实结构是
   「插件 URL→[节点名]」，所以官方兜底一直没生效。适配层重建反查表，
   实测 41935 个节点可反查，`IPAdapterUnifiedLoader → cubiq/ComfyUI_IPAdapter_plus` 正确命中。

2. **Echo 的 `wheels.probe()` 探测 torch 永远失败（SyntaxError）**
   它拼给 `python -c` 的源码是 `gn='';try:\n import torch;...` —— `gn='';try:` 把复合
   语句接在分号后面，Python 直接 SyntaxError。所以 python_version / torch_version
   永远是空字符串，"重型轮子" 从未真正探测过 torch，只能退回 nvidia-smi 猜 CUDA。
   适配层用语法正确的多行脚本重实现。实测：
   `Python 3.13.14 | torch 2.12.0+cu132 | CUDA 13.2 | 显卡 RTX 4060 Laptop GPU`。

3. **Echo 的 `echo_launcher.ui.__init__.py` eagerly import tkinter**
   整个 Qt 应用一旦 `from echo_launcher.ui.tray import ...` 就会触发 tkinter 导入，
   而 venv 里没装。适配层的托盘图标/通知直接用 PIL + win11toast 重写。
   （注：`echo_launcher/ui/` 死代码包已于 2026-10-12 整体删除。）

4. **Echo 的批量 Civitai 匹配双重 start**
   `threading.Thread(target=work, daemon=True).start()` 被写了两遍，等于并发跑两轮。
   修掉。

5. **Echo 的批量匹配用 `f.name` 访问 ModelFile**
   `ModelFile` 没有 `name` 属性（只有 `rel_path`），原版会直接 AttributeError。
   适配层改用 `rel_path`。

6. **`os.path.normpath("") == "."`**
   空路径条目会被当成有效路径写进配置。+ 「含 main.py」不足以判定 ComfyUI。
7. **Echo 的日志队列阻塞式 put**，Qt 下没有消费者会死锁 → 适配层加常驻 Qt 定时器泵。
8. **性能**：插件扫描 44s→10s、节点映射 26.6s→10.3s（8 线程并发）。

---

## 数据源（全部公开）

| 用途 | 接口 |
|---|---|
| 节点 → 插件 | `Comfy-Org/ComfyUI-Manager` 的 `extension-node-map.json`（jsdelivr / raw / ghfast） |
| 插件目录 | `custom-node-list.json`（同上） |
| 版本历史 | `api.github.com/repos/{o}/{r}/commits`（Token 60→5000/h） |
| LoRA 反查 | `civitai.com/api/v1/model-versions/by-hash/{sha256}` |
| pip | 官方 / 阿里云 / 清华 / 腾讯云 |
| torch | `download.pytorch.org/whl/{cpu,cu118...cu132}` |
| SageAttention | `woct0rdho/SageAttention` GitHub Release |
| xFormers | `Wildminder/AI-windows-whl` HuggingFace |

`net/mirrors.py` 的 `BLOCKED_HOSTS` 每次请求前硬校验，
保证不会访问原作者的私有域名（`tetae.top` / `588187.xyz` / `teynex.com`）。

---

## 打包（已实现）

```bat
build_release.bat                  正式版：单文件 exe（无控制台）
build_release.bat --console        诊断版：保留控制台
build_release.bat --tool nuitka    改用 Nuitka（需先解除 reg.exe 限制，见下）
```

产物（`dist/`）：

```
dist\
├─ ComfyUI_Echo启动器.exe    单文件主程序（约 86 MB，含图标/版本资源）
└─ resources\                外置资源：便携 Git + 图标（约 67 MB）
```

分发：把 `dist/` 整个拷给用户，**exe 必须与 resources/ 同级**。

**为什么 `resources/` 必须外置**
便携 Git 有 328 个文件、155 个 `.exe`/`.dll`。冻结器的数据文件收集对二进制都不可靠
（Nuitka `--include-data-dir` 实测二进制 0/155 全丢，`cmd/` 整个目录消失），
所以由 `build_release.py` 在构建后把 `resources/` 原样复制到 exe 旁边；
`echo_launcher/__init__.py` 的 `_resolve_resource_dir()` 已优先取 `BASE_DIR/resources`。

**默认工具是 PyInstaller，不是 Nuitka**
README 原来写的是 Nuitka，代码里的冻结态注释也是按 Nuitka 4.2.2 写的。但本机环境中
Nuitka 走不通：Python 3.13 起 Nuitka 禁用 MinGW64，必须用 MSVC；而 MSVC 的 SDK 定位链
（Nuitka → SCons → `vcvarsall.bat` → `reg.exe`）要调用 `reg.exe`，该程序被安全策略列为
**不可绕过的黑名单**，于是 SCons 拿不到 Windows SDK 版本，报 `CC is not set` 而失败。
PyInstaller 不需要任何 C 编译器，产物形态与 Nuitka 一致（onefile + 外置 resources）；
解除 `reg.exe` 限制后可用 `--tool nuitka` 回到原始路线。

**构建脚本参数**

| 参数 | 作用 |
|---|---|
| `--tool pyinstaller\|nuitka` | 打包工具（默认 pyinstaller） |
| `--console` | 保留控制台（诊断用） |
| `--entry PATH` | 换入口脚本（默认 `main.py`） |
| `--tag NAME` | 诊断产物名，给定时强制控制台且不改名/不复制资源 |
| `--no-resources` | 跳过 resources 外置 |

两种工具都会排除源码里从未 import 的 PySide6 模块
（WebEngine 197M / qml / 3D / Multimedia / Designer / Charts / Pdf …），
避免把 639M 的 Qt 全家桶塞进产物；`--noinclude-qt-translations`（Nuitka）同理。

**冻结态验证方法**（不需要显示器）

```bat
build_release.bat --entry tests\ui_smoke.py --tag _selftest
dist\_selftest.exe        :: 退出码 0 = 11 页 + 救援对话框 + 托盘全部构建成功
```

即把项目自带的 `tests/ui_smoke.py` 打进冻结包再跑一遍——它覆盖 PySide6 全部页面、
救援对话框与 pystray 托盘，用退出码判定成败，可捕捉冻结后才暴露的漏包问题。

