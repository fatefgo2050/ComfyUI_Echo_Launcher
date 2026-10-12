"""进程链路联调（离屏）：用临时「假 ComfyUI 便携包」验证启动/输出/就绪/停止。

    .venv/Scripts/python.exe tests/process_check.py

会真的起一个子进程（假 ComfyUI），但**不会**碰用户真实的 ComfyUI。
覆盖：Echo ComfyProcess 启动、参数拼装、日志总线 -> Qt 信号、wait_ready、进程树终止。
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

FAKE_MAIN = r'''
import socket, sys, time
port = 8188
argv = sys.argv[1:]
if "--port" in argv:
    port = int(argv[argv.index("--port") + 1])
print("Fake ComfyUI starting, argv=%s" % " ".join(argv), flush=True)
print("WARNING: this is a fake warning line", flush=True)
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", port))
srv.listen(5)
print("To see the GUI go to: http://127.0.0.1:%d" % port, flush=True)
try:
    while True:
        time.sleep(0.5)
except KeyboardInterrupt:
    pass
'''


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> int:
    from PySide6.QtWidgets import QApplication
    from comfyui_echo.core import paths
    from comfyui_echo.core.process import ComfyRunner, LaunchOptions

    tmp = tempfile.mkdtemp(prefix="echo_proc_")
    try:
        root = os.path.join(tmp, "portable")
        embed = os.path.join(root, "python_embeded")
        comfy = os.path.join(root, "ComfyUI")
        os.makedirs(comfy)
        print("[1] 建临时便携包（python_embeded 用真 venv，保证 python.exe 可用）")
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", embed],
                       check=True, capture_output=True)
        with open(os.path.join(comfy, "main.py"), "w", encoding="utf-8") as f:
            f.write(FAKE_MAIN)
        assert paths.validate_root(root) is None, paths.validate_root(root)
        d = paths.dirs(root)
        assert os.path.isfile(d["python_exe"]), d["python_exe"]
        print("    OK  ", d["python_exe"])

        app = QApplication(sys.argv)
        port = _free_port()
        runner = ComfyRunner()
        lines, states, ready = [], [], []
        runner.output.connect(lambda t, lv: lines.append((t, lv)))
        runner.state_changed.connect(states.append)
        runner.server_ready.connect(ready.append)
        runner.stopped.connect(lambda c: states.append(f"exit:{c}"))

        opt = LaunchOptions({
            "fp16_accumulation": False, "cuda_malloc": False,
            "attention_mode": "FLASH", "port": port,
        })
        print("[2] 参数拼装")
        args = opt.build_args()
        print("    ", args)
        assert "--use-flash-attention" in args
        print("[3] 预览命令")
        print("    ", runner.preview(root, opt))

        print(f"[4] 启动假 ComfyUI（端口 {port}）")
        err = runner.start(root, opt)
        assert err == "", err
        deadline = time.time() + 60
        while time.time() < deadline and not ready:
            app.processEvents()
            time.sleep(0.05)
        assert ready, f"没等到就绪信号；已收 {len(lines)} 行输出"
        print("    OK  就绪信号:", ready[0])
        print("    OK  状态变化:", states)
        assert runner.is_running

        joined = "\n".join(t for t, _ in lines)
        print("[5] 控制台输出（日志总线 -> Qt 信号）")
        for t, lv in lines[:6]:
            print(f"     [{lv or 'info'}] {t}")
        assert "Fake ComfyUI starting" in joined, joined
        assert "argv" in joined
        # 参数真的传到了子进程
        assert f"--port {port}" in joined, joined
        assert "--use-flash-attention" in joined, joined
        # 级别分类：warning 行应被标成 warning
        assert any(lv == "warning" for _t, lv in lines), lines
        print("    OK  子进程收到了拼装好的参数，warning 行分级正确")

        print("[6] 停止（psutil 进程树终止）")
        runner.stop()
        deadline = time.time() + 20
        while time.time() < deadline and runner.is_running:
            app.processEvents()
            time.sleep(0.05)
        assert not runner.is_running, "进程没停下来"
        # 退出信号是跨线程队列投递的，必须继续泵事件才能收到；
        # UI 依赖它把「启动」按钮重新启用，所以这里必须断言收到。
        deadline = time.time() + 10
        while time.time() < deadline and not any(
                isinstance(s, str) and s.startswith("exit:") for s in states):
            app.processEvents()
            time.sleep(0.05)
        exits = [s for s in states if isinstance(s, str) and s.startswith("exit:")]
        assert exits, f"没收到退出信号；states={states}"
        assert False in states, f"没收到 state_changed(False)；states={states}"
        print(f"    OK  已停止；状态变化: {states}")

        print("\n进程链路联调通过")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
