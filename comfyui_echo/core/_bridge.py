"""适配层内部工具：Echo 功能层的单例入口 + 通用依赖。

只给 `comfyui_echo/core/*` 内部使用，UI 不应直接 import 本模块。
"""
from __future__ import annotations

import os
import sys
import threading
from contextlib import contextmanager

# 保证项目根在 sys.path 上（直接跑 main.py 时已在；被当包导入时兜底）
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from echo_launcher import BASE_DIR, DATA_DIR, RESOURCE_DIR  # noqa: E402
from echo_launcher.core.config import get_config  # noqa: E402
from echo_launcher.core.log import get_log  # noqa: E402
from echo_launcher.core.rescue import RescueManager  # noqa: E402
from echo_launcher.net.http_client import HttpClient  # noqa: E402
from echo_launcher.plugin.market import Market  # noqa: E402
from echo_launcher.plugin.node_map import NodeMap  # noqa: E402

__all__ = [
    "BASE_DIR", "DATA_DIR", "RESOURCE_DIR",
    "config", "log", "node_map", "market", "rescue", "http_client", "git_env",
]

_lock = threading.RLock()
_singletons: dict = {}


def _once(key: str, factory):
    with _lock:
        if key not in _singletons:
            _singletons[key] = factory()
        return _singletons[key]


def config():
    """Echo 的全局配置单例（launcher_config.json）。"""
    return _once("config", lambda: get_config(BASE_DIR))


def log():
    """Echo 的日志总线单例（文件 + 队列）。"""
    return _once("log", lambda: get_log(BASE_DIR))


def node_map():
    """节点映射管理器（官方表 + 本地缓存）。"""
    return _once("node_map", lambda: NodeMap(DATA_DIR))


def market():
    """插件市场目录（ComfyUI-Manager 公开数据）。"""
    return _once("market", lambda: Market(DATA_DIR))


def rescue():
    """救援还原点管理器。"""
    return _once("rescue", lambda: RescueManager(DATA_DIR))


def http_client(proxy: str = "", token: str = "") -> HttpClient:
    """按当前配置构造 HTTP 客户端（含私有域名拦截）。"""
    c = config()
    return HttpClient(
        proxy=proxy or (c.get("proxy_address") if c.get("enable_proxy") else ""),
        verify_ssl=not c.get("disable_ssl_verify"),
        github_token=token or (c.get("github_token") or ""),
    )


# --------------------------------------------------------------------------
# 便携 Git 的代理注入
# --------------------------------------------------------------------------
# Echo 的 plugin.gitops.run() 固定用 os.environ.copy() 作为子进程环境，
# 没有「单次调用带代理」的参数。启动器 UI 有「启动器 git/pip 走代理」开关，
# 这里在调用前后临时改环境变量，用完立即还原；加锁避免并发串味。
_GIT_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
_git_env_lock = threading.RLock()


@contextmanager
def git_env(proxy_url: str = ""):
    """在 with 块内让 Echo 的 git 子进程带上代理。proxy_url 为空则原样透传。"""
    if not proxy_url:
        yield
        return
    with _git_env_lock:
        saved = {k: os.environ.get(k) for k in _GIT_ENV_KEYS}
        try:
            for k in _GIT_ENV_KEYS:
                os.environ[k] = proxy_url
            yield
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
