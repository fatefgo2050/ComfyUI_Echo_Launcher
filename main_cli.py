"""ComfyUI Echo 启动器 —— 无界面子命令（移植自 Echo 的 main.py）。

图形界面用 `python main.py`；这里的命令不需要 Qt，纯命令行可用：

    python main_cli.py paths            冻结态关键路径（BASE_DIR / resources / 便携 Git）
    python main_cli.py selftest         核心链路自检（模式切换 / 节点映射 / 工作流 / 配置）
    python main_cli.py scan             扫描当前包的插件
    python main_cli.py git-info         便携 Git 可用性
    python main_cli.py net-check        公开数据源连通性（不会访问任何私有域名）
    python main_cli.py attention-check  实测 comfy-kitchen INT8 注意力（CK 档前置条件）
    python main_cli.py video-check      PyAV + ffmpeg 链路（媒体浏览器前置条件）
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def cmd_paths() -> int:
    from echo_launcher import BASE_DIR as B, DATA_DIR, RESOURCE_DIR
    print("BASE_DIR     :", B)
    print("DATA_DIR     :", DATA_DIR)
    print("RESOURCE_DIR :", RESOURCE_DIR, "存在:", os.path.isdir(RESOURCE_DIR))
    g = os.path.join(RESOURCE_DIR, "git", "cmd", "git.exe")
    print("便携 Git     :", os.path.isfile(g), "->", g)
    return 0


def cmd_scan() -> int:
    from comfyui_echo.core import config as cfgmod
    from comfyui_echo.core import plugins as pluginmod

    root = cfgmod.current_root(cfgmod.load())
    if not root:
        print("未配置 ComfyUI 路径（先跑一次图形界面，或在 launcher_config.json 里填）")
        return 1
    infos = pluginmod.scan(root)
    print(f"{'插件':<40}{'状态':<8}{'Git':<6}分支")
    for i in infos:
        print(f"{i.name:<40}{'启用' if i.enabled else '禁用':<8}"
              f"{'是' if i.is_git else '':<6}{i.branch}")
    print(f"合计 {len(infos)} 个")
    return 0


def cmd_git_info() -> int:
    from echo_launcher.net.mirrors import portable_git
    from echo_launcher.plugin import gitops

    print("git:", portable_git())
    rc, out, err = gitops.run(["--version"])
    print("version:", out or err)
    return 0 if rc == 0 else 1


def cmd_net_check() -> int:
    from echo_launcher.net.http_client import HttpClient
    from echo_launcher.net.mirrors import (
        CIVITAI_API, CUSTOM_NODE_LIST_SOURCES, EXTENSION_NODE_MAP_SOURCES, HUGGINGFACE_API,
    )

    client = HttpClient(proxy="", timeout=20)
    checks = [
        ("节点映射表", lambda: client.get_json_multi(EXTENSION_NODE_MAP_SOURCES)[1]),
        ("插件目录", lambda: client.get_json_multi(CUSTOM_NODE_LIST_SOURCES)[1]),
        ("HuggingFace", lambda: f"{HUGGINGFACE_API}/models?search=sdxl&limit=1"),
        ("Civitai", lambda: f"{CIVITAI_API}/models?limit=1"),
    ]
    ok = 0
    for name, fn in checks:
        try:
            src = fn()
            if src.startswith("http"):
                r = client.get(src)
                print(f"  {name}: HTTP {r.status_code}  <- {src.split('/')[2]}")
                ok += r.status_code == 200
            else:
                print(f"  {name}: OK  <- {src.split('/')[2]}")
                ok += 1
        except Exception as e:
            print(f"  {name}: 失败 ({type(e).__name__}) {e}")
    print(f"通过 {ok}/{len(checks)}")
    return 0 if ok else 1


def cmd_attention_check() -> int:
    from comfyui_echo.core import config as cfgmod
    from comfyui_echo.core import attention as attn
    from comfyui_echo.core import paths as pathmod

    cfg = cfgmod.load()
    root = cfgmod.current_root(cfg)
    if not root:
        print("未配置 ComfyUI 路径")
        return 1
    lc = cfg.get("launch") or {}
    mode = attn.normalize_mode(lc.get("attention_mode"))
    print("注意力档位:", mode, "|", attn.describe(mode))
    print("将产出参数:", attn.build_flags(lc) or "(无)")
    py = pathmod.dirs(root)["python_exe"]
    print("目标解释器:", py or "(未找到)")
    if not py:
        return 1
    ok, txt = attn.probe_ck(py, pathmod.dirs(root)["comfyui"])
    print("CK INT8注意力:", "可用" if ok else "不可用", "-", txt)
    return 0 if ok else 1


def cmd_video_check() -> int:
    import tempfile

    from echo_launcher.media.video import probe, seek_frame

    tmp = tempfile.mkdtemp(prefix="echo_vcheck_")
    mp4 = os.path.join(tmp, "t.mp4")
    import av
    out = av.open(mp4, "w")
    st = out.add_stream("libx264", rate=10)
    st.width, st.height, st.pix_fmt = 64, 64, "yuv420p"
    for i in range(10):
        fr = av.VideoFrame(64, 64, "rgb24")
        fr.pts = i
        for pkt in st.encode(fr):
            out.mux(pkt)
    for pkt in st.encode():
        out.mux(pkt)
    out.close()

    info = probe(mp4)
    print(f"encode+probe OK: duration={info.get('duration')} "
          f"{info.get('width')}x{info.get('height')}")
    print(f"seek_frame OK: {seek_frame(mp4, 0.3).size}")
    print("视频链路自检通过")
    return 0


def cmd_selftest() -> int:
    """无界面自检：临时目录里模拟一个 ComfyUI，验证「适配层 + Echo 功能层」链路。"""
    import shutil
    import tempfile

    from comfyui_echo.core import attention as attn
    from comfyui_echo.core import config as cfgmod
    from comfyui_echo.core import nodemap as nm
    from comfyui_echo.core import paths as pathmod
    from comfyui_echo.core import plugins as pluginmod

    tmp = tempfile.mkdtemp(prefix="echo_selftest_")
    try:
        # 把配置 / 节点映射缓存 / 日志都重定向到临时目录，
        # 保证自检**绝不**改动用户真实的 launcher_config.json 与 data/。
        from echo_launcher.core.config import Config
        from echo_launcher.plugin.node_map import NodeMap
        from comfyui_echo.core import _bridge
        _bridge._singletons["config"] = Config(os.path.join(tmp, "launcher_config.json"))
        _bridge._singletons["node_map"] = NodeMap(os.path.join(tmp, "data"))

        # 造一个便携包：python_embeded\python.exe + ComfyUI\main.py
        root = os.path.join(tmp, "portable")
        os.makedirs(os.path.join(root, "python_embeded"))
        os.makedirs(os.path.join(root, "ComfyUI", "custom_nodes"))
        with open(os.path.join(root, "python_embeded", "python.exe"), "wb"):
            pass
        with open(os.path.join(root, "ComfyUI", "main.py"), "w", encoding="utf-8") as f:
            f.write("# fake\n")
        with open(os.path.join(root, "ComfyUI", "comfyui_version.py"), "w",
                  encoding="utf-8") as f:
            f.write('__version__ = "9.9.9"\n')
        cn = os.path.join(root, "ComfyUI", "custom_nodes")
        for name in ("ComfyUI-AAA", "ComfyUI-BBB", "ComfyUI-CCC"):
            d = os.path.join(cn, name)
            os.makedirs(d)
            with open(os.path.join(d, "nodes.py"), "w", encoding="utf-8") as f:
                f.write(f'NODE_CLASS_MAPPINGS = {{"{name}Node": A, "{name}Loader": B}}\n')

        print("[1] 路径适配（便携包布局）")
        assert pathmod.validate_root(root) is None, pathmod.validate_root(root)
        d = pathmod.dirs(root)
        assert d["comfyui"] == os.path.join(root, "ComfyUI"), d["comfyui"]
        assert d["python_exe"] == os.path.join(root, "python_embeded", "python.exe")
        assert pathmod.comfyui_version(root) == "9.9.9"
        print("    OK  dirs/validate/版本读取")

        print("[2] 插件扫描")
        infos = pluginmod.scan(root)
        assert len(infos) == 3, [i.name for i in infos]
        print(f"    OK  {len(infos)} 个插件")

        print("[3] 启用/禁用（.disabled 就地改名）")
        ok, msg = pluginmod.set_enabled(root, "ComfyUI-BBB", False)
        assert ok, msg
        assert os.path.isdir(os.path.join(cn, "ComfyUI-BBB.disabled"))
        assert pluginmod.capture_enabled(root) == ["ComfyUI-AAA", "ComfyUI-CCC"]
        ok, msg = pluginmod.set_enabled(root, "ComfyUI-BBB", True)
        assert ok and os.path.isdir(os.path.join(cn, "ComfyUI-BBB")), msg
        print("    OK  禁用/启用往返一致")

        print("[4] 应用模式（白名单外全部禁用）")
        moved, errs = pluginmod.apply_mode(root, ["ComfyUI-AAA"])
        assert not errs, errs
        assert os.path.isdir(os.path.join(cn, "ComfyUI-AAA"))
        assert os.path.isdir(os.path.join(cn, "ComfyUI-BBB.disabled"))
        assert os.path.isdir(os.path.join(cn, "ComfyUI-CCC.disabled"))
        assert pluginmod.apply_mode(root, []) == (0, []), "空白名单应不干预"
        errs = pluginmod.enable_all(root)
        assert not errs and os.path.isdir(os.path.join(cn, "ComfyUI-CCC"))
        print(f"    OK  移动 {moved} 个；空白名单不干预；可一键恢复")

        print("[5] 节点映射（本地 AST 扫描）")
        mapping = nm.build_local_map(root, force=True)
        assert mapping.get("ComfyUI-AAANode") == "ComfyUI-AAA", mapping
        print(f"    OK  {len(mapping)} 条节点映射")

        print("[6] 工作流识别")
        wf = os.path.join(tmp, "wf.json")
        with open(wf, "w", encoding="utf-8") as f:
            f.write('{"nodes":[{"type":"ComfyUI-AAANode"},{"type":"ComfyUI-CCCLoader"}],"links":[]}')
        res = nm.analyze(root, wf, extmap={})
        assert {r["plugin"] for r in res["results"]} == {"ComfyUI-AAA", "ComfyUI-CCC"}, res
        print(f"    OK  识别到 {sorted({r['plugin'] for r in res['results']})}")

        print("[7] 官方映射表结构解析（URL -> 节点名）")
        remote = nm.build_remote_map({
            "https://github.com/x/Foo": [["FooNode", "FooLoader"], {"title_aux": "Foo"}],
        })
        assert remote == {"FooNode": "https://github.com/x/Foo",
                          "FooLoader": "https://github.com/x/Foo"}, remote
        print("    OK  反查表按真实结构建立")

        print("[8] 启动参数拼装（Echo 注意力四档）")
        from comfyui_echo.core.process import LaunchOptions
        a = LaunchOptions({"cuda_malloc": True, "attention_mode": "CK"})
        assert "--use-ck-attention" in a.build_args(), a.build_args()
        b = LaunchOptions({"cuda_malloc": True, "attention_mode": "默认"})
        assert "--use-sage-attention" not in b.build_args()
        c = LaunchOptions({"attention_mode": "SAGE",
                           "extra_args": "--use-flash-attention --lowvram"})
        args = c.build_args()
        assert args.count("--use-sage-attention") == 1 and "--use-flash-attention" not in args, args
        print("    OK  四档互斥、自定义参数里的重复开关被剔除")

        print("[9] 配置适配（UI 视图 <-> Echo 配置）")
        view = cfgmod.load()
        view["paths"] = [{"remark": "测试", "root": root}]
        view["current_path_index"] = 0
        view.setdefault("launch", {})["attention_mode"] = "FLASH"
        view["launch"]["port"] = 8890
        cfgmod.save(view)
        again = cfgmod.load()
        assert cfgmod.current_root(again) == root, cfgmod.current_root(again)
        assert again["launch"]["attention_mode"] == "FLASH", again["launch"]["attention_mode"]
        assert again["launch"]["port"] == 8890, again["launch"]["port"]
        raw = _bridge.config().raw()
        assert raw["attention_mode"] == "FLASH" and raw["custom_port"] == "8890"
        assert raw["path_configs"][0]["comfyui_path"] == os.path.join(root, "ComfyUI")
        print("    OK  双向映射一致，Echo 侧键名正确")

        print("[10] 注意力档位工具")
        assert attn.normalize_mode("ck") == "CK"
        assert attn.mark_for("CK") == "ck" and attn.mark_for("默认") == ""
        print("    OK  档位规整与 shim 标记换算")

        print("\n全部自检通过")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


COMMANDS = {
    "paths": cmd_paths,
    "selftest": cmd_selftest,
    "scan": cmd_scan,
    "git-info": cmd_git_info,
    "net-check": cmd_net_check,
    "attention-check": cmd_attention_check,
    "video-check": cmd_video_check,
}


def main() -> int:
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd in ("-h", "--help"):
            print(__doc__)
            return 0
        fn = COMMANDS.get(cmd)
        if fn:
            return fn()
        print(f"未知命令: {cmd}\n{__doc__}")
        return 2
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
