# -*- coding: utf-8 -*-
"""小虫管理器 端到端冒烟自检。

对着「已经在跑的」服务跑一遍：缩略图 / 压缩包 / 内嵌解压 / 文件关联 / 3D 看图 / 前端交互，
顺手把过程截图丢到 tools/shots/。

用法：
    python tools/smoke_test.py
    XC_SAMPLE="D:\\我的素材" python tools/smoke_test.py

环境变量：
    XC_BASE    服务地址（默认 http://127.0.0.1:8765）
    XC_SAMPLE  抽查素材的目录（默认取服务里第一个素材库根目录）
    XC_EXE     小虫管理器.exe 的路径，连不上服务时用它顺手启动（默认不启动）
    XC_SHOTS   截图输出目录（默认 tools/shots）
"""
import asyncio, base64, json, os, shutil, struct, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request
import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("XC_BASE") or "http://127.0.0.1:8765"
OUT = os.environ.get("XC_SHOTS") or os.path.join(HERE, "shots")
EXE = os.environ.get("XC_EXE") or ""
SAMPLE = os.environ.get("XC_SAMPLE") or ""
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT, exist_ok=True)

def _wait_up(tries):
    for _ in range(tries):
        try:
            return json.load(urllib.request.urlopen(BASE + "/api/state", timeout=5))
        except Exception:
            time.sleep(1)
    return None

st = _wait_up(2)
if st is None and EXE and os.path.isfile(EXE):
    subprocess.Popen([EXE, "--no-browser"])
    st = _wait_up(90)
if st is None:
    raise SystemExit("连不上 " + BASE + "，请先启动小虫管理器（或用 XC_EXE 指到 exe）")
SAMPLE = SAMPLE or (st.get("roots") or [{}])[0].get("path", "")
BROWSE = SAMPLE or (os.environ.get("SystemDrive", "C:") + "\\")
print("服务:", BASE, "|", SAMPLE or "（没有素材库根目录）")
print("服务版本:", json.dumps({k: st.get(k) for k in ("app", "sub", "version", "total", "models", "images", "archives", "thumbs_ready")}, ensure_ascii=False))

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return json.loads(r.read())

# 缩略图全覆盖抽查
import glob
def first(pattern):
    g = glob.glob(pattern, recursive=True)
    return g[0] if g else None
samples = [
    ("SketchUp 模型", first(os.path.join(SAMPLE, "**", "*.skp")) if SAMPLE else None),
    ("图片", first(os.path.join(SAMPLE, "**", "*.jpg")) if SAMPLE else None),
    ("压缩包", first(os.path.join(SAMPLE, "**", "*.zip")) if SAMPLE else None),
    ("Word 文档", first(os.path.join(SAMPLE, "**", "*.docx")) if SAMPLE else None),
    ("压包内图片", None),
]
for label, p in samples:
    if not p:
        print("  %-14s 无样本" % label); continue
    t0 = time.time()
    try:
        st2, b = (lambda u: (u.status, u.read()))(urllib.request.urlopen(
            BASE + "/api/thumb-path?path=" + urllib.parse.quote(p), timeout=60))
        print("  %-14s %s %6d B  %.2fs  %s" % (label, st2, len(b), time.time() - t0, os.path.basename(p)))
    except urllib.error.HTTPError as e:
        print("  %-14s HTTP %s  %s" % (label, e.code, os.path.basename(p)))
# 压缩包内部预览路径
z = first(os.path.join(SAMPLE, "**", "*.zip")) if SAMPLE else None
if z:
    inner = get("/api/archive-entries?path=" + urllib.parse.quote(z))
    img = [x for x in inner["items"] if x["inner"].lower().endswith((".jpg", ".png", ".skp"))][:1]
    if img:
        u = BASE + "/api/raw?path=" + urllib.parse.quote(z) + "&inner=" + urllib.parse.quote(img[0]["inner"])
        b = urllib.request.urlopen(u, timeout=60).read()
        print("  压缩包内取流   %6d B  %s" % (len(b), img[0]["inner"]))
print("索引统计:", get("/api/kinds")["total"], "条 /", len([k for k in get("/api/kinds")["kinds"] if k["count"]]), "种类型")

st3 = get("/api/state")
print("解压内核:", "已内嵌" if st3.get("seven_zip_bundled") else "外部/缺失", st3.get("seven_zip"))

# ---- 内嵌解压：真解一个文件出来 ----
def post(path, data):
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        BASE + path, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}),
        timeout=600).read())

tmpd = tempfile.mkdtemp(prefix="xc_selfcheck_")
if z:
    ent = get("/api/archive-entries?path=" + urllib.parse.quote(z))
    small = sorted([x for x in ent["items"] if 0 < x["size"] < 4 * 1048576], key=lambda x: x["size"])[:1]
    if small:
        t0 = time.time()
        r = post("/api/archive-extract-one", {"archive": z, "inner": small[0]["inner"], "dest": tmpd})
        got = []
        for rt, _, fs in os.walk(tmpd):
            got += fs
        print("  单文件解压  %s  %.2fs  -> %s" % ("OK" if r.get("ok") and got else "失败", time.time() - t0, got[:1]))
# rar / 7z 走内嵌 7-Zip
alt = (first(os.path.join(SAMPLE, "**", "*.rar")) or first(os.path.join(SAMPLE, "**", "*.7z"))) if SAMPLE else None
if alt:
    try:
        ent = get("/api/archive-entries?path=" + urllib.parse.quote(alt))
        print("  7-Zip 读包  %s  条目 %d  %s" % ("OK" if ent["total"] else "空包", ent["total"], os.path.basename(alt)))
    except Exception as e:
        print("  7-Zip 读包  失败:", str(e)[:80])
else:
    print("  rar/7z 样本  本机没有，跳过")
shutil.rmtree(tmpd, ignore_errors=True)

# ---- 「复制到剪贴板」已移除 / 文件类型默认程序 ----
print()
try:
    urllib.request.urlopen(BASE + "/api/clipboard", timeout=10)
    print("复制到剪贴板接口: 还在（不该）")
except urllib.error.HTTPError as e:
    print("复制到剪贴板接口: %s" % ("已删除" if e.code == 404 else "HTTP %s" % e.code))
except Exception as e:
    print("复制到剪贴板接口: 探测失败", str(e)[:60])
print("内部剪贴板 /api/clip:", "在" if get("/api/clip") else "不在")
for e_ in (".psd", ".skp", ".dwg", ".jpg"):
    d = get("/api/assoc?ext=" + e_)
    print("  系统默认 %-6s %s" % (e_, d.get("exe") or "(查不到)"))
# 临时配一条 .tif 规则（素材库里那条 psd 素材实际是 .tif），验证规则被真正用上
post("/api/settings", {"open_with": {".tif": r"C:\Windows\System32\notepad.exe"}})
print("open_with 规则:", get("/api/state")["open_with"])
psd = get("/api/assets?kind=psd&limit=3")["items"]
if psd:
    r2 = post("/api/open", {"id": psd[0]["id"], "mode": "open"})
    print("  打开 psd 素材 ->", r2.get("ok"), "用的程序:", r2.get("msg"))
    time.sleep(2)
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)

# ---- 内置 SKP 3D 看图 ----
s3 = st3.get("skp3d") or {}
print("SKP 3D 看图:", "可用" if s3.get("available") else "不可用", s3.get("sk_dir") or s3.get("why"))
if s3.get("available"):
    cand = [i for i in get("/api/assets?kind=model&limit=60")["items"] if i.get("preview") == "model3d"]
    for it in cand[:2]:
        t0 = time.time()
        r = post("/api/model3d/prepare", {"id": it["id"]})
        while not r.get("cached"):
            j = get("/api/job/" + r["job"])
            if j["status"] != "running":
                break
            time.sleep(0.4)
        b = urllib.request.urlopen(BASE + "/api/model3d?id=%d" % it["id"], timeout=900).read()
        n, ni, wide = struct.unpack_from("<III", b, 8)
        ver = struct.unpack_from("<I", b, 4)[0]
        print("  %-22s XCM3v%d %6d 三角面 %5.1fMB 真实最长边 %.2fm  %.1fs"
              % (it["name"][:20], ver, ni // 3, len(b) / 1048576,
                 struct.unpack_from("<f", b, 48)[0], time.time() - t0))

EDGE = [c for c in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if os.path.isfile(c)][0]
PORT = 9381
edge = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--window-size=1680,1000",
                         "--remote-debugging-port=%d" % PORT, "--user-data-dir=" + os.path.join(OUT, "p"),
                         "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def hp(p, m="GET"):
    return json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, p), method=m), timeout=20))
for i in range(60):
    time.sleep(0.5)
    try: hp("/json/version"); break
    except Exception: pass
tab = hp("/json/new?about:blank", "PUT")

async def main():
    n = [0]
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=100*1024*1024) as ws:
        async def send(method, params=None):
            n[0] += 1; mid = n[0]
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == mid: return m.get("result", {})
        await send("Page.enable"); await send("Runtime.enable")
        async def js(e, wait=0.0):
            if wait: await asyncio.sleep(wait)
            r = await send("Runtime.evaluate", {"expression": e, "returnByValue": True})
            if r.get("exceptionDetails"): print("  JSERR:", str(r["exceptionDetails"].get("exception", {}).get("description"))[:250])
            return (r.get("result") or {}).get("value")
        async def shot(name):
            r = await send("Page.captureScreenshot", {"format": "png"})
            open(os.path.join(OUT, name), "wb").write(base64.b64decode(r["data"])); print("  shot", name)
        async def click(x, y, mods=0, dbl=False):
            for t in ("mousePressed", "mouseReleased"):
                await send("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left",
                                                        "clickCount": 2 if dbl else 1, "modifiers": mods})
            await asyncio.sleep(0.4)
        await send("Page.navigate", {"url": BASE + "/"})
        await asyncio.sleep(9)
        print("素材库:", await js("JSON.stringify({mode:S.mode,total:S.total,items:S.items.length})"))
        await shot("final_01_lib.png")
        # 点分类 = 只看该组模型
        cat = await js("(()=>{const b=document.querySelector('#cats button'); if(!b) return null; b.click(); return b.dataset.cat;})()")
        await asyncio.sleep(4)
        print("点分类 '%s' ->" % cat, await js("JSON.stringify({kind:S.kind,total:S.total,全是模型:S.items.every(i=>i.kind==='model')})"))
        await shot("final_01b_cat_model.png")
        # 3D 看图
        ok3d = await js("""(()=>{const it=S.items.find(x=>x.preview==='model3d'); if(!it) return null;
            const c=[...document.querySelectorAll('#grid .card')].find(k=>k.dataset.key===it.key);
            if(c) c.click(); return it.name;})()""")
        if ok3d:
            for _ in range(120):
                await asyncio.sleep(1)
                if await js("!!V3.r"):
                    break
            print("3D 看图:", ok3d, "->", (await js("(document.querySelector('#pv3dInfo')||{}).textContent||''") or "")[:70])
            await shot("final_01c_3d.png")
        await send("Page.navigate", {"url": BASE + "/#browse"})
        await asyncio.sleep(6)
        await js("browse(%s)" % json.dumps(BROWSE))
        await asyncio.sleep(4)
        print("浏览:", await js("JSON.stringify({mode:S.mode,dir:S.dir,n:S.dirItems.length})"))
        await shot("final_02_browse.png")
        # 选中 + 批量重命名弹窗
        c = await js("""[...document.querySelectorAll('#grid .card')].slice(1,4).map(c=>{const r=c.getBoundingClientRect();return [Math.round(r.x+r.width/2),Math.round(r.y+r.height/2)];})""")
        for i, (x, y) in enumerate(c):
            await click(x, y, 2 if i else 0)
        print("多选:", await js("S.sel.size"))
        await js("doAct('rename')"); await asyncio.sleep(2)
        print("重命名弹窗:", await js("!document.querySelector('#modal').classList.contains('hidden')"))
        await shot("final_03_rename.png")
        await js("closeModal && closeModal(); S.sel.clear(); S.map={}; syncSel();")
        # 设置弹窗
        await js("document.querySelector('#btnSettings').click()"); await asyncio.sleep(1.5)
        await shot("final_04_settings.png")
asyncio.run(main())
post("/api/settings", {"open_with": {}})
print("open_with 已复位:", get("/api/state")["open_with"])
edge.kill()
print("shots ->", OUT)
