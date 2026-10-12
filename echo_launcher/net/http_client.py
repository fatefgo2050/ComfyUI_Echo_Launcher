"""统一 HTTP 入口：多源回退 + 超时 + 失败黑名单 + 私有域名拦截。

要点：
- 依次尝试多个镜像源，任一成功即返回
- 失败的源进入黑名单，10 分钟内不再尝试（避免每次都卡超时）
- 所有请求都会先过 BLOCKED_HOSTS 检查
"""
from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from .mirrors import BLOCKED_HOSTS

DEFAULT_TIMEOUT = 15
BLACKLIST_SECONDS = 600


class BlockedHostError(Exception):
    pass


class HttpClient:
    def __init__(self, proxy: str = "", timeout: int = DEFAULT_TIMEOUT,
                 verify_ssl: bool = True, github_token: str = ""):
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.github_token = github_token
        self._blacklist: Dict[str, float] = {}
        self._session = requests.Session()
        self._session.trust_env = True
        self.set_proxy(proxy)
        self._session.headers.update({"User-Agent": "Echo-Launcher/0.1.1"})

    def set_proxy(self, proxy: str) -> None:
        self.proxy = (proxy or "").strip()
        if self.proxy:
            self._session.proxies.update({"http": self.proxy, "https": self.proxy})
        else:
            self._session.proxies.clear()

    # ---------- 内部 ----------
    @staticmethod
    def _check_host(url: str) -> None:
        host = urlparse(url).netloc.lower()
        for bad in BLOCKED_HOSTS:
            if host == bad or host.endswith("." + bad):
                raise BlockedHostError(f"已阻止访问私有服务端域名: {host}")

    def _is_blacklisted(self, url: str) -> bool:
        host = urlparse(url).netloc
        until = self._blacklist.get(host)
        return until is not None and time.time() < until

    def _mark_fail(self, url: str) -> None:
        self._blacklist[urlparse(url).netloc] = time.time() + BLACKLIST_SECONDS

    def _headers_for(self, url: str) -> Dict[str, str]:
        h = {}
        if "api.github.com" in url and self.github_token:
            h["Authorization"] = f"Bearer {self.github_token}"
            h["Accept"] = "application/vnd.github+json"
        return h

    # ---------- 对外 ----------
    def get(self, url: str, timeout: Optional[int] = None, **kw) -> requests.Response:
        self._check_host(url)
        return self._session.get(url, timeout=timeout or self.timeout,
                                 verify=self.verify_ssl,
                                 headers=self._headers_for(url), **kw)

    def get_json_multi(self, urls: List[str], timeout: Optional[int] = None):
        """按序尝试多个源，返回 (数据, 实际使用的 URL)；全失败抛最后一次异常。"""
        last_err: Optional[Exception] = None
        for url in urls:
            if self._is_blacklisted(url):
                continue
            try:
                r = self.get(url, timeout=timeout)
                r.raise_for_status()
                return r.json(), url
            except Exception as e:
                last_err = e
                self._mark_fail(url)
                continue
        raise RuntimeError(f"所有源均失败: {last_err}")

    def get_text_multi(self, urls: List[str], timeout: Optional[int] = None) -> Tuple[str, str]:
        last_err: Optional[Exception] = None
        for url in urls:
            if self._is_blacklisted(url):
                continue
            try:
                r = self.get(url, timeout=timeout)
                r.raise_for_status()
                r.encoding = r.encoding or "utf-8"
                return r.text, url
            except Exception as e:
                last_err = e
                self._mark_fail(url)
                continue
        raise RuntimeError(f"所有源均失败: {last_err}")

    def download(self, url: str, dest: str, chunk: int = 1 << 20,
                 progress=None, timeout: int = 60) -> str:
        """流式下载，可选进度回调 progress(done, total)。"""
        self._check_host(url)
        tmp = dest + ".part"
        with self._session.get(url, stream=True, timeout=timeout,
                               verify=self.verify_ssl,
                               headers=self._headers_for(url)) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as f:
                for block in r.iter_content(chunk):
                    if not block:
                        continue
                    f.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
        os.replace(tmp, dest)
        return dest
