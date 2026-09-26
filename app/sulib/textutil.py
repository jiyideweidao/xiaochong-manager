"""文件名文本处理：修复乱码、识别广告命名、推导显示名/分类/风格。"""
import os
import re

BOX = (r"[░▒▓│┤╡╢╖╕╣║╗╝╜╛┐└┴┬├─┼╞╟╚╔╩╦╠═╬╧╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀■◄▲▼]")
BOX_RE = re.compile(BOX)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
TEXTURE_DIRS = {"map", "maps", "texture", "textures", "material", "materials",
                "backup", "backups", "temp", "tmp"}

AD_PATTERNS = [
    r"更多免费资料", r"淘宝店铺", r"淘宝店", r"扫码关注", r"关注公众号", r"模型下载",
    r"会员全系列", r"免费拍", r"免费领取", r"免费获取", r"全套素材", r"设计邦",
    r"微信号", r"公众号", r"加群", r"QQ群", r"客服", r"订阅店铺", r"淘宝", r"手机淘宝",
    r"www\.", r"http", r"\.com", r"\.cn", r"素材号", r"出品", r"资料", r"获取",
    r"会员全店", r"全店任下", r"全店免费", r"会员", r"任下", r"点击进入", r"店铺首页",
]
AD_RE = re.compile("|".join(AD_PATTERNS), re.I)
ID_RE = re.compile(r"ID[_\-]?\d{5,}", re.I)
NUM_ONLY_RE = re.compile(r"^[\d\s._\-()（）\[\]【】~]+$")
HASH_RE = re.compile(r"^(?:[0-9a-f]{10,}|[0-9a-z]{16,})$")
JUNK_WORDS = re.compile(r"(New|NEW|新)\s*模型|模型|图片|案例|素材|下载|免费|合集|整理", re.I)
ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SET_COUNT_RE = re.compile(r"[（(]\s*\d+\s*套[^)）]*[)）]")
PAREN_RE = re.compile(r"[（(][^)）]{0,24}[)）]")
LEAD_NUM_RE = re.compile(r"^\s*\d{1,4}\s*[.\-、,，:：]\s*")

STYLES = ["新中式", "现代中式", "现代简约", "现代轻奢", "轻奢", "现代", "中式", "北欧",
          "日式", "美式", "法式", "欧式", "极简", "简约", "工业风", "工业", "复古",
          "侘寂", "原木", "奶油", "田园", "地中海", "东南亚", "后现代", "港式",
          "意式", "中古", "混搭", "禅意", "大理石", "北欧风", "新古典"]


def repair(s: str) -> str:
    """把老下载站的 GBK 字节被当 CP437 显示的乱码还原成中文。"""
    if not s or not BOX_RE.search(s):
        return s
    for frm in ("cp437", "cp850"):
        try:
            cand = s.encode(frm).decode("gbk")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if cand != s and CJK_RE.search(cand):
            return cand
    return s


def clean(s: str) -> str:
    s = repair(s or "")
    s = AD_RE.sub(" ", s)
    s = ID_RE.sub(" ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" _-·.,、,;；:：")


def is_generic(stem: str) -> bool:
    s = clean(stem).strip()
    if not s:
        return True
    if NUM_ONLY_RE.match(s):
        return True
    if AD_RE.search(repair(stem)) or ID_RE.search(stem):
        return True
    body = JUNK_WORDS.sub("", s).strip(" _-·.,、:：")
    if len(CJK_RE.findall(body)) < 2 and not re.search(r"[A-Za-z]{3,}", body):
        return True
    return False


def split_segments(inner: str):
    parts = [p for p in re.split(r"[\\/]+", inner or "") if p.strip()]
    if not parts:
        return [], ""
    return parts[:-1], parts[-1]


def meaningful_dirs(dirs):
    out = []
    for d in dirs or []:
        dd = repair(d).strip()
        if not dd:
            continue
        low = dd.lower()
        if low in TEXTURE_DIRS:
            continue
        if re.fullmatch(r"attachment[_\-]?\d{6,}", low):
            continue
        if NUM_ONLY_RE.match(dd):
            continue
        if HASH_RE.match(low):
            continue
        out.append(dd)
    return out


def display_name(basename: str, dirs, fallback: str = "") -> str:
    raw_stem = re.sub(r"\.[^.]+$", "", basename or "")
    good = meaningful_dirs(dirs)
    cs = clean(raw_stem)
    if cs and not is_generic(raw_stem):
        return cs
    if good:
        return tidy_label(good[-1])
    if cs and not AD_RE.search(repair(raw_stem)) and not is_generic(cs):
        return cs
    fb = tidy_label(fallback)
    if fb:
        return fb
    return cs or raw_stem.strip() or "未命名"


def tidy_label(s: str) -> str:
    """显示名/分类用：去掉前导编号与 (114套551M) 这类统计后缀，其余保留。"""
    s = repair(s or "").strip()
    s = LEAD_NUM_RE.sub("", s)
    s = SET_COUNT_RE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" _-·.,、,;；:：")[:40]


def clean_category(s: str) -> str:
    s = repair(s or "").strip()
    s = LEAD_NUM_RE.sub("", s)
    s = SET_COUNT_RE.sub("", s)
    s = PAREN_RE.sub("", s)
    s = re.sub(r"[-_—]{2,}", "-", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" _-·.,、,;；:：")[:24]


def category_of(dirs, container_stem: str = "", top: str = "") -> str:
    good = meaningful_dirs(dirs)
    if good:
        c = clean_category(good[0])
        if c:
            return c
    cs = clean_category(container_stem)
    if cs and not is_generic(cs):
        return cs
    return repair(top or "") or "未分类"


def detect_style(*texts) -> str:
    blob = " ".join(repair(t or "") for t in texts)
    for s in STYLES:
        if s in blob:
            return s
    return ""


def group_of(dirs) -> str:
    good = meaningful_dirs(dirs)
    return "/".join(good[-2:]) if good else ""


def folder_label(dirs) -> str:
    return " / ".join(repair(d) for d in meaningful_dirs(dirs))


def cover_key(path: str) -> str:
    if not path:
        return ""
    return (os.path.dirname(path).lower() + "\x1f"
            + os.path.splitext(os.path.basename(path))[0].lower())


def safe_filename(s: str, fallback: str = "未命名") -> str:
    s = ILLEGAL_RE.sub("", (s or "").strip()).strip(" .")
    return s[:120] or fallback