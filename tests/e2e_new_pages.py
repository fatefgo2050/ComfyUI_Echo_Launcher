"""新增页面联调（离屏）：在真实 ComfyUI 上跑 媒体 / 模型 / 市场 / 版本管理 的数据链路。

    .venv/Scripts/python.exe tests/e2e_new_pages.py

只读 + 只查本地缓存；**不安装、不删除、不启动 ComfyUI**。
市场页需要联网拉目录，失败会跳过而不是判失败。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main() -> int:
    from PySide6.QtWidgets import QApplication
    from comfyui_echo.core import core_version as CV
    from comfyui_echo.core import media as M, model as MODEL, market as MK
    from comfyui_echo.core import paths as P
    from comfyui_echo.ui import style
    from comfyui_echo.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    style.apply(app)
    win = MainWindow()
    win.show()

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.05)

    root = win.ctx.root()
    print("当前包路径:", root or "(未配置)")
    if not root:
        return 1
    d = P.dirs(root)

    print("\n[1] 媒体页：扫描输出目录")
    page = win.pages["media"]
    out = d["outputs"]
    print("    输出目录:", out, "存在:", os.path.isdir(out))
    if os.path.isdir(out):
        items = M.scan([out])
        print(f"    扫到 {len(items)} 个媒体文件")
        kinds = {}
        for i in items:
            kinds[i.kind] = kinds.get(i.kind, 0) + 1
        print("    分类:", kinds)
        if items:
            it = items[0]
            meta = M.load_meta(it)
            print(f"    首个文件: {it.name} ({it.kind}, {it.size_mb:.1f} MB)")
            print(f"    生成参数字段: {sorted(meta.keys())[:8]}")
            thumb = M.thumb_pixmap_bytes(it, 150)
            print(f"    缩略图: {len(thumb)} 字节 PNG")
            assert thumb, "缩略图生成失败"

    print("\n[2] 模型页：扫描 models")
    mp = win.pages["model"]
    models = d["models"]
    print("    models 目录:", models, "存在:", os.path.isdir(models))
    if os.path.isdir(models):
        files = MODEL.scan(models)
        print(f"    扫到 {len(files)} 个模型，"
              f"合计 {sum(f.size for f in files)/1073741824:.1f} GB")
        kinds = {}
        for f in files:
            kinds[f.kind] = kinds.get(f.kind, 0) + 1
        print("    类型分布:", kinds)
        dups = MODEL.duplicates_by_size(models, files)
        print(f"    同尺寸疑似重复: {len(dups)} 组")
        if files:
            f = files[0]
            print(f"    示例: {f.rel_path} ({f.kind}, {f.size_mb:.0f} MB)")

    print("\n[3] 市场页：本地缓存 / 节点反查")
    mkp = win.pages["market"]
    cached = MK.items()
    print(f"    本地缓存条目: {len(cached)}")
    if not cached:
        print("    （未缓存，尝试联网拉取一次…）")
        try:
            n = MK.refresh("")
            print(f"    拉取结果: {n}")
            cached = MK.items()
        except Exception as e:
            print(f"    拉取失败（跳过）: {type(e).__name__}")
    if cached:
        hits = MK.search("IPAdapter", limit=5)
        print(f"    搜 IPAdapter: {len(hits)} 条 -> "
              f"{[h.name for h in hits[:3]]}")
        print(f"    已安装插件: {len(MK.installed_names(root))} 个")
    pairs = MK.search_by_node("IPAdapter", d["comfyui"])
    print(f"    按节点名反查 IPAdapter: {len(pairs)} 条 -> {pairs[:3]}")

    print("\n[4] 版本管理：ComfyUI 内核（只读，不 fetch、不切版本）")
    repo, err = CV.is_ready(root)
    if not repo:
        print(f"    跳过：{err}")
    else:
        cur = CV.current(root)
        print(f"    仓库    : {repo}")
        print(f"    远端    : {CV.remote_url(root)}")
        print(f"    当前版本: {cur.get('describe')}   HEAD {cur.get('short')} "
              f"[{cur.get('date')}]   游离={cur.get('detached')}")
        print(f"    浅克隆  : {CV.is_shallow(root)}   最新正式版: {CV.latest_tag(root) or '（无标签）'}")
        print(f"    本地改动: {len(CV.local_changes(root))} 处")
        rows = CV.commits(root, CV.DEFAULT_REF, limit=5)
        print(f"    开发版最近 {len(rows)} 条提交:")
        for r in rows:
            print(f"      {r['short']}  {r['date']}  {r['subject'][:56]}")
        if rows:
            log = CV.show(root, rows[0]["short"])
            lines = log.splitlines()
            # 版式：0=完整 sha，1=时间+作者，2=空行，3=标题
            print(f"    更新日志 {len(log)} 字符，标题: "
                  f"{lines[3][:60] if len(lines) > 3 else ''}")
        tag = CV.latest_tag(root)
        if tag:
            trs = CV.commits(root, tag, limit=1)
            print(f"    正式版 {tag} 首条: "
                  f"{trs[0]['short'] + '  ' + trs[0]['subject'][:44] if trs else '（空）'}")

    print("\n[5] 环境页重型轮子：探测")
    from comfyui_echo.core import wheels as W
    py = d["python_exe"]
    p = W.probe(py)
    print("    ", W.describe(p))
    print(f"    torch 索引({p.cuda_tag}): {W.torch_index(p.cuda_tag)}")
    print(f"    SageAttention wheel: {(W.pick_sage(p.torch_version) or '无')[:80]}")

    print("\n新增页面联调结束")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
