# -*- coding: utf-8 -*-
"""收藏功能的自检脚本（不需要你的真实素材，也不碰你的真实数据）。

全程在系统临时目录里跑：临时数据目录 + 临时端口，跑完自动删干净。
覆盖：
  1  收藏 / 取消收藏（素材库里的条目，按 id）
  2  库外的文件也能收藏（按路径，自动补进索引，缩略图才有得生成）
  3  收藏时间 fav_at 有没有写进去 / 取消时有没有清零
  4  「只看收藏」（fav=1）
  5  「按收藏时间」排序
  6  「浏览文件」里也带收藏星标
  7  文件夹收藏不了、乱七八糟的参数不会崩
  8  老版本的库（没有 fav_at 列）能自动补列，原有收藏不丢

用法：  python tools/favorite_test.py
"""
import json, os, shutil, socket, subprocess, sys, tempfile, time
import urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(tempfile.gettempdir(), "xc_favorite_test")
shutil.rmtree(TMP, ignore_errors=True)
DATA = os.path.join(TMP, "data")
LIB = os.path.join(TMP, "假素材库")
OUT = os.path.join(TMP, "库外")
for _d in (DATA, LIB, OUT):
    os.makedirs(_d, exist_ok=True)

LIB_SKP = os.path.join(LIB, "单人沙发.skp")
LIB_JPG = os.path.join(LIB, "旧效果图.jpg")
OUT_PDF = os.path.join(OUT, "外来参考图.pdf")
OUT_TXT = os.path.join(OUT, "说明.txt")
for _p, _n in ((LIB_SKP, 2048), (LIB_JPG, 1024), (OUT_PDF, 512), (OUT_TXT, 128)):
    with open(_p, "wb") as f:
        f.write(b"x" * _n)

FAIL = []
def chk(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAIL.append(msg)

# ---------------------------------------------------------------- 造一个已有索引的库
os.environ["XC_DATA"] = DATA
sys.path.insert(0, ROOT)
from app.sulib import db          # noqa: E402

_INS = ("INSERT INTO assets(kind,name,ext,source_type,source_path,inner_path,folder,size,"
        "mtime,added_at,thumb_status,favorite,fav_at) VALUES(?,?,?,'file',?,'','',?,1,1,?,?,?)")
db.ex(_INS, ("image", "旧效果图", ".jpg", LIB_JPG, 1024, "ok", 1, 111))
db.ex(_INS, ("model", "单人沙发", ".skp", LIB_SKP, 2048, "pending", 0, 0))
OLD_ID = db.q("SELECT id FROM assets WHERE source_path=?", (LIB_JPG,), one=True)["id"]
SKP_ID = db.q("SELECT id FROM assets WHERE source_path=?", (LIB_SKP,), one=True)["id"]
db.conn().close()          # 服务要用同一个库文件，先松手

# ---------------------------------------------------------------- 起一个临时端口的服务
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p

PORT = free_port()
BASE = "http://127.0.0.1:%d" % PORT
env = dict(os.environ, XC_PORT=str(PORT), XC_DATA=DATA, PYTHONIOENCODING="utf-8")
proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "server.py")],
                        env=env, cwd=os.path.join(ROOT, "app"),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=(0x08000000 if os.name == "nt" else 0))

def api(path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))

def asset(aid):
    return api("/api/asset/%d" % aid)

def browse(d):
    return api("/api/browse?path=" + urllib.parse.quote(d))

try:
    for _ in range(160):
        try:
            api("/api/state"); break
        except Exception:
            time.sleep(0.25)
    else:
        raise SystemExit("服务起不来，测试中止")

    print("[1] 起始状态：库里已经有一条老收藏")
    st = api("/api/state")
    chk(st["favorites"] == 1, "「我的收藏」计数 = 1（实际 %s）" % st["favorites"])
    d = api("/api/assets?fav=1")
    chk(d["total"] == 1 and d["items"][0]["name"] == "旧效果图", "「只看收藏」只返回那一条")
    chk(api("/api/assets?fav=1")["items"][0]["fav_at"] == 111, "老收藏的收藏时间是 111")

    print("\n[2] 按 id 收藏（素材库里的条目）")
    r = api("/api/favorite", {"ids": [SKP_ID], "value": True})
    chk(r["ok"] == 1 and r["state"][str(SKP_ID)] == 1, "接口返回 ok=1、状态=已收藏")
    chk(asset(SKP_ID)["favorite"] == 1, "条目已经是收藏")
    chk(asset(SKP_ID)["fav_at"] > 1000, "收藏时间写进去了（%s）" % asset(SKP_ID)["fav_at"])
    chk(api("/api/state")["favorites"] == 2, "收藏计数变成 2")

    print("\n[3] 库外的文件：按路径收藏，会自动补进索引")
    r = api("/api/favorite", {"paths": [OUT_PDF], "value": True})
    chk(r["ok"] == 1 and r["missed"] == 0, "库外文件收藏成功、没有漏项")
    PID = r["ids"][0]
    got = asset(PID)
    chk(got["source_path"] == OUT_PDF, "补进索引的路径对得上")
    chk(got["kind"] == "pdf", "类型自动认出来了（%s）" % got["kind"])
    chk(got["favorite"] == 1, "它就是收藏状态")

    print("\n[4] 按收藏时间排序：最近收藏的排前面")
    names = [x["name"] for x in api("/api/assets?fav=1&sort=fav")["items"]]
    chk(len(names) == 3, "收藏一共 3 条（%s）" % names)
    chk(names[0] == "外来参考图", "刚收藏的排第一（%s）" % names[0])
    chk(names[-1] == "旧效果图", "老收藏排最后（%s）" % names[-1])

    print("\n[5] 「浏览文件」里也带收藏星标")
    by = {f["name"]: f for f in browse(OUT)["files"]}
    chk(by["外来参考图.pdf"]["favorite"] == 1, "收藏过的文件 favorite=1")
    chk(by["外来参考图.pdf"]["asset_id"] == PID, "还带上了索引里的 id")
    chk(by["说明.txt"]["favorite"] == 0, "没收藏的 favorite=0")
    by2 = {f["name"]: f for f in browse(LIB)["files"]}
    chk(by2["单人沙发.skp"]["favorite"] == 1, "素材库目录里的星标也对")

    print("\n[6] 取消收藏")
    r = api("/api/favorite", {"paths": [OUT_PDF], "value": False})
    chk(r["ok"] == 1, "取消成功")
    chk(asset(PID)["favorite"] == 0, "状态变成未收藏")
    chk(asset(PID)["fav_at"] == 0, "收藏时间清零了")
    chk({f["name"]: f for f in browse(OUT)["files"]}["外来参考图.pdf"]["favorite"] == 0,
        "浏览里的星标跟着灭了")
    chk(api("/api/state")["favorites"] == 2, "计数回到 2")

    print("\n[7] 重复收藏不会刷新收藏时间")
    t1 = asset(SKP_ID)["fav_at"]
    api("/api/favorite", {"ids": [SKP_ID], "value": True})
    chk(asset(SKP_ID)["fav_at"] == t1, "收藏时间还是原来那次")

    print("\n[8] 文件夹 / 参数边界")
    r = api("/api/favorite", {"paths": [OUT], "value": True})
    chk(r["ok"] == 0 and r["missed"] == 1, "文件夹收藏不了，算作漏项")
    r = api("/api/favorite", {"ids": ["abc", None], "value": True})
    chk(r["ok"] == 0, "乱七八糟的 id 不崩（%s）" % r)
    r = api("/api/favorite", {"ids": [99999999], "value": True})
    chk(r["ok"] == 1 and api("/api/state")["favorites"] == 2, "不存在的 id 不会凭空多出收藏")
    r = api("/api/favorite", {"ids": [], "paths": [], "value": True})
    chk(r["ok"] == 0, "空选择不报错")
    r = api("/api/favorite", {"paths": ["D:\\肯定不存在\\x.skp"], "value": True})
    chk(r["ok"] == 0 and r["missed"] == 1, "不存在的文件算漏项，不崩")
    r = api("/api/favorite", {"ids": [OLD_ID], "paths": [OUT_TXT], "value": True})
    chk(r["ok"] == 2, "id 和路径可以一起传（%s）" % r["ok"])
finally:
    try:
        proc.terminate(); proc.wait(timeout=10)
    except Exception:
        try: proc.kill()
        except Exception: pass

# ---------------------------------------------------------------- 老版本的库能不能升级
print("\n[9] 老版本的库（没有 fav_at 列）自动补列")
OLD = os.path.join(TMP, "老库")
os.makedirs(OLD, exist_ok=True)
probe = """import os, sqlite3, sys
os.environ["XC_DATA"] = r"__OLD__"
sys.path.insert(0, r"__ROOT__")
from app.sulib import db
old_schema = "\\n".join(l for l in db.SCHEMA.split("\\n") if "fav_at" not in l)
c = sqlite3.connect(os.path.join(r"__OLD__", "index.db"))
c.executescript(old_schema)
c.execute("INSERT INTO assets(kind,name,source_type,source_path,inner_path,favorite) "
          "VALUES('model','旧模型','file','D:\\\\x\\\\a.skp','',1)")
c.commit(); c.close()
db.conn()
row = db.q("SELECT * FROM assets", one=True)
idx = [r[1] for r in db.conn().execute("PRAGMA index_list(assets)")]
ok = ("fav_at" in row.keys()) and row["favorite"] == 1 and ("idx_assets_fav_at" in idx)
print("PROBE_OK" if ok else "PROBE_BAD")
""".replace("__OLD__", OLD).replace("__ROOT__", ROOT)
pr = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    env=dict(os.environ, PYTHONIOENCODING="utf-8"))
out = (pr.stdout or "") + (pr.stderr or "")
chk("PROBE_OK" in out, "老库能自动补 fav_at 列，原有收藏没丢" +
    ("" if "PROBE_OK" in out else "  ← " + out.strip()[-300:]))

shutil.rmtree(TMP, ignore_errors=True)
print("\n结果:", "全部通过" if not FAIL else "有失败项：%s" % FAIL)
sys.exit(1 if FAIL else 0)
