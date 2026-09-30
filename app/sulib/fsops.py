# -*- coding: utf-8 -*-
"""资源管理器操作：浏览任意目录、复制/粘贴/剪切、回收站、压缩、打开任意文件。"""
import os
import re
import shutil
import string
import subprocess
import tempfile
import threading
import time
import zipfile

from . import config, textutil

HIDDEN_ATTR = 0x2

_R = chr(92)
_CLASSES = "SOFTWARE" + _R + "Classes" + _R


def norm(p: str) -> str:
    s = (p or "").strip().strip('"')
    if re.fullmatch(r"[A-Za-z]:", s or ""):
        return s.upper() + "\\"
    s = os.path.abspath(os.path.expandvars(os.path.expanduser(s)))
    if s.endswith(":\\") or s == "\\":
        return s
    return s.rstrip("\\") or "\\"


def is_hidden(path: str) -> bool:
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        return attrs != -1 and bool(attrs & HIDDEN_ATTR)
    except Exception:
        return os.path.basename(path).startswith(".")


def _volume_label(root: str) -> str:
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        ctypes.windll.kernel32.GetVolumeInformationW(root, buf, 261, None, None, None, fs, 261)
        return buf.value
    except Exception:
        return ""


def drives():
    import ctypes
    out = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i, ch in enumerate(string.ascii_uppercase):
        if not (mask & (1 << i)):
            continue
        root = ch + ":\\"
        try:
            total, used, free = shutil.disk_usage(root)
        except OSError:
            continue
        out.append({"path": root, "name": ch + ": 盘", "label": _volume_label(root),
                    "free": free, "total": total, "kind": "drive"})
    return out


def quick_places():
    home = os.path.expanduser("~")
    cand = [("桌面", os.path.join(home, "Desktop")), ("下载", os.path.join(home, "Downloads")),
            ("文档", os.path.join(home, "Documents")), ("图片", os.path.join(home, "Pictures")),
            ("视频", os.path.join(home, "Videos")), ("音乐", os.path.join(home, "Music"))]
    out = [{"name": n, "path": p, "kind": "place"} for n, p in cand if os.path.isdir(p)]
    for r in config.get("roots", []):
        if os.path.isdir(r):
            out.append({"name": os.path.basename(r.rstrip("\\")) or r, "path": r, "kind": "root"})
    return out


def entry(path: str, is_dir: bool = False, with_stat: bool = True) -> dict:
    name = os.path.basename(path.rstrip("\\")) or path
    d = {"name": name, "path": path, "is_dir": is_dir, "kind": "folder" if is_dir else "other",
         "ext": "" if is_dir else config.ext_of(name), "size": 0, "mtime": 0}
    if not is_dir:
        d["kind"] = config.kind_of(name)
    if with_stat:
        try:
            st = os.stat(path)
            d["mtime"] = st.st_mtime
            if not is_dir:
                d["size"] = st.st_size
        except OSError:
            pass
    if is_dir:
        d["hidden"] = is_hidden(path)
    return d


def list_dir(path: str, show_hidden: bool = False, limit: int = 4000):
    path = norm(path)
    if not os.path.isdir(path):
        raise FileNotFoundError(path)
    dirs, files, counts = [], [], {}
    try:
        names = list(os.scandir(path))
    except OSError as e:
        raise OSError("无法读取目录：" + str(e))
    for e in names:
        try:
            hid = e.name.startswith(".") or is_hidden(e.path)
            if hid and not show_hidden:
                continue
            is_dir = e.is_dir(follow_symlinks=False)
            d = entry(e.path, is_dir, with_stat=True)
            d["hidden"] = hid
            if is_dir:
                dirs.append(d)
            else:
                files.append(d)
                counts[d["kind"]] = counts.get(d["kind"], 0) + 1
        except OSError:
            continue
    dirs.sort(key=lambda x: x["name"].lower())
    files.sort(key=lambda x: x["name"].lower())
    parent = os.path.dirname(path.rstrip("\\"))
    if not parent or parent == path:
        parent = ""
    return {"path": path, "parent": parent, "dirs": dirs[:limit], "files": files[:limit],
            "total": len(dirs) + len(files), "counts": counts,
            "free": (shutil.disk_usage(os.path.splitdrive(path)[0] + "\\").free
                     if os.path.splitdrive(path)[0] else 0)}


def crumbs(path: str):
    path = norm(path)
    drive, tail = os.path.splitdrive(path)
    parts = [p for p in tail.split("\\") if p]
    out, cur = [], ""
    if drive:
        cur = drive + "\\"
        out.append({"name": drive + "\\", "path": cur})
    for p in parts:
        cur = os.path.join(cur, p) if cur else p
        out.append({"name": p, "path": cur})
    return out

# ----------------------------------------------------------------- 读写操作
def unique_name(dest_dir: str, name: str) -> str:
    target = os.path.join(dest_dir, name)
    if not os.path.exists(target):
        return target
    stem, ext = os.path.splitext(name)
    for i in range(1, 100000):
        cand = os.path.join(dest_dir, "%s (%d)%s" % (stem, i, ext))
        if not os.path.exists(cand):
            return cand
    return os.path.join(dest_dir, "%s_%d%s" % (stem, int(time.time()), ext))


def copy_items(paths, dest_dir: str, move: bool = False, job=None):
    dest_dir = norm(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    ok, errors, out = 0, [], []
    total = len(paths)
    for i, src in enumerate(paths):
        try:
            src = norm(src)
            if not os.path.exists(src):
                raise RuntimeError("源文件不存在")
            if os.path.isdir(src) and os.path.normcase(dest_dir).startswith(
                    os.path.normcase(src) + os.sep):
                raise RuntimeError("不能把文件夹放进它自己的子目录")
            same_dir = os.path.normcase(os.path.dirname(src)) == os.path.normcase(dest_dir)
            if os.path.isdir(src):
                target = unique_name(dest_dir, os.path.basename(src)) if not move else (
                    os.path.join(dest_dir, os.path.basename(src)) if not same_dir
                    else unique_name(dest_dir, os.path.basename(src)))
                if move:
                    if os.path.exists(target):
                        target = unique_name(dest_dir, os.path.basename(src))
                    shutil.move(src, target)
                else:
                    shutil.copytree(src, target, dirs_exist_ok=True)
            else:
                target = unique_name(dest_dir, os.path.basename(src))
                if move:
                    shutil.move(src, target)
                else:
                    shutil.copy2(src, target)
            out.append(target)
            ok += 1
        except Exception as e:
            errors.append("%s: %s" % (os.path.basename(src), e))
        if job:
            job(i + 1, total, os.path.basename(src))
    return {"ok": ok, "errors": errors[:20], "files": out, "dest": dest_dir}


def rename_path(path: str, new_name: str, overwrite: bool = False):
    path = norm(path)
    if not os.path.exists(path):
        return False, "文件不存在"
    new_name = textutil.safe_filename(new_name, fallback=os.path.basename(path))
    if not os.path.splitext(new_name)[1] and os.path.splitext(path)[1] and not os.path.isdir(path):
        new_name += os.path.splitext(path)[1]
    target = os.path.join(os.path.dirname(path), new_name)
    if os.path.normcase(target) == os.path.normcase(path):
        return True, path
    if os.path.exists(target) and not overwrite:
        return False, "同名文件已存在"
    try:
        os.rename(path, target)
        return True, target
    except OSError as e:
        return False, str(e)


def mkdir(parent: str, name: str):
    name = textutil.safe_filename(name, fallback="新建文件夹")
    target = os.path.join(norm(parent), name)
    if os.path.exists(target):
        return False, "同名文件夹已存在"
    try:
        os.makedirs(target)
        return True, target
    except OSError as e:
        return False, str(e)


def _ps_quote(p: str) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def recycle(paths, job=None):
    """删除到回收站（可恢复），不做永久删除。"""
    paths = [norm(p) for p in paths if os.path.exists(norm(p))]
    if not paths:
        return {"ok": 0, "errors": ["没有可删除的项目"], "total": 0}
    lines = ["Add-Type -AssemblyName Microsoft.VisualBasic",
             "$ErrorActionPreference = 'Continue'",
             "$n = 0"]
    for i, p in enumerate(paths):
        var = "$p%d" % i
        lines.append("%s = %s" % (var, _ps_quote(p)))
        lines.append("if (Test-Path -LiteralPath %s) {" % var)
        lines.append("  if ((Get-Item -LiteralPath %s).PSIsContainer) {" % var)
        lines.append("    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
                     + var + ",'OnlyErrorDialogs','SendToRecycleBin')")
        lines.append("  } else {")
        lines.append("    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
                     + var + ",'OnlyErrorDialogs','SendToRecycleBin')")
        lines.append("  }")
        lines.append("  $n++")
        lines.append("}")
    lines.append("Write-Output $n")
    fd, sp = tempfile.mkstemp(suffix=".ps1", prefix="xc_recycle_")
    os.close(fd)
    try:
        with open(sp, "w", encoding="utf-8-sig") as f:
            f.write("\r\n".join(lines))
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", sp], capture_output=True, timeout=1800)
        out = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        n = int(out[-1]) if out and out[-1].strip().isdigit() else 0
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        return {"ok": n, "errors": [err[-300:]] if err else [], "total": len(paths)}
    except Exception as e:
        return {"ok": 0, "errors": [str(e)[:200]], "total": len(paths)}
    finally:
        try:
            os.remove(sp)
        except OSError:
            pass


def make_zip(paths, out_zip: str, job=None, compression=None):
    """把选中的文件/文件夹打包成 zip。"""
    if compression is None:
        compression = zipfile.ZIP_DEFLATED
    paths = [norm(p) for p in paths if os.path.exists(norm(p))]
    if not paths:
        return {"ok": False, "msg": "没有选中任何文件"}
    out_zip = norm(out_zip)
    os.makedirs(os.path.dirname(out_zip), exist_ok=True)
    if os.path.exists(out_zip):
        out_zip = unique_name(os.path.dirname(out_zip), os.path.basename(out_zip))
    n = 0
    with zipfile.ZipFile(out_zip, "w", compression) as zf:
        for p in paths:
            if os.path.isdir(p):
                parent = os.path.dirname(p)
                for dp, dn, fn in os.walk(p):
                    for f in fn:
                        full = os.path.join(dp, f)
                        try:
                            zf.write(full, os.path.relpath(full, parent))
                            n += 1
                        except OSError:
                            continue
                        if job and n % 40 == 0:
                            job(n, 0, f)
            else:
                try:
                    zf.write(p, os.path.basename(p))
                    n += 1
                except OSError:
                    continue
    return {"ok": True, "zip": out_zip, "count": n,
            "size": os.path.getsize(out_zip) if os.path.exists(out_zip) else 0}


# ----------------------------------------------------------------- 打开
# --------------------------------------------------------------- SketchUp
SKP_ROOTS = (r"C:\Program Files\SketchUp", r"D:\Program Files\SketchUp",
             r"C:\Program Files (x86)\SketchUp", r"D:\SketchUp",
             r"D:\Program Files (x86)\SketchUp")


def _exe_from_cmd(cmd: str) -> str:
    """从注册表命令串里抠出程序路径：去引号、去参数、展开 %SystemRoot% 这类变量。"""
    c = (cmd or "").strip()
    if not c:
        return ""
    m = re.match(r'\s*"([^"]+)"', c)
    if m:
        exe = m.group(1)
    else:
        # 没加引号：取到第一个 .exe 为止（避免把 "C:\Program Files\x.exe -a" 切成 "C:\Program"）
        m = re.match(r"\s*(.+?\.exe)(?=\s|$)", c, re.I)
        exe = m.group(1) if m else c.split(" ")[0].strip('"')
    return os.path.expandvars(exe).strip().strip('"')


_SCRIPT_HOSTS = ('wscript.exe', 'cscript.exe', 'mshta.exe', 'rundll32.exe',
                 'cmd.exe', 'powershell.exe', 'pwsh.exe')


def _script_target(exe: str, cmd: str) -> str:
    """命令是 wscript / cscript 这类脚本宿主时，进脚本里找它真正启动的 exe。

    SketchUp 2026 把 .skp 的打开命令登记成
    `wscript.exe ... SketchUpOpen.vbs "%1"`。直接显示 wscript 会让人以为
    「默认程序是 wscript」，真拿它当默认程序打开还会弹脚本错误。
    """
    if os.path.basename(exe or '').lower() not in _SCRIPT_HOSTS:
        return ""
    pat = r'"([^"]+\.(?:vbs|js|wsf))"|([A-Za-z]:\\[^\s"]+\.(?:vbs|js|wsf))'
    for m in re.finditer(pat, cmd or '', re.I):
        sp = m.group(1) or m.group(2)
        if not sp or not os.path.isfile(sp):
            continue
        try:
            with open(sp, encoding="utf-8-sig", errors="replace") as f:
                txt = f.read(300000)
        except Exception:
            continue
        for mm in re.finditer(r'([A-Za-z]:\\[^"\r\n;]{0,200}?\.exe)', txt, re.I):
            cand = os.path.expandvars(mm.group(1).strip())
            if os.path.isfile(cand) and os.path.basename(cand).lower() not in _SCRIPT_HOSTS:
                return cand
    return ""


def _assoc_cmds(ext: str) -> list:
    """注册表里为该扩展名登记的「打开命令」完整串（含 /photo /view 这类参数）。

    豆包（Doubao.Image）这类商店应用在注册表里没有命令串，所以这里查不到，
    说明只能交给系统 shell 去打开。优先级：用户选择 > HKCR > HKLM。
    """
    try:
        import winreg
    except ImportError:
        return []
    hkcr = getattr(winreg, "HKEY_CLASSES_ROOT", None)
    roots = [(winreg.HKEY_CURRENT_USER, _CLASSES)]
    if hkcr is not None:
        roots.append((hkcr, ""))
    roots.append((winreg.HKEY_LOCAL_MACHINE, _CLASSES))
    roots.append((winreg.HKEY_LOCAL_MACHINE, _CLASSES + "Wow6432Node" + _R))

    def val(hive, sub, name=None):
        try:
            with winreg.OpenKey(hive, sub) as k:
                return winreg.QueryValueEx(k, name)[0]
        except Exception:
            return None

    progs = []
    uc = val(winreg.HKEY_CURRENT_USER,
             "SOFTWARE" + _R + "Microsoft" + _R + "Windows" + _R + "CurrentVersion"
             + _R + "Explorer" + _R + "FileExts" + _R + ext + _R + "UserChoice", "ProgId")
    if uc:
        progs.append(str(uc))
    for hive, base in roots:
        prog = val(hive, (base + ext) if base else ext)
        if prog and str(prog) not in progs:
            progs.append(str(prog))

    out = []
    for prog in progs:
        cmd = None
        for hive, base in roots:
            for sub in ("shell" + _R + "open" + _R + "command",
                        "shell" + _R + "openas" + _R + "command"):
                cmd = val(hive, (base + prog + _R + sub) if base else (prog + _R + sub))
                if cmd:
                    break
            if cmd:
                break
        if cmd and str(cmd) not in out:
            out.append(str(cmd))
    return out


def _assoc_exes(ext: str) -> list:
    """注册表登记的打开程序（exe 路径），含 wscript 这类脚本宿主解析后的真身。"""
    out = []
    for cmd in _assoc_cmds(ext):
        exe = _exe_from_cmd(cmd)
        if not exe:
            continue
        real = _script_target(exe, cmd)
        for e2 in (real, exe):
            if e2 and e2 not in out:
                out.append(e2)
    return out


def _split_cmd(cmd: str) -> list:
    """把 '"C:/x.exe" /photo /view "%1"' 拆成 ['C:/x.exe', '/photo', '/view', '%1']。"""
    return [m.group(1) if m.group(1) is not None else m.group(2)
            for m in re.finditer(r'"([^"]*)"|(\S+)', cmd or "")]


def launch_with_command(cmd: str, path: str) -> str:
    """按注册表里的完整命令启动（保留 /photo /view 这类参数，替掉 %1 / %L）。

    只认第一段是真实存在 .exe 的命令；像 .exe 文件自己那种 "%1 %*" 会被拒绝，
    免得把用户选中的程序又启动一遍。返回实际用到的 exe，失败返回空串。
    """
    toks = [t for t in _split_cmd(cmd) if t]
    if not toks:
        return ""
    exe = os.path.expandvars(toks[0].strip().strip('"'))
    if not exe.lower().endswith(".exe") or not os.path.isfile(exe):
        return ""
    if os.path.normcase(exe) == os.path.normcase(os.path.abspath(path)):
        return ""
    args, hit = [], False
    for t in toks[1:]:
        if re.search(r"%[1lLs*]", t):
            args.append(re.sub(r"%[1lLs*]", lambda m: path, t))
            hit = True
        else:
            args.append(t)
    if not hit:
        args.append(path)
    subprocess.Popen([exe] + args)
    return exe


_PHOTO_VIEWER_DIRS = (r"C:\Program Files\Windows Photo Viewer",
                      r"C:\Program Files (x86)\Windows Photo Viewer")

# 看图软件下拉里「系统自带」那一项在配置里存这个标记
PHOTO_VIEWER_ID = "@photoviewer"

_FRIENDLY_NAMES = {
    "photolaunch": "WPS 图片查看器", "wps": "WPS", "et": "WPS 表格", "wpp": "WPS 演示",
    "sketchup": "SketchUp", "photoshop": "Photoshop", "acad": "AutoCAD",
    "notepad": "记事本", "mspaint": "画图", "explorer": "资源管理器",
    "wmplayer": "Windows Media Player", "7zfm": "7-Zip", "winrar": "WinRAR",
    "acdsee": "ACDSee", "honeyview": "Honeyview", "irfanview": "IrfanView",
    "xnview": "XnView", "jpegview": "JPEGView", "photos": "Windows 照片",
    "rundll32": "Windows 照片查看器",
    "cadreader": "CAD快速看图", "cadreader-editor": "CAD快速看图（编辑器）",
}


def friendly_name(exe: str) -> str:
    """把 exe 路径变成用户看得懂的名字，例如 photolaunch.exe → WPS 图片查看器。"""
    base = os.path.basename(str(exe or "").strip().strip('"'))
    if not base:
        return ""
    key = base[:-4].lower() if base.lower().endswith(".exe") else base.lower()
    return _FRIENDLY_NAMES.get(key, base)


def photo_viewer() -> tuple:
    """Windows 自带的「照片查看器」：(rundll32.exe, PhotoViewer.dll)，没有则空串。"""
    sysroot = os.environ.get("SystemRoot") or r"C:\Windows"
    rundll = os.path.join(sysroot, "System32", "rundll32.exe")
    if not os.path.isfile(rundll):
        return "", ""
    for d in _PHOTO_VIEWER_DIRS:
        dll = os.path.join(d, "PhotoViewer.dll")
        if os.path.isfile(dll):
            return rundll, dll
    return "", ""


def photo_viewer_available() -> bool:
    return bool(photo_viewer()[0])


def launch_photo_viewer(path: str) -> bool:
    """用系统自带的照片查看器打开图片（不依赖任何第三方看图软件）。"""
    rundll, dll = photo_viewer()
    if not rundll:
        return False
    try:
        subprocess.Popen('"%s" "%s",ImageView_Fullscreen "%s"' % (rundll, dll, path))
        return True
    except Exception:
        return False


def image_viewer_spec() -> tuple:
    """用户设置的看图软件 → ('exe', 路径) / ('photo', 自带) / ('', '')。"""
    v = str(config.get("image_viewer") or "").strip().strip('"')
    if not v:
        return "", ""
    if v.lower() in ("@photoviewer", "photoviewer"):
        return ("photo", "") if photo_viewer_available() else ("", "")
    return ("exe", v) if os.path.isfile(v) else ("", "")


def _visible_windows() -> list:
    """当前所有「可见且有标题」的顶层窗口句柄，按 z 序（最前面在前）。"""
    out = []
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        cb = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def each(hwnd, _):
            if u.IsWindowVisible(hwnd) and u.GetWindowTextLengthW(hwnd):
                out.append(int(hwnd))
            return True

        u.EnumWindows(cb(each), 0)
    except Exception:
        pass
    return out


def raise_later(before, seconds: float = 8.0) -> None:
    """把刚弹出来的窗口提到最前（有些看图/文档程序是共享单实例，自己不抢焦点）。

    用户点了「打开」却看不到任何反应，多半就是这个原因，所以这里补一下。
    只提升「调用之后新出现的」窗口，不会乱动别的窗口。
    """
    before = set(before or ())

    def work():
        try:
            import ctypes
            u = ctypes.windll.user32
        except Exception:
            return
        t0, hwnd = time.time(), 0
        while time.time() - t0 < seconds:
            time.sleep(0.35)
            for h in _visible_windows():
                if h not in before:
                    hwnd = h
                    break
            if hwnd:
                break
        if not hwnd:
            return
        try:
            u.ShowWindow(hwnd, 9)                                  # SW_RESTORE
            u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)   # 临时置顶
            u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)   # 取消置顶
            u.SetForegroundWindow(hwnd)
        except Exception:
            pass

    threading.Thread(target=work, daemon=True).start()


def _launch_exe(exe: str, path: str) -> tuple:
    """用指定程序打开文件，并尽量把窗口提到最前。"""
    before = _visible_windows()
    subprocess.Popen([exe, path])
    raise_later(before)
    return True, friendly_name(exe)


def _assoc_exe(ext: str) -> str:
    """注册表登记的默认程序里，第一个确实存在的。"""
    for exe in _assoc_exes(ext):
        if os.path.isfile(exe):
            return exe
    return ""


def system_default(ext: str) -> dict:
    """系统为该扩展名登记的默认程序（registered 可能不存在，用于提示）。"""
    ext = config.norm_ext(ext)
    regs = _assoc_exes(ext) if ext else []
    usable = ""
    for e in regs:
        if os.path.isfile(e):
            usable = e
            break
    return {"ext": ext, "registered": regs[0] if regs else "",
            "exe": usable, "all": regs}


def custom_program(ext: str) -> str:
    """用户在「设置 → 文件类型默认程序」里为某个扩展名指定的程序。"""
    rules = config.get("open_with") or {}
    if not isinstance(rules, dict):
        return ""
    exe = str(rules.get(config.norm_ext(ext)) or "").strip().strip('"')
    return exe if exe and os.path.isfile(exe) else ""


# ------------------------------------------------- 内置默认程序（开箱即用）
# 「CAD 快速看图」的常见安装位置：.dwg 这类图纸默认就用它打开
CAD_READER_CANDIDATES = (
    r"C:\Program Files (x86)\CADReader\CADReader.exe",
    r"C:\Program Files\CADReader\CADReader.exe",
    r"D:\Program Files (x86)\CADReader\CADReader.exe",
    r"D:\Program Files\CADReader\CADReader.exe",
    r"D:\CADReader\CADReader.exe",
)
CAD_EXT = (".dwg", ".dxf", ".dwt", ".dwf")


def cad_reader_exe() -> str:
    """找「CAD 快速看图」主程序：先看常见安装目录，再看注册表里 .dwg 登记的程序。"""
    for c in CAD_READER_CANDIDATES:
        if os.path.isfile(c):
            return c
    for ext in (".dwg", ".dxf"):
        for e in _assoc_exes(ext):
            if os.path.basename(e).lower() == "cadreader.exe" and os.path.isfile(e):
                return e
    return ""


def builtin_program(ext: str) -> str:
    """内置认的默认程序：CAD 图纸 → CAD 快速看图（本机没装就返回空，回落到系统默认）。"""
    if config.norm_ext(ext) in CAD_EXT:
        return cad_reader_exe()
    return ""


def builtin_open_with() -> list:
    """给设置界面显示用：内置默认程序现在认到哪个程序；没认到就返回空列表。"""
    exe = cad_reader_exe()
    if not exe:
        return []
    return [{"ext": ".dwg", "exts": list(CAD_EXT),
             "name": friendly_name(exe) or "CAD 快速看图", "exe": exe}]


def program_for(path: str) -> str:
    """打开某个文件该用哪个程序：自定义规则 > 内置默认 > 看图软件 > .skp 用 SketchUp > 空。"""
    ext = config.ext_of(path)
    exe = custom_program(ext) or builtin_program(ext)
    if exe:
        return exe
    if ext in config.IMAGE_EXT and image_viewer_spec()[0] == "exe":
        return image_viewer_spec()[1]
    if ext == ".skp":
        return sketchup_exe()
    return ""


def viewer_status() -> dict:
    """给界面看的「看图软件」状态。"""
    kind, val = image_viewer_spec()
    sysdef = system_default(".jpg")
    return {"kind": kind, "exe": val,
            "photo_builtin": photo_viewer_available(),
            "system_default": friendly_name(sysdef.get("exe") or "")
                             or (sysdef.get("registered") or ""),
            "system_default_exe": sysdef.get("exe") or ""}


def sketchup_exe() -> str:
    """找本机 SketchUp 主程序：设置 > 安装目录 > 注册表关联。"""
    manual = str(config.get("sketchup_exe") or "").strip().strip('"')
    if manual and os.path.isfile(manual):
        return manual
    cands = []
    try:
        from . import skp3d            # 与读图用的 SketchUpAPI.dll 同目录
        d = skp3d.find_sketchup_dir()
        if d:
            cands.append(os.path.join(d, "SketchUp.exe"))
    except Exception:
        pass
    for root in SKP_ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            for name in os.listdir(root):
                cands.append(os.path.join(root, name, "SketchUp", "SketchUp.exe"))
                cands.append(os.path.join(root, name, "SketchUp.exe"))
        except OSError:
            pass
    cands.append(_assoc_exe(".skp"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ""


def assoc_target(ext: str) -> str:
    """注册表里该扩展名名义上指向哪个程序（可能存在也可能不存在，用于报错提示）。"""
    regs = _assoc_exes(config.norm_ext(ext))
    return regs[0] if regs else ""


def open_with_default(path: str) -> tuple:
    """用合适的程序打开文件，并尽量把窗口提到最前。

    顺序：① 该扩展名单独指定的程序 → ② 看图软件（图片）→ ③ .skp 用 SketchUp
    → ④ 注册表里登记的完整命令（保留 /photo /view 这类参数）→ ⑤ 交给系统默认。
    返回 (ok, 说明)，说明里会写清「用了哪个程序」，用户一眼能看出到底开没开。
    """
    p = os.path.normpath(path)
    if os.path.isdir(p):
        os.startfile(p)  # noqa: S606  文件夹 → 资源管理器
        return True, "资源管理器"
    if not os.path.isfile(p):
        return False, "文件不存在：" + p
    ext = config.ext_of(p)
    # ① 用户单独指定的程序 → 其次是内置认的（.dwg 这类图纸用 CAD 快速看图）
    exe = custom_program(ext) or builtin_program(ext)
    if exe:
        return _launch_exe(exe, p)
    # ② 图片：看用户指定的看图软件
    if ext in config.IMAGE_EXT:
        kind, val = image_viewer_spec()
        if kind == "exe":
            return _launch_exe(val, p)
        if kind == "photo":
            before = _visible_windows()
            if launch_photo_viewer(p):
                raise_later(before)
                return True, "Windows 照片查看器"
    # ③ .skp 用 SketchUp
    if ext == ".skp":
        su = sketchup_exe()
        if su:
            return _launch_exe(su, p)
    # ④ 注册表里登记的完整命令（带参数启动，比直接 startfile 可靠得多）
    for cmd in _assoc_cmds(ext):
        before = _visible_windows()
        used = launch_with_command(cmd, p)
        if not used:
            continue
        raise_later(before)
        return True, friendly_name(_script_target(used, cmd) or used) or used
    if ext == ".skp":
        tgt = assoc_target(".skp")
        why = ("注册表把 .skp 指给了 %s，但那个文件不存在" % tgt) if tgt else "本机没找到 SketchUp.exe"
        return False, ("打不开 .skp（%s）。\n"
                       "装好 / 修好 SketchUp 就能直接打开，"
                       "也可以在「设置 → 文件关联」里给 .skp 指定程序。\n"
                       "临时想看模型，可以在小虫里点「3D 看图」直接预览，不用装 SketchUp。"
                       % why)
    # ⑤ 交给系统默认（Windows 商店应用，比如豆包 / 系统照片，走这条路）
    before = _visible_windows()
    os.startfile(p)  # noqa: S606
    raise_later(before)
    d = system_default(ext)
    return True, (friendly_name(d.get("exe") or "") or "系统默认程序")


def open_path(path: str, mode: str = "open", job=None):
    """打开 / 定位 / 打开方式 / 属性。返回 (ok, 说明)：成功时说明是「用哪个程序打开」或「做了什么」。"""
    path = norm(path)
    if not os.path.exists(path):
        return False, "路径不存在"
    try:
        if mode == "reveal":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return True, "已在资源管理器中定位"
        if mode == "folder":
            subprocess.Popen(["explorer", os.path.normpath(
                path if os.path.isdir(path) else os.path.dirname(path))])
            return True, "已打开所在文件夹"
        if mode == "openas":
            subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL",
                              os.path.normpath(path)])
            return True, "已打开「打开方式」窗口"
        if mode == "properties":
            subprocess.Popen(["rundll32.exe", "shell32.dll,ShellExec_RunDLL",
                              "properties", os.path.normpath(path)])
            return True, "已打开属性窗口"
        return open_with_default(path)
    except Exception as e:
        return False, str(e)


def text_head(path: str, limit: int = 262144, inner: str = "", source: str = ""):
    from . import archives
    src = source or path
    if inner:
        data = archives.read_head(src, inner, limit)
    else:
        with open(src, "rb") as f:
            data = f.read(limit)
    text, enc_used = None, "utf-8"
    for enc in ("utf-8-sig", "utf-8", "gbk", "big5", "latin-1"):
        try:
            text = data.decode(enc)
            enc_used = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", "replace")
        enc_used = "utf-8(replace)"
    try:
        size = os.path.getsize(src)
    except OSError:
        size = 0
    return {"text": text, "encoding": enc_used, "truncated": size > limit,
            "bytes": len(data), "size": size}


def dir_stats(path: str, limit_sec: float = 4.0):
    """粗略统计文件夹大小（超时即返回已统计部分）。"""
    total, files, t0 = 0, 0, time.time()
    for dp, dn, fn in os.walk(norm(path)):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
                files += 1
            except OSError:
                continue
        if time.time() - t0 > limit_sec:
            return {"size": total, "files": files, "partial": True}
    return {"size": total, "files": files, "partial": False}

# ----------------------------------------------------------------- 看图软件候选
def _find_exe(names, roots, depth: int = 3) -> list:
    """在几个安装目录里按文件名找 exe（限深度，避免扫盘）。"""
    out = []
    names = {n.lower() for n in names}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dp, dns, fns in os.walk(root):
            if dp[len(root):].count(_R) >= depth:
                dns[:] = []
                continue
            for f in fns:
                if f.lower() in names:
                    out.append(os.path.join(dp, f))
    return out


def viewer_candidates() -> list:
    """本机能用来看图的程序候选（给「看图软件」下拉用）。"""
    cands = []

    def add(name, exe, note=""):
        exe = (exe or "").strip()
        if not exe:
            return
        if exe != PHOTO_VIEWER_ID and not os.path.isfile(exe):
            return
        if any(c["exe"].lower() == exe.lower() for c in cands):
            return
        cands.append({"name": name, "exe": exe, "note": note})

    if photo_viewer_available():
        add("Windows 自带的照片查看器", PHOTO_VIEWER_ID, "不用另装软件，最稳")
    d = system_default(".jpg")
    add("系统默认看图程序（%s）" % (friendly_name(d.get("exe") or "") or d.get("registered") or "未知"),
        d.get("exe") or "", "双击图片时 Windows 用的那个")
    for p in _find_exe(("photolaunch.exe",),
                       (r"D:\Program Files\WPS Office", r"C:\Program Files\WPS Office",
                        r"C:\Program Files (x86)\WPS Office"), 3):
        add("WPS 图片查看器", p, "看 jpg/png/psd 都行")
    for p in _find_exe(("photoshop.exe",),
                       (r"D:\Program Files\Adobe", r"C:\Program Files\Adobe",
                        r"D:\Program Files\Adobe Photoshop 2024",
                        r"C:\Program Files\Adobe Photoshop 2024"), 3):
        add("Adobe Photoshop", p, "和 PSD 一起用")
    for p in _find_exe(("honeyview.exe", "irfanview.exe", "jpegview.exe", "xnview.exe"),
                       (r"D:\Program Files", r"C:\Program Files", r"C:\Program Files (x86)"), 2):
        add(friendly_name(p), p)
    sysroot = os.environ.get("SystemRoot") or r"C:\Windows"
    add("Windows 画图", os.path.join(sysroot, "System32", "mspaint.exe"), "临时看 / 简单改")
    return cands
