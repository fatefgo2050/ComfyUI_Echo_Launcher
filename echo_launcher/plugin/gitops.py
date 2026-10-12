"""便携 Git 封装（不依赖系统 Git）。"""
from __future__ import annotations

import os
import subprocess
from typing import List, Optional, Tuple

from ..net.mirrors import portable_git

NO_WINDOW = 0x08000000
TIMEOUT = 300


def run(args: List[str], cwd: Optional[str] = None,
        timeout: int = TIMEOUT) -> Tuple[int, str, str]:
    git = portable_git()
    env = os.environ.copy()
    # 便携 Git 需要知道自己的根目录，否则找不到 etc/gitconfig
    if "resources" in git.replace("\\", "/"):
        root = os.path.dirname(os.path.dirname(os.path.dirname(git)))
        env["GIT_EXEC_PATH"] = os.path.join(root, "mingw64", "libexec", "git-core")
        env["GIT_SSL_NO_VERIFY"] = "0"
    try:
        p = subprocess.run([git] + args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace",
                           creationflags=NO_WINDOW, env=env)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", f"找不到 git: {git}"
    except subprocess.TimeoutExpired:
        return 124, "", "git 超时"


def is_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


def clone(url: str, dest: str, depth: int = 1) -> Tuple[bool, str]:
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    args = ["clone"]
    if depth and depth > 0:
        args += ["--depth", str(depth)]
    args += [url, dest]
    rc, out, err = run(args, timeout=900)
    return rc == 0, err or out


def current_commit(path: str) -> str:
    rc, out, _ = run(["rev-parse", "HEAD"], cwd=path)
    return out if rc == 0 else ""


def remote_url(path: str) -> str:
    rc, out, _ = run(["config", "--get", "remote.origin.url"], cwd=path)
    return out if rc == 0 else ""


def fetch(path: str, depth: int = 1) -> Tuple[bool, str]:
    rc, out, err = run(["fetch", "--depth", str(depth), "origin"], cwd=path, timeout=600)
    return rc == 0, err or out


def latest_remote_commit(path: str, branch: str = "") -> str:
    ref = f"origin/{branch}" if branch else "@{u}"
    rc, out, _ = run(["rev-parse", ref], cwd=path)
    if rc != 0 and not branch:
        rc, out, _ = run(["rev-parse", "origin/HEAD"], cwd=path)
    return out if rc == 0 else ""


def has_update(path: str) -> Tuple[bool, str, str]:
    """返回 (是否有更新, 本地 commit, 远端 commit)。"""
    local = current_commit(path)
    if not fetch(path)[0]:
        return False, local, ""
    remote = latest_remote_commit(path)
    return bool(remote and remote != local), local, remote


def log(path: str, limit: int = 30) -> List[Tuple[str, str, str]]:
    """返回 [(短 sha, 相对时间, 说明)]。"""
    rc, out, _ = run(["log", f"-{limit}", "--pretty=format:%h|%ar|%s"], cwd=path)
    if rc != 0:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def reset_hard(path: str, commit: str) -> Tuple[bool, str]:
    rc, out, err = run(["reset", "--hard", commit], cwd=path)
    return rc == 0, err or out


def unshallow(path: str) -> Tuple[bool, str]:
    """--depth 1 克隆的仓库拿不到历史，切版本前需要 unshallow。"""
    rc, out, err = run(["fetch", "--unshallow"], cwd=path, timeout=900)
    return rc == 0, err or out


def switch_branch(path: str, branch: str) -> Tuple[bool, str]:
    rc, out, err = run(["checkout", branch], cwd=path)
    return rc == 0, err or out


def branches(path: str) -> List[str]:
    rc, out, _ = run(["branch", "-a", "--format=%(refname:short)"], cwd=path)
    return [l.strip() for l in out.splitlines() if l.strip()] if rc == 0 else []
