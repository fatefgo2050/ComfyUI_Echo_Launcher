"""pip 封装：列包 / 安装 / 卸载 / 快照 / 对比 / 还原。"""
from __future__ import annotations

import subprocess
from typing import Dict, List, Tuple

NO_WINDOW = 0x08000000


def _run(python: str, args: List[str], timeout: int = 1800,
         index: str = "") -> Tuple[int, str, str]:
    cmd = [python, "-m", "pip"] + args
    if index and "install" in args:
        cmd += ["-i", index]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", f"找不到解释器: {python}"
    except subprocess.TimeoutExpired:
        return 124, "", "pip 超时"


def freeze(python: str) -> Dict[str, str]:
    rc, out, err = _run(python, ["freeze"], timeout=300)
    pkgs = {}
    for line in out.splitlines():
        if "==" in line:
            k, v = line.split("==", 1)
            pkgs[k.strip().lower()] = v.strip()
        elif line.strip() and "@" not in line:
            pkgs[line.strip().lower()] = ""
    return pkgs


def list_packages(python: str) -> List[Tuple[str, str]]:
    return sorted(freeze(python).items())


def install(python: str, spec: str, index: str = "") -> Tuple[bool, str]:
    rc, out, err = _run(python, ["install", spec], index=index)
    return rc == 0, out or err


def uninstall(python: str, name: str) -> Tuple[bool, str]:
    rc, out, err = _run(python, ["uninstall", "-y", name])
    return rc == 0, out or err


def install_requirements(python: str, req_file: str, index: str = "") -> Tuple[bool, str]:
    rc, out, err = _run(python, ["install", "-r", req_file], index=index)
    return rc == 0, out or err


def diff(a: Dict[str, str], b: Dict[str, str]) -> Dict[str, List[Tuple[str, str, str]]]:
    """对比两份环境，返回 added / removed / changed。"""
    ka, kb = set(a), set(b)
    return {
        "added": [(k, "", b[k]) for k in sorted(kb - ka)],
        "removed": [(k, a[k], "") for k in sorted(ka - kb)],
        "changed": [(k, a[k], b[k]) for k in sorted(ka & kb) if a[k] != b[k]],
    }
