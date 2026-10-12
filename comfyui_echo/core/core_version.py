"""ComfyUI **内核**（本体仓库）版本适配。

只管一件事：把 `<root>/ComfyUI` 这个 git 仓库的版本看清楚、换干净。
**刻意不碰 custom_nodes** —— 插件更新/回滚在「插件」页，两边分开，互不牵扯。

语义对齐「版本管理 → ComfyUI 更新」：
    当前版本   v0.39.0-26-gd91ed5f5        HEAD: d91ed5f5 [2026-10-07 19:22]
    提交列表   提交 / 提交日期（当前版本那行加标记）
    更新日志   选中提交的完整说明 + 文件改动统计
    更新       checkout --force 到选中提交（游离 HEAD，不动 master）

两个实测结论（决定了这里怎么写）
--------------------------------
1. 目标 `ComfyUI/` 是**浅克隆**（有 `.git/shallow`，只能列 ~280 条提交）。
   `git fetch --prune --tags origin master` 一次 3.8 s 就能把 origin/master 从
   d91ed5f5 推到 926d828e 并且可列条目不变（仍 280 条）——够用了，
   **不需要 `--unshallow`**（那是几百 MB 的全量历史，纯粹浪费）。
2. 该仓库是**游离 HEAD**（不在任何分支上），所以切版本必须用
   `checkout --force`；用 `reset --hard` 会把 master 分支指针也拖走。
"""
from __future__ import annotations

import os
import re

from echo_launcher.plugin import gitops

from . import paths as _paths

__all__ = [
    "DATE_FMT", "repo_dir", "is_ready", "is_shallow", "describe", "current",
    "remote_url", "latest_tag", "fetch", "commits", "show", "update_to",
    "local_changes", "DEFAULT_REF", "github_mirror", "mirror_options",
]

DATE_FMT = "%Y-%m-%d %H:%M"
DEFAULT_REF = "origin/master"
FETCH_TIMEOUT = 1800          # 首次 fetch 可能要拉几百 MB，给足时间
_TAG_RX = re.compile(r"^v?\d+\.\d+")


# ---------- GitHub 镜像 ----------
def github_mirror() -> str:
    """当前选用的 GitHub 镜像名（空串 = 官方直连）。键存在 launcher_config.json。"""
    try:
        from . import _bridge as B
        return (B.config().get("github_mirror", "") or "").strip()
    except Exception:
        return ""


def mirror_options() -> list:
    """镜像下拉的候选项（含「官方（GitHub 直连）」）。"""
    from echo_launcher.net.mirrors import GITHUB_MIRRORS
    return list(GITHUB_MIRRORS.keys())


def _mirror_args() -> list:
    """把镜像设置翻译成 git 参数：`-c url.<镜像前缀>.insteadOf=https://github.com/`。

    这样**不动仓库的 origin**（仍是 github.com），`origin/master` 也照常更新 ——
    只是 git 真正去连的地址被透明换成了镜像。官方直连时返回空列表。
    """
    from echo_launcher.net.mirrors import GITHUB_MIRRORS
    prefix = GITHUB_MIRRORS.get(github_mirror(), "")
    if not prefix:
        return []
    return ["-c", f"url.{prefix}.insteadOf=https://github.com/"]


# ---------- 仓库定位 ----------
def repo_dir(root: str) -> str:
    """ComfyUI 本体仓库目录；不是 git 仓库时返回空串。"""
    d = _paths.comfyui_dir_of(root)
    return d if d and os.path.isdir(os.path.join(d, ".git")) else ""


def is_ready(root: str):
    """返回 (仓库目录, 错误信息)。两者必有一空。"""
    d = _paths.comfyui_dir_of(root)
    if not d or not os.path.isdir(d):
        return "", "找不到 ComfyUI 本体目录，请先在「启动」页选好包路径"
    if not os.path.isdir(os.path.join(d, ".git")):
        return "", ("这个 ComfyUI 不是 Git 仓库（多半是解压的便携版），"
                    "没有历史版本可切换")
    return d, ""


def is_shallow(root: str) -> bool:
    d = repo_dir(root)
    return bool(d) and os.path.isfile(os.path.join(d, ".git", "shallow"))


# ---------- 读当前状态 ----------
def _one(d: str, args: list, timeout: int = 60) -> str:
    rc, out, _ = gitops.run(args, cwd=d, timeout=timeout)
    return out if rc == 0 else ""


def describe(root: str) -> str:
    """`v0.39.0-26-gd91ed5f5` 这种描述串；没有标签时退化成短 sha。"""
    d = repo_dir(root)
    return _one(d, ["describe", "--tags", "--always"]) if d else ""


def current(root: str) -> dict:
    """当前 HEAD 的信息。空字典 = 拿不到（路径无效 / 仓库损坏）。"""
    d = repo_dir(root)
    if not d:
        return {}
    sha = _one(d, ["rev-parse", "HEAD"])
    if not sha:
        return {}
    # 用 %cd（提交时间）而不是 %ad（作者时间）：git log 是按提交时间排的，
    # 拿作者时间显示会出现「下面那行比上面还新」的乱序感（rebase / merge 很常见）。
    line = _one(d, ["log", "-1", "--pretty=format:%h|%cd|%s",
                    f"--date=format:{DATE_FMT}"])
    parts = (line or "").split("|", 2)
    branch = _one(d, ["rev-parse", "--abbrev-ref", "HEAD"])
    return {
        "sha": sha,
        "short": parts[0] if parts and parts[0] else sha[:8],
        "date": parts[1] if len(parts) > 1 else "",
        "subject": parts[2] if len(parts) > 2 else "",
        "branch": branch,
        # 游离 HEAD 时 rev-parse --abbrev-ref 返回字面量 "HEAD"
        "detached": branch == "HEAD",
        "describe": describe(root),
    }


def remote_url(root: str) -> str:
    d = repo_dir(root)
    return gitops.remote_url(d) if d else ""


def local_changes(root: str) -> list:
    """工作区相对 HEAD 的改动清单（`status --porcelain`）。

    本机实测有几条 `D input/example.png` 之类的占位文件删除记录——
    切版本会把这些占位文件还原回来，属于预期行为，但要如实告诉用户。
    """
    d = repo_dir(root)
    if not d:
        return []
    out = _one(d, ["status", "--porcelain"], timeout=120)
    return [ln for ln in (out or "").splitlines() if ln.strip()]


# ---------- 版本列表 ----------
def latest_tag(root: str) -> str:
    """最新的正式发布标签（`v0.39.2`）；仓库里没打标签则返回空串。"""
    d = repo_dir(root)
    if not d:
        return ""
    out = _one(d, ["tag", "--sort=-v:refname"], timeout=60)
    for t in (out or "").splitlines():
        t = t.strip()
        if _TAG_RX.match(t):
            return t
    return ""


def fetch(root: str, ref: str = "master"):
    """拉取远端最新提交与标签。返回 (ok, 输出)。

    浅克隆也能拿到新提交——实测 origin/master 从 d91ed5f5 前进到 926d828e，
    耗时 3.8 s，可列条目数量不变。所以这里**不做 unshallow**。

    另外按「GitHub 镜像」设置走 `url.<前缀>.insteadOf` 透明改写（见 `_mirror_args`），
    国内直连 GitHub 慢/不通时换成镜像即可，`origin` 本身不动。
    """
    d = repo_dir(root)
    if not d:
        return False, "找不到 Git 仓库"
    rc, out, err = gitops.run(
        _mirror_args() + ["fetch", "--prune", "--tags", "origin", ref],
        cwd=d, timeout=FETCH_TIMEOUT)
    return rc == 0, (err or out or "已是最新")


def commits(root: str, ref: str = "", limit: int = 120) -> list:
    """提交列表 [{short, date, subject}]，新的在前。"""
    d = repo_dir(root)
    if not d:
        return []
    target = (ref or "HEAD").strip()
    # 同上：%cd 保证「列表顺序 == 日期倒序」，看起来才不别扭
    out = _one(d, ["log", target, f"-{limit}", "--pretty=format:%h|%cd|%s",
                   f"--date=format:{DATE_FMT}"], timeout=180)
    rows = []
    for line in (out or "").splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            rows.append({"short": parts[0], "date": parts[1], "subject": parts[2]})
    return rows


def show(root: str, sha: str, stat_lines: int = 40) -> str:
    """某个提交的完整说明（更新日志面板用）：作者 + 时间 + 正文 + 文件改动统计。"""
    d = repo_dir(root)
    if not d or not sha:
        return ""
    body = _one(d, ["log", "-1", "--pretty=format:%H%n%cd  %an%n%n%s%n%n%b",
                    f"--date=format:{DATE_FMT}", sha], timeout=60)
    stat = _one(d, ["show", "--stat", "--no-renames", "--format=", sha], timeout=180)
    stat = "\n".join(ln for ln in (stat or "").splitlines()
                     if ln.strip() and not ln.startswith(" "))
    if stat:
        lines = stat.splitlines()
        if len(lines) > stat_lines:
            lines = lines[:stat_lines] + [f"…（共 {len(lines)} 个文件，此处只列前 {stat_lines} 个）"]
        body = f"{body}\n\n{'-' * 46}\n改动文件\n{'-' * 46}\n" + "\n".join(lines)
    return body


# ---------- 切版本 ----------
def update_to(root: str, sha: str):
    """把工作区切到指定提交（游离 HEAD，不动任何分支）。返回 (ok, 输出)。"""
    d = repo_dir(root)
    if not d:
        return False, "找不到 Git 仓库"
    if not (sha or "").strip():
        return False, "没有指定要切换到的版本"
    # --force：丢弃工作区改动。ComfyUI 内核文件被插件改过时，这一步会还原回来，
    # 属于预期行为（确认对话框里会先讲清楚）。
    rc, out, err = gitops.run(["checkout", "--force", sha.strip()], cwd=d, timeout=600)
    return rc == 0, (err or out)
