# -*- coding: utf-8 -*-
"""资源管理器操作：浏览任意目录、复制/粘贴/剪切、回收站、压缩、打开任意文件。"""
import os
import re
import shutil
import string
import subprocess
import tempfile
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


def _assoc_exes(ext: str) -> list:
    """注册表里为该扩展名登记过的打开程序，按 Windows 的优先级排列。

    先找「ProgId」（用户在资源管理器里选的 UserChoice 优先），再拿 ProgId 去各个
    注册表根里找 shell\\open\\command。不检查文件是否存在，方便界面提示
    「登记的是哪个程序、在不在」。
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
    # 1) 用户在资源管理器里「始终用这个应用打开」选定的 ProgId
    uc = val(winreg.HKEY_CURRENT_USER,
             "SOFTWARE" + _R + "Microsoft" + _R + "Windows" + _R + "CurrentVersion"
             + _R + "Explorer" + _R + "FileExts" + _R + ext + _R + "UserChoice", "ProgId")
    if uc:
        progs.append(str(uc))
    # 2) 各注册表根下该扩展名登记的 ProgId
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
        exe = _exe_from_cmd(cmd or "")
        if not exe:
            continue
        real = _script_target(exe, cmd or "")   # wscript -> 脚本里真正的 exe
        for e2 in (real, exe):
            if e2 and e2 not in out:
                out.append(e2)
    return out


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


def program_for(path: str) -> str:
    """打开某个文件该用哪个程序：自定义规则 > .skp 用 SketchUp > 空（交给系统）。"""
    ext = config.ext_of(path)
    exe = custom_program(ext)
    if exe:
        return exe
    if ext == ".skp":
        return sketchup_exe()
    return ""


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
    """用合适的程序打开文件。

    顺序：设置里的「文件类型默认程序」 > .skp 用 SketchUp > 系统默认程序。
    返回 (ok, 说明)：成功时第二个值是「实际用的程序」，失败时是原因。
    """
    p = os.path.normpath(path)
    if os.path.isdir(p):
        os.startfile(p)  # noqa: S606   文件夹 → 资源管理器
        return True, "资源管理器"
    if not os.path.isfile(p):
        return False, "文件不存在：" + p
    exe = program_for(p)
    if exe:
        subprocess.Popen([exe, p])
        return True, exe
    if config.ext_of(p) == ".skp":
        tgt = assoc_target(".skp")
        why = ("注册表里 .skp 指向 %s，但这个文件不存在" % tgt) if tgt else "本机没找到 SketchUp.exe"
        return False, ("打不开 .skp：%s。\n"
                       "装好 / 修复 SketchUp 后就能直接打开；"
                       "也可以在「设置 → 文件类型默认程序」里给 .skp 指定程序。\n"
                       "临时看模型可以点详情里的 3D 看图。" % why)
    os.startfile(p)  # noqa: S606
    return True, "系统默认程序"


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