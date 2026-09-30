# -*- coding: utf-8 -*-
r"""自检：关窗口是「隐藏到任务栏」还是「退出程序」

在临时目录 + 临时端口上跑，不动你的素材、不动你已经装好的程序，跑完自己收拾干净。
界面窗口用一个临时小窗口（标题一样）来冒充，不需要真的开浏览器。

跑法：
    python tools\close_action_test.py
"""
import ctypes
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PYW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
TMP = os.path.join(os.environ.get("TEMP") or ".", "xc_closeaction")
PORT = 8801
BASE = "http://127.0.0.1:%d" % PORT
TITLE = "小虫管理器 · 本地资源管理器"

FAIL = []
N = [0]


def chk(ok, msg):
    N[0] += 1
    print(("  PASS  " if ok else "  FAIL  ") + msg)
    if not ok:
        FAIL.append(msg)


def api(path, payload=None):
    url = BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def state():
    return api("/api/state")


def up():
    with socket.socket() as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def start_service(config_extra=None):
    data = os.path.join(TMP, "data")
    os.makedirs(data, exist_ok=True)
    if config_extra is not None:
        io.open(os.path.join(data, "config.json"), "w", encoding="utf-8").write(
            json.dumps(config_extra, ensure_ascii=False))
    env = dict(os.environ, XC_PORT=str(PORT), XC_DATA=data, PYTHONIOENCODING="utf-8")
    log = io.open(os.path.join(TMP, "svc.log"), "a", encoding="utf-8")
    p = subprocess.Popen([PY, os.path.join(APP, "server.py")], env=env, cwd=APP,
                         creationflags=0x8 | 0x200 | 0x8000000,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    for _ in range(60):
        if up():
            return p
        if p.poll() is not None:
            return p
        time.sleep(0.5)
    return p


def stop_service(p):
    try:
        if p.poll() is None:
            subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True)
    except Exception:
        pass


def pump(win, seconds, on_tick=None):
    """让临时窗口活一段时间（顺便跑一点别的检查）"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        if win is not None:
            win.update()
        if on_tick:
            on_tick()
        time.sleep(0.1)


def make_window():
    import tkinter as tk
    win = tk.Tk()
    win.title(TITLE)
    win.geometry("320x120+40+40")
    win.update()
    return win


def wait_for(fn, seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def foreign_windows():
    """别人（比如你装好并开着的那个）的界面窗口。自检期间必须没有，
    否则服务看门狗会把它们当成「界面还在」，[3]/[6] 就测不准。"""
    EP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    u = ctypes.windll.user32
    me = os.getpid()
    out = []

    def cb(h, _):
        if not h or not u.IsWindowVisible(h):
            return True
        n = u.GetWindowTextLengthW(h)
        if n <= 0:
            return True
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        t = b.value
        if "小虫管理器" in t and not any(x in t for x in ("Microsoft", "Chrome", "Google", "另外", "和另外")):
            pid = ctypes.c_ulong()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value != me:
                out.append(int(h))
        return True

    u.EnumWindows(EP(cb), None)
    return out


shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)

other = foreign_windows()
if other:
    print("注意：现在还有别的「小虫管理器」界面窗口开着（可能是你已经装好并运行的那个）。")
    print("      自检期间先把它关掉（只关窗口，后台服务不动），跑完你再双击图标打开就行。")
    for h in other:
        ctypes.windll.user32.PostMessageW(ctypes.c_void_p(h), 0x0010, 0, 0)
    for _ in range(20):
        if not foreign_windows():
            break
        time.sleep(0.5)
    print("      已关掉 %d 个窗口" % len(other))
win = None
svc = None
try:
    print("=" * 60)
    print("[1] 默认设置应该是「隐藏到任务栏」，托盘图标应该就位")
    svc = start_service()
    chk(up(), "服务起来了（端口 %d）" % PORT)
    time.sleep(4)
    st = state()
    chk((st.get("ui") or {}).get("close_action") == "tray",
        "默认 close_action = tray（实际 %s）" % (st.get("ui") or {}).get("close_action"))
    chk(bool((st.get("ui") or {}).get("tray")), "托盘图标已就位")

    print()
    print("[2] 界面窗口在的时候，设置面板能读到「有窗口」")
    win = make_window()
    chk(wait_for(lambda: (state().get("ui") or {}).get("window_seen"), 12),
        "看门狗认出了界面窗口")
    pump(win, 1)
    ui = state().get("ui") or {}
    chk(ui.get("windows", 0) >= 1, "数到窗口个数 %s" % ui.get("windows"))

    print()
    print("[3] 关掉窗口：tray 模式应该「不退出」，并且提示一次")
    win.destroy()
    win = None
    time.sleep(2)
    chk(wait_for(lambda: (state().get("ui") or {}).get("ballooned"), 14),
        "弹了一次「我还在后台跑着」的提示")
    pump(None, 9)
    chk(up(), "tray 模式下服务照旧在跑（没有退出）")

    print()
    print("[4] 窗口开着的状态下改成「退出程序」：托盘收起来，服务不能退")
    win = make_window()
    chk(wait_for(lambda: (state().get("ui") or {}).get("window_seen"), 12),
        "看门狗又认出了窗口")
    api("/api/settings", {"close_action": "quit"})
    st = state()
    chk((st.get("ui") or {}).get("close_action") == "quit",
        "设置已变成 quit（实际 %s）" % (st.get("ui") or {}).get("close_action"))
    chk(wait_for(lambda: not (state().get("ui") or {}).get("tray"), 20),
        "托盘图标已收起")

    print()
    print("[5] quit 模式：窗口开着的时候不能误退（比如你按 F5 刷新）")
    pump(win, 15)
    chk(up(), "窗口一直开着，服务没有误退")

    print()
    print("[6] quit 模式：关掉窗口，后台服务应该跟着退出")
    win.destroy()
    win = None
    gone = wait_for(lambda: not up(), 25)
    chk(gone, "窗口关掉后服务自己退了")
    try:
        if svc:
            svc.wait(timeout=10)
    except Exception:
        pass

    print()
    print("[7] quit 模式但从来没开过界面（比如 --no-browser）：不能自己退")
    stop_service(svc)
    time.sleep(2)
    svc = start_service({"close_action": "quit"})
    chk(up(), "服务起来了")
    pump(None, 20)
    chk(up(), "一直没开窗口，服务老老实实待着")

    print()
    print("[8] 设置面板的选项值能不能原样存下来")
    api("/api/settings", {"close_action": "tray"})
    chk((state().get("ui") or {}).get("close_action") == "tray", "改回 tray 成功")
    api("/api/settings", {"close_action": "乱写的值"})
    v = (state().get("ui") or {}).get("close_action")
    chk(v == "tray", "乱写的值被挡掉，没把设置搞坏（实际 %s）" % v)
finally:
    if win is not None:
        try:
            win.destroy()
        except Exception:
            pass
    stop_service(svc)
    time.sleep(1)
    shutil.rmtree(TMP, ignore_errors=True)
    print()
    print("结果:", "全部通过" if not FAIL else ("有失败项：" + str(FAIL)))
    print("自检项：%d" % N[0])
sys.exit(0 if not FAIL else 1)
