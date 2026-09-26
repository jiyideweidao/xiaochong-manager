"""对素材的实际操作：导出/复制到剪贴板/解压/批量重命名/打开。"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile

from . import archives, config, db, fsops, textutil, thumbs

JOBS = {}
_JOBS_LOCK = threading.Lock()


# ----------------------------------------------------------------- 任务进度
def job_start(kind: str, title: str, total: int = 0) -> str:
    jid = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        JOBS[jid] = {"id": jid, "kind": kind, "title": title, "status": "running",
                     "total": total, "done": 0, "msg": "", "t0": time.time(),
                     "ended": 0, "errors": []}
    cur = db.ex("INSERT INTO jobs(kind,title,status,total,done,msg,started_at) "
                "VALUES(?,?,?,?,0,'',?)", (kind, title, "running", total, time.time()))
    with _JOBS_LOCK:
        JOBS[jid]["row_id"] = cur.lastrowid
    return jid


def job_update(jid: str, **kw):
    with _JOBS_LOCK:
        j = JOBS.get(jid)
        if not j:
            return
        for k, v in kw.items():
            if k in ("error", "err"):
                j["errors"].append(str(v))
                if len(j["errors"]) > 60:
                    j["errors"] = j["errors"][-60:]
            elif k == "errors":
                continue
            elif k == "current":
                j["msg"] = str(v)[-120:]
            else:
                j[k] = v


def job_finish(jid: str, status: str = "done", msg: str = ""):
    with _JOBS_LOCK:
        j = JOBS.get(jid)
        if j:
            j["status"] = status
            j["msg"] = msg
            j["ended"] = time.time()
    rid = (JOBS.get(jid) or {}).get("row_id")
    if rid:
        db.ex("UPDATE jobs SET status=?,msg=?,done=?,ended_at=? WHERE id=?",
              (status, msg, (JOBS.get(jid) or {}).get("done", 0), time.time(), rid))


def job_get(jid: str):
    with _JOBS_LOCK:
        j = JOBS.get(jid)
        if not j:
            return None
        d = {k: v for k, v in j.items() if k not in ("errors", "row_id")}
        d["errors"] = list(j["errors"])[-6:]
        return d


def job_list():
    with _JOBS_LOCK:
        return [{k: v for k, v in j.items() if k not in ("errors", "row_id")}
                for j in sorted(JOBS.values(), key=lambda x: -x["t0"])[:12]]


# ----------------------------------------------------------------- 文件名
def asset_filename(a) -> str:
    base = textutil.safe_filename(a["name"] or "未命名")
    ext = a["ext"] or os.path.splitext(a["orig_name"] or "")[1] or ".skp"
    return base + ext


def unique_path(dest: str, filename: str) -> str:
    target = os.path.join(dest, filename)
    if not os.path.exists(target):
        return target
    stem, ext = os.path.splitext(filename)
    for i in range(1, 10000):
        cand = os.path.join(dest, f"{stem} ({i}){ext}")
        if not os.path.exists(cand):
            return cand
    return os.path.join(dest, f"{stem}_{uuid.uuid4().hex[:6]}{ext}")


# ----------------------------------------------------------------- 提取
def extract_one(asset, target_path: str, bundle: bool = False) -> str:
    """把一条素材落成磁盘上的真实文件，返回路径。"""
    src, inner = asset["source_path"], asset["inner_path"] or ""
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if not inner:
        shutil.copy2(src, target_path)
        return target_path
    # 注意：一定要走 archives.open_inner —— 它能把老式 GBK 中文名还原后再定位条目。
    # 直接 zf.open(inner) 在中文名压缩包上会 KeyError，导致「打不开」。
    if archives.archive_kind(src) in ("zip", "tar"):
        fh = archives.open_inner(src, inner)
        if fh is not None:
            try:
                with fh, open(target_path, "wb") as out:
                    shutil.copyfileobj(fh, out, 1024 * 1024)
                return target_path
            except Exception:
                try:
                    os.remove(target_path)
                except OSError:
                    pass
    tmp = tempfile.mkdtemp(prefix="suex_", dir=str(config.STAGE_DIR))
    try:
        if not archives.extract(src, tmp, inner=inner):
            raise RuntimeError("解压失败")
        want = os.path.basename(inner.replace("\\", "/"))
        hits = [os.path.join(r, f) for r, _, fs in os.walk(tmp) for f in fs
                if f.lower() == want.lower()]
        if not hits:
            raise RuntimeError("压缩包内未找到该文件")
        shutil.move(hits[0], target_path)
        return target_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def export_assets(ids, dest: str, bundle: bool = False):
    """把选中的素材导出（=复制）到目标文件夹。"""
    dest = os.path.abspath(dest)
    os.makedirs(dest, exist_ok=True)
    out, errors = [], []
    for a in db.q(f"SELECT * FROM assets WHERE id IN ({_ph(ids)})", tuple(ids)):
        try:
            target = unique_path(dest, asset_filename(a))
            extract_one(a, target, bundle)
            out.append(target)
            if bundle and a["inner_path"]:
                _copy_siblings(a, os.path.dirname(target))
        except Exception as e:
            errors.append(f"{a['name']}: {e}")
    return {"ok": len(out), "errors": errors, "files": out}


def _copy_siblings(asset, dest: str):
    """连同压缩包内同目录的贴图/说明文件一起复制。"""
    try:
        entries = archives.list_archive(asset["source_path"])
    except Exception:
        return
    folder = os.path.dirname(asset["inner_path"].replace("/", "\\"))
    if not folder:
        return
    for ent in entries:
        inner = ent["inner"]
        if ent.get("is_dir") or inner == asset["inner_path"]:
            continue
        if os.path.dirname(inner.replace("/", "\\")).lower() != folder.lower():
            continue
        name = os.path.basename(inner)
        try:
            tmp_asset = dict(asset)
            tmp_asset["inner_path"] = inner
            tmp_asset["ext"] = os.path.splitext(name)[1]
            tmp_asset["name"] = os.path.splitext(name)[0]
            extract_one(tmp_asset, unique_path(dest, name), False)
        except Exception:
            pass


def stage_assets(ids):
    """把素材提取到暂存目录（用于剪贴板/临时打开）。"""
    stage = config.STAGE_DIR / time.strftime("stage_%Y%m%d_%H%M%S")
    stage.mkdir(parents=True, exist_ok=True)
    return export_assets(ids, str(stage))


# ----------------------------------------------------------------- 压缩包
def extract_archives(paths, dest: str, per_folder: bool = True, delete_source: bool = False,
                     job_id: str = ""):
    dest = os.path.abspath(dest)
    os.makedirs(dest, exist_ok=True)
    ok, errors = 0, []
    for i, p in enumerate(paths):
        try:
            target = os.path.join(dest, os.path.splitext(os.path.basename(p))[0]) if per_folder else dest
            if not archives.extract(p, target):
                raise RuntimeError("解压失败")
            ok += 1
            if delete_source:
                shutil.rmtree(os.path.dirname(p), ignore_errors=True)
        except Exception as e:
            errors.append(f"{os.path.basename(p)}: {e}")
        if job_id:
            job_update(job_id, done=i + 1, msg=os.path.basename(p))
    return {"ok": ok, "errors": errors}


# ----------------------------------------------------------------- 批量重命名
def build_names(assets, rules):
    """根据规则为每条素材算出新名字（不含扩展名）。"""
    find = rules.get("find", "")
    repl = rules.get("replace", "")
    use_regex = bool(rules.get("regex"))
    prefix = rules.get("prefix", "")
    suffix = rules.get("suffix", "")
    num_on = bool(rules.get("numbering"))
    num_start = int(rules.get("num_start") or 1)
    num_pad = int(rules.get("num_pad") or 2)
    num_sep = rules.get("num_sep", "-")
    use_display = bool(rules.get("use_display"))
    drop_ads = bool(rules.get("drop_ads"))
    fix_moji = bool(rules.get("fix_moji"))
    case_mode = rules.get("case", "keep")
    out = []
    for i, a in enumerate(assets):
        stem = a["name"] if use_display else (a["orig_name"] or a["name"])
        stem = os.path.splitext(stem)[0]
        if fix_moji:
            stem = textutil.repair(stem)
        if drop_ads:
            stem = textutil.clean(stem)
        if find:
            try:
                stem = re.sub(find, repl, stem) if use_regex else stem.replace(find, repl)
            except re.error:
                pass
        if case_mode == "lower":
            stem = stem.lower()
        elif case_mode == "upper":
            stem = stem.upper()
        if num_on:
            num = str(num_start + i).zfill(num_pad)
            stem = f"{stem}{num_sep}{num}" if stem else num
        stem = f"{prefix}{stem}{suffix}"
        out.append(textutil.safe_filename(stem, fallback=f"未命名{i+1:03d}"))
    return out


def rename_preview(ids, rules):
    assets = db.q(f"SELECT * FROM assets WHERE id IN ({_ph(ids)}) ORDER BY id", tuple(ids))
    assets = [dict(a) for a in assets]
    names = build_names(assets, rules)
    rows = []
    for a, n in zip(assets, names):
        old = asset_filename(a)
        new = n + (a["ext"] or "")
        rows.append({"id": a["id"], "kind": a["kind"], "source_type": a["source_type"],
                     "old": old, "new": new, "changed": old != new,
                     "inner_path": a["inner_path"], "source_path": a["source_path"],
                     "origin": a["origin"]})
    return rows


def rename_apply(ids, rules, job_id=""):
    rows = rename_preview(ids, rules)
    batch = time.strftime("%Y%m%d_%H%M%S")
    logs, ok, skipped = [], 0, 0
    for i, r in enumerate(rows):
        if not r["changed"]:
            skipped += 1
            continue
        a = dict(db.q("SELECT * FROM assets WHERE id=?", (r["id"],), one=True))
        scope = "archive" if a["inner_path"] else "file"
        try:
            if scope == "file":
                folder = os.path.dirname(a["source_path"])
                dest = os.path.join(folder, r["new"])
                if os.path.exists(dest):
                    raise RuntimeError("同名文件已存在")
                os.rename(a["source_path"], dest)
                db.ex("UPDATE assets SET source_path=?, name=?, ext=?, cover_key=? WHERE id=?",
                      (dest, os.path.splitext(r["new"])[0], a["ext"],
                       textutil.cover_key(dest), a["id"]))
            else:
                new_inner = os.path.join(os.path.dirname(a["inner_path"]), r["new"]) \
                    if os.path.dirname(a["inner_path"]) else r["new"]
                good, msg = archives.rename_inner(a["source_path"], a["inner_path"], new_inner)
                if not good:
                    raise RuntimeError(msg)
                db.ex("UPDATE assets SET inner_path=?, name=?, orig_name=? WHERE id=?",
                      (new_inner, os.path.splitext(r["new"])[0], r["new"], a["id"]))
            ok += 1
            logs.append((batch, time.time(), scope, a["source_path"], r["old"], r["new"], 1, ""))
        except Exception as e:
            logs.append((batch, time.time(), scope, a["source_path"], r["old"], r["new"], 0, str(e)[:180]))
        if job_id:
            job_update(job_id, done=i + 1, msg=r["new"])
    if logs:
        db.exmany("INSERT INTO rename_log(batch,ts,scope,container,old_name,new_name,ok,msg) "
                  "VALUES(?,?,?,?,?,?,?,?)", logs)
    errs = [l[7] for l in logs if not l[6]]
    return {"batch": batch, "ok": ok, "skipped": skipped, "errors": errs[:10], "failed": len(errs)}


def rename_undo(batch: str):
    logs = db.q("SELECT * FROM rename_log WHERE batch=? AND ok=1 ORDER BY id DESC", (batch,))
    ok, errors = 0, []
    for l in logs:
        try:
            if l["scope"] == "file":
                cur = os.path.join(os.path.dirname(l["container"]), l["new_name"])
                back = os.path.join(os.path.dirname(l["container"]), l["old_name"])
                if os.path.exists(cur) and not os.path.exists(back):
                    os.rename(cur, back)
                    row = db.q("SELECT id FROM assets WHERE source_path=?", (cur,), one=True)
                    if row:
                        db.ex("UPDATE assets SET source_path=?, name=?, cover_key=? WHERE id=?",
                              (back, os.path.splitext(l["old_name"])[0], textutil.cover_key(back), row["id"]))
                    ok += 1
            else:
                row = db.q("SELECT * FROM assets WHERE source_path=? AND inner_path=?",
                           (l["container"], l["new_name"]), one=True)
                if row:
                    good, msg = archives.rename_inner(l["container"], l["new_name"], l["old_name"])
                    if good:
                        db.ex("UPDATE assets SET inner_path=?, name=?, orig_name=? WHERE id=?",
                              (l["old_name"], os.path.splitext(l["old_name"])[0], l["old_name"], row["id"]))
                        ok += 1
                    else:
                        errors.append(msg)
        except Exception as e:
            errors.append(str(e)[:120])
    return {"ok": ok, "errors": errors[:8]}


# ----------------------------------------------------------------- 打开 / 定位
def open_asset(asset_id: int, mode: str = "open"):
    a = db.q("SELECT * FROM assets WHERE id=?", (asset_id,), one=True)
    if not a:
        return False, "素材不存在"
    a = dict(a)
    try:
        if a["source_type"] == "loose" and os.path.exists(a["source_path"]):
            path = a["source_path"]
        else:
            r = export_assets([asset_id], str(config.STAGE_DIR / "open"))
            if not r["files"]:
                return False, (r["errors"] or ["提取失败"])[0]
            path = r["files"][0]
        # 打开/定位/打开方式/属性 全部走同一套逻辑（含「文件类型默认程序」）
        return fsops.open_path(path, mode)
    except Exception as e:
        return False, str(e)


def reveal_archive(path: str):
    try:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        return True, path
    except Exception as e:
        return False, str(e)


# ----------------------------------------------------------------- 其它
# ----------------------------------------------------------------- 系统对话框
def _ps_exe() -> str:
    """powershell.exe 的绝对路径（打包后没有 python.exe，开对话框只能靠它）。"""
    p = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                     "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return p if os.path.isfile(p) else "powershell.exe"


def _ps_q(v) -> str:
    """包成 PowerShell 单引号字符串字面量。"""
    return "'" + str(v).replace("'", "''") + "'"


def _ps_pick(kind: str, title: str, filt: str = "") -> str:
    """用 PowerShell + WinForms 弹系统对话框，返回选中的路径（取消 = 空串）。

    不能用 `sys.executable -c` 那套：打包成 exe 之后 sys.executable 就是小虫管理器自己，
    拿它跑 Python 代码只会又启动一遍服务 + 多开一个浏览器页，对话框根本不出现。
    """
    out = os.path.join(tempfile.gettempdir(), f"supick_{uuid.uuid4().hex[:8]}.txt")
    ps1 = os.path.join(tempfile.gettempdir(), f"supick_{uuid.uuid4().hex[:8]}.ps1")
    lines = [
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null",
        "Add-Type -AssemblyName System.Drawing | Out-Null",
        "$f = New-Object System.Windows.Forms.Form",
        "$f.TopMost = $true; $f.ShowInTaskbar = $false; $f.FormBorderStyle = 'None'",
        "$f.Opacity = 0; $f.Size = New-Object System.Drawing.Size(1,1)",
        "$f.StartPosition = 'CenterScreen'; $f.Show(); $f.Activate()",
        "$d = New-Object System.Windows.Forms.%s" % ("OpenFileDialog" if kind == "file" else "FolderBrowserDialog"),
        "$d.Title = %s" % _ps_q(title),
    ]
    if kind == "file":
        lines += ["$d.Filter = %s" % _ps_q(filt or "所有文件 (*.*)|*.*"),
                  "$d.CheckFileExists = $true",
                  "$d.RestoreDirectory = $true"]
    lines += ["if ($d.ShowDialog($f) -eq [System.Windows.Forms.DialogResult]::OK) {",
              "  $v = %s" % ("$d.FileName" if kind == "file" else "$d.SelectedPath"),
              "  [IO.File]::WriteAllText(%s, $v)" % _ps_q(out),
              "}",
              "$f.Close()"]
    try:
        with open(ps1, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write("\n".join(lines))
        subprocess.run([_ps_exe(), "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", ps1],
                       timeout=900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if os.path.exists(out):
            val = open(out, encoding="utf-8").read().strip()
            os.unlink(out)
            return val
    except Exception:
        pass
    finally:
        try:
            if os.path.isfile(ps1):
                os.unlink(ps1)
        except Exception:
            pass
    return ""


def pick_folder(title: str = "选择文件夹") -> str:
    """弹系统「选择文件夹」对话框（返回空串 = 取消）。"""
    return _ps_pick("dir", title)


def pick_file(title: str = "选择程序", ext: str = ".exe") -> str:
    """弹系统「选择文件」对话框挑一个程序（返回空串 = 取消）。"""
    pats = [x.strip() for x in (ext or ".exe").replace(";", ",").split(",") if x.strip()]
    pats = [x if x.startswith("*") else ("*" + (x if x.startswith(".") else "." + x))
            for x in pats]
    label = (pats[0].lstrip("*").lstrip(".").upper() + " 程序") if pats else "程序"
    filt = "%s|%s|所有文件 (*.*)|*.*" % (label, ";".join(pats) or "*.*")
    return _ps_pick("file", title, filt)

def disk_free(path: str) -> int:
    try:
        return shutil.disk_usage(os.path.splitdrive(os.path.abspath(path))[0] + "\\").free
    except Exception:
        return 0


def _ph(ids) -> str:
    return ",".join("?" * len(ids))

def preview_path(asset, job=None) -> str:
    """返回可以直接读取/播放的本地文件路径（压缩包内文件先取到缓存目录）。"""
    src = asset.get("source_path") or ""
    inner = asset.get("inner_path") or ""
    if not src or not os.path.exists(src):
        return ""
    if not inner:
        return src
    name = os.path.basename(inner.replace("\\", "/")) or "file"
    key = thumbs.cache_key(src, inner, int(asset.get("mtime") or 0),
                           int(asset.get("size") or 0))
    d = config.STAGE_DIR / "preview"
    d.mkdir(parents=True, exist_ok=True)
    out = d / (key + "_" + textutil.safe_filename(name, "file"))
    want = int(asset.get("size") or 0)
    try:
        if out.exists() and (not want or out.stat().st_size == want):
            return str(out)
    except OSError:
        pass
    try:
        extract_one({"source_path": src, "inner_path": inner}, str(out), False)
        return str(out)
    except Exception:
        return ""


def preview_dir_size() -> int:
    d = config.STAGE_DIR / "preview"
    total = 0
    if d.is_dir():
        for root, _, files in os.walk(d):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    return total