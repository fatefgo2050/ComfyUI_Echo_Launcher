"""把 ComfyUI Echo 启动器打成发布版（单文件 exe + resources 外置）。

用法（必须用项目自带 .venv 的 Python 运行）：

    .venv\\Scripts\\python.exe build_release.py                默认：PyInstaller
    .venv\\Scripts\\python.exe build_release.py --tool nuitka  改用 Nuitka（见下）
    .venv\\Scripts\\python.exe build_release.py --console      诊断版（保留控制台）

产物：

    dist\\ComfyUI_Echo启动器.exe     单文件主程序
    dist\\resources\\                 外置资源（便携 Git + 图标）
    dist\\build-report.xml           仅 Nuitka：本次构建打进了哪些模块（排查用）

为什么 resources 要外置（对两种打包工具都成立）
----------------------------------------------------------
便携 Git 有 68 MB / 328 个文件，其中 155 个是 .exe/.dll。冻结器的
``--include-data-dir`` / ``--add-data`` 对二进制的处理都不可靠（Nuitka 实测
二进制 0/155 全丢，``cmd/`` 整个目录消失），所以统一在构建后把 ``resources/``
原样复制到 exe 旁边；``echo_launcher/__init__.py`` 的
``_resolve_resource_dir()`` 会**优先**读 exe 旁的 ``resources/``。

顺带：源码只 import 了 ``QtCore/QtGui/QtWidgets/QtSvg``（QtSvg 是 2026-10-10 起要的
—— 左上角品牌字标 `resources/echo_wordmark.svg` 靠它渲染），其余 PySide6 模块
（WebEngine 197M / qml / 3D / Multimedia / Designer / Charts / Pdf …）
全部排除，避免把 639M 的 Qt 全家桶塞进产物。

关于 --tool nuitka
------------------
早期方案走的是 Nuitka 路线，代码里的冻结态注释也是按 Nuitka 4.2.2 写的。
但本机环境中 **Nuitka 走不通**：Python 3.13 起 Nuitka 禁用 MinGW64，必须用
MSVC；而 MSVC 的 SDK 定位链（Nuitka → SCons → vcvarsall.bat → reg.exe）
要调用 ``reg.exe``，该程序被本机安全策略列为**不可绕过的黑名单**，
于是 SCons 拿不到 Windows SDK 版本，报 ``CC is not set`` 而失败。
PyInstaller 不需要任何 C 编译器，因此作为默认工具；解除 reg.exe 限制后
可用 ``--tool nuitka`` 回到 Nuitka 路线。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

PROJECT = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(PROJECT, ".venv", "Scripts", "python.exe")
MAIN_PY = os.path.join(PROJECT, "main.py")
ICON = os.path.join(PROJECT, "resources", "echo_white.ico")
SRC_RES = os.path.join(PROJECT, "resources")
OUT_DIR = os.path.join(PROJECT, "dist")
BUILD_DIR = os.path.join(PROJECT, "build")

BUILD_NAME = "ComfyUI_Echo_Launcher"               # 构建用纯 ASCII 名（避开工具链编码问题）
RELEASE_NAME = "ComfyUI_Echo启动器.exe"            # 交付名（构建后重命名）
VERSION = "0.1.0"
COMPANY = "ComfyUI Echo"
PRODUCT = "ComfyUI Echo 启动器"
DESC = "ComfyUI Echo 启动器 - ComfyUI 多包启动 / 插件 / 模型 / 媒体管理"

# 源码里从未 import 的 PySide6 模块（已 grep 全库确认）：
# 排除后其绑定 / 对应 Qt DLL 不会再进产物。
QT_UNUSED = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2",
    "PySide6.QtQuickTest", "PySide6.QtQml",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtDesigner", "PySide6.QtUiTools", "PySide6.QtCharts",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDataVisualization",
    "PySide6.QtGraphs", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtNetworkAuth", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtStateMachine", "PySide6.QtHelp", "PySide6.QtLocation",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtTextToSpeech",
    "PySide6.QtHttpServer", "PySide6.QtConcurrent",
]

# 运行时绝不碰的东西
NEVER = [
    "tkinter",                  # 界面是 PySide6；旧图形工具链，运行时不加载
    "unittest", "pydoc", "doctest", "pdb", "pytest",
]

# 托盘通知用的 winrt 子模块（win11toast 的 import 面）
WINRT_IMPORTS = [
    "winrt.windows.data.xml.dom", "winrt.windows.foundation",
    "winrt.windows.globalization", "winrt.windows.graphics.imaging",
    "winrt.windows.media.core", "winrt.windows.media.ocr",
    "winrt.windows.media.playback", "winrt.windows.media.speechsynthesis",
    "winrt.windows.storage", "winrt.windows.storage.streams",
    "winrt.windows.ui.notifications",
]

# 本机装了 14.5(VS 2026) 与 14.3(VS 2022 BuildTools)；14.5 下 Nuitka 找不到 SDK
MSVC_VERSION = os.environ.get("ECHO_MSVC", "14.3")


def _du(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


# ---------------------------------------------------------------- PyInstaller

def write_version_file() -> str:
    """生成 PyInstaller 的版本资源文件（中文友好：0804/1200）。"""
    os.makedirs(BUILD_DIR, exist_ok=True)
    path = os.path.join(BUILD_DIR, "version_info.txt")
    def ss(k, v):
        return f"            StringStruct('{k}', '{v}')"
    lines = [
        "VSVersionInfo(",
        "  ffi=FixedFileInfo(",
        f"    filevers=({VERSION.replace('.', ', ')}, 0),",
        f"    prodvers=({VERSION.replace('.', ', ')}, 0),",
        "    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)",
        "    ),",
        "  kids=[",
        "    StringFileInfo([",
        "      StringTable('080404B0', [",
        ss("CompanyName", COMPANY) + ",",
        ss("FileDescription", DESC) + ",",
        ss("FileVersion", f"{VERSION}.0") + ",",
        ss("ProductName", PRODUCT) + ",",
        ss("ProductVersion", f"{VERSION}.0") + ",",
        ss("OriginalFilename", RELEASE_NAME) + ",",
        "      ])",
        "    ]),",
        "    VarFileInfo([VarStruct('Translation', [0x0804, 1200])])",
        "  ]",
        ")",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def pyinstaller_args(console: bool, jobs: int, name: str, entry: str) -> list[str]:
    args = [
        VENV_PY, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--noupx",
        "--windowed" if not console else "--console",
        f"--icon={ICON}",
        f"--name={name}",
        f"--distpath={OUT_DIR}",
        f"--workpath={os.path.join(BUILD_DIR, 'pyinstaller')}",
        f"--specpath={BUILD_DIR}",
        f"--version-file={write_version_file()}",
        "--paths", PROJECT,
        # 托盘通知：win11toast 用到的 winrt 子模块（无专用 hook，显式声明）
        "--hidden-import=win11toast",
        "--hidden-import=pystray._win32",
        "--hidden-import=six",
    ]
    for m in WINRT_IMPORTS:
        args.append(f"--hidden-import={m}")
    for m in NEVER:
        args.append(f"--exclude-module={m}")
    for m in QT_UNUSED:
        args.append(f"--exclude-module={m}")
    args.append(entry)
    return args


# --------------------------------------------------------------------- Nuitka

def nuitka_args(console: bool, jobs: int) -> list[str]:
    args = [
        VENV_PY, "-m", "nuitka",
        "--onefile",
        "--enable-plugins=pyside6",
        f"--msvc={MSVC_VERSION}",
        "--assume-yes-for-downloads",
        f"--windows-console-mode={'force' if console else 'disable'}",
        f"--windows-icon-from-ico={ICON}",
        f"--company-name={COMPANY}",
        f"--product-name={PRODUCT}",
        f"--file-description={DESC}",
        f"--file-version={VERSION}.0",
        f"--product-version={VERSION}.0",
        f"--output-dir={OUT_DIR}",
        f"--output-filename={BUILD_NAME}.exe",
        "--include-qt-plugins=sensible",
        "--noinclude-qt-translations",
        "--no-pyi-file",
        f"--jobs={jobs}",
        f"--report={os.path.join(OUT_DIR, 'build-report.xml')}",
        "--follow-imports",
    ]
    for m in NEVER:
        args.append(f"--nofollow-import-to={m}")
    for m in QT_UNUSED:
        args.append(f"--nofollow-import-to={m}")
    # 通配覆盖整族（PyInstaller 不支持通配，Nuitka 支持）
    for m in ("PySide6.QtWebEngine*", "PySide6.Qt3D*", "PySide6.QtQuick*",
              "PySide6.QtQml*", "PySide6.QtMultimedia*", "PySide6.QtDesigner*"):
        args.append(f"--nofollow-import-to={m}")
    args.append(MAIN_PY)
    return args


# ----------------------------------------------------------------------- 主流程

def copy_resources() -> int:
    dst = os.path.join(OUT_DIR, "resources")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(SRC_RES, dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return _du(dst)


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 ComfyUI Echo 启动器发布版")
    ap.add_argument("--tool", choices=("pyinstaller", "nuitka"), default="pyinstaller",
                    help="打包工具（默认 pyinstaller；nuitka 需先解除 reg.exe 限制）")
    ap.add_argument("--console", action="store_true", help="保留控制台（诊断用）")
    ap.add_argument("--jobs", type=int, default=min(8, (os.cpu_count() or 4)),
                    help="并行编译任务数（仅 Nuitka 用）")
    ap.add_argument("--no-resources", action="store_true", help="跳过 resources 外置")
    ap.add_argument("--diag", action="store_true",
                    help="诊断版：强制控制台、产物叫 _diag.exe、不改名不复制资源")
    ap.add_argument("--entry", default=MAIN_PY, help="入口脚本（默认 main.py）")
    ap.add_argument("--tag", default=None,
                    help="诊断产物名（不含 .exe）；给定时即诊断模式（控制台/不改名/不复制资源）")
    opts = ap.parse_args()

    entry = os.path.abspath(opts.entry)
    diag = bool(opts.tag) or opts.diag
    build_name = opts.tag or ("_diag" if opts.diag else BUILD_NAME)
    console = opts.console or diag

    if not os.path.isfile(VENV_PY):
        print(f"[x] 找不到 venv 解释器：{VENV_PY}", file=sys.stderr)
        return 2
    if not os.path.isfile(entry):
        print(f"[x] 找不到入口：{entry}", file=sys.stderr)
        return 2
    if not os.path.isfile(ICON):
        print(f"[!] 找不到图标（继续，仅不带图标）：{ICON}")

    os.makedirs(OUT_DIR, exist_ok=True)

    for name in ((f"{BUILD_NAME}.exe", RELEASE_NAME) if not diag else ()):
        p = os.path.join(OUT_DIR, name)
        if os.path.exists(p):
            os.remove(p)

    if opts.tool == "pyinstaller":
        args = pyinstaller_args(console, opts.jobs, build_name, entry)
    else:
        args = nuitka_args(console, opts.jobs)
    print("=" * 72)
    print("工具 :", opts.tool)
    print("入口 :", entry)
    print("模式 :", "onefile + " + ("console" if console else "no-console")
          + ("  [诊断]" if diag else ""))
    print("输出 :", OUT_DIR)
    print("=" * 72)

    t0 = time.time()
    proc = subprocess.run(args, cwd=PROJECT)
    if proc.returncode != 0:
        print(f"\n[x] 构建失败，{opts.tool} 退出码 {proc.returncode}", file=sys.stderr)
        return proc.returncode
    print(f"\n[√] 构建完成，用时 {time.time() - t0:.0f}s")

    built = os.path.join(OUT_DIR, f"{build_name}.exe")
    if not os.path.isfile(built):
        print(f"[x] 没找到产物：{built}", file=sys.stderr)
        return 3

    if diag:
        print(f"\n[√] 诊断产物：{built}")
        return 0

    final = os.path.join(OUT_DIR, RELEASE_NAME)
    os.replace(built, final)

    res_bytes = 0
    if not opts.no_resources:
        print("[*] 外置 resources/ …")
        res_bytes = copy_resources()

    exe_bytes = _du(final)
    print("\n" + "=" * 72)
    print("发布产物")
    print("-" * 72)
    print(f"  {os.path.basename(final):<34}{_mb(exe_bytes)}")
    if res_bytes:
        print(f"  {'resources/':<34}{_mb(res_bytes)}")
    print(f"  {'合计':<34}{_mb(exe_bytes + res_bytes)}")
    print("-" * 72)
    print("分发：把 dist/ 整个拷给用户；exe 必须与 resources/ 同级。")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
