# -*- coding: utf-8 -*-
"""小虫管理器 启动器。

- 双击运行：若服务未启动则后台拉起，然后打开浏览器
- 带 --server 参数：直接在当前进程里跑服务（打包后由本程序自身再拉起一个进程）
"""
import os
import socket
import subprocess
import sys
import time
import webbrowser


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


PORT = int(os.environ.get("XC_PORT") or os.environ.get("SU_PORT") or "8765")
URL = "http://127.0.0.1:%d/" % PORT


def is_up() -> bool:
    with socket.socket() as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def run_server():
    """在当前进程里跑 FastAPI 服务。"""
    here = base_dir()
    if here not in sys.path:
        sys.path.insert(0, here)
    import uvicorn
    try:
        import server as srv
    except ImportError:
        sys.path.insert(0, os.path.join(here, "app"))
        import server as srv
    uvicorn.run(srv.app, host="127.0.0.1", port=PORT, log_level="warning",
                access_log=False)


def spawn_server():
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, name, 0)
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--server"]
        cwd = base_dir()
    else:
        here = base_dir()
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else sys.executable
        cmd = [exe, os.path.join(here, "server.py")]
        cwd = here
    subprocess.Popen(cmd, cwd=cwd, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def main():
    if "--server" in sys.argv:
        run_server()
        return
    if not is_up():
        spawn_server()
        for _ in range(120):
            if is_up():
                break
            time.sleep(0.5)
    if "--no-browser" not in sys.argv:
        webbrowser.open(URL)


if __name__ == "__main__":
    main()