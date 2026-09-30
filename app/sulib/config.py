# -*- coding: utf-8 -*-
"""小虫管理器 配置：路径、文件类型表、程序设置。"""
import json
import os
import sys
from pathlib import Path

APP_NAME = "小虫管理器"
APP_SUB = "本地资源管理器 · 素材库"
APP_EN = "XiaoChongManager"
APP_VERSION = "1.2.2"

# ----------------------------------------------------------------- 路径
def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return APP_DIR


APP_DIR = _app_dir()
RES_DIR = _resource_dir()
def _res_sub(name: str) -> Path:
    """资源子目录：打包后在 _MEIPASS 下，开发时在 app/ 下。"""
    for d in (RES_DIR / name, APP_DIR / "app" / name, APP_DIR / name):
        if d.is_dir():
            return d
    return RES_DIR / name


STATIC_DIR = _res_sub("static")
BIN_DIR = _res_sub("bin")
ICON_PATH = RES_DIR / "小虫.ico"
if not ICON_PATH.is_file() and (APP_DIR / "app" / "小虫.ico").is_file():
    ICON_PATH = APP_DIR / "app" / "小虫.ico"
BUNDLED_7Z = BIN_DIR / "7z.exe"          # 随程序一起装的 7-Zip，解压不依赖外部安装


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        t = p / ".wtest"
        t.write_text("1", encoding="utf-8")
        t.unlink()
        return True
    except Exception:
        return False


def _data_dir() -> Path:
    env = os.environ.get("XC_DATA")
    if env:
        return Path(env).expanduser()
    cand = APP_DIR / "data"
    if _writable(cand):
        return cand
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / APP_EN / "data"


DATA_DIR = _data_dir()
MODEL3D_DIR = DATA_DIR / "model3d"       # SKP 3D 预览缓存
THUMB_DIR = DATA_DIR / "thumbs"
NESTED_DIR = DATA_DIR / "nested"
STAGE_DIR = DATA_DIR / "stage"
LOG_DIR = DATA_DIR / "logs"
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "index.db"

SEVENZIP_CANDIDATES = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
    r"D:\Program Files\7-Zip\7z.exe",
    r"D:\7-Zip\7z.exe",
]

FFMPEG_CANDIDATES = [
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"D:\ffmpeg\bin\ffmpeg.exe",
    os.path.join(os.environ.get("LOCALAPPDATA") or "", "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
]

# ----------------------------------------------------------------- 类型表
# 顺序决定归类优先级（靠前的先匹配）
GROUPS = [
    ("model", "3D 模型", "#f97316", [
        ".skp", ".3ds", ".max", ".obj", ".fbx", ".dae", ".stl", ".rvt", ".ifc",
        ".blend", ".c4d", ".3dm", ".step", ".stp", ".iges", ".igs", ".ply", ".skb"]),
    ("cad", "CAD 图纸", "#475569", [
        ".dwg", ".dxf", ".dwt", ".dwf", ".plt", ".hpgl", ".hgl"]),
    ("psd", "分层 / 素材图", "#3b82f6", [
        ".psd", ".psb", ".xcf", ".tga", ".exr", ".hdr", ".dds", ".ktx", ".tif", ".tiff"]),
    ("image", "图片 / 效果图", "#38a169", [
        ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".bmp", ".gif", ".webp",
        ".heic", ".avif", ".ico", ".wmf", ".emf"]),
    ("vector", "矢量图", "#8b5cf6", [
        ".svg", ".svgz", ".eps", ".ai", ".cdr", ".pdfx"]),
    ("video", "视频", "#dc2626", [
        ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg",
        ".webm", ".rmvb", ".rm", ".3gp", ".ts", ".mts", ".m2ts", ".vob"]),
    ("audio", "音频", "#b7791f", [
        ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma", ".ape", ".mid"]),
    ("pdf", "PDF 文档", "#c53030", [".pdf"]),
    ("doc", "文档 / 表格", "#2b6cb0", [
        ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".ppt", ".pptx", ".rtf",
        ".wps", ".et", ".dps", ".odt", ".ods", ".odp", ".csv", ".txt", ".md", ".log"]),
    ("archive", "压缩包", "#b45309", [
        ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".cab",
        ".iso", ".001", ".z01", ".z02", ".lzh", ".arj"]),
    ("font", "字体", "#7c3aed", [
        ".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2", ".fon", ".fnt", ".shx"]),
    ("code", "代码 / 脚本", "#0f766e", [
        ".py", ".js", ".mjs", ".ts", ".json", ".xml", ".yml", ".yaml", ".html",
        ".htm", ".css", ".scss", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs",
        ".php", ".rb", ".sql", ".sh", ".ini", ".cfg", ".conf", ".toml", ".bat",
        ".cmd", ".ps1", ".lsp", ".vbs", ".reg", ".mdx", ".vue", ".jsx", ".tsx"]),
    ("app", "程序 / 快捷方式", "#64748b", [
        ".exe", ".msi", ".lnk", ".url", ".dll", ".msix", ".appx", ".apk"]),
    ("other", "其它文件", "#6b7280", []),
]

EXT2KIND = {}
KIND_LABEL = {}
KIND_COLOR = {}
for _k, _label, _color, _exts in GROUPS:
    KIND_LABEL[_k] = _label
    KIND_COLOR[_k] = _color
    for _e in _exts:
        EXT2KIND.setdefault(_e, _k)

KIND_ORDER = [g[0] for g in GROUPS]

MODEL_EXT = {e for e in EXT2KIND if EXT2KIND[e] == "model"}
IMAGE_EXT = {e for e in EXT2KIND if EXT2KIND[e] in ("image", "psd")}
ARCHIVE_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz",
               ".cab", ".iso", ".001", ".z01", ".lzh", ".arj"}
TEXT_EXT = {".txt", ".md", ".csv", ".log", ".ini", ".cfg", ".conf", ".json",
            ".xml", ".yml", ".yaml", ".html", ".htm", ".css", ".js", ".mjs",
            ".ts", ".py", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs",
            ".php", ".rb", ".sql", ".sh", ".bat", ".cmd", ".ps1", ".vue",
            ".jsx", ".tsx", ".srt", ".ass", ".lsp", ".reg", ".toml", ".mdx"}
VIDEO_EXT = {e for e in EXT2KIND if EXT2KIND[e] == "video"}
AUDIO_EXT = {e for e in EXT2KIND if EXT2KIND[e] == "audio"}
FONT_EXT = {e for e in EXT2KIND if EXT2KIND[e] == "font"}


def kind_of(name: str) -> str:
    return EXT2KIND.get(os.path.splitext(name or "")[1].lower(), "other")


def norm_ext(ext: str) -> str:
    """把用户输入的扩展名规范成 ".psd" 这种形式。"""
    e = (ext or "").strip().lower()
    if not e:
        return ""
    if not e.startswith("."):
        e = "." + e
    return e


def ext_of(name: str) -> str:
    return os.path.splitext(name or "")[1].lower()


def as_bool(v, default: bool = False) -> bool:
    """把勾选框的值稳稳地变成真假。

    前端正常传的是 true/false，但偶尔会传来字符串或数字，
    这里统一处理：'false' / '0' / 'no' / 'off' / 空 都算假，其余非空算真。
    """
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return v != 0
    t = str(v).strip().lower()
    if t in ("", "0", "false", "no", "off", "none", "null"):
        return False
    if t in ("1", "true", "yes", "on"):
        return True
    return default

# ----------------------------------------------------------------- 设置
# 首次运行时自动收录的目录：只挑本机真实存在的，都没有就留空，
# 由界面上的「+ 添加素材目录」手动加。
DEFAULT_ROOT_HINTS = (r"D:\SU素材", r"D:\素材库", r"D:\3D素材", r"D:\模型素材")
DEFAULT_ROOTS = [p for p in DEFAULT_ROOT_HINTS if os.path.isdir(p)]

DEFAULTS = {
    "roots": DEFAULT_ROOTS,
    "seven_zip": "",
    "ffmpeg": "",
    "thumb_max_px": 720,
    "workers": 8,
    "index_images": True,
    "image_max_mb": 30,
    "index_all_files": True,
    "max_file_mb": 2048,
    "index_inside_archives": True,
    "text_preview_kb": 256,
    "use_3d": True,
    "sketchup_exe": "",
    # 每种扩展名单独指定打开程序：{".psd": "C:\\...\\Photoshop.exe"}，空 = 用系统默认
    "open_with": {},
    # 看图软件：所有图片类型通用；空 = 用系统默认；"@photoviewer" = 用 Windows 自带的照片查看器
    "image_viewer": "",
    # 定时提醒清理缓存
    "cache_remind_on": True,
    # True = 同一次超限只提醒一次（清理后自动重新武装）
    "cache_remind_once": True,
    "cache_remind_min": 60,
    "cache_limit_mb": 1500,
    # 关掉界面窗口时怎么办：
    #   "tray" = 隐藏到任务栏（后台继续跑，右下角托盘留图标，双击就回来）
    #   "quit" = 直接退出程序（后台服务一起关掉）
    "close_action": "tray",
    # 图片（.jpg / .png 这类）要不要生成和显示缩略图
    #   False = 不生成也不显示：列表里图片只占一个「点开看原图」的格子，
    #           不花时间生成、也不占缓存；想看就点开，直接看原图更清楚
    #   True  = 照旧生成并显示缩略图
    "image_thumbs": False,
    # 列表卡片显示：
    #   show_thumbs = 总开关。False = 所有卡片都不显示缩略图，只显示文字（翻页更快）
    #   show_path   = 卡片上写「真实文件名 + 所在文件夹的完整路径」（方便在硬盘里找）
    "show_thumbs": True,
    "show_path": False,
}


def detect_seven_zip() -> str:
    """优先用随程序内嵌的 7-Zip，其次本机安装的，最后看 PATH。"""
    if BUNDLED_7Z.is_file():
        return str(BUNDLED_7Z)
    for cand in SEVENZIP_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    import shutil as _sh
    return _sh.which("7z") or ""


def detect_ffmpeg() -> str:
    for cand in FFMPEG_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    import shutil as _sh
    return _sh.which("ffmpeg") or ""


def ensure_dirs() -> None:
    for d in (DATA_DIR, THUMB_DIR, NESTED_DIR, STAGE_DIR, LOG_DIR, MODEL3D_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    ensure_dirs()
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    # 内嵌的 7-Zip 永远优先，保证「不装 7-Zip 也能解压」
    if BUNDLED_7Z.is_file():
        cfg["seven_zip"] = str(BUNDLED_7Z)
    elif not cfg.get("seven_zip"):
        cfg["seven_zip"] = detect_seven_zip()
    if not cfg.get("ffmpeg"):
        cfg["ffmpeg"] = detect_ffmpeg()
    return cfg


def save(cfg: dict) -> dict:
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def get(key, default=None):
    return load().get(key, DEFAULTS.get(key, default))