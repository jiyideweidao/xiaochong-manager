"""素材库扫描：把散装文件与压缩包内容统一索引进 SQLite。

要点：
- 压缩包只读目录（必要时只取文件头部），不解压整包
- 自动修复老下载站的 GBK/CP437 乱码文件名
- 模型显示名优先取有信息量的内层文件夹名（卖家常把 skp 统一命名）
- 配对效果图：优先同目录内效果图，其次与压缩包同名的外部效果图
"""
import hashlib
import os
import re
import time

from . import archives, config, db, textutil

SKIP_DIRS = {"$recycle.bin", "system volume information", "config.msi",
             "wpsystem", "windowsapps", "deliveryoptimization", "wudownloadcache"}
OTHER_EXT = {".txt", ".url", ".html", ".htm", ".docx", ".doc", ".pdf",
             ".xlsx", ".3ds", ".dwg", ".dxf", ".max", ".rvt", ".psd", ".ai"}


def ext_is_image(ext: str) -> bool:
    return ext in config.IMAGE_EXT
NESTED_MAX_MB = 3072
# 索引规则版本：改动分类/收录规则后 +1，老用户升级会自动重建压缩包索引
SCAN_REV = 4

# 压缩包里的广告/网页文件不建索引（卖家放的淘宝链接、店铺页面）
JUNK_IN_ARCHIVE = {".url", ".lnk", ".html", ".htm", ".webloc", ".website"}


class Scanner:
    def __init__(self, progress=None, force: bool = False):
        self.progress = progress or (lambda **kw: None)
        self.force = bool(force)
        self.stats = {"files": 0, "archives": 0, "models": 0, "images": 0,
                      "others": 0, "nested": 0, "errors": 0, "repaired": 0}
        self.rows = []
        self.errors = []

    def run(self, roots):
        t0 = time.time()
        cfg = config.load()
        if int(cfg.get("scan_rev") or 0) != SCAN_REV:
            self.force = True
        for root in roots:
            if os.path.isdir(root):
                self._walk(root, root)
                self._flush(force=True)
            else:
                self.errors.append(f"根目录不存在：{root}")
        self._flush(force=True)
        self._prune_missing([r for r in roots if os.path.isdir(r)])
        self._pair_renders()
        self._mark_cached_thumbs()
        self._skip_image_thumbs()
        cfg = config.load()
        cfg["scan_rev"] = SCAN_REV
        config.save(cfg)
        self.stats["seconds"] = round(time.time() - t0, 1)
        return self.stats

    # ---------- write ----------
    def _flush(self, force=False):
        if not self.rows or (not force and len(self.rows) < 500):
            return
        db.exmany(
            """INSERT INTO assets (kind,name,ext,orig_name,source_type,source_path,inner_path,
                   origin,folder,group_key,cover_key,size,mtime,category,style,tags,added_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_path,inner_path) DO UPDATE SET
                   kind=excluded.kind, name=excluded.name, ext=excluded.ext,
                   orig_name=excluded.orig_name, origin=excluded.origin, folder=excluded.folder,
                   group_key=excluded.group_key, cover_key=excluded.cover_key,
                   size=excluded.size, mtime=excluded.mtime,
                   category=excluded.category, style=excluded.style, tags=excluded.tags""",
            self.rows)
        self.rows = []

    def _add(self, kind, source_path, inner_path="", orig_name="", directory=(),
             size=0, mtime=0, top="", container=""):
        dirs = textutil.meaningful_dirs(directory)
        raw_stem = re.sub(r"\.[^.]+$", "", orig_name or "")
        if kind == "model":
            fallback = os.path.splitext(os.path.basename(container))[0] if inner_path else ""
            name = textutil.display_name(orig_name, directory, fallback=fallback)
            if textutil.is_generic(raw_stem):
                m = re.search(r"[\uff08(]\s*(\d{1,4})\s*[)\uff09]\s*$", raw_stem)
                if (m and not textutil.NUM_ONLY_RE.match(name)
                        and not re.search(r"\d\s*[)\uff09]?\s*$", name)):
                    name = f"{name} {m.group(1)}"
        else:
            name = textutil.clean(raw_stem) or raw_stem
        folder = textutil.folder_label(directory)
        if textutil.BOX_RE.search(raw_stem) or textutil.BOX_RE.search(folder):
            self.stats["repaired"] += 1
        ext = os.path.splitext(orig_name)[1].lower()
        container_stem = os.path.splitext(os.path.basename(container))[0]
        cat = textutil.category_of(directory, container_stem if inner_path else "", top)
        style = textutil.detect_style(name, folder, orig_name)
        self.rows.append((kind, name, ext, orig_name, "archive" if inner_path else "loose",
                          source_path, inner_path, "", folder,
                          textutil.group_of(directory), textutil.cover_key(container),
                          size, mtime, cat, style, self._tags(name, folder, orig_name), time.time()))
        self.stats["k_" + kind] = self.stats.get("k_" + kind, 0) + 1
        if kind == "model":
            self.stats["models"] += 1
        elif kind in ("image", "psd"):
            self.stats["images"] += 1
        else:
            self.stats["others"] += 1
        if len(self.rows) >= 500:
            self._flush(force=True)
            self.progress(phase="index", **self.stats)

    @staticmethod
    def _tags(name, folder, orig_name):
        cand = []
        for src in (name, folder, re.sub(r"\.[^.]+$", "", textutil.repair(orig_name))):
            for p in re.split(r"[_\-\s+·,，、|（）()\[\]【】]+", src or ""):
                p = p.strip()
                if 2 <= len(p) <= 10 and not re.fullmatch(r"[\d.]+", p) and not textutil.AD_RE.search(p):
                    cand.append(p)
        out, seen = [], set()
        for p in cand:
            if p not in seen:
                seen.add(p)
                out.append(p)
            if len(out) >= 8:
                break
        return ",".join(out)

    # ---------- walk ----------
    def _walk(self, root, path, depth=0):
        try:
            entries = list(os.scandir(path))
        except OSError as e:
            self.errors.append(f"{path}: {e}")
            return
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() in SKIP_DIRS or e.name.startswith("."):
                        continue
                    self._walk(root, e.path, depth + 1)
                elif e.is_file(follow_symlinks=False):
                    self._file(root, e)
            except OSError:
                self.stats["errors"] += 1
        self.progress(phase="index", current=path, **self.stats)

    def _file(self, root, e):
        self.stats["files"] += 1
        ext = os.path.splitext(e.name)[1].lower()
        rel = os.path.relpath(os.path.dirname(e.path), root)
        top = textutil.repair(rel.split(os.sep)[0]) if rel != "." else textutil.repair(os.path.basename(root))
        try:
            st = e.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, 0
        if ext in config.MODEL_EXT:
            self._add("model", e.path, orig_name=e.name, directory=[], size=size,
                      mtime=mtime, top=top, container=e.path)
        elif archives.is_archive(e.path):
            self.stats["archives"] += 1
            self._archive(e.path, top, size, mtime, depth=0, origin="", container=e.path)
        elif config.get("index_all_files", True):
            kind = config.kind_of(e.name)
            cap = int(config.get("max_file_mb", 2048)) * 1024 * 1024
            if size > cap:
                return
            self._add(kind, e.path, orig_name=e.name, directory=[], size=size,
                      mtime=mtime, top=top, container=e.path)

    def _archive(self, path, top, size, mtime, depth, origin, container):
        meta = db.q("SELECT mtime,size FROM archive_meta WHERE path=?", (path,), one=True)
        if (not self.force and meta and abs((meta["mtime"] or 0) - mtime) < 1
                and meta["size"] == size):
            if db.count("SELECT COUNT(*) FROM assets WHERE source_path=?", (path,)):
                return
        if self.force:
            db.ex("DELETE FROM assets WHERE source_path=? AND kind<>'image'", (path,))
            db.ex("DELETE FROM assets WHERE source_path=? AND kind='image' AND inner_path<>''",
                  (path,))
        try:
            entries = archives.list_archive(path)
        except Exception as ex:
            self.stats["errors"] += 1
            self.errors.append(f"{os.path.basename(path)}: {ex}")
            return
        db.ex("INSERT INTO archive_meta(path,mtime,size,listed_at) VALUES(?,?,?,?) "
              "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime,size=excluded.size,listed_at=excluded.listed_at",
              (path, mtime, size, time.time()))
        label = origin or textutil.repair(os.path.basename(path))
        cfg = config.load()
        nested_files = []
        for ent in entries:
            inner = ent["inner"]
            if ent.get("is_dir"):
                continue
            dirs, iname = textutil.split_segments(inner)
            if not iname:
                continue
            iext = os.path.splitext(iname)[1].lower()
            isize = ent.get("size") or 0
            if iext in config.MODEL_EXT:
                self._add("model", path, inner_path=inner, orig_name=iname, directory=dirs,
                          size=isize, mtime=mtime, top=top, container=container)
            elif archives.is_archive(iname):
                nested_files.append((inner, iname, isize))
            elif not cfg.get("index_inside_archives", True):
                continue
            elif ext_is_image(iext):
                if not cfg.get("index_images", True):
                    continue
                if any(d.strip().lower() in textutil.TEXTURE_DIRS for d in dirs):
                    continue
                if isize > int(cfg.get("image_max_mb", 30)) * 1024 * 1024 or isize <= 0:
                    continue
                self._add(config.kind_of(iname), path, inner_path=inner, orig_name=iname,
                          directory=dirs, size=isize, mtime=mtime, top=top, container=container)
            else:
                ikind = config.kind_of(iname)
                if iext in JUNK_IN_ARCHIVE or ikind == "other" or isize <= 0:
                    continue
                if iext in (".txt", ".doc", ".docx") and textutil.is_generic(iname):
                    continue          # 卖家放的广告文本，不进索引
                if isize > 512 * 1024 * 1024:
                    continue
                self._add(ikind, path, inner_path=inner, orig_name=iname, directory=dirs,
                          size=isize, mtime=mtime, top=top, container=container)
        if nested_files and depth < 1:
            self._nested(path, label, top, nested_files, depth, container)

    def _nested(self, path, label, top, nested_files, depth, container):
        for inner, iname, isize in nested_files:
            if isize > NESTED_MAX_MB * 1024 * 1024:
                continue
            key = hashlib.sha1(f"{path}|{inner}".encode("utf-8", "surrogatepass")).hexdigest()[:16]
            outdir = config.NESTED_DIR / key
            target, hits = None, []
            if outdir.exists():
                hits = [p for p in outdir.rglob("*") if p.suffix.lower() in config.ARCHIVE_EXT]
            if hits:
                target = hits[0]
            else:
                try:
                    outdir.mkdir(parents=True, exist_ok=True)
                    if not archives.extract(path, str(outdir), inner=inner):
                        raise RuntimeError("解压失败")
                    hits = [p for p in outdir.rglob("*") if p.suffix.lower() in config.ARCHIVE_EXT]
                    if not hits:
                        raise RuntimeError("未找到内部压缩包")
                    target = hits[0]
                except Exception as ex:
                    self.stats["errors"] += 1
                    self.errors.append(f"{label} → {iname}: {ex}")
                    continue
            try:
                st = target.stat()
                self.stats["nested"] += 1
                self._archive(str(target), top, st.st_size, st.st_mtime, depth + 1,
                              origin=f"{label} → {textutil.repair(iname)}", container=container)
            except OSError:
                pass

    def _mark_cached_thumbs(self):
        """把已经有缩略图缓存的行标回 ok，界面上不会显示成「待生成」。"""
        from . import thumbs as _th
        ids = []
        for r in db.q("SELECT * FROM assets WHERE thumb_status<>'ok'"):
            a = dict(r)
            try:
                if os.path.exists(_th.path_for(a)):
                    ids.append(a["id"])
            except Exception:
                continue
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            db.ex("UPDATE assets SET thumb_status='ok', thumb_msg='' WHERE id IN (%s)"
                  % ",".join("?" * len(chunk)), tuple(chunk))
        self.stats["thumbs_cached"] = len(ids)

    def _skip_image_thumbs(self):
        """按设置不生成图片缩略图时，把图片标成 skip。

        这样「生成缩略图」不会白跑几千张图，界面上「待生成」也不会一直挂着；
        已经生成过的（ok）保持不动。设置里一打开，图片会自动排回队列。
        """
        if bool(config.get("image_thumbs", False)):
            return
        n = db.count("SELECT COUNT(*) FROM assets WHERE kind='image' AND thumb_status<>'ok'")
        if not n:
            return
        db.ex("UPDATE assets SET thumb_status='skip', thumb_msg='按设置不生成图片缩略图' "
              "WHERE kind='image' AND thumb_status<>'ok'")
        self.stats["thumbs_skipped"] = n

    # ---------- cleanup / pairing ----------
    def _prune_missing(self, roots=()):
        roots = [os.path.normcase(os.path.abspath(r)) + os.sep for r in (roots or [])]

        def under_root(p):
            if not roots:
                return True
            q = os.path.normcase(os.path.abspath(p))
            return any(q.startswith(r) for r in roots)

        gone = [(r["id"],) for r in db.q("SELECT id, source_path FROM assets WHERE source_type='loose'")
                if not os.path.exists(r["source_path"]) or not under_root(r["source_path"])]
        if gone:
            db.exmany("DELETE FROM assets WHERE id=?", gone)
        dead = [(r["path"],) for r in db.q("SELECT path FROM archive_meta")
                if not os.path.exists(r["path"])]
        if dead:
            db.exmany("DELETE FROM archive_meta WHERE path=?", dead)
        db.ex("DELETE FROM assets WHERE source_type='archive' AND source_path NOT IN (SELECT path FROM archive_meta)")

    def _pair_renders(self):
        """给模型配对效果图：先同目录，再同名外部效果图。"""
        db.ex("UPDATE assets SET render_id=0 WHERE kind='model'")
        db.ex("""
            UPDATE assets SET render_id = (
                SELECT a2.id FROM assets a2
                WHERE a2.kind='image' AND a2.source_path = assets.source_path
                  AND a2.folder = assets.folder AND assets.folder <> ''
                ORDER BY a2.size DESC LIMIT 1)
            WHERE kind='model' AND folder <> ''
              AND (SELECT COUNT(*) FROM assets m WHERE m.kind='model'
                   AND m.source_path=assets.source_path AND m.folder=assets.folder) = 1
        """)
        db.ex("""
            UPDATE assets SET render_id = (
                SELECT i.id FROM assets i
                WHERE i.kind='image' AND i.cover_key = assets.cover_key AND assets.cover_key <> ''
                ORDER BY i.size DESC LIMIT 1)
            WHERE kind='model' AND render_id = 0 AND cover_key <> ''
              AND (SELECT COUNT(*) FROM assets m WHERE m.kind='model'
                   AND m.cover_key = assets.cover_key) = 1
        """)