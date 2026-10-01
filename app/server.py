# -*- coding: utf-8 -*-
"""小虫管理器 本地服务（FastAPI）。只监听 127.0.0.1，浏览器即界面。"""
import mimetypes
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import (FileResponse, JSONResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from sulib import (archives, config, db, fsops, ops, scanner, skp3d,
                    textutil, thumbs)
import winui  # 本目录下的窗口 / 任务栏托盘小工具（纯 ctypes）

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = str(config.STATIC_DIR) if os.path.isdir(str(config.STATIC_DIR)) else os.path.join(HERE, "static")
THUMB_SEM = threading.Semaphore(6)
STATE = {"scanning": False, "last_stats": None, "scan_job": ""}
CLIP = {"paths": [], "mode": "copy"}

app = FastAPI(title=config.APP_NAME, docs_url=None, redoc_url=None)


# ------------------------------------------------------------------ 基础
def _row(r):
    return {k: r[k] for k in r.keys()}


def all_roots():
    """读取资源库根目录；首次使用时用配置文件播种，避免误删。"""
    rows = db.q("SELECT path FROM roots")
    if not rows:
        for p in config.load().get("roots", []):
            db.ex("INSERT OR IGNORE INTO roots(path,enabled,added_at) VALUES(?,1,?)",
                  (p, time.time()))
        rows = db.q("SELECT path FROM roots")
    return [r["path"] for r in rows]


def preview_kind(name: str, kind: str = "") -> str:
    ext = config.ext_of(name)
    kind = kind or config.kind_of(name)
    # .skp 用内置 3D 看图（不需要装 SketchUp，程序直接读几何）
    if ext == ".skp":
        return "model3d"
    if kind in ("image", "psd") and ext not in (".svg", ".eps", ".ai", ".cdr"):
        return "image"
    if kind == "vector" and ext in (".svg",):
        return "svg"
    if kind == "video":
        return "video"
    if kind == "audio":
        return "audio"
    if ext == ".pdf":
        return "pdf"
    if ext in config.TEXT_EXT:
        return "text"
    if kind == "archive":
        return "archive"
    if kind == "font":
        return "font"
    return "none"


@app.get("/favicon.ico")
def favicon():
    f = config.RES_DIR / "小虫.ico"
    if f.exists():
        return FileResponse(str(f), media_type="image/x-icon")
    return Response(status_code=204)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/state")
def api_state():
    cfg = config.load()
    counts = {r["kind"]: r["c"] for r in db.q("SELECT kind, COUNT(*) c FROM assets GROUP BY kind")}
    return {
        "app": config.APP_NAME,
        "sub": config.APP_SUB,
        "version": config.APP_VERSION,
        "roots": [{"path": p, "enabled": 1} for p in all_roots()],
        "counts": counts,
        "total": db.count("SELECT COUNT(*) FROM assets"),
        "models": counts.get("model", 0),
        "images": counts.get("image", 0) + counts.get("psd", 0),
        "archives": db.count("SELECT COUNT(*) FROM archive_meta"),
        "favorites": db.count("SELECT COUNT(*) FROM assets WHERE favorite=1"),
        "thumbs_ready": db.count("SELECT COUNT(*) FROM assets WHERE thumb_status='ok'"),
        "thumbs_pending": db.count("SELECT COUNT(*) FROM assets WHERE thumb_status='pending'"),
        "thumb_dir": str(config.THUMB_DIR),
        "data_dir": str(config.DATA_DIR),
        "seven_zip": cfg.get("seven_zip") or "",
        "seven_zip_bundled": os.path.isfile(str(config.BUNDLED_7Z)),
        "skp3d": skp3d.status(),
        "sketchup": fsops.sketchup_exe(),
        "sketchup_assoc": fsops.assoc_target(".skp"),
        "open_with": _open_with_list(cfg),
        "builtin_open_with": fsops.builtin_open_with(),
        "viewer": fsops.viewer_status(),
        "cache": ops.cache_stats(),
        "ffmpeg": cfg.get("ffmpeg") or "",
        "settings": {k: cfg.get(k) for k in ("thumb_max_px", "workers", "index_images",
                                            "image_max_mb", "index_all_files",
                                            "index_inside_archives", "max_file_mb",
                                            "text_preview_kb", "sketchup_exe",
                                            "image_viewer", "cache_remind_on",
                                            "cache_remind_once", "cache_remind_min",
                                            "cache_limit_mb", "close_action",
                                            "image_thumbs", "show_thumbs", "show_path",
                                            "loaded_root")},
        "disk_free": ops.disk_free(str(config.DATA_DIR)),
        "scanning": STATE["scanning"],
        "scanning_stats": STATE["last_stats"],
        "ui": _ui_state(),
    }


@app.get("/api/kinds")
def api_kinds():
    counts = {r["kind"]: r["c"] for r in db.q("SELECT kind, COUNT(*) c FROM assets GROUP BY kind")}
    items = [{"key": k, "label": config.KIND_LABEL.get(k, k),
              "color": config.KIND_COLOR.get(k, "#6b7280"),
              "count": counts.get(k, 0), "present": 1 if counts.get(k) else 0}
             for k in config.KIND_ORDER]
    extra = [{"key": k, "label": config.KIND_LABEL.get(k, k),
              "color": config.KIND_COLOR.get(k, "#6b7280"), "count": c, "present": 1}
             for k, c in counts.items() if k not in config.KIND_ORDER]
    return {"kinds": items + extra, "total": db.count("SELECT COUNT(*) FROM assets")}


@app.get("/api/facets")
def api_facets(kind: str = "", category: str = "", style: str = "", q: str = "", fav: int = 0):
    # 分类 / 风格：还是按「模型」统计（原来就这样，别改语义）
    cats = [{"value": r["category"], "count": r["c"]} for r in
            db.q("SELECT category, COUNT(*) c FROM assets WHERE kind='model' AND category<>'' "
                 "GROUP BY category ORDER BY c DESC")]
    styles = [{"value": r["style"], "count": r["c"]} for r in
              db.q("SELECT style, COUNT(*) c FROM assets WHERE kind='model' AND style<>'' "
                   "GROUP BY style ORDER BY c DESC")]
    # 按文件夹：跟着当前的「类型 / 分类 / 风格 / 搜索词 / 收藏」走，
    # 这样点开 3D模型（或效果图、图纸）之后，能把这一大堆按所在文件夹拆开
    where, args = ["folder <> ''"], []
    if kind == "fav" or fav:
        where.append("favorite = 1")
    elif kind and kind != "all":
        where.append("kind = ?")
        args.append(kind)
    for col, val in (("category", category), ("style", style)):
        if val:
            where.append(f"{col} = ?")
            args.append(val)
    if q.strip():
        like = f"%{q.strip()}%"
        where.append("(name LIKE ? OR orig_name LIKE ? OR folder LIKE ? OR category LIKE ? "
                     "OR tags LIKE ? OR origin LIKE ? OR ext LIKE ? OR source_path LIKE ?)")
        args += [like] * 8
    w = " AND ".join(where)
    folders = [{"value": r["folder"], "count": r["c"]} for r in
               db.q(f"SELECT folder, COUNT(*) c FROM assets WHERE {w} "
                    f"GROUP BY folder ORDER BY c DESC, folder COLLATE NOCASE ASC LIMIT 800", tuple(args))]
    folder_total = db.count(f"SELECT COUNT(DISTINCT folder) FROM assets WHERE {w}", tuple(args))
    return {"categories": cats, "styles": styles, "folders": folders, "folder_total": folder_total}


# ------------------------------------------------------------------ 列表
SORTS = {"name": "name COLLATE NOCASE ASC", "name_desc": "name COLLATE NOCASE DESC",
         "size": "size DESC", "size_asc": "size ASC", "new": "mtime DESC",
         "old": "mtime ASC", "category": "category COLLATE NOCASE ASC, name ASC",
         "kind": "kind ASC, name COLLATE NOCASE ASC", "random": "RANDOM()",
         "fav": "favorite DESC, fav_at DESC, name COLLATE NOCASE ASC"}


@app.get("/api/assets")
def api_assets(q: str = "", kind: str = "", category: str = "", style: str = "",
               origin: str = "", folder: str = "", fav: int = 0, has_render: int = 0,
               source: str = "", sort: str = "category",
               offset: int = 0, limit: int = Query(80, le=500)):
    where, args = ["1=1"], []
    if q.strip():
        like = f"%{q.strip()}%"
        where.append("(name LIKE ? OR orig_name LIKE ? OR folder LIKE ? OR category LIKE ? "
                     "OR tags LIKE ? OR origin LIKE ? OR ext LIKE ? OR source_path LIKE ?)")
        args += [like] * 8
    for col, val in (("kind", kind), ("category", category), ("style", style),
                     ("origin", origin), ("folder", folder), ("source_type", source)):
        if val:
            where.append(f"{col} = ?")
            args.append(val)
    if fav:
        where.append("favorite = 1")
    if has_render:
        where.append("render_id > 0")
    w = " AND ".join(where)
    total = db.count(f"SELECT COUNT(*) FROM assets WHERE {w}", tuple(args))
    order = SORTS.get(sort, SORTS["category"])
    rows = db.q(f"SELECT * FROM assets WHERE {w} ORDER BY {order} LIMIT ? OFFSET ?",
                tuple(args) + (limit, offset))
    items = []
    for r in rows:
        d = _row(r)
        d["preview"] = preview_kind(d.get("orig_name") or d.get("name") or "", d.get("kind") or "")
        items.append(d)
    return {"total": total, "offset": offset, "limit": limit, "items": items}


@app.get("/api/asset/{aid}")
def api_asset(aid: int):
    r = db.q("SELECT * FROM assets WHERE id=?", (aid,), one=True)
    if not r:
        raise HTTPException(404, "素材不存在")
    a = _row(r)
    if a.get("render_id"):
        rr = db.q("SELECT id,name,size,source_path,inner_path,ext FROM assets WHERE id=?",
                  (a["render_id"],), one=True)
        a["render"] = _row(rr) if rr else None
    a["filename"] = ops.asset_filename(a)
    a["preview"] = preview_kind(a.get("orig_name") or a.get("name") or "", a.get("kind") or "")
    a["preview_url"] = f"/api/raw?id={aid}"
    a["text_url"] = f"/api/text?id={aid}"
    a["collection_images"] = [_row(x) for x in db.q(
        "SELECT id,name,size FROM assets WHERE kind='image' AND cover_key=? AND cover_key<>'' "
        "AND id<>? ORDER BY size DESC LIMIT 9", (a.get("cover_key") or "\x00", a.get("render_id") or 0))]
    a["siblings"] = [_row(x) for x in db.q(
        "SELECT id,name,render_id FROM assets WHERE kind='model' AND source_path=? AND folder=? "
        "AND id<>? LIMIT 24", (a["source_path"], a["folder"], aid))]
    return a


@app.get("/api/thumb/{aid}")
def api_thumb(aid: int, size: int = 0, render: int = 1):
    r = db.q("SELECT * FROM assets WHERE id=?", (aid,), one=True)
    if not r:
        raise HTTPException(404)
    a = _row(r)
    p = thumbs.path_for(a)
    if not (os.path.exists(p) and os.path.getsize(p) > 0):
        with THUMB_SEM:
            p2, status, msg = thumbs.get(a, size, bool(render))
        if not p2:
            raise HTTPException(404, msg or "无缩略图")
        db.ex("UPDATE assets SET thumb_status=?, thumb_msg=? WHERE id=?", (status, msg, aid))
        p = p2
    return FileResponse(p, media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/thumb-path")
def api_thumb_path(path: str, size: int = 0):
    """为磁盘上任意文件现算一张缩略图（浏览模式用）。"""
    p = fsops.norm(path)
    if not os.path.exists(p):
        raise HTTPException(404, "文件不存在")
    if os.path.isdir(p):
        raise HTTPException(404, "文件夹没有缩略图")
    ent = fsops.entry(p, False)
    asset = {"source_path": p, "inner_path": "", "orig_name": ent["name"],
             "kind": ent["kind"], "size": ent["size"], "mtime": ent["mtime"],
             "id": 0, "render_id": 0}
    with THUMB_SEM:
        out, status, msg = thumbs.get(asset, size, True)
    if not out:
        raise HTTPException(404, msg or "无缩略图")
    return FileResponse(out, media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=604800"})


# ------------------------------------------------------------- SKP 3D 看图
def _skp_source(id: int, path: str, inner: str):
    """定位要预览的 .skp，返回 (源文件, 包内路径, mtime, size, 显示名)。"""
    if id:
        r = db.q("SELECT * FROM assets WHERE id=?", (id,), one=True)
        if not r:
            raise HTTPException(404, "素材不存在")
        a = _row(r)
        src = a["source_path"]
        inner = a.get("inner_path") or ""
        name = os.path.basename(inner or src)
        mtime, size = float(a.get("mtime") or 0), int(a.get("size") or 0)
    else:
        src = fsops.norm(path)
        if not os.path.isfile(src):
            raise HTTPException(404, "文件不存在")
        st = os.stat(src)
        mtime, size = st.st_mtime, st.st_size
        name = os.path.basename(inner or src)
        if not inner and not src.lower().endswith(".skp"):
            raise HTTPException(400, "只有 .skp 文件能用内置 3D 看图")
    if not name.lower().endswith(".skp"):
        raise HTTPException(400, "这不是 SketchUp 模型（.skp）")
    if inner and archives.archive_kind(src) not in ("zip", "tar"):
        raise HTTPException(400, "zip / tar 里的 skp 能直接预览，rar / 7z 请先用「解压」取出")
    return src, inner, float(mtime or 0), int(size or 0), name


def _skp_build(jid, src, inner, mtime, size):
    try:
        ops.job_update(jid, msg="正在读取模型几何…")
        out, st, cached = skp3d.build(str(config.DATA_DIR), src, inner, mtime, size)
        msg = ("已缓存" if cached else
               "转换完成：%s 个三角面 / %.1f MB"
               % (format(st.get("tris", 0), ","), os.path.getsize(out) / 1048576))
        ops.job_update(jid, done=1, total=1, msg=msg)
        ops.job_finish(jid, "done", msg)
    except Exception as e:
        ops.job_finish(jid, "error", str(e)[:300])


@app.get("/api/skp3d-status")
def api_skp3d_status():
    return skp3d.status()


@app.post("/api/model3d/prepare")
def api_model3d_prepare(payload: dict = Body(...)):
    """先把 .skp 转成内置网格（可能几秒），返回任务号供前端看进度。"""
    src, inner, mtime, size, name = _skp_source(int(payload.get("id") or 0),
                                                payload.get("path") or "",
                                                payload.get("inner") or "")
    if not skp3d.available():
        raise HTTPException(400, skp3d.status().get("why") or "3D 看图不可用")
    out = skp3d.cache_path(str(config.DATA_DIR), src, inner, mtime, size)
    if os.path.isfile(out) and os.path.getsize(out) > 64:
        return {"ok": True, "cached": True, "name": name}
    jid = ops.job_start("model3d", "生成 3D 预览 · " + name, 1)
    threading.Thread(target=_skp_build, args=(jid, src, inner, mtime, size),
                     daemon=True).start()
    return {"ok": True, "cached": False, "job": jid, "name": name}


@app.get("/api/model3d")
def api_model3d(id: int = 0, path: str = "", inner: str = ""):
    """返回内置 3D 网格（XCM3 二进制），前端用 three.js 显示。"""
    src, inner, mtime, size, name = _skp_source(id, path, inner)
    if not skp3d.available():
        raise HTTPException(400, skp3d.status().get("why") or "3D 看图不可用")
    try:
        out, st, cached = skp3d.build(str(config.DATA_DIR), src, inner, mtime, size)
    except Exception as e:
        raise HTTPException(400, "生成 3D 预览失败：%s" % str(e)[:200])
    return FileResponse(out, media_type="application/octet-stream",
                        headers={"Cache-Control": "public, max-age=86400",
                                 "X-XCM-Cached": "1" if cached else "0",
                                 "X-XCM-Tris": str(st.get("tris", 0))})


# ------------------------------------------------------------------ 索引
@app.post("/api/roots")
def api_roots(payload: dict = Body(...)):
    action = payload.get("action", "add")
    path = (payload.get("path") or "").strip().strip('"')
    all_roots()          # 先播种，避免把原有根目录挤掉
    if action == "add":
        if not os.path.isdir(path):
            return {"ok": False, "msg": "目录不存在"}
        db.ex("INSERT OR REPLACE INTO roots(path,enabled,added_at) VALUES(?,1,?)",
              (fsops.norm(path), time.time()))
        cfg = config.load()
        cfg["roots"] = all_roots()
        config.save(cfg)
        return {"ok": True, "roots": cfg["roots"]}
    if action == "remove":
        db.ex("DELETE FROM roots WHERE path=?", (path,))
        cfg = config.load()
        cfg["roots"] = all_roots()
        config.save(cfg)
        return {"ok": True, "roots": cfg["roots"]}
    return {"ok": False, "msg": "未知操作"}


def _run_scan(jid: str, roots, force: bool = False):
    STATE["scanning"] = True
    try:
        sc = scanner.Scanner(progress=lambda **kw: ops.job_update(jid, **kw), force=force)
        stats = sc.run(roots)
        STATE["last_stats"] = stats
        ops.job_update(jid, done=len(roots), total=len(roots),
                       msg=f"完成：共 {stats['files']} 个文件，索引 {sum(v for k, v in stats.items() if k.startswith('k_'))} 条")
        for e in sc.errors[:8]:
            ops.job_update(jid, error=e)
        ops.job_finish(jid, "done" if not sc.errors else "warn")
    except Exception as e:
        ops.job_finish(jid, "error", str(e)[:300])
    finally:
        STATE["scanning"] = False


@app.post("/api/scan")
def api_scan(payload: dict = Body(default={})):
    if STATE["scanning"]:
        return {"ok": False, "msg": "正在扫描中，请稍候"}
    roots = payload.get("roots") or all_roots()
    roots = [r for r in roots if os.path.isdir(r)]
    if not roots:
        return {"ok": False, "msg": "没有可扫描的目录，请先添加"}
    jid = ops.job_start("scan", "扫描资源库", len(roots))
    threading.Thread(target=_run_scan, args=(jid, roots, bool(payload.get("force"))),
                     daemon=True).start()
    return {"ok": True, "job": jid}


def _run_prefetch(jid, kind="", ids=()):
    try:
        args = []
        where = []
        if ids:
            where.append("id IN (%s)" % ",".join("?" * len(ids)))
            args += list(ids)
        elif kind:
            where.append("kind = ?")
            args.append(kind)
        elif not bool(config.get("image_thumbs", False)):
            # 没指定范围时跳过图片：按设置图片不生成缩略图（点开看原图）
            where.append("kind <> 'image'")
        sql = ("SELECT * FROM assets WHERE " + " AND ".join(where) if where else
               "SELECT * FROM assets")
        sql += " ORDER BY CASE WHEN thumb_status='ok' THEN 1 ELSE 0 END,"
        sql += " CASE WHEN render_id>0 THEN 0 ELSE 1 END, size ASC"
        rows = [_row(r) for r in db.q(sql, tuple(args))]
        rows = [r for r in rows if not os.path.exists(thumbs.path_for(r))]
        ops.job_update(jid, total=len(rows), msg=f"待生成 {len(rows)} 张")
        done, fails = 0, 0
        lock = threading.Lock()

        def work(a):
            nonlocal done, fails
            p = thumbs.get(a, 0, True)
            with lock:
                done += 1
                if not p[0]:
                    fails += 1
                db.ex("UPDATE assets SET thumb_status=?, thumb_msg=? WHERE id=?",
                      (p[1], p[2], a["id"]))
                if done % 25 == 0:
                    ops.job_update(jid, done=done, msg=f"{done}/{len(rows)}，失败 {fails}")

        with _pool(config.get("workers", 8)) as pool:
            list(pool.map(work, rows))
        ops.job_update(jid, done=len(rows), msg=f"完成，失败 {fails}")
        ops.job_finish(jid, "done")
    except Exception as e:
        ops.job_finish(jid, "error", str(e)[:300])


def _pool(n):
    from concurrent.futures import ThreadPoolExecutor
    return ThreadPoolExecutor(max_workers=max(1, min(int(n or 8), 24)))


@app.post("/api/prefetch")
def api_prefetch(payload: dict = Body(default={})):
    kind = payload.get("kind", "")
    ids = payload.get("ids") or []
    jid = ops.job_start("prefetch", "生成缩略图", 0)
    threading.Thread(target=_run_prefetch, args=(jid, kind, ids), daemon=True).start()
    return {"ok": True, "job": jid}


@app.get("/api/jobs")
def api_jobs():
    return {"jobs": ops.job_list(), "scanning": STATE["scanning"]}


@app.get("/api/job/{jid}")
def api_job(jid: str):
    j = ops.job_get(jid)
    return j or {"error": "任务不存在"}


# ------------------------------------------------------------------ 素材操作
@app.post("/api/export")
def api_export(payload: dict = Body(...)):
    ids = payload.get("ids") or []
    dest = payload.get("dest") or ""
    if not ids:
        return {"ok": 0, "errors": ["未选择素材"]}
    if not dest:
        dest = str(config.DATA_DIR / "导出")
    return ops.export_assets(ids, dest, bool(payload.get("bundle")))


@app.post("/api/stage")
def api_stage(payload: dict = Body(...)):
    """把素材库里的素材提取到暂存目录，供「浏览文件」里粘贴。"""
    ids = payload.get("ids") or []
    if not ids:
        return {"ok": False, "msg": "未选择素材"}
    r = ops.stage_assets(ids)
    if not r["files"]:
        return {"ok": False, "msg": (r["errors"] or ["提取失败"])[0]}
    return {"ok": True, "msg": f"已提取 {len(r['files'])} 个文件",
            "files": r["files"], "stage": str(config.STAGE_DIR)}


@app.get("/api/archives")
def api_archives(q: str = "", offset: int = 0, limit: int = Query(100, le=500)):
    where, args = ["1=1"], []
    if q.strip():
        where.append("path LIKE ?")
        args.append(f"%{q.strip()}%")
    w = " AND ".join(where)
    rows = db.q(f"""
        SELECT m.path, m.size, m.mtime,
               (SELECT COUNT(*) FROM assets a WHERE a.source_path=m.path) items,
               (SELECT COUNT(*) FROM assets a WHERE a.source_path=m.path AND a.kind='model') models
        FROM archive_meta m WHERE {w} ORDER BY models DESC, m.size DESC LIMIT ? OFFSET ?""",
        tuple(args) + (limit, offset))
    total = db.count(f"SELECT COUNT(*) FROM archive_meta m WHERE {w}", tuple(args))
    return {"total": total, "items": [{**_row(r), "name": os.path.basename(r["path"])} for r in rows]}


@app.get("/api/archive-entries")
def api_archive_entries(path: str, limit: int = Query(3000, le=20000)):
    p = fsops.norm(path)
    if not os.path.isfile(p):
        raise HTTPException(404, "压缩包不存在")
    try:
        entries = archives.list_archive(p)
    except Exception as e:
        raise HTTPException(400, str(e)[:200])
    files = [e for e in entries if not e.get("is_dir")]
    return {"path": p, "total": len(files), "items": files[:limit]}


@app.post("/api/archive-extract-one")
def api_archive_extract_one(payload: dict = Body(...)):
    """把压缩包里的某一个文件解压到指定文件夹。"""
    arc = fsops.norm(payload.get("archive") or "")
    inner = payload.get("inner") or ""
    dest = fsops.norm(payload.get("dest") or "")
    if not os.path.isfile(arc) or not inner or not dest:
        return {"ok": False, "msg": "参数不完整"}
    os.makedirs(dest, exist_ok=True)
    try:
        if not archives.extract(arc, dest, inner=inner):
            raise RuntimeError("解压失败")
        return {"ok": True, "dest": dest}
    except Exception as e:
        return {"ok": False, "msg": str(e)[:200]}


@app.post("/api/extract")
def api_extract(payload: dict = Body(...)):
    paths = [fsops.norm(p) for p in (payload.get("paths") or [])]
    if not paths and payload.get("ids"):
        ids = payload["ids"]
        rows = db.q(f"SELECT DISTINCT source_path FROM assets WHERE id IN ({','.join('?'*len(ids))})",
                    tuple(ids))
        paths = [r["source_path"] for r in rows]
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        return {"ok": 0, "errors": ["未找到可解压的压缩包"]}
    dest = payload.get("dest") or ""
    if not dest:
        dest = os.path.join(os.path.dirname(paths[0]), "解压")
    jid = ops.job_start("extract", "解压", len(paths))
    r = ops.extract_archives(paths, dest, bool(payload.get("per_folder", True)),
                             bool(payload.get("delete_source")), job_id=jid)
    ops.job_finish(jid, "done" if not r["errors"] else "warn", f"完成 {r['ok']}/{len(paths)}")
    r["dest"] = dest
    r["job"] = jid
    return r


@app.post("/api/rename/preview")
def api_rename_preview(payload: dict = Body(...)):
    ids = payload.get("ids") or []
    if not ids:
        return {"rows": []}
    return {"rows": ops.rename_preview(ids, payload.get("rules") or {})}


@app.post("/api/rename/apply")
def api_rename_apply(payload: dict = Body(...)):
    ids = payload.get("ids") or []
    if not ids:
        return {"ok": 0, "errors": ["未选择素材"]}
    jid = ops.job_start("rename", "批量重命名", len(ids))
    r = ops.rename_apply(ids, payload.get("rules") or {}, job_id=jid)
    ops.job_finish(jid, "done" if not r["errors"] else "warn", f"改名 {r['ok']} 个")
    r["job"] = jid
    return r


@app.post("/api/rename/undo")
def api_rename_undo(payload: dict = Body(...)):
    batch = payload.get("batch") or ""
    if not batch:
        return {"ok": 0, "errors": ["缺少批次号"]}
    return ops.rename_undo(batch)


@app.get("/api/rename/history")
def api_rename_history():
    rows = db.q("SELECT batch, COUNT(*) n, SUM(ok) ok, MAX(ts) ts FROM rename_log "
                "GROUP BY batch ORDER BY ts DESC LIMIT 20")
    return {"items": [_row(r) for r in rows]}


@app.post("/api/favorite")
def api_favorite(payload: dict = Body(...)):
    """收藏 / 取消收藏。三种写法都支持：
      ids    素材库条目的 id 列表
      path   一个磁盘文件路径（收藏「浏览文件」里看到的文件）
      paths  多个磁盘文件路径
    库里还没有的文件会先补进索引，这样缩略图和「我的收藏」里都能看到。
    收藏时会记下收藏时间（fav_at），方便按收藏时间排序。
    """
    val = 1 if payload.get("value", True) else 0
    ids = []
    for x in (payload.get("ids") or []):
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            pass
    paths = [p for p in (payload.get("paths") or []) if p]
    if payload.get("path"):
        paths.append(payload["path"])
    missed = 0
    for p in paths:
        aid = ops.add_file_asset(p)
        if aid:
            ids.append(aid)
        else:
            missed += 1
    ids = sorted({i for i in ids if i})
    state = {}
    if ids:
        ph = ",".join("?" * len(ids))
        if val:
            db.ex("UPDATE assets SET favorite=1, "
                  "fav_at=CASE WHEN favorite=0 OR fav_at=0 THEN ? ELSE fav_at END "
                  "WHERE id IN (%s)" % ph, (time.time(), *ids))
        else:
            db.ex("UPDATE assets SET favorite=0, fav_at=0 WHERE id IN (%s)" % ph, tuple(ids))
        for r in db.q("SELECT id,favorite FROM assets WHERE id IN (%s)" % ph, tuple(ids)):
            state[str(r["id"])] = r["favorite"]
    return {"ok": len(ids), "value": val, "ids": ids, "state": state, "missed": missed}


@app.post("/api/open")
def api_open(payload: dict = Body(...)):
    aid = payload.get("id")
    mode = payload.get("mode", "open")
    if not aid:
        return {"ok": False, "msg": "缺少 id"}
    ok, msg = ops.open_asset(int(aid), mode)
    return {"ok": ok, "msg": msg}


@app.post("/api/reveal")
def api_reveal(payload: dict = Body(...)):
    path = payload.get("path") or ""
    if path and os.path.exists(path):
        return {"ok": ops.reveal_archive(path)[0]}
    return {"ok": False, "msg": "路径不存在"}


@app.post("/api/pick-folder")
def api_pick_folder(payload: dict = Body(default={})):
    p = ops.pick_folder(payload.get("title") or "选择文件夹")
    return {"path": p}


@app.post("/api/pick-file")
def api_pick_file(payload: dict = Body(default={})):
    """挑一个程序文件（给「文件类型默认程序」用）。"""
    p = ops.pick_file(payload.get("title") or "选择程序", payload.get("ext") or ".exe")
    return {"path": p}


@app.get("/api/assoc")
def api_assoc(ext: str = ""):
    """查某个扩展名在系统里登记的默认程序。"""
    return fsops.system_default(ext)


@app.get("/api/viewers")
def api_viewers():
    """本机能用的看图软件候选（给「指定看图软件」用）。"""
    return {"items": fsops.viewer_candidates(),
            "current": fsops.image_viewer_spec()[1] if fsops.image_viewer_spec()[0] == "exe"
                       else (fsops.PHOTO_VIEWER_ID if fsops.image_viewer_spec()[0] == "photo" else ""),
            "status": fsops.viewer_status()}


def _open_with_list(cfg) -> list:
    """设置面板用：[{ext, exe, ok}]，ok = 那个程序还在不在。"""
    rules = cfg.get("open_with") or {}
    if not isinstance(rules, dict):
        return []
    return [{"ext": e, "exe": p, "ok": os.path.isfile(p)}
            for e, p in sorted(rules.items()) if e and p]


def _clean_open_with(raw) -> dict:
    """把前端传来的 {扩展名: 程序路径} 洗干净：扩展名统一成 .xxx，空值丢掉。"""
    out = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            ext = config.norm_ext(str(k))
            exe = str(v or "").strip().strip('"')
            if ext and exe:
                out[ext] = exe
    return out


@app.post("/api/settings")
def api_settings(payload: dict = Body(...)):
    cfg = config.load()
    for k in ("seven_zip", "ffmpeg", "thumb_max_px", "workers", "index_images",
              "image_max_mb", "index_all_files", "index_inside_archives", "max_file_mb",
              "text_preview_kb", "sketchup_exe", "image_viewer",
              "cache_remind_on", "cache_remind_once", "cache_remind_min",
              "cache_limit_mb"):
        if k in payload:
            cfg[k] = payload[k]
    # 卡片显示开关：只认真假，避免前端偶尔传来字符串把状态弄乱
    for k in ("show_thumbs", "show_path"):
        if k in payload:
            cfg[k] = config.as_bool(payload[k], bool(cfg.get(k)))
    if "open_with" in payload:
        cfg["open_with"] = _clean_open_with(payload["open_with"])
    # 关窗口怎么办：tray = 隐藏到任务栏（托盘），quit = 直接退出程序
    if payload.get("close_action") in ("tray", "quit"):
        cfg["close_action"] = payload["close_action"]
    # 图片缩略图：关掉就不再批量生成（卡片上点开看原图）；重新打开时
    # 把之前标了 skip 的图片排回队列，「生成缩略图」会补上。
    if "image_thumbs" in payload:
        on = config.as_bool(payload["image_thumbs"])
        cfg["image_thumbs"] = on
        if on:
            db.ex("UPDATE assets SET thumb_status='pending' WHERE kind='image' "
                  "AND thumb_status='skip'")
        else:
            db.ex("UPDATE assets SET thumb_status='skip', thumb_msg='按设置不生成图片缩略图' "
                  "WHERE kind='image' AND thumb_status<>'ok'")
    # 上次加载的根目录（下次打开程序自动回到这里；空字符串 = 不自动加载）
    if "loaded_root" in payload:
        v = payload["loaded_root"]
        cfg["loaded_root"] = v.strip()[:500] if isinstance(v, str) else ""
    config.save(cfg)
    return {"ok": True, "settings": {k: cfg.get(k) for k in payload},
            "open_with": cfg.get("open_with") or {}}

# ------------------------------------------------------------------ 原文件流
MIME_FIX = {
    ".mkv": "video/x-matroska", ".webm": "video/webm", ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv", ".mov": "video/quicktime", ".rmvb": "video/vnd.rn-realvideo",
    ".m4v": "video/x-m4v", ".ts": "video/mp2t", ".flac": "audio/flac",
    ".ape": "audio/x-ape", ".wma": "audio/x-ms-wma", ".m4a": "audio/mp4",
    ".ogg": "audio/ogg", ".wav": "audio/wav", ".mp3": "audio/mpeg",
    ".ttf": "font/ttf", ".otf": "font/otf", ".ttc": "font/collection",
    ".woff": "font/woff", ".woff2": "font/woff2", ".dwg": "application/acad",
    ".dxf": "application/dxf", ".skp": "application/octet-stream",
    ".psd": "image/vnd.adobe.photoshop", ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8", ".csv": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".pdf": "application/pdf",
}
CHUNK = 1024 * 1024


def _mime_for(name: str) -> str:
    ext = config.ext_of(name)
    if ext in MIME_FIX:
        return MIME_FIX[ext]
    mt, _ = mimetypes.guess_type(name)
    if mt and (mt.startswith("video/") or mt.startswith("audio/") or
               mt.startswith("image/") or mt.startswith("text/") or mt == "application/pdf"):
        if mt.startswith("text/") and "charset" not in mt:
            mt += "; charset=utf-8"
        return mt
    return "application/octet-stream"


def _iter_file(path, start, end):
    with open(path, "rb") as f:
        f.seek(start)
        left = end - start + 1
        while left > 0:
            b = f.read(min(CHUNK, left))
            if not b:
                break
            left -= len(b)
            yield b


def _serve(request: Request, path: str, name: str, inline: bool = True):
    from urllib.parse import quote
    if not os.path.isfile(path):
        raise HTTPException(404, "文件不存在")
    size = os.path.getsize(path)
    mime = _mime_for(name)
    disp = "inline" if inline else "attachment"
    headers = {"Content-Disposition": f"{disp}; filename*=UTF-8''{quote(name)}",
               "Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    rng = request.headers.get("range") or ""
    if rng:
        m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
                end = size - 1
            start = max(0, min(start, max(0, size - 1)))
            end = max(start, min(end, size - 1))
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers["Content-Length"] = str(end - start + 1)
            return StreamingResponse(_iter_file(path, start, end), status_code=206,
                                     headers=headers, media_type=mime)
    headers["Content-Length"] = str(size)
    return StreamingResponse(_iter_file(path, 0, size - 1), headers=headers, media_type=mime)


@app.get("/api/raw")
def api_raw(request: Request, path: str = "", inner: str = "", id: int = 0, dl: int = 0):
    """直接播放/预览原始文件（支持压缩包内文件）。"""
    if id:
        r = db.q("SELECT * FROM assets WHERE id=?", (id,), one=True)
        if not r:
            raise HTTPException(404, "素材不存在")
        a = _row(r)
        p = ops.preview_path(a)
        if not p:
            raise HTTPException(404, a.get("msg") or "无法读取")
        name = os.path.basename(a.get("inner_path") or a["source_path"])
        return _serve(request, p, name, not dl)
    if not path:
        raise HTTPException(400, "缺少参数")
    if inner:
        a = {"source_path": fsops.norm(path), "inner_path": inner, "mtime": 0, "size": 0}
        p = ops.preview_path(a)
        if not p:
            raise HTTPException(404, "无法读取压缩包内容")
        return _serve(request, p, os.path.basename(inner.replace("\\", "/")), not dl)
    p = fsops.norm(path)
    return _serve(request, p, os.path.basename(p), not dl)


@app.get("/api/text")
def api_text(path: str = "", inner: str = "", id: int = 0):
    kb = int(config.get("text_preview_kb", 256) or 256)
    limit = max(16, kb) * 1024
    if id:
        r = db.q("SELECT * FROM assets WHERE id=?", (id,), one=True)
        if not r:
            raise HTTPException(404, "素材不存在")
        a = _row(r)
        info = fsops.text_head(a["source_path"], limit,
                               inner=a.get("inner_path") or "", source=a["source_path"])
        info["name"] = os.path.basename(a.get("inner_path") or a["source_path"])
    else:
        p = fsops.norm(path)
        if inner:
            info = fsops.text_head(p, limit, inner=inner, source=p)
        else:
            info = fsops.text_head(p, limit)
        info["name"] = os.path.basename(path)
    return info


# ------------------------------------------------------------------ 浏览磁盘
@app.get("/api/places")
def api_places():
    return {"drives": fsops.drives(), "places": fsops.quick_places(),
            "roots": [p for p in all_roots()]}


def _mark_favorites(files) -> None:
    """给「浏览文件」列出来的文件补上 favorite / asset_id，用来显示收藏星标。"""
    paths = [e.get("path") for e in files if e.get("path")]
    fav = {}
    step = 800
    for i in range(0, len(paths), step):
        chunk = paths[i:i + step]
        ph = ",".join("?" * len(chunk))
        for r in db.q("SELECT id,source_path FROM assets WHERE inner_path='' "
                      "AND favorite=1 AND source_path IN (%s)" % ph, tuple(chunk)):
            fav[r["source_path"]] = r["id"]
    for e in files:
        aid = fav.get(e.get("path"))
        e["favorite"] = 1 if aid else 0
        e["asset_id"] = aid or 0


@app.get("/api/browse")
def api_browse(path: str = "", hidden: int = 0, limit: int = Query(4000, le=20000)):
    p = fsops.norm(path) if path else ""
    if not p or not os.path.isdir(p):
        return {"path": "", "parent": "", "dirs": [], "files": [], "total": 0,
                "counts": {}, "crumbs": [], "error": "目录不存在" if p else ""}
    data = fsops.list_dir(p, bool(hidden), limit)
    data["crumbs"] = fsops.crumbs(p)
    data["error"] = ""
    for e in data["dirs"]:
        e["preview"] = "folder"
    for e in data["files"]:
        e["preview"] = preview_kind(e["name"], e["kind"])
    _mark_favorites(data["files"])
    return data


@app.get("/api/fs/groups")
def api_fs_groups(path: str = "", hidden: int = 0, limit: int = Query(400, le=2000)):
    """浏览模式左侧「分类」：当前文件夹里的子文件夹 + 各自里面的文件数量。"""
    p = fsops.norm(path) if path else ""
    if not p or not os.path.isdir(p):
        return {"path": p, "groups": [], "error": "目录不存在" if p else ""}
    return fsops.subfolder_stats(p, bool(hidden), limit)


@app.get("/api/fs/search")
def api_fs_search(path: str = "", q: str = "", hidden: int = 0,
                  limit: int = Query(400, le=2000)):
    """在 path 里递归搜文件名（给素材库「加载了根目录」之后用）。"""
    p = fsops.norm(path) if path else ""
    kw = (q or "").strip()
    if not p or not os.path.isdir(p):
        return {"path": p, "items": [], "error": "目录不存在" if p else ""}
    if not kw:
        return {"path": p, "items": [], "truncated": False, "scanned": 0}
    d = fsops.search_tree(p, kw, bool(hidden), limit)
    files = [e for e in d["items"] if not e.get("is_dir")]
    _mark_favorites(files)
    for e in d["items"]:
        e["preview"] = "folder" if e.get("is_dir") else preview_kind(e["name"], e.get("kind") or "")
    return d


@app.post("/api/fs/open")
def api_fs_open(payload: dict = Body(...)):
    ok, msg = fsops.open_path(payload.get("path") or "", payload.get("mode") or "open")
    return {"ok": ok, "msg": msg}


@app.post("/api/fs/rename")
def api_fs_rename(payload: dict = Body(...)):
    ok, msg = fsops.rename_path(payload.get("path") or "", payload.get("name") or "",
                                bool(payload.get("overwrite")))
    return {"ok": ok, "msg": "" if ok else msg, "path": msg if ok else ""}


@app.post("/api/fs/mkdir")
def api_fs_mkdir(payload: dict = Body(...)):
    ok, msg = fsops.mkdir(payload.get("parent") or "", payload.get("name") or "")
    return {"ok": ok, "msg": "" if ok else msg, "path": msg if ok else ""}


@app.post("/api/fs/recycle")
def api_fs_recycle(payload: dict = Body(...)):
    paths = payload.get("paths") or []
    if not paths:
        return {"ok": 0, "errors": ["未选择文件"]}
    return fsops.recycle(paths)


@app.get("/api/clip")
def api_clip():
    return {"paths": CLIP["paths"], "mode": CLIP["mode"]}


@app.post("/api/clip")
def api_clip_set(payload: dict = Body(...)):
    paths = [fsops.norm(p) for p in (payload.get("paths") or []) if os.path.exists(fsops.norm(p))]
    mode = payload.get("mode") or "copy"
    if payload.get("clear"):
        CLIP["paths"], CLIP["mode"] = [], "copy"
    else:
        CLIP["paths"], CLIP["mode"] = paths, ("move" if mode == "move" else "copy")
    return {"ok": len(CLIP["paths"]), "paths": CLIP["paths"], "mode": CLIP["mode"]}


def _run_copy(jid, paths, dest, move):
    try:
        r = fsops.copy_items(paths, dest, move,
                             job=lambda i, n, name: ops.job_update(jid, done=i, total=n, msg=name))
        if move:
            CLIP["paths"] = []
        ops.job_finish(jid, "done" if not r["errors"] else "warn",
                       f"{'移动' if move else '复制'} {r['ok']}/{len(paths)}")
        return r
    except Exception as e:
        ops.job_finish(jid, "error", str(e)[:300])


@app.post("/api/fs/paste")
def api_fs_paste(payload: dict = Body(...)):
    dest = payload.get("dest") or ""
    if not CLIP["paths"]:
        return {"ok": 0, "errors": ["剪贴板是空的"]}
    if not dest or not os.path.isdir(fsops.norm(dest)):
        return {"ok": 0, "errors": ["目标目录不存在"]}
    paths, move = list(CLIP["paths"]), CLIP["mode"] == "move"
    jid = ops.job_start("paste", "移动" if move else "复制", len(paths))
    threading.Thread(target=_run_copy, args=(jid, paths, dest, move), daemon=True).start()
    return {"ok": len(paths), "job": jid, "mode": CLIP["mode"], "dest": fsops.norm(dest)}


@app.post("/api/fs/copy")
def api_fs_copy(payload: dict = Body(...)):
    paths = payload.get("paths") or []
    dest = payload.get("dest") or ""
    move = bool(payload.get("move"))
    if not paths or not dest:
        return {"ok": 0, "errors": ["缺少参数"]}
    jid = ops.job_start("paste", "移动" if move else "复制", len(paths))
    threading.Thread(target=_run_copy, args=(jid, list(paths), dest, move), daemon=True).start()
    return {"ok": len(paths), "job": jid}


def _run_zip(jid, paths, out_zip):
    try:
        r = fsops.make_zip(paths, out_zip,
                           job=lambda i, n, name: ops.job_update(jid, done=i, msg=name))
        ops.job_finish(jid, "done" if r.get("ok") else "error",
                       f"已打包 {r.get('count', 0)} 个文件" if r.get("ok") else r.get("msg", ""))
        return r
    except Exception as e:
        ops.job_finish(jid, "error", str(e)[:300])


@app.post("/api/fs/zip")
def api_fs_zip(payload: dict = Body(...)):
    paths = [fsops.norm(p) for p in (payload.get("paths") or []) if os.path.exists(fsops.norm(p))]
    if not paths:
        return {"ok": False, "msg": "未选择文件"}
    dest = payload.get("dest") or os.path.dirname(paths[0])
    name = payload.get("name") or (os.path.basename(paths[0]) if len(paths) == 1 else "打包")
    out_zip = os.path.join(fsops.norm(dest), textutil.safe_filename(name, "打包") + ".zip")
    jid = ops.job_start("zip", "压缩打包", len(paths))
    threading.Thread(target=_run_zip, args=(jid, paths, out_zip), daemon=True).start()
    return {"ok": True, "job": jid, "zip": out_zip}


@app.post("/api/fs/stats")
def api_fs_stats(payload: dict = Body(...)):
    return fsops.dir_stats(payload.get("path") or "")


# ------------------------------------------------------------------ 维护
def _mb_text(n: float) -> str:
    if n < 1024:
        return "%d B" % n
    if n < 1048576:
        return "%.0f KB" % (n / 1024.0)
    mb = n / 1048576.0
    return ("%.0f MB" % mb) if mb < 1024 else ("%.2f GB" % (mb / 1024.0))


@app.get("/api/cache/detail")
def api_cache_detail(fresh: int = 0):
    """缓存明细：按分类 / 类型 / 新旧分好组，给「自己挑着清」用。"""
    return ops.cache_detail(force=bool(fresh))


@app.post("/api/cleanup")
def api_cleanup(payload: dict = Body(default={})):
    """清理缓存释放磁盘空间。

    带 items 的走「挑着清」：items 就是 /api/cache/detail 里每个分组的 sel 字段。
    只带 what 的是老的一键清法，留给旧界面 / 脚本用。
    nested 清掉后需要重新扫描素材库才会恢复。
    """
    items = payload.get("items")
    if items is not None:
        if not items:
            return {"ok": True, "freed": 0, "done": [], "msg": "没有勾选任何项目，什么都没清"}
        r = ops.cleanup_cache(items)
        ops.cache_stats(force=True)
        what = "、".join(r["done"]) or "所选项"
        return {"ok": True, "freed": r["freed"], "done": r["done"],
                "msg": "已清理 %s，释放 %s" % (what, _mb_text(r["freed"]))}
    import shutil as _sh
    what = payload.get("what") or "thumbs"
    freed = 0
    targets = {"thumbs": [config.THUMB_DIR], "nested": [config.NESTED_DIR],
               "stage": [config.STAGE_DIR], "model3d": [config.MODEL3D_DIR],
               "all": [config.THUMB_DIR, config.NESTED_DIR, config.STAGE_DIR,
                       config.MODEL3D_DIR]}
    for d in targets.get(what, []):
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for f in files:
                try:
                    freed += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        _sh.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
    if what in ("nested", "all"):
        db.ex("DELETE FROM archive_meta WHERE path LIKE ?", (str(config.NESTED_DIR) + "%",))
        db.ex("UPDATE assets SET thumb_status='pending'")
    if what in ("thumbs", "all"):
        db.ex("UPDATE assets SET thumb_status='pending', thumb_key=''")
    ops.invalidate_cache_stats()
    ops.cache_stats(force=True)
    return {"ok": True, "freed": freed, "msg": "已释放 " + _mb_text(freed)}


# ------------------------------------------------- 关掉窗口以后怎么办
# 由「设置 → 常规 → 关闭窗口时」决定：
#   tray（默认）= 隐藏到任务栏：后台继续跑，右下角托盘留个图标，双击就回来
#   quit        = 退出程序：窗口一关，后台服务也一起关掉
_UI = {"seen": False, "miss": 0, "ballooned": False, "act": ""}


def _ui_state():
    try:
        act = config.load().get("close_action") or "tray"
        n = len(winui.app_windows())
    except Exception:
        act, n = "tray", 0
    return {"close_action": act, "tray": winui.tray_on(), "windows": n,
            "window_seen": _UI["seen"], "miss": _UI["miss"],
            "ballooned": _UI["ballooned"]}


def quit_program(delay: float = 0.5):
    """真正退出：先把托盘图标摘掉（免得留个假图标），再结束进程。"""
    try:
        winui.remove_tray()
    except Exception:
        pass

    def bye():
        time.sleep(delay)
        os._exit(0)

    threading.Thread(target=bye, daemon=True).start()


def _ui_watchdog():
    """每 3 秒看一眼界面窗口（只认自己开的窗口，见 winui.app_windows）。

    - 隐藏到任务栏：窗口没了就留着托盘图标，并提示一次「还在后台跑」
    - 退出程序：窗口连着 3 轮（约 9 秒）都不在，就把后台服务也一起关掉
      （刷新页面时窗口一直在，所以不会误伤）
    """
    while True:
        time.sleep(3.0)
        try:
            act = config.load().get("close_action") or "tray"
            if _UI["act"] != act:
                # 刚换了模式：重新从头数，免得刚切成「退出程序」就立刻退
                _UI["act"], _UI["miss"] = act, 0
            if act == "tray":
                winui.ensure_tray(on_open=winui.open_ui, on_quit=quit_program)
            else:
                winui.remove_tray()
            if winui.app_windows():
                _UI["seen"], _UI["miss"], _UI["ballooned"] = True, 0, False
                continue
            if not _UI["seen"]:
                continue                      # 一直没开过界面（比如 --no-browser）
            _UI["miss"] += 1
            if act == "quit":
                if _UI["miss"] >= 3:
                    quit_program(0.2)
                    return
            elif not _UI["ballooned"]:
                _UI["ballooned"] = True
                winui.tray_balloon(
                    config.APP_NAME,
                    "窗口关掉了，我还在后台跑着。双击右下角的小虫图标就能回来。")
        except Exception:
            pass


_WD = {"started": False}


def start_ui_watchdog():
    """看门狗只能起一次。

    打包后是 start.py 直接跑 uvicorn（不走本文件的 main），所以在「第一个请求」
    和 main() 两处都挂一下，保证它一定会起来。
    """
    if _WD["started"]:
        return
    _WD["started"] = True
    threading.Thread(target=_ui_watchdog, daemon=True, name="xc-ui-watchdog").start()


@app.post("/api/shutdown")
def api_shutdown():
    """退出程序（关闭本地服务）。"""
    quit_program(0.6)
    return {"ok": True}


@app.post("/api/open-folder")
def api_open_folder(payload: dict = Body(...)):
    p = payload.get("path") or str(config.DATA_DIR)
    if os.path.isdir(p):
        os.startfile(p)  # noqa: S606
        return {"ok": True}
    return {"ok": False, "msg": "目录不存在"}


# ------------------------------------------------------------- 首次启动自检
_FIRST = {"done": False}


def _auto_scan_if_empty():
    """索引为空时后台自动扫一次，让新装的用户开箱可用。"""
    try:
        if db.count("SELECT COUNT(*) FROM assets"):
            return
        roots = [r for r in all_roots() if os.path.isdir(r)]
        if not roots or STATE["scanning"]:
            return
        api_scan({})
    except Exception:
        pass


@app.middleware("http")
async def _first_run(request, call_next):
    if not _FIRST["done"]:
        _FIRST["done"] = True
        threading.Timer(1.5, _auto_scan_if_empty).start()
    start_ui_watchdog()
    return await call_next(request)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main():
    import uvicorn
    port = int(os.environ.get("XC_PORT") or os.environ.get("SU_PORT") or "8765")
    print(f"\n  {config.APP_NAME} 已启动： http://127.0.0.1:{port}\n")
    start_ui_watchdog()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()