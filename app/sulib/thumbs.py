# -*- coding: utf-8 -*-
"""缩略图引擎：任意文件类型都能出一张图，懒生成 + 磁盘缓存（WebP）。

顺序：效果图（模型）→ 类型专用方案（图片/视频/PDF/字体/文本）
      → 通用类型图标（彩色圆角块 + 图形 + 扩展名）
"""
import hashlib
import io
import os
import subprocess
import threading

from PIL import Image, ImageDraw, ImageFile, ImageFont, ImageOps

ImageFile.LOAD_TRUNCATED_IMAGES = True
# 素材库里存在 9000x9000 级别的材质大图，放宽像素上限避免硬报错；
# 真正的内存保护靠下面的 JPEG draft 降采样。
Image.MAX_IMAGE_PIXELS = 400_000_000
BIG_PIXELS = 36_000_000

from . import archives, config

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_END = b"IEND\xaeB`\x82"
SKP_HEAD = 262144
TEXT_HEAD = 262144
MATERIALIZE_MB = 320

_LOCK = threading.Lock()
_CJK_FONTS = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\Deng.ttf",
              r"C:\Windows\Fonts\simsun.ttc"]
_MONO_FONTS = [r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\cour.ttf",
               r"C:\Windows\Fonts\msyh.ttc"]
_TEXT_FONTS = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
               r"C:\Windows\Fonts\Deng.ttf", r"C:\Windows\Fonts\simsun.ttc"]

# 渲染版本：只对新增类型生效，已有的模型/图片缓存继续命中
REV_BY_KIND = {"model": 0, "image": 0}


def _rev(kind: str) -> int:
    return REV_BY_KIND.get(kind or "", 4)


def skp_thumb_png(buf: bytes):
    i = buf.find(PNG_SIG)
    if i < 0:
        return None
    j = buf.find(PNG_END, i)
    if j < 0:
        return None
    return buf[i:j + 8]


def _font(paths, size):
    for f in paths:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def cache_key(*parts) -> str:
    raw = "|".join(str(p) for p in parts).encode("utf-8", "surrogatepass")
    return hashlib.sha1(raw).hexdigest()[:24]


def path_for(asset, max_px=None) -> str:
    # 注意：old 版缓存只用了前 6 个字段，这里必须保持一致，
    # 只在需要「换算法」的类别上追加版本号，否则老缓存会全部失效。
    # max_px 是给批量统计用的：传进来就不用每条都读一次配置文件。
    parts = [asset["source_path"], asset.get("inner_path") or "",
             int(asset.get("mtime") or 0), int(asset.get("size") or 0),
             config.get("thumb_max_px", 720) if max_px is None else max_px,
             asset.get("render_id") or 0]
    rev = _rev(asset.get("kind") or "")
    if rev:
        parts.append(rev)
    return str(config.THUMB_DIR / (cache_key(*parts) + ".webp"))


def _normalize(im: Image.Image) -> Image.Image:
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        rgba = im.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    if im.mode != "RGB":
        return im.convert("RGB")
    return im


def encode_thumb(im: Image.Image, max_px: int) -> bytes:
    im = _normalize(im)
    im.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=84, method=4)
    return buf.getvalue()


def open_scaled(data: bytes, max_px: int) -> Image.Image:
    """打开图片；超大 JPEG 用 libjpeg 内置缩放解码，避免几百 MB 内存占用。"""
    im = Image.open(io.BytesIO(data))
    try:
        w, h = im.size
        if w * h > BIG_PIXELS and im.format == "JPEG":
            im.draft("RGB", (max_px * 2, max_px * 2))
    except Exception:
        pass
    return im


def _head_bytes(source_path: str, inner: str, n: int) -> bytes:
    if inner:
        return archives.read_head(source_path, inner, n)
    with open(source_path, "rb") as f:
        return f.read(n)


# ----------------------------------------------------------------- 通用图标
def _hex2rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _glyph(d: ImageDraw.ImageDraw, kind: str, S: int, col=(255, 255, 255)):
    def rr(x1, y1, x2, y2, r, fill=None, outline=None, w=0):
        d.rounded_rectangle([x1 * S, y1 * S, x2 * S, y2 * S], radius=r * S,
                            fill=fill, outline=outline, width=max(0, int(w * S)))
    def ln(x1, y1, x2, y2, w):
        d.line([x1 * S, y1 * S, x2 * S, y2 * S], fill=col, width=max(1, int(w * S)))
    def el(x1, y1, x2, y2, fill=None, outline=None, w=0):
        d.ellipse([x1 * S, y1 * S, x2 * S, y2 * S], fill=fill, outline=outline,
                  width=max(0, int(w * S)))
    def pg(pts, fill=None):
        d.polygon([(x * S, y * S) for x, y in pts], fill=fill)
    W = 0.055
    if kind == "model":                      # 立方体（描边）
        w = max(1, int(0.045 * S))
        d.line([(0.28 * S, 0.32 * S), (0.50 * S, 0.18 * S), (0.72 * S, 0.32 * S),
                (0.72 * S, 0.62 * S), (0.50 * S, 0.76 * S), (0.28 * S, 0.62 * S),
                (0.28 * S, 0.32 * S)], fill=col, width=w, joint="curve")
        d.line([(0.50 * S, 0.18 * S), (0.50 * S, 0.46 * S)], fill=col, width=w)
        d.line([(0.50 * S, 0.46 * S), (0.72 * S, 0.32 * S)], fill=col, width=w)
        d.line([(0.50 * S, 0.46 * S), (0.28 * S, 0.32 * S)], fill=col, width=w)
    elif kind == "cad":                      # 三角板 + 圆
        pg([(0.5, 0.2), (0.86, 0.76), (0.14, 0.76)])
        el(0.36, 0.56, 0.64, 0.84, fill=(0, 0, 0, 0))
        el(0.36, 0.56, 0.64, 0.84, outline=col, w=W)
    elif kind == "psd":                      # 图层
        for i, y in enumerate((0.28, 0.5, 0.72)):
            pg([(0.5, y - 0.13), (0.88, y), (0.5, y + 0.13), (0.12, y)])
            if i < 2:
                pg([(0.5, y + 0.03), (0.82, y + 0.115), (0.5, y + 0.2), (0.18, y + 0.115)])
    elif kind == "image":                    # 相框 + 山 + 太阳
        rr(0.14, 0.2, 0.86, 0.8, 0.06, outline=col, w=W)
        el(0.6, 0.31, 0.74, 0.45, fill=col)
        pg([(0.18, 0.76), (0.42, 0.47), (0.6, 0.76)])
        pg([(0.5, 0.76), (0.68, 0.55), (0.84, 0.76)])
    elif kind == "vector":                   # 贝塞尔曲线
        d.arc([0.16 * S, 0.34 * S, 0.84 * S, 1.02 * S], 190, 350, fill=col,
              width=max(1, int(W * S)))
        el(0.12, 0.5, 0.26, 0.64, fill=col)
        el(0.74, 0.5, 0.88, 0.64, fill=col)
    elif kind == "video":                    # 播放
        rr(0.12, 0.24, 0.88, 0.76, 0.1, outline=col, w=W)
        pg([(0.44, 0.37), (0.68, 0.5), (0.44, 0.63)], fill=col)
    elif kind == "audio":                    # 音符
        el(0.24, 0.6, 0.46, 0.78, fill=col)
        el(0.56, 0.48, 0.78, 0.66, fill=col)
        ln(0.44, 0.69, 0.44, 0.3, W)
        ln(0.76, 0.57, 0.76, 0.2, W)
        ln(0.44, 0.3, 0.78, 0.2, W)
    elif kind in ("pdf", "doc", "other"):    # 文档页
        pg([(0.24, 0.14), (0.62, 0.14), (0.78, 0.3), (0.78, 0.86), (0.24, 0.86)])
        d.line([(0.62 * S, 0.14 * S), (0.62 * S, 0.3 * S), (0.78 * S, 0.3 * S)],
               fill=col, width=max(1, int(W * S)), joint="curve")
        for i, y in enumerate((0.46, 0.58, 0.7)):
            ln(0.34, y, 0.68 - i * 0.06, y, W * 0.8)
    elif kind == "archive":                  # 纸箱 + 拉链
        rr(0.14, 0.3, 0.86, 0.84, 0.05, outline=col, w=W)
        ln(0.14, 0.44, 0.86, 0.44, W * 0.9)
        for y in (0.52, 0.62, 0.72):
            ln(0.47, y, 0.53, y, W * 0.8)
    elif kind == "font":
        pass
    elif kind == "code":
        pass
    elif kind == "app":                      # 窗口
        rr(0.14, 0.22, 0.86, 0.78, 0.06, outline=col, w=W)
        ln(0.14, 0.36, 0.86, 0.36, W * 0.9)
        for x in (0.22, 0.3, 0.38):
            el(x - 0.03, 0.26, x + 0.03, 0.32, fill=col)


def tile(kind: str, ext: str, label: str = "", max_px: int = 720, seed: str = "") -> bytes:
    """生成一张类型图标缩略图。"""
    mp = max_px or 720
    S = mp * 2 if mp <= 512 else mp
    base = _hex2rgb(config.KIND_COLOR.get(kind, "#6b7280"))
    im = Image.new("RGB", (S, S), _mix(base, (255, 255, 255), 0.86))
    d = ImageDraw.Draw(im)
    pad = int(S * 0.09)
    card = _mix(base, (255, 255, 255), 0.06)
    top = _mix(base, (255, 255, 255), 0.22)
    # 圆角卡片 + 竖向渐变
    grad = Image.new("RGB", (1, S), card)
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.point((0, y), fill=_mix(top, card, y / max(1, S - 1)))
    grad = grad.resize((S, S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, S - pad, S - pad],
                                           radius=int(S * 0.14), fill=255)
    im.paste(grad, (0, 0), mask)
    # 图形（水平居中，放在卡片上半部）
    inner = int(S * 0.27)
    glyph_layer = Image.new("RGBA", (inner, inner), (0, 0, 0, 0))
    _glyph(ImageDraw.Draw(glyph_layer), kind, inner, (255, 255, 255))
    im.paste(glyph_layer, (int((S - inner) / 2), int(S * 0.145)), glyph_layer)
    # 文字：字体/代码显示符号，其余显示扩展名 + 类型名
    if kind == "font":
        d.text((S / 2, S * 0.285), "Aa", font=_font(_CJK_FONTS, int(S * 0.20)),
               fill=(255, 255, 255), anchor="mm")
    elif kind == "code":
        d.text((S / 2, S * 0.29), "</>", font=_font(_MONO_FONTS, int(S * 0.17)),
               fill=(255, 255, 255), anchor="mm")
    txt = (ext or "").lstrip(".").upper()
    if txt:
        d.text((S / 2, S * 0.585), txt, font=_font(_CJK_FONTS, int(S * 0.105)),
               fill=(255, 255, 255), anchor="mm")
    if label:
        d.text((S / 2, S * 0.715), label, font=_font(_CJK_FONTS, int(S * 0.066)),
               fill=(226, 238, 252), anchor="mm")
    im = im.resize((mp, mp), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=84, method=4)
    return buf.getvalue()


def _tile_bytes(asset, max_px: int) -> bytes:
    name = asset.get("orig_name") or os.path.basename(asset.get("source_path") or "")
    kind = asset.get("kind") or "other"
    return tile(kind, config.ext_of(name), config.KIND_LABEL.get(kind, ""), max_px,
                seed=str(asset.get("id") or name))


# ----------------------------------------------------------------- 文本卡片
def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "big5", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def text_card(data: bytes, ext: str, max_px: int) -> bytes:
    mp = max_px or 720
    S = mp * 2 if mp <= 512 else mp
    im = Image.new("RGB", (S, S), (252, 252, 250))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, S, int(S * 0.1)], fill=(240, 240, 236))
    d.text((int(S * 0.03), int(S * 0.05)), (ext or "").lstrip(".").upper() or "TXT",
           font=_font(_CJK_FONTS, int(S * 0.045)), fill=(120, 120, 110), anchor="lm")
    f = _font(_TEXT_FONTS, int(S * 0.038))
    text = _decode(data[:TEXT_HEAD]).replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    cols = max(16, int(S * 0.92 / (S * 0.0225)))
    for raw in text.split("\n"):
        if len(lines) >= 26:
            break
        raw = raw.replace("\t", "    ")
        if not raw:
            lines.append("")
            continue
        while raw and len(lines) < 26:
            lines.append(raw[:cols])
            raw = raw[cols:]
    y = int(S * 0.14)
    for ln in lines:
        d.text((int(S * 0.035), y), ln, font=f, fill=(60, 62, 58))
        y += int(S * 0.0425)
    im = im.resize((mp, mp), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=84, method=4)
    return buf.getvalue()


# ----------------------------------------------------------------- 外部工具
def _ffmpeg() -> str:
    return config.get("ffmpeg") or config.detect_ffmpeg()


class _Pump(threading.Thread):
    def __init__(self, src, dst):
        super().__init__(daemon=True)
        self.src, self.dst = src, dst

    def run(self):
        try:
            while True:
                b = self.src.read(1 << 20)
                if not b:
                    break
                self.dst.write(b)
        except Exception:
            pass
        finally:
            try:
                self.dst.close()
            except Exception:
                pass


def _ffmpeg_frame(asset, extra_in=(), limit_mb: int = 48) -> bytes:
    """用 ffmpeg 取一帧（视频海报帧 / 音频封面）。压缩包内文件走 stdin 流式。"""
    exe = _ffmpeg()
    if not exe:
        raise RuntimeError("未找到 ffmpeg")
    src, inner = asset["source_path"], (asset.get("inner_path") or "")
    args = [exe, "-hide_banner", "-loglevel", "error"]
    if not inner:
        args += list(extra_in) + ["-i", src]
    else:
        args += list(extra_in) + ["-i", "pipe:0"]
    args += ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"]
    if not inner:
        r = subprocess.run(args, capture_output=True, timeout=120)
        if not r.stdout:
            raise RuntimeError("ffmpeg 未取到画面")
        return r.stdout
    fh = _open_inner(asset)
    if fh is None:
        raise RuntimeError("无法读取压缩包内容")
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    _Pump(_Limited(fh, limit_mb * 1048576), proc.stdin).start()
    try:
        out = _read_capped(proc.stdout, limit_mb * 1048576)
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass
    if not out:
        raise RuntimeError("ffmpeg 未取到画面")
    return out


class _Limited:
    """只让下游读走前 n 字节，避免为一个缩略图解压整个大文件。"""

    def __init__(self, fh, limit):
        self.fh, self.limit = fh, limit

    def read(self, n=-1):
        if self.limit <= 0:
            return b""
        if n is None or n < 0:
            n = 1 << 20
        n = min(n, self.limit)
        b = self.fh.read(n)
        self.limit -= len(b)
        return b

    def close(self):
        try:
            self.fh.close()
        except Exception:
            pass


def _read_capped(stream, cap: int) -> bytes:
    buf = []
    got = 0
    try:
        while got < cap:
            b = stream.read(min(1 << 20, cap - got))
            if not b:
                break
            buf.append(b)
            got += len(b)
    except Exception:
        pass
    return b"".join(buf)


def _open_inner(asset):
    """打开压缩包内的文件流（返回可读对象）。"""
    src, inner = asset["source_path"], (asset.get("inner_path") or "")
    if not inner:
        try:
            return open(src, "rb")
        except OSError:
            return None
    return archives.open_inner(src, inner)


def materialize(asset, cap_mb: int = MATERIALIZE_MB):
    """把（压缩包内）文件取到本地临时文件，返回路径；调用方负责清理所在目录。"""
    import shutil
    import tempfile
    inner = asset.get("inner_path") or ""
    if not inner:
        return asset["source_path"], ""
    tmp = tempfile.mkdtemp(prefix="xc_th_", dir=str(config.STAGE_DIR))
    name = os.path.basename(inner.replace("\\", "/")) or "file"
    out = os.path.join(tmp, name)
    fh = _open_inner(asset)
    if fh is None:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("无法读取压缩包内容")
    try:
        with open(out, "wb") as w:
            _copy_capped(fh, w, cap_mb * 1048576)
    finally:
        try:
            fh.close()
        except Exception:
            pass
    return out, tmp


def _copy_capped(src, dst, cap: int):
    got = 0
    while got < cap:
        b = src.read(min(1 << 20, cap - got))
        if not b:
            break
        dst.write(b)
        got += len(b)


# ----------------------------------------------------------------- 主流程
def _from_render(asset, max_px):
    render_id = asset.get("render_id") or 0
    if not render_id:
        return None
    from . import db as _db
    r = _db.q("SELECT * FROM assets WHERE id=?", (render_id,), one=True)
    if not r:
        return None
    r = dict(r)
    try:
        data = _head_bytes(r["source_path"], r["inner_path"] or "",
                           int(r["size"] or 0) + 4096)
    except Exception:
        return None
    if not data:
        return None
    with open_scaled(data, max_px) as im:
        return encode_thumb(im, max_px)


def build(asset, max_px: int = 0, allow_render: bool = True):
    """生成缩略图并落盘。返回 (路径, 状态, 说明)。"""
    max_px = max_px or config.get("thumb_max_px", 720)
    kind = asset.get("kind") or "other"
    name = asset.get("orig_name") or os.path.basename(asset.get("source_path") or "")
    ext = config.ext_of(name)
    out = None
    status, msg = "ok", ""
    try:
        if allow_render and kind == "model":
            out = _from_render(asset, max_px)
        if out is None:
            if kind == "model":
                head = _head_bytes(asset["source_path"], asset.get("inner_path") or "", SKP_HEAD)
                png = skp_thumb_png(head)
                if png:
                    with open_scaled(png, max_px) as im:
                        out = encode_thumb(im, max_px)
            elif kind in ("image", "psd") and ext not in (".svg", ".eps", ".ai", ".cdr"):
                data = _head_bytes(asset["source_path"], asset.get("inner_path") or "",
                                   int(asset.get("size") or 0) + 4096)
                with open_scaled(data, max_px) as im:
                    out = encode_thumb(im, max_px)
            elif ext in config.TEXT_EXT or kind == "code":
                data = _head_bytes(asset["source_path"], asset.get("inner_path") or "",
                                   TEXT_HEAD)
                out = text_card(data, ext, max_px)
        if out is None and kind in ("video", "audio"):
            try:
                extra = ["-an"] if kind == "video" else ["-vn"]
                frame = _ffmpeg_frame(asset, extra)
                with open_scaled(frame, max_px) as im:
                    out = encode_thumb(im, max_px)
            except Exception as e:
                status, msg = "tile", str(e)[:120]
        if out is None and ext == ".pdf":
            try:
                out = _pdf_thumb(asset, max_px)
            except Exception as e:
                status, msg = "tile", str(e)[:120]
        if out is None and kind == "font" and ext in (".ttf", ".otf", ".ttc", ".otc"):
            try:
                out = _font_sample(asset, max_px)
            except Exception as e:
                status, msg = "tile", str(e)[:120]
        if out is None:
            out = _tile_bytes(asset, max_px)
        p = path_for(asset)
        with _LOCK:
            tmp = p + ".tmp"
            with open(tmp, "wb") as f:
                f.write(out)
            os.replace(tmp, p)
        return p, status, msg
    except Exception as e:
        try:
            out = _tile_bytes(asset, max_px)
            p = path_for(asset)
            with _LOCK:
                tmp = p + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(out)
                os.replace(tmp, p)
            return p, "tile", str(e)[:120]
        except Exception as e2:
            return None, "fail", str(e2)[:160]


def _pdf_thumb(asset, max_px):
    import pypdfium2 as pdfium
    data = _head_bytes(asset["source_path"], asset.get("inner_path") or "",
                       min(int(asset.get("size") or 0) + 4096, MATERIALIZE_MB * 1048576))
    pdf = pdfium.PdfDocument(data)
    page = pdf[0]
    scale = max(1.0, (max_px * 2) / max(1, page.get_width()))
    pil = page.render(scale=scale).to_pil()
    return encode_thumb(pil, max_px)


def _font_sample(asset, max_px):
    mp = max_px
    S = mp * 2 if mp <= 512 else mp
    im = Image.new("RGB", (S, S), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, S, 6], fill=(226, 232, 240))
    path, tmp = materialize(asset, cap_mb=64)
    try:
        font = ImageFont.truetype(path, int(S * 0.3))
        font2 = ImageFont.truetype(path, int(S * 0.13))
    except Exception:
        font = font2 = None
    try:
        if font:
            d.text((S / 2, S * 0.34), "Aa 字!", font=font, fill=(28, 32, 38), anchor="mm")
            d.text((S / 2, S * 0.6), "永和九年 0123456789", font=font2,
                   fill=(90, 96, 105), anchor="mm")
            d.text((S / 2, S * 0.76), "设计排版 素材标题", font=font2,
                   fill=(140, 146, 155), anchor="mm")
        else:
            d.text((S / 2, S / 2), "字体", font=_font(_CJK_FONTS, int(S * 0.2)),
                   fill=(120, 126, 135), anchor="mm")
    finally:
        if tmp:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
    im = im.resize((mp, mp), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=86, method=4)
    return buf.getvalue()


def get(asset, max_px: int = 0, allow_render: bool = True):
    """取缩略图：命中缓存直接返回，否则生成。"""
    p = path_for(asset)
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p, "ok", ""
    return build(asset, max_px, allow_render)