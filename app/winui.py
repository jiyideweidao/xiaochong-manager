# -*- coding: utf-8 -*-
"""小虫管理器 · 窗口 & 托盘小工具（纯 ctypes，不依赖第三方库）

- app_windows()  找出「小虫管理器」自己开的界面窗口（不会碰用别人的浏览器窗口）
- open_ui()      把界面叫回来（复用启动器：已开着就提到最前，没开就开一个）
- ensure_tray()  在任务栏右下角放一个小虫图标：双击打开界面，右键菜单「打开界面 / 退出程序」
- remove_tray()  摘掉托盘图标

注意：start.py 里有一份同样的窗口判断（启动器要能在最早期独立跑起来），
两边的 TITLE_KEY / TITLE_SKIP 必须保持一致。
"""
import ctypes
import os
import subprocess
import sys
import threading
from ctypes import wintypes

TITLE_KEY = "小虫管理器"
# app 窗口标题  = 小虫管理器 · 本地资源管理器
# 浏览器标签页  = 小虫管理器 · 本地资源管理器 - 个人 - Microsoft Edge
TITLE_SKIP = ("Microsoft", "Chrome", "Google", "另外", "和另外")

_EP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def app_windows(exclude_pid=None):
    """本程序自己的可见窗口 [(hwnd, 标题), ...]。"""
    u = ctypes.windll.user32
    me = os.getpid() if exclude_pid is None else exclude_pid
    out = []

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

    u.EnumWindows(_EP(cb), None)
    return out


def open_ui():
    """把界面叫回来：复用启动器（已开着就提到最前，没开就开一个）。"""
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, name, 0)
    try:
        if getattr(sys, "frozen", False):
            exe = os.path.abspath(sys.executable)
            cmd, cwd = [exe], os.path.dirname(exe)
        else:
            here = os.path.dirname(os.path.abspath(__file__))
            exe = sys.executable
            pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
            cmd = [pyw if os.path.exists(pyw) else exe, os.path.join(here, "start.py")]
            cwd = here
        subprocess.Popen(cmd, cwd=cwd, creationflags=flags, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ 托盘图标
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0x0, 0x1, 0x2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
WM_DESTROY, WM_CLOSE = 0x0002, 0x0010
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0201, 0x0203, 0x0205
MF_STRING = 0x0
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x2, 0x100
ID_OPEN, ID_QUIT = 1, 2


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONDATA(ctypes.Structure):
    """Vista+ 的结构；cbSize 填自己的大小就行。"""
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", _GUID), ("hBalloonIcon", wintypes.HICON)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class Tray:
    """托盘图标。跑在一个单独线程的消息循环里（不影响主线程干活）。"""

    CLASS = "XiaoChongTrayWnd"

    def __init__(self, tip, on_open=None, on_quit=None):
        self.tip = (tip or "小虫管理器")[:127]
        self.on_open = on_open
        self.on_quit = on_quit
        self.hwnd = 0
        self.ok = False
        self._ready = threading.Event()
        self._proc_ref = None          # 防止回调被垃圾回收
        self._taskbar_msg = 0

    # ---------------- 内部 ----------------
    # 用私有的 WinDLL 实例：类型声明只影响这里，不会打扰别的模块
    U = ctypes.WinDLL("user32")
    S = ctypes.WinDLL("shell32")
    K = ctypes.WinDLL("kernel32")

    @classmethod
    def _wire(cls):
        if getattr(cls, "_wired", False):
            return
        u, s, k = cls.U, cls.S, cls.K
        k.GetModuleHandleW.restype = ctypes.c_void_p
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        s.ExtractIconW.restype = ctypes.c_void_p
        s.ExtractIconW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_uint]
        s.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
        u.LoadIconW.restype = ctypes.c_void_p
        u.LoadIconW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.CreateWindowExW.restype = ctypes.c_void_p
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_void_p]
        u.DefWindowProcW.restype = ctypes.c_void_p
        u.DefWindowProcW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.CreatePopupMenu.restype = ctypes.c_void_p
        u.AppendMenuW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.LPCWSTR]
        u.TrackPopupMenu.restype = ctypes.c_int
        u.TrackPopupMenu.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        u.DestroyMenu.argtypes = [ctypes.c_void_p]
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u.PostMessageW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.DestroyWindow.argtypes = [ctypes.c_void_p]
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p,
                                  wintypes.UINT, wintypes.UINT]
        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.restype = ctypes.c_void_p
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        cls._wired = True

    def _icon(self):
        try:
            h = self.S.ExtractIconW(None, sys.executable, 0)
            if h:
                return h
        except Exception:
            pass
        return self.U.LoadIconW(None, ctypes.c_void_p(32512))

    def _nid(self):
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self.hwnd
        nid.uID = 1
        return nid

    def _add(self):
        nid = self._nid()
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icon()
        nid.szTip = self.tip
        return bool(self.S.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _popup_menu(self):
        u = self.U
        menu = u.CreatePopupMenu()
        u.AppendMenuW(menu, MF_STRING, ID_OPEN, "打开界面")
        u.AppendMenuW(menu, MF_STRING, ID_QUIT, "退出程序")
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        u.SetForegroundWindow(self.hwnd)
        cmd = u.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0,
                               self.hwnd, None)
        u.PostMessageW(self.hwnd, 0, 0, 0)
        u.DestroyMenu(menu)
        if cmd == ID_OPEN and self.on_open:
            try:
                self.on_open()
            except Exception:
                pass
        elif cmd == ID_QUIT and self.on_quit:
            try:
                self.on_quit()
            except Exception:
                pass

    def _wndproc(self, hwnd, msg, wp, lp):
        u = self.U
        if msg == WM_TRAY:
            what = lp & 0xFFFF
            if what in (WM_LBUTTONDBLCLK, WM_LBUTTONUP):
                if self.on_open:
                    try:
                        self.on_open()
                    except Exception:
                        pass
            elif what == WM_RBUTTONUP:
                self._popup_menu()
            return 0
        if self._taskbar_msg and msg == self._taskbar_msg:
            self._add()                      # 任务栏重启过（explorer 崩了）就补一个
            return 0
        if msg == WM_CLOSE:
            u.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            self.ok = False
            u.PostQuitMessage(0)
            return 0
        return u.DefWindowProcW(hwnd, msg, wp, lp)

    def _run(self):
        self._wire()
        u, k = self.U, self.K
        self._proc_ref = WNDPROC(self._wndproc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc_ref
        wc.hInstance = k.GetModuleHandleW(None)
        wc.lpszClassName = self.CLASS
        u.RegisterClassExW(ctypes.byref(wc))
        try:
            self._taskbar_msg = u.RegisterWindowMessageW("TaskbarCreated")
        except Exception:
            self._taskbar_msg = 0
        HWND_MESSAGE = ctypes.c_void_p(-3)
        self.hwnd = u.CreateWindowExW(0, self.CLASS, "小虫管理器", 0, 0, 0, 0, 0,
                                      HWND_MESSAGE, None, wc.hInstance, None)
        if not self.hwnd:
            self._ready.set()
            return
        self.ok = self._add()
        self._ready.set()
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        self.ok = False

    # ---------------- 对外 ----------------
    def start(self, wait=3.0):
        if self.hwnd:
            return self.ok
        threading.Thread(target=self._run, name="xc-tray", daemon=True).start()
        self._ready.wait(wait)
        return self.ok

    def balloon(self, title, text):
        if not self.hwnd:
            return False
        nid = self._nid()
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = (title or "")[:63]
        nid.szInfo = (text or "")[:255]
        nid.dwInfoFlags = NIIF_INFO
        return bool(self.S.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))

    def stop(self):
        if not self.hwnd:
            return
        try:
            self._wire()
            nid = self._nid()
            self.S.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self.U.PostMessageW(ctypes.c_void_p(self.hwnd), WM_CLOSE, 0, 0)
        except Exception:
            pass
        self.hwnd = 0
        self.ok = False


# --------------------------------------------------------------- 全局单例
_lock = threading.Lock()
_tray = None


def tray_on():
    return bool(_tray and _tray.ok)


def ensure_tray(on_open=None, on_quit=None, tip=None):
    """已经就有就不重复加；没有再补一个。"""
    global _tray
    with _lock:
        if _tray and _tray.ok:
            return True
        t = Tray(tip or "小虫管理器 · 本地资源管理器", on_open=on_open, on_quit=on_quit)
        if t.start():
            _tray = t
            return True
        return False


def remove_tray():
    global _tray
    with _lock:
        if _tray:
            _tray.stop()
            _tray = None


def tray_balloon(title, text):
    with _lock:
        if _tray:
            return _tray.balloon(title, text)
    return False
