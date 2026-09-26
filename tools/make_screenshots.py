
# -*- coding: utf-8 -*-
"""给 README 生成示例截图。

用临时的数据目录 + 临时的示例素材目录（默认取 Windows 自带壁纸），
所以截出来的图里不会出现你自己的文件，跑完会自动删掉临时目录。

用法：  python tools/make_screenshots.py
"""
import os
import asyncio, base64, json, os, shutil, subprocess, sys, tempfile, time, urllib.request
import websockets

DEMO = os.path.join(tempfile.gettempdir(), "xc_demo")
ROOT = os.path.join(DEMO, "示例素材")
DATA = os.path.join(DEMO, "data")
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
OUT = os.path.join(SRC, "docs", "img")
PORT_HTTP = 8799
BASE = "http://127.0.0.1:%d" % PORT_HTTP

shutil.rmtree(DEMO, ignore_errors=True)
os.makedirs(ROOT, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

src = r"C:\Windows\Web\Wallpaper"
n = 0
for root, dirs, fs in os.walk(src):
    for f in sorted(fs):
        if not f.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        sub = os.path.join(ROOT, os.path.basename(root).replace(" ", "") or "壁纸")
        os.makedirs(sub, exist_ok=True)
        try:
            shutil.copy2(os.path.join(root, f), os.path.join(sub, f))
            n += 1
        except Exception:
            pass
        if n >= 14:
            break
    if n >= 14:
        break
print("示例素材:", n, "个 ->", ROOT)

env = dict(os.environ)
env["XC_PORT"] = str(PORT_HTTP); env["XC_DATA"] = DATA; env["PYTHONIOENCODING"] = "utf-8"
_pw = os.path.join(SRC, ".venv", "Scripts", "pythonw.exe")
PYW = _pw if os.path.isfile(_pw) else sys.executable
proc = subprocess.Popen([PYW, os.path.join(SRC, "app", "server.py")],
                        env=env, cwd=os.path.join(SRC, "app"),
                        creationflags=0x8 | 0x200 | 0x8000000,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def get(p):
    with urllib.request.urlopen(BASE + p, timeout=60) as r:
        return json.loads(r.read())
def post(p, d):
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        BASE + p, data=json.dumps(d).encode(), headers={"Content-Type": "application/json"}),
        timeout=600).read())
st = None
for _ in range(60):
    time.sleep(0.5)
    try:
        st = get("/api/state"); break
    except Exception:
        pass
print("临时实例起来:", st["version"], st["data_dir"])
print("加目录:", post("/api/roots", {"action": "add", "path": ROOT}))
j = post("/api/scan", {"force": True})
print("扫描:", j)
jj = {}
for _ in range(300):
    time.sleep(1)
    jj = get("/api/job/" + j["job"])
    if jj["status"] != "running":
        break
print("扫描结束:", jj["status"], jj.get("done"), jj.get("msg"))
post("/api/prefetch", {})
s2 = get("/api/state")
for _ in range(120):
    time.sleep(1)
    s2 = get("/api/state")
    if s2["thumbs_pending"] == 0:
        break
print("缩略图:", s2["thumbs_ready"], "已生成 /", s2["thumbs_pending"], "待生成  | 索引", s2["total"])

EDGE = [c for c in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if os.path.isfile(c)][0]
CDP = 9441
edge = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--window-size=1600,900",
                         "--remote-debugging-port=%d" % CDP,
                         "--user-data-dir=" + os.path.join(DEMO, "edge"), "about:blank"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def hp(p, m="GET"):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:%d%s" % (CDP, p), method=m), timeout=20))
for _ in range(60):
    time.sleep(0.5)
    try:
        hp("/json/version"); break
    except Exception:
        pass
tab = hp("/json/new?about:blank", "PUT")

async def run():
    n = [0]
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=200*1024*1024) as ws:
        async def send(method, params=None):
            n[0] += 1; mid = n[0]
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == mid:
                    return m.get("result", {})
        await send("Page.enable"); await send("Runtime.enable")
        async def js(e):
            r = await send("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
            if r.get("exceptionDetails"):
                print("JSERR", str(r["exceptionDetails"].get("exception", {}).get("description"))[:200])
            return (r.get("result") or {}).get("value")
        async def shot(name):
            d = await send("Page.captureScreenshot", {"format": "png"})
            p = os.path.join(OUT, name)
            open(p, "wb").write(base64.b64decode(d["data"]))
            print("  shot ->", p)

        await send("Page.navigate", {"url": BASE + "/"})
        await asyncio.sleep(9)
        print("素材库:", await js("JSON.stringify({mode:S.mode,total:S.total,n:S.items.length})"))
        await shot("demo-01-library.png")

        await js("document.querySelector('#btnSettings').click()")
        await asyncio.sleep(1.5)
        await js("document.querySelector('#modalBox .tab[data-tab=tab2]').click()")
        await asyncio.sleep(1.0)
        await js("document.querySelector('#owExt').value='.psd,.psb';"
                 "document.querySelector('#owExe').value='C:\\\\Program Files\\\\Adobe\\\\Adobe Photoshop 2024\\\\Photoshop.exe';"
                 "document.querySelector('#owAdd').click();"
                 "document.querySelector('#owExt').value='.dwg';"
                 "document.querySelector('#owExe').value='C:\\\\Program Files\\\\Autodesk\\\\AutoCAD 2024\\\\acad.exe';"
                 "document.querySelector('#owAdd').click();")
        await asyncio.sleep(0.8)
        await shot("demo-03-settings-openwith.png")
        await js("closeModal && closeModal()")
        await asyncio.sleep(0.5)

        await send("Page.navigate", {"url": BASE + "/#browse"})
        await asyncio.sleep(6)
        await js("browse(%s)" % json.dumps(r"C:\Windows\Web\Wallpaper"))
        await asyncio.sleep(5)
        print("浏览:", await js("JSON.stringify({mode:S.mode,dir:S.dir,n:S.dirItems.length})"))
        await js("(()=>{const a=document.querySelector('aside.sidebar'); if(a) a.style.display='none'; return !!a;})()")
        await asyncio.sleep(0.8)
        await shot("demo-02-browse.png")

asyncio.run(run())
edge.kill()
subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)], capture_output=True)
time.sleep(1.5)
shutil.rmtree(DEMO, ignore_errors=True)
print("临时数据已删除:", not os.path.isdir(DEMO))
