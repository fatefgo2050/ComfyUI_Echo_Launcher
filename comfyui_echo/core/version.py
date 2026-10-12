"""Git **插件**版本操作适配（Echo 的 plugin/gitops.py）。

⚠️ 当前 UI 已不再引用本模块。
2026-10-09 起「版本管理」对话框改为只管 ComfyUI 内核（见 `core/core_version.py`），
插件侧的更新走 `core/plugins.py`（`plugins_page.py` 的「更新」按钮），
所以这里的批量更新 / 切分支 / 切 commit 暂时没有入口。
保留它是因为这些函数本身是好的（含自动建还原点、浅克隆 unshallow），
将来若要做「插件批量回滚到指定 commit」可直接接着用，别当垃圾删掉。

- 列出 Git 插件的分支 / 当前 commit
- 批量检查更新（8 并发，沿用 Echo 的做法）
- 更新 = fetch + reset --hard（Echo 的做法），更新前自动建救援还原点
- 切 commit / 切分支：浅克隆仓库先 `fetch --unshallow` 才能拿到历史
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from echo_launcher.core import mode as _mode
from echo_launcher.plugin import gitops

from . import _bridge as B
from . import paths as _paths

__all__ = [
    "list_git_plugins", "check_updates", "update_many", "commits",
    "branches", "switch_commit", "switch_branch", "unshallow", "has_update",
]


def _dir_of(root: str, name: str) -> str:
    """按逻辑名找磁盘目录（可能在 .disabled 状态）。"""
    cn = _paths.dirs(root)["custom_nodes"]
    for p in _mode.scan_plugins(cn).plugins:
        if p.name.lower() == (name or "").lower():
            return os.path.join(cn, p.dirname)
    return ""


def list_git_plugins(root: str) -> list:
    """[(逻辑名, 目录全路径, 分支, 短 commit)]"""
    cn = _paths.dirs(root)["custom_nodes"]
    out = []
    for p in _mode.scan_plugins(cn).plugins:
        if not p.is_git:
            continue
        full = os.path.join(cn, p.dirname)
        out.append((p.name, full, _branch(full), gitops.current_commit(full)[:8]))
    return out


def _branch(path: str) -> str:
    rc, out, _err = gitops.run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path, timeout=60)
    return out if rc == 0 else "-"


def has_update(path: str):
    """返回 (是否有更新, 本地 commit, 远端 commit)。"""
    return gitops.has_update(path)


def check_updates(root: str, names=None, report=None) -> dict:
    """并发检查更新。返回 {插件名: "有更新" / "最新" / "失败:..."}"""
    items = list_git_plugins(root)
    if names:
        wanted = {n.lower() for n in names}
        items = [i for i in items if i[0].lower() in wanted]
    results = {}

    def one(t):
        name, full = t[0], t[1]
        try:
            has, _local, remote = gitops.has_update(full)
            return name, ("有更新" if has else "最新") if remote else "检查失败"
        except Exception as e:
            return name, f"失败:{e}"

    if not items:
        return results
    with ThreadPoolExecutor(max_workers=min(8, len(items))) as ex:
        for i, (name, st) in enumerate(ex.map(one, items), 1):
            results[name] = st
            if report:
                report(i, len(items), name)
    return results


def update_many(root: str, names, report=None, make_point: bool = True) -> dict:
    """更新若干插件。返回 {"ok":n, "fail":n, "point":还原点ID}"""
    from . import rescue as _rescue

    dirs = [d for d in (_dir_of(root, n) for n in names) if d]
    point_id = ""
    if make_point and dirs:
        try:
            p = _rescue.create(root, list(names), note=f"更新 {len(names)} 个插件前")
            point_id = p.id if p else ""
        except Exception as e:
            B.log().warn(f"建还原点失败（继续更新）：{e}")

    ok = fail = 0
    for i, d in enumerate(dirs, 1):
        name = os.path.basename(d)
        if report:
            report(i, len(dirs), name)
        good, msg = gitops.fetch(d, depth=1)
        if not good:
            B.log().error(f"{name} fetch 失败：{msg}")
            fail += 1
            continue
        remote = gitops.latest_remote_commit(d)
        if not remote:
            B.log().warn(f"{name} 无法确定远端版本")
            fail += 1
            continue
        good, msg = gitops.reset_hard(d, remote)
        if good:
            ok += 1
            B.log().info(f"{name} 已更新到 {remote[:8]}")
        else:
            fail += 1
            B.log().error(f"{name} 切换失败：{msg}")
    return {"ok": ok, "fail": fail, "point": point_id}


def commits(root: str, name: str, limit: int = 40, allow_unshallow: bool = False):
    """历史提交列表。浅克隆会自动 unshallow（需要网络）。"""
    d = _dir_of(root, name)
    if not d:
        return []
    rows = gitops.log(d, limit=limit)
    if not rows and allow_unshallow:
        B.log().info(f"{name} 是浅克隆，正在拉取完整历史…")
        gitops.unshallow(d)
        rows = gitops.log(d, limit=limit)
    return rows


def branches(root: str, name: str):
    d = _dir_of(root, name)
    return gitops.branches(d) if d else []


def unshallow(root: str, name: str):
    d = _dir_of(root, name)
    return gitops.unshallow(d) if d else (False, "找不到插件目录")


def switch_commit(root: str, name: str, commit: str):
    d = _dir_of(root, name)
    return gitops.reset_hard(d, commit) if d else (False, "找不到插件目录")


def switch_branch(root: str, name: str, branch: str):
    d = _dir_of(root, name)
    return gitops.switch_branch(d, branch) if d else (False, "找不到插件目录")
