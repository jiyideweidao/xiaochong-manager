
# -*- coding: utf-8 -*-
"""压缩包读取 / 解压 / 改名。

分工：
  * zip、tar 系列 —— 走 Python 标准库，随程序内嵌，不依赖任何外部程序
  * rar、7z、iso、cab 等 —— 走随程序内嵌的 7-Zip（app\\bin\\7z.exe）
两者对用户透明，界面上不区分，用的都是「小虫管理器」自己的解压内核。
"""
import os
import shutil
import subprocess
import tarfile
import zipfile

from . import config

# 中文压缩包老问题：早期 Windows 压缩工具把 GBK 文件名直接当字节写进 zip，
# 又不打 UTF-8 标记，Python 会按 cp437 解出乱码。这里还原回来。
_UTF8_FLAG = 0x800

NO_SEVENZIP = ("未找到解压组件。小虫管理器自带 7-Zip，正常无需另装；"
               "若杀毒软件误删了程序目录下的 bin\\7z.exe，请重新安装，"
               "或在「设置」里手动指定 7z.exe 路径。")
TAR_SUFFIX = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz", ".tbz2", ".txz")


def is_archive(path: str) -> bool:
    name = os.path.basename(path)
    if name.lower().endswith(TAR_SUFFIX):
        return True
    return os.path.splitext(name)[1].lower() in config.ARCHIVE_EXT


def archive_kind(path: str) -> str:
    """zip / tar / sevenz —— 决定用哪种方式打开。"""
    name = os.path.basename(path).lower()
    ext = os.path.splitext(name)[1]
    if ext == ".zip":
        return "zip"
    if ext == ".tar" or name.endswith(TAR_SUFFIX):
        return "tar"
    return "sevenz"


def seven_zip() -> str:
    return config.get("seven_zip") or config.detect_seven_zip()


def require_sevenzip() -> str:
    sz = seven_zip()
    if not sz or not os.path.isfile(sz):
        raise RuntimeError(NO_SEVENZIP)
    return sz


def _run(args, timeout=180):
    return subprocess.run([require_sevenzip()] + args, capture_output=True,
                          timeout=timeout)


# ------------------------------------------------------------------ 名字修正
def _fix_name(info) -> str:
    nm = info.filename
    if info.flag_bits & _UTF8_FLAG:
        return nm
    try:
        return nm.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return nm


def _norm(inner: str) -> str:
    return (inner or "").replace("\\", "/").strip("/")


def _zip_pairs(zf: zipfile.ZipFile):
    """[(显示名, ZipInfo)]，显示名已修正中文乱码。"""
    return [(_fix_name(i), i) for i in zf.infolist()]


def _zip_find(zf: zipfile.ZipFile, inner: str):
    """按名字找条目；先按修正名找，再回退原名（兼容旧索引）。"""
    want = _norm(inner)
    for name, info in _zip_pairs(zf):
        if name == want or _norm(name) == want:
            return info
    try:
        return zf.getinfo(inner)
    except KeyError:
        return None


def _safe_join(dest: str, name: str):
    """防止 zip-slip：解压目标必须落在 dest 里面。"""
    rel = _norm(name)
    if not rel:
        return ""
    out = os.path.normpath(os.path.join(dest, *rel.split("/")))
    root = os.path.normpath(dest)
    if out != root and not out.startswith(root + os.sep):
        return ""
    return out


# ------------------------------------------------------------------ 列表
def list_archive(path: str):
    """返回 [{'inner','size','mtime','is_dir'}]。"""
    kind = archive_kind(path)
    if kind == "zip":
        return _list_zip(path)
    if kind == "tar":
        return _list_tar(path)
    return _list_7z(path)


def _list_zip(path: str):
    out = []
    with zipfile.ZipFile(path) as zf:
        for name, info in _zip_pairs(zf):
            out.append({"inner": name, "size": info.file_size,
                        "mtime": info.date_time, "is_dir": info.is_dir()})
    return out


def _list_tar(path: str):
    out = []
    with tarfile.open(path, "r:*") as tf:
        for m in tf.getmembers():
            if m.isdir():
                continue
            out.append({"inner": m.name, "size": m.size, "mtime": None,
                        "is_dir": False})
    return out


def _list_7z(path: str):
    res = _run(["l", "-ba", "-slt", "-sccUTF-8", path])
    text = res.stdout.decode("utf-8", "replace")
    if "Path = " not in text:
        text = res.stdout.decode("gbk", "replace")
    rows, cur = [], {}
    for line in text.splitlines():
        if line.startswith("Path = "):
            if cur:
                rows.append(cur)
            cur = {"inner": line[7:], "size": 0, "mtime": None, "is_dir": False}
        elif cur:
            if line.startswith("Size = "):
                try:
                    cur["size"] = int(line[7:].strip())
                except ValueError:
                    cur["size"] = 0
            elif line.startswith("Folder = "):
                cur["is_dir"] = line[9:].strip() == "+"
    if cur:
        rows.append(cur)
    return [r for r in rows if r["inner"] != path]


# ------------------------------------------------------------------ 读流
class _InnerFile:
    """压缩包内成员的可读流；close() 会一并关掉容器。"""

    def __init__(self, container, fh):
        self._container, self._fh = container, fh

    def read(self, n=-1):
        return self._fh.read(n)

    def close(self):
        for x in (self._fh, self._container):
            try:
                x.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def open_inner(src: str, inner: str):
    """打开压缩包内某个成员，返回可读流；不支持或出错时返回 None。"""
    kind = archive_kind(src)
    try:
        if kind == "zip":
            zf = zipfile.ZipFile(src)
            try:
                info = _zip_find(zf, inner)
                if info is None:
                    raise KeyError(inner)
                return _InnerFile(zf, zf.open(info))
            except Exception:
                zf.close()
                raise
        if kind == "tar":
            tf = tarfile.open(src, "r:*")
            try:
                m = tf.getmember(_norm(inner))
                fh = tf.extractfile(m) if m else None
                if fh is None:
                    raise KeyError(inner)
                return _InnerFile(tf, fh)
            except Exception:
                tf.close()
                raise
    except Exception:
        return None
    return None


def read_head(path: str, inner: str, nbytes: int = 262144) -> bytes:
    """只读取压缩包内某个文件的前 nbytes 字节（不解整包）。"""
    kind = archive_kind(path)
    if kind in ("zip", "tar"):
        fh = open_inner(path, inner)
        if fh is None:
            raise RuntimeError("压缩包内找不到该文件，或压缩包已损坏")
        try:
            return fh.read(nbytes)
        finally:
            fh.close()
    sz = require_sevenzip()
    proc = subprocess.Popen([sz, "x", "-so", "-y", path, inner],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        return proc.stdout.read(nbytes)
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.kill()


# ------------------------------------------------------------------ 解压
def extract(path: str, dest: str, inner: str = "", overwrite: bool = True,
            timeout: int = 7200) -> bool:
    """解压压缩包（可只解某个内部路径）到 dest。"""
    os.makedirs(dest, exist_ok=True)
    kind = archive_kind(path)
    if kind == "zip":
        try:
            return _extract_zip(path, dest, inner, overwrite)
        except NotImplementedError:
            pass              # 加密 zip 之类，交给内嵌 7-Zip
    if kind == "tar":
        return _extract_tar(path, dest, inner, overwrite)
    args = ["x", path, "-o" + dest, "-y", "-sccUTF-8"]
    if not overwrite:
        args.append("-aos")
    if inner:
        args.append(inner)
    return _run(args, timeout=timeout).returncode == 0


def _extract_zip(path: str, dest: str, inner: str, overwrite: bool) -> bool:
    want = _norm(inner)
    with zipfile.ZipFile(path) as zf:
        pairs = _zip_pairs(zf)
        if want:
            sel = [(n, i) for n, i in pairs
                   if _norm(n) == want or _norm(n).startswith(want + "/")]
            if not sel:
                raise RuntimeError("压缩包内没有 " + inner)
        else:
            sel = pairs
        for name, info in sel:
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:          # 加密条目
                raise NotImplementedError("加密 zip")
            target = _safe_join(dest, name)
            if not target:
                continue
            if os.path.exists(target) and not overwrite:
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 512 * 1024)
    return True


def _extract_tar(path: str, dest: str, inner: str, overwrite: bool) -> bool:
    want = _norm(inner)
    with tarfile.open(path, "r:*") as tf:
        members = [m for m in tf.getmembers()
                   if not m.isdir() and (not want or _norm(m.name) == want
                                         or _norm(m.name).startswith(want + "/"))]
        if want and not members:
            raise RuntimeError("压缩包内没有 " + inner)
        for m in members:
            target = _safe_join(dest, m.name)
            if not target:
                continue
            if os.path.exists(target) and not overwrite:
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tf.extractfile(m)
            if src is None:
                continue
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 512 * 1024)
    return True


# ------------------------------------------------------------------ 改名 / 校验
def rename_inner(path: str, inner: str, new_name: str) -> tuple:
    """重命名压缩包内条目（zip/7z 可写）。返回 (ok, message)。"""
    if os.path.splitext(path)[1].lower() == ".rar":
        return False, "RAR 为只读格式，无法改写其中的文件名"
    try:
        sz = require_sevenzip()
    except RuntimeError as e:
        return False, str(e)
    res = subprocess.run([sz, "rn", path, inner, new_name, "-sccUTF-8"],
                         capture_output=True, timeout=600)
    if res.returncode == 0:
        return True, "ok"
    msg = (res.stderr or res.stdout).decode("gbk", "replace").strip()
    return False, msg.splitlines()[-1] if msg else "重命名失败"


def test_archive(path: str) -> tuple:
    """校验压缩包是否可完整解压。"""
    kind = archive_kind(path)
    if kind == "zip":
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            return (bad is None), ("全部条目完好" if bad is None else "损坏：" + str(bad))
    if kind == "tar":
        try:
            with tarfile.open(path, "r:*") as tf:
                for m in tf.getmembers():
                    if m.isfile():
                        fh = tf.extractfile(m)
                        if fh:
                            while fh.read(1 << 20):
                                pass
            return True, "全部条目完好"
        except Exception as e:
            return False, str(e)[:200]
    res = _run(["t", "-sccUTF-8", path], timeout=7200)
    return res.returncode == 0, (res.stdout or b"").decode("gbk", "replace")[-400:]
