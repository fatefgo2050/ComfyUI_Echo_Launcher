"""ComfyUI Echo 启动器。

无任何私有服务端依赖：插件市场直连 GitHub / ComfyUI-Manager，
模型相关走 HuggingFace / Civitai 公开接口。

路径解析（冻结 vs 源码）说明
----------------------------
Nuitka onefile 会先把程序解压到**临时目录**再运行；实测（Nuitka 4.2.2）：
  - `sys.argv[0]`          → 用户双击的原始 exe（持久化数据放这里旁边）
  - `__compiled__.containing_dir` → 原始 exe 所在目录（同上，用于持久化）
  - `sys.executable`       → 临时解压目录里的 payload 副本（不能用于持久化）
  - `__file__`             → 临时解压目录（打包资源在这里）
  - `NUITKA_ONEFILE_BINARY`→ 本版 Nuitka 未设置（不可依赖）

因此：
  - 持久化数据（launcher_config.json / data/ / logs/）用 sys.argv[0] 定位 → 原始 exe 旁
  - 资源目录（resources/，含便携 git）**优先**用原始 exe 旁的 resources/（外置分发），
    没有才回退 __file__ 定位的包内临时解压目录
源码模式下上述全部回退到项目根，行为一致。
"""
from __future__ import annotations

import os
import sys


def _is_frozen() -> bool:
    """Nuitka 冻结检测。

    Nuitka 4.2.2 实测：onefile/standalone 下 sys.frozen 未必被设为 True，
    但 __compiled__ 一定存在（内置全局名字）。因此同时检查两者。
    """
    if getattr(sys, "frozen", False):
        return True
    try:
        __compiled__  # noqa: F821 — Nuitka 内置全局名字
        return True
    except NameError:
        return False


def _payload_dir() -> str:
    """程序实际运行/解压到的目录（resources/、av/ 等都在这里）。

    onefile：临时解压目录。__file__ 在 onefile 下指向临时路径，
    因此 dirname(dirname(__file__)) 即为临时 payload 根目录。
    standalone：dist 目录（__file__ 同样指向正确位置）。
    源码：echo_launcher 包的上一级（项目根）。

    注意：__compiled__.containing_dir 在 onefile 下指向**原始 exe 目录**（用户双击的位置），
    不是临时解压目录。资源文件被解压到临时目录，所以不能用 containing_dir。
    containing_dir 适合定位持久化数据（config/data），不适合定位打包资源。
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_base_dir() -> str:
    """持久化数据根目录（launcher_config.json / data/ / logs/）。"""
    if _is_frozen():
        import ctypes as _ct
        _buf = _ct.create_unicode_buffer(260)
        _ct.windll.kernel32.GetModuleFileNameW(None, _buf, 260)
        _exe = _buf.value or sys.argv[0]
        return os.path.dirname(os.path.abspath(_exe))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_resource_dir() -> str:
    """打包资源目录（resources/，含便携 git）。

    **优先用程序目录旁的 resources/**（外置分发），找不到才回退到包内 payload。

    为什么外置：便携 git 有 68 MB / 328 个文件，其中 155 个是 .exe/.dll。
    Nuitka 的 `--include-data-dir` **不会**把 .exe/.dll 当数据文件收进去
    （实测：非二进制 271/271 全进，二进制 0/155 全丢，cmd/ 整个目录消失），
    所以 git 根本进不了 onefile 产物。外置后：
      1. git 的二进制文件原样送达，冻结态不再回退到系统 PATH 上的 git；
      2. exe 体积少一大截；
      3. 启动时不用把 68 MB git 解压进 temp；
      4. 以后换 git 版本只需替换 resources/git，不必重新打包。

    注意：onefile 下 `_payload_dir()` 指向临时解压目录，所以包内回退路径必须
    用它，不能用 BASE_DIR / sys.argv[0] / __compiled__.containing_dir。
    """
    external = os.path.join(BASE_DIR, "resources")
    if os.path.isdir(external):
        return external
    return os.path.join(_payload_dir(), "resources")


# 程序根目录（持久化数据都放这里）
BASE_DIR = _resolve_base_dir()
DATA_DIR = os.path.join(BASE_DIR, "data")
RESOURCE_DIR = _resolve_resource_dir()

# 冻结模式下，确保 PyAV 的 ffmpeg 动态库可被找到：
# PyAV 在 import 时执行 os.add_dll_directory('<av 包>/../av.libs')。
# 正常情况下 Windows 加载器会从 _core.pyd 同目录（<payload>/av/）直接找到 ffmpeg 的 .dll；
# 若 Nuitka 把它们放到了 av.libs，则无需处理。
# 这里做一个兜底：若 av.libs 不存在、而 ffmpeg 的 .dll 在 av/ 目录里，则整目录复制到 av.libs，
# 保证 PyAV 的 add_dll_directory 总能命中，视频编解码（含 swscale/swresample/postproc）功能可用。
# 注意：ffmpeg 的动态库名前缀有 av* / sw* / postproc*，不能只按 "av" 前缀筛选，
# 否则会漏掉 swscale / swresample / postproc，导致视频缩放、重采样在冻结态崩溃。
if _is_frozen():
    _pd = _payload_dir()
    _av_pkg = os.path.join(_pd, "av")
    if os.path.isdir(_av_pkg):
        _libs = os.path.join(_pd, "av.libs")
        if not os.path.isdir(_libs):
            import shutil as _shutil
            _found = []
            for _d in (_av_pkg, os.path.dirname(_av_pkg)):
                if os.path.isdir(_d):
                    for _f in os.listdir(_d):
                        # 只搬 ffmpeg / PyAV 相关的 .dll（av* / sw* / postproc* 前缀），
                        # 避免把随机无关 dll 加进 PyAV 的 DLL 搜索路径。
                        _lf = _f.lower()
                        if _lf.endswith(".dll") and (
                            _lf.startswith("av")
                            or _lf.startswith("sw")
                            or _lf.startswith("postproc")
                        ):
                            _found.append(os.path.join(_d, _f))
            if _found:
                os.makedirs(_libs, exist_ok=True)
                for _s in _found:
                    try:
                        _shutil.copy(_s, _libs)
                    except OSError:
                        pass

__version__ = "0.1.1"
