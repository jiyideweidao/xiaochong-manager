# -*- coding: utf-8 -*-
"""小虫管理器 启动器。

双击运行时：
  1) 保证后台服务只有一个（用系统互斥体拦住连点两下那种抢跑）
  2) 界面已经开着 → 直接把它恢复并提到最前，**不会再开一个**
  3) 界面没开 → 开一个独立的程序窗口（像普通桌面软件，不是浏览器标签页）

其它参数：
  --server      直接在当前进程里跑服务（打包后由本程序自身再拉起一个进程）
  --no-browser  只保证服务在跑，不开界面（自检脚本用）
  --browser     用系统默认浏览器打开（回到旧行为；一般用不到）
"""
import ctypes
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser

TITLE_KEY = "小虫管理器"

# 只认「本程序自己的窗口」：排除用户浏览器里的标签页标题
#   app 窗口标题  = 小虫管理器 · 本地资源管理器
#   浏览器标签页  = 小虫管理器 · 本地资源管理器 - 个人 - Microsoft Edge
TITLE_SKIP = ("Microsoft", "Chrome", "Google", "另外", "和另外")

MUTEX_NAME = "Local\\XiaoChongManager.single_instance"
SW_SHOW, SW_RESTORE = 5, 9

BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)

_ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


PORT = int(os.environ.get("XC_PORT") or os.environ.get("SU_PORT") or "8765")
URL = "http://127.0.0.1:%d/" % PORT


def data_dir() -> str:
    """数据目录（和主程序一致；自检脚本改了 XC_DATA 也照样生效）。"""
    try:
        here = base_dir()
        for p in (os.path.join(here, "app"), here):
            if p not in sys.path:
                sys.path.insert(0, p)
        from sulib import config
        return str(config.DATA_DIR)
    except Exception:
        base = os.environ.get("LOCALAPPDATA") or base_dir()
        return os.path.join(base, "XiaoChongManager", "data")


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


# --------------------------------------------------------------- 只允许一个实例
def acquire_lock(timeout_ms: int = 120000):
    """拿到「只跑一个」的锁；拿不到说明另一次双击正在启动。"""
    k = ctypes.windll.kernel32
    k.CreateMutexW.restype = ctypes.c_void_p
    k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    h = k.CreateMutexW(None, False, MUTEX_NAME)
    if not h:
        return None
    r = k.WaitForSingleObject(ctypes.c_void_p(h), ctypes.c_uint(timeout_ms))
    if r in (0x00000000, 0x00000080):        # 拿到 / 上一个进程异常退出后留下
        return h
    k.CloseHandle(ctypes.c_void_p(h))
    return None


def release_lock(h) -> None:
    if not h:
        return
    k = ctypes.windll.kernel32
    k.ReleaseMutex(ctypes.c_void_p(h))
    k.CloseHandle(ctypes.c_void_p(h))


# --------------------------------------------------------------- 界面窗口
def app_windows():
    """本程序自己的可见窗口 [(hwnd, 标题), ...]。"""
    u = ctypes.windll.user32
    out = []
    me = os.getpid()

    def cb(hwnd, _):
        if not hwnd or not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        t = buf.value
        if TITLE_KEY in t and not any(s in t for s in TITLE_SKIP):
            pid = ctypes.c_ulong()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != me:
                out.append((int(hwnd), t))
        return True

    u.EnumWindows(_ENUMPROC(cb), None)
    return out


def focus_window(hwnd) -> bool:
    """把窗口恢复出来并提到最前。"""
    u = ctypes.windll.user32
    h = ctypes.c_void_p(hwnd)
    if not u.IsWindow(h):
        return False
    u.ShowWindow(h, SW_RESTORE if u.IsIconic(h) else SW_SHOW)
    fg = u.GetForegroundWindow()
    tid = u.GetWindowThreadProcessId(fg, None) if fg else 0
    mine = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = False
    if tid and tid != mine:          # 附到前台线程上，绕过「不许抢焦点」的限制
        attached = bool(u.AttachThreadInput(mine, tid, True))
    u.SetForegroundWindow(h)
    u.BringWindowToTop(h)
    if attached:
        u.AttachThreadInput(mine, tid, False)
    if u.GetForegroundWindow() != hwnd:
        # Windows 有「前台锁定」，抢不到焦点时再用 Alt+Tab 同一套机制兜底
        try:
            u.SwitchToThisWindow(h, True)
        except Exception:
            pass
    if u.GetForegroundWindow() != hwnd:
        # 还是抢不到焦点：至少把它顶到 z 序最上面，让你看得见（不动别的窗口）
        HWND_TOPMOST, HWND_NOTOPMOST = ctypes.c_void_p(-1), ctypes.c_void_p(-2)
        NOMOVE_NOSIZE = 0x0001 | 0x0002          # SWP_NOSIZE | SWP_NOMOVE
        u.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, NOMOVE_NOSIZE)
        u.SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0, NOMOVE_NOSIZE)
    if u.GetForegroundWindow() == hwnd:
        return True
    u.GetWindow.restype = ctypes.c_void_p         # 句柄是 64 位，别被截断
    return u.GetWindow(h, 3) is None              # GW_HWNDPREV=3，返回 0 说明就在最上面


def find_browser() -> str:
    for p in BROWSER_CANDIDATES:
        if os.path.isfile(p):
            return p
    for name in ("msedge", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def launch_app_window(exe: str) -> int:
    """开一个独立的程序窗口（没有地址栏和标签页），返回它的 hwnd。"""
    prof = os.path.join(data_dir(), "ui")
    try:
        os.makedirs(prof, exist_ok=True)
    except OSError:
        prof = os.path.join(os.environ.get("TEMP") or ".", "xiaochong_ui")
        os.makedirs(prof, exist_ok=True)
    before = {w[0] for w in app_windows()}
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, name, 0)
    try:
        subprocess.Popen([exe, "--app=" + URL, "--user-data-dir=" + prof,
                          "--no-first-run", "--no-default-browser-check",
                          "--window-size=1500,940", "--window-position=60,30"],
                         creationflags=flags, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return 0
    for _ in range(80):                      # 最多等 40 秒
        time.sleep(0.5)
        for hwnd, _t in app_windows():
            if hwnd not in before:
                return hwnd
    return 0


def show_ui(force_browser: bool = False) -> str:
    """已经开着就提到最前，没开就开一个。"""
    wins = app_windows()
    if wins:
        focus_window(wins[0][0])
        return "focus"
    if not force_browser:
        exe = find_browser()
        if exe:
            # 再等两秒多看几眼：枚举偶尔会漏一次，
            # 而「明明有窗口却又开一个」会让 Edge 去抢 profile，风险大得多
            for _ in range(4):
                time.sleep(0.5)
                wins = app_windows()
                if wins:
                    focus_window(wins[0][0])
                    return "focus"
            hwnd = launch_app_window(exe)
            if hwnd:
                focus_window(hwnd)
                return "app-window"
    webbrowser.open(URL)
    return "browser"


def main():
    if "--server" in sys.argv:
        run_server()
        return

    lock = acquire_lock()
    try:
        if not is_up():
            spawn_server()
            for _ in range(180):
                if is_up():
                    break
                time.sleep(0.5)
    finally:
        release_lock(lock)

    if "--no-browser" in sys.argv:
        return
    show_ui(force_browser="--browser" in sys.argv)


if __name__ == "__main__":
    main()
