# -*- coding: utf-8 -*-
"""选择性清理缓存的自检脚本（不需要服务，也不需要你的真实素材）。

在临时数据目录里造出假的索引 + 假的缓存文件，验证：
  · 明细统计分得对不对（嵌套按分类 / 缩略图按类型 / 暂存按新旧）
  · 只清勾选的项，没勾的一点都不动
  · key 只接受 16 位十六进制，防目录穿越
  · 清掉之后缩略图还能重新生成

用法：  python tools/cache_clean_test.py
"""
import os, shutil, sys, tempfile, time

TMP = os.path.join(tempfile.gettempdir(), "xc_cache_clean_test")
shutil.rmtree(TMP, ignore_errors=True)
DATA = os.path.join(TMP, "data")
os.makedirs(DATA, exist_ok=True)
os.environ["XC_DATA"] = DATA
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.sulib import config, db, ops, thumbs  # noqa: E402

FAIL = []
def chk(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAIL.append(msg)

NEST, THUMB = str(config.NESTED_DIR), str(config.THUMB_DIR)
STAGE, M3D = str(config.STAGE_DIR), str(config.MODEL3D_DIR)
for d in (NEST, THUMB, STAGE, M3D):
    os.makedirs(d, exist_ok=True)

# ---------------------------------------------------------------- 造数据
KEYS = {"a" * 15 + "1": "灯具", "b" * 15 + "2": "灯具", "c" * 15 + "3": "茶几边几"}
SIZES = {k: 2 * 1024 * (i + 1) for i, k in enumerate(KEYS)}   # 每个 key 目录的大小
for i, (key, cat) in enumerate(KEYS.items()):
    for j in range(2):
        p = os.path.join(NEST, key, "sub%d" % j, "inner.7z")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"x" * (1024 * (i + 1)))
    db.ex("INSERT INTO assets(kind,name,source_type,source_path,inner_path,category,"
          "mtime,size,thumb_status) VALUES('model',?, 'archive',?,'m.skp',?,1,1,'pending')",
          ("模型%d" % i, os.path.join(NEST, key, "sub0", "inner.7z"), cat))
    db.ex("INSERT INTO archive_meta(path,mtime,size,listed_at) VALUES(?,1,1,1)",
          (os.path.join(NEST, key, "sub0", "inner.7z"),))

ids = {}
for kind, name in (("image", "图"), ("model", "模"), ("doc", "文")):
    db.ex("INSERT INTO assets(kind,name,source_type,source_path,inner_path,thumb_status) "
          "VALUES(?,'%s','file',?,'','ok')" % name, (kind, "C:\\假素材\\" + name))
    ids[kind] = db.q("SELECT id FROM assets ORDER BY id DESC LIMIT 1", one=True)[0]

def row(aid):
    return dict(db.q("SELECT * FROM assets WHERE id=?", (aid,), one=True))

def mkthumb(aid):
    p = thumbs.path_for(row(aid))
    with open(p, "wb") as f:
        f.write(b"w" * 2048)
    return p

thumb_paths = {k: mkthumb(v) for k, v in ids.items()}
orphan = os.path.join(THUMB, "0" * 24 + ".webp")
with open(orphan, "wb") as f:
    f.write(b"o" * 4096)
for d, tag in ((STAGE, "stage"), (M3D, "m3d")):
    for i in range(3):
        p = os.path.join(d, "%s%d.bin" % (tag, i))
        with open(p, "wb") as f:
            f.write(b"s" * 8192)
        t = time.time() - i * 40 * 86400
        os.utime(p, (t, t))

# ---------------------------------------------------------------- 明细
print("[1] 明细统计")
d = ops.cache_detail(force=True)
secs = {s["id"]: s for s in d["sections"]}
chk(set(secs) == {"nested", "thumbs", "stage", "model3d"}, "四个分区都在：%s" % sorted(secs))
chk(sorted(g["label"] for g in secs["nested"]["groups"]) == ["灯具", "茶几边几"],
    "嵌套按分类分：%s" % sorted(g["label"] for g in secs["nested"]["groups"]))
chk(sorted(g["sel"]["keys"] for g in secs["nested"]["groups"]) ==
    sorted([[k for k, c in KEYS.items() if c == "灯具"], ["c" * 15 + "3"]]), "每个分类的 key 分对了")
chk(secs["nested"]["size"] == sum(SIZES.values()), "嵌套体积 = 各分类之和 %d" % secs["nested"]["size"])
kinds = {g["id"]: g for g in secs["thumbs"]["groups"]}
chk({"thumbs:image", "thumbs:model", "thumbs:doc"} <= set(kinds), "缩略图按类型分：%s" % sorted(kinds))
chk(all(g["sel"].get("what") for s in d["sections"] for g in s["groups"]), "每一项都带选择器")
chk(kinds.get("thumbs_orphan") and kinds["thumbs_orphan"]["size"] == 4096,
    "残留缩略图单独列出（%s 字节）" % (kinds.get("thumbs_orphan") or {}).get("size"))
chk([g["id"] for g in secs["stage"]["groups"]] == ["stage:today", "stage:old"],
    "暂存按新旧分：%s" % [g["id"] for g in secs["stage"]["groups"]])
chk(secs["nested"]["groups"][-1]["id"] != "nested_stale", "没有旧的重复记录时不硬塞这一项")

# ---------------------------------------------------------------- 只清一项
print("\n[2] 只清「茶几边几」这一类嵌套解压")
victim = [g for g in secs["nested"]["groups"] if g["label"] == "茶几边几"][0]["sel"]
r = ops.cleanup_cache([victim])
chk(not os.path.isdir(os.path.join(NEST, "c" * 15 + "3")), "目标目录已删")
chk(all(os.path.isdir(os.path.join(NEST, k)) for k, c in KEYS.items() if c == "灯具"), "别的分类目录都在")
chk(db.count("SELECT COUNT(*) FROM archive_meta") == 2, "archive_meta 只删了对应的那条")
chk(db.count("SELECT COUNT(*) FROM assets") == 6, "素材记录没被删（靠重新扫描恢复）")
chk(r["freed"] == SIZES["c" * 15 + "3"], "返回释放 %s 字节" % r["freed"])
chk(r["done"] == ["嵌套解压"], "返回里写明清了什么：%s" % r["done"])

print("\n[3] key 防目录穿越")
before = sorted(os.listdir(NEST))
ops.cleanup_cache([{"what": "nested", "keys": ["../../Windows", "..", "zz", "", None, 7]}])
chk(sorted(os.listdir(NEST)) == before, "非法 key 全部忽略，目录没变")

print("\n[4] 只清「文档 / 表格」的缩略图")
n0 = len(os.listdir(THUMB))
ops.cleanup_cache([{"what": "thumbs", "kind": "doc"}])
chk(len(os.listdir(THUMB)) == n0 - 1, "只少了 doc 那一张（%d -> %d）" % (n0, len(os.listdir(THUMB))))
chk(not os.path.exists(thumb_paths["doc"]), "doc 的图删了")
chk(os.path.exists(thumb_paths["image"]) and os.path.exists(thumb_paths["model"]), "图片和模型的图都还在")
chk(db.count("SELECT COUNT(*) FROM assets WHERE kind='doc' AND thumb_status='pending'") == 1, "doc 素材标记待重建")
chk(db.count("SELECT COUNT(*) FROM assets WHERE kind='image' AND thumb_status='ok'") == 1, "别的类型状态没动")

print("\n[5] 清「图片 / 效果图」")
ops.cleanup_cache([{"what": "thumbs", "kind": "image"}])
chk(not os.path.exists(thumb_paths["image"]), "图片的图删了")
chk(os.path.exists(thumb_paths["model"]), "模型的图没被牵连")
chk(db.count("SELECT COUNT(*) FROM assets WHERE kind='image' AND thumb_status='pending'") == 1, "对应素材标记待重建")

print("\n[6] 清残留缩略图")
ops.cleanup_cache([{"what": "thumbs_orphan"}])
chk(not os.path.exists(orphan), "残留文件已删")
chk(os.path.exists(thumb_paths["model"]), "在用的没被牵连")

print("\n[7] 只清「一个月以前」的暂存 / 3D 缓存")
ops.cleanup_cache([{"what": "stage", "buckets": ["old"]}, {"what": "model3d", "buckets": ["old"]}])
chk(os.listdir(STAGE) == ["stage0.bin"], "暂存只剩今天的：%s" % os.listdir(STAGE))
chk(os.listdir(M3D) == ["m3d0.bin"], "3D 缓存只剩今天的：%s" % os.listdir(M3D))

print("\n[8] 清掉之后能重新生成 + 边界情况")
first_image = db.q("SELECT * FROM assets WHERE kind='image' ORDER BY id LIMIT 1", one=True)
p2, status, _msg = thumbs.get(dict(first_image))
chk(bool(p2) and os.path.exists(p2), "缩略图能重新生成（%s）" % status)
chk(ops.cleanup_cache([])["freed"] == 0, "空选择什么都不清")
chk(ops.cleanup_cache([{"what": "没这个"}, 42, "x"])["freed"] == 0, "乱七八糟的选择不崩")

db.conn().close()          # 不关掉连接，Windows 上删不掉临时目录
shutil.rmtree(TMP, ignore_errors=True)
print("\n结果:", "全部通过" if not FAIL else "有失败项：%s" % FAIL)
sys.exit(1 if FAIL else 0)
