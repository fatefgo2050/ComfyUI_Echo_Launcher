"""插件适配：扫描 / 更新 / 安装 / 启停 / 模式应用。

启停与模式**沿用 Echo 的机制**：把目录重命名为 `<插件名>.disabled`（就地改名），
ComfyUI 原生就会跳过它 —— 不需要把插件搬到 `custom_nodes_disabled/`，
也不需要 ComfyUI-Manager 配合。Git 操作全部走 Echo 的便携 Git（`plugin.gitops`），
没装系统 Git 的机器也能 clone / fetch / 切版本。

对 Echo 的两处修正（已在 README 记录）：
1. Echo 的 `mode.apply_mode()` 处理**单文件插件**时算错目标名
   （`foo.disabled.py` 启用后会变成 `foo.disabled`，丢掉 `.py`），这里在
   `set_enabled()` 里按 `foo.py` / `foo.disabled.py` 正确换算。
2. Echo 的 `gitops` 没有「单次调用带代理」的参数，这里通过 `_bridge.git_env()`
   临时注入环境变量实现，用完还原。
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from echo_launcher.core import mode as _mode
from echo_launcher.plugin import gitops
from echo_launcher.plugin.market import repo_dir_name

from . import _bridge as B
from . import paths as _paths

SKIP_NAMES = {"__pycache__", ".git", "node_modules"}


class PluginInfo:
    """插件条目（字段与启动器 UI 的表格列一一对应）。"""

    def __init__(self, name, path, enabled):
        self.name = name
        self.path = path
        self.enabled = enabled
        self.is_git = False
        self.branch = ""
        self.commit = ""
        self.url = ""
        self.update_count = None   # None = 未检查
        self.error = ""

    def as_row(self):
        state = "启用" if self.enabled else "禁用"
        if self.error:
            state += f"({self.error})"
        upd = "-" if self.update_count is None else str(self.update_count)
        return [self.name, state, self.branch, self.commit[:8], upd,
                self.url or ("本地目录" if not self.is_git else "")]


# --------------------------------------------------------------------------
# 枚举 / 扫描
# --------------------------------------------------------------------------
def _custom_nodes(root: str) -> str:
    return _paths.dirs(root)["custom_nodes"]


def list_plugin_dirs(root: str, include_disabled: bool = True) -> list:
    """[(插件名, 目录全路径, 是否启用)]"""
    cn = _custom_nodes(root)
    out = []
    for p in _mode.scan_plugins(cn).plugins:
        if not include_disabled and not p.enabled:
            continue
        out.append((p.name, os.path.join(cn, p.dirname), p.enabled))
    return out


def _git_info(info: PluginInfo):
    """读取单个插件的 Git 信息（在子线程里跑）。"""
    rc, out, _err = gitops.run(["rev-parse", "--abbrev-ref", "HEAD"],
                               cwd=info.path, timeout=60)
    info.branch = out if rc == 0 else ""
    info.commit = gitops.current_commit(info.path)
    info.url = gitops.remote_url(info.path)
    if not info.branch:
        info.error = "git读取失败"
    return info


def scan(root: str, proxy_url: str = "") -> list:
    """扫描 custom_nodes，读取每个插件的 Git 信息。

    每个 Git 插件要跑 3 次 `git` 子进程（分支 / commit / 远程地址）。
    便携 Git 每次启动约 300 ms，45 个插件串行实测 44 秒 —— 太慢，
    所以按 Echo 检查更新时的做法用线程池并发（8 并发）。
    """
    cn = _custom_nodes(root)
    entries = _mode.scan_plugins(cn).plugins
    result = []
    with B.git_env(proxy_url):
        targets = []
        for p in entries:
            info = PluginInfo(p.name, os.path.join(cn, p.dirname), p.enabled)
            info.is_git = p.is_git
            result.append(info)
            if p.is_git:
                targets.append(info)
        if targets:
            workers = min(8, len(targets))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(_git_info, targets))
    return result


def capture_enabled(root: str) -> list:
    """当前启用中的插件名（用于「捕获当前启用为新模式」）。"""
    return [p.name for p in _mode.scan_plugins(_custom_nodes(root)).plugins if p.enabled]


# --------------------------------------------------------------------------
# 更新检查 / 更新
# --------------------------------------------------------------------------
def _upstream_count(path: str) -> int:
    """本地落后远端多少个提交。"""
    for ref in ("@{u}", "origin/HEAD", "origin/master", "origin/main"):
        rc, out, _err = gitops.run(["rev-list", "--count", f"HEAD..{ref}"], cwd=path)
        if rc == 0 and (out or "").strip().isdigit():
            return int(out.strip())
    return 0


def check_update(info: PluginInfo, proxy_url: str = "") -> PluginInfo:
    if not info.is_git:
        return info
    with B.git_env(proxy_url):
        ok, msg = gitops.fetch(info.path, depth=1)
        if not ok:
            info.update_count = None
            info.error = "fetch失败"
            return info
        info.update_count = _upstream_count(info.path)
        info.error = ""
    return info


def update(info: PluginInfo, proxy_url: str = ""):
    """更新到远端最新（Echo 的做法：fetch + reset --hard）。返回 (ok, 消息)。"""
    if not info.is_git:
        return False, "非 git 仓库插件"
    with B.git_env(proxy_url):
        ok, msg = gitops.fetch(info.path, depth=1)
        if not ok:
            return False, (msg or "fetch 失败").splitlines()[-1]
        remote = gitops.latest_remote_commit(info.path)
        if not remote:
            return False, "无法确定远端版本"
        ok, msg = gitops.reset_hard(info.path, remote)
        if ok:
            info.commit = remote
            info.update_count = 0
            return True, f"已更新到 {remote[:8]}"
        return False, (msg or "切换失败").splitlines()[-1]


def install(url: str, root: str, proxy_url: str = ""):
    """git clone 到 custom_nodes。返回 (ok, 消息, 目录名)。"""
    url = (url or "").strip().rstrip("/")
    name = repo_dir_name(url)
    if not name:
        return False, "URL 无效", ""
    cn = _custom_nodes(root)
    dst = os.path.join(cn, name)
    if os.path.exists(dst) or os.path.exists(dst + _mode.DISABLED_SUFFIX):
        return False, f"已存在同名目录: {name}", name
    os.makedirs(cn, exist_ok=True)
    with B.git_env(proxy_url):
        ok, msg = gitops.clone(url, dst, depth=1)
    if not ok:
        return False, (msg or "克隆失败").strip(), name
    return True, "克隆完成", name


def _rm_readonly(func, path, _exc):
    """Windows 下 `.git` 里的只读文件会让 rmtree 半途失败 —— 去掉只读位再试一次。"""
    import stat
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def uninstall(root: str, name: str):
    """卸载插件：删掉它的整个目录（启用中的和 `.disabled` 形态都认）。返回 (ok, 消息)。

    ⚠️ 这是**破坏性操作**，调用方必须先建救援还原点 + 让用户确认；
    这里只负责删，不做任何二次确认。
    """
    cn = _custom_nodes(root)
    if not os.path.isdir(cn):
        return False, f"custom_nodes 不存在: {cn}"
    entry = _find_entry(cn, name)
    if entry is None:
        return False, f"找不到插件: {name}"
    target = os.path.join(cn, entry.dirname)
    if not os.path.isdir(target):
        return False, f"插件目录不存在: {target}"
    import shutil
    try:
        shutil.rmtree(target, onexc=_rm_readonly)
    except Exception as e:
        return False, f"删除失败: {e}"
    if os.path.exists(target):
        return False, f"删除不完整，仍有残留: {target}"
    return True, f"已卸载 {name}"


# --------------------------------------------------------------------------
# 启用 / 禁用（.disabled 就地改名）
# --------------------------------------------------------------------------
def _find_entry(cn: str, name: str):
    for p in _mode.scan_plugins(cn).plugins:
        if p.name.lower() == (name or "").lower():
            return p
    return None


def set_enabled(root: str, name: str, enable: bool):
    """启用/禁用单个插件（重命名为 <名>.disabled）。返回 (ok, 消息)。"""
    cn = _custom_nodes(root)
    if not os.path.isdir(cn):
        return False, f"custom_nodes 不存在: {cn}"
    entry = _find_entry(cn, name)
    if entry is None:
        return False, f"找不到插件: {name}"
    if entry.enabled == bool(enable):
        return True, ("已是启用状态 " if enable else "已是禁用状态 ") + name

    src = os.path.join(cn, entry.dirname)
    suffix = _mode.DISABLED_SUFFIX
    if entry.dirname.endswith(".py"):
        # 单文件插件：foo.py  <->  foo.disabled.py（修正 Echo 的换算）
        dst_name = f"{entry.name}.py" if enable else f"{entry.name}{suffix}.py"
    else:
        dst_name = entry.name if enable else entry.name + suffix
    dst = os.path.join(cn, dst_name)
    if os.path.exists(dst):
        return False, f"目标已存在: {dst_name}"
    try:
        os.rename(src, dst)
    except OSError as e:
        return False, f"重命名失败 {entry.dirname}: {e}"
    B.log().info(f"{'启用' if enable else '禁用'}插件: {entry.name}")
    return True, ("已启用 " if enable else "已禁用 ") + entry.name


def apply_mode(root: str, whitelist: list):
    """把插件集合调整为白名单。返回 (移动数, 错误列表)。

    空列表 = **不做任何改动**（沿用启动器 UI 的「默认模式」语义：
    空白名单代表「不干预」，而不是「全部禁用」）。
    """
    wl = [w for w in (whitelist or []) if w]
    if not wl:
        return 0, []
    cn = _custom_nodes(root)
    if not os.path.isdir(cn):
        return 0, [f"custom_nodes 不存在: {cn}"]
    enabled, disabled, errors = _mode.apply_mode(cn, wl)
    return enabled + disabled, errors


def enable_all(root: str) -> list:
    """恢复全部插件为启用（Echo 的「退出模式」）。返回错误列表。"""
    return _mode.enable_all(_custom_nodes(root))


def find_duplicates(root: str) -> list:
    """重复插件检测（Echo 的顶层文件签名法）。返回 [(签名, [目录名...])]。"""
    return _mode.find_duplicate_plugins(_custom_nodes(root))
