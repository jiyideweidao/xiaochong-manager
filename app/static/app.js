/* 小虫管理器 前端 */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtSize = n => !n && n !== 0 ? "-" : n === 0 ? "0 B" : n > 1073741824 ? (n / 1073741824).toFixed(2) + " GB" : n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : n > 1024 ? (n / 1024).toFixed(0) + " KB" : n + " B";
const fmtTime = t => !t ? "-" : new Date(t * 1000).toLocaleString("zh-CN", { hour12: false });

const S = {
  mode: "lib", q: "", kind: "all", category: "", style: "", fav: 0, onlyRender: 0,
  sort: "category", offset: 0, limit: 80, total: 0, items: [], sel: new Set(), map: {},
  lastIndex: -1, loading: false, st: null, kinds: [], kindMap: {},
  dir: "", dirData: null, dirFilter: "", showHidden: 0, sortDir: "name", dirItems: [],
  clip: { paths: [], mode: "copy" }, busy: false, loaded: "",
  searchRes: null, searchFor: "", searchSeq: 0,
};

// 素材库模式下「已加载文件夹」：加载后右边只看这个文件夹，左边类型/分类也都统计它
const isLib = () => S.mode === "lib" && !S.loaded;
function gridItems() { return isLib() ? S.items : S.dirItems; }
async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error((await r.text()).slice(0, 200) || r.statusText);
  return r.json();
}
function toast(msg, cls = "", ms = 4200) {
  const d = document.createElement("div");
  d.className = "toast " + cls; d.textContent = msg;
  $("#toasts").appendChild(d);
  setTimeout(() => d.remove(), ms);
}
const kindLabel = k => (S.kindMap[k] || {}).label || k || "文件";
const BS = String.fromCharCode(92);
function shortProg(s) {
  if (!s) return "";
  const base = s.split(BS).pop().split("/").pop();
  return base.toLowerCase().endsWith(".exe") ? base.slice(0, -4) : base;
}
const kindColor = k => (S.kindMap[k] || {}).color || "#6b7280";

/* ---------------- 缓存提醒 ---------------- */
const CACHE = { info: null, running: false, ticked: 0 };
const WARN_KEY = "xc_cache_warned";      // 置 1 = 这一次超限已经提醒过了
function cacheParts(c) {
  return ["stage", "thumbs", "nested", "model3d"].filter(k => c[k] && c[k].size)
    .sort((a, b) => c[b].size - c[a].size)
    .map(k => `<i>${esc(c[k].label)} ${fmtSize(c[k].size)}</i>`).join("　·　");
}
function showCacheBar(c, s) {
  const once = (s || {}).cache_remind_once !== false;
  const bar = $("#cachebar");
  bar.classList.remove("hidden");
  bar.innerHTML = `<span class="ic">🧹</span>
    <span class="tx">缓存已经占了 <b>${fmtSize(c.total)}</b><br><span class="dim">${cacheParts(c)}　清掉不影响素材文件，缩略图以后会自动重建；可以只挑你不想留的几项清</span></span>
    <button class="btn primary" id="cacheClean">挑着清理…</button>
    ${once ? "" : '<button class="btn ghost" id="cacheLater">稍后再说</button>'}
    <button class="btn ghost" id="cacheOff">${once ? "知道了" : "不再提醒"}</button>`;
  $("#cacheClean").onclick = () => { bar.classList.add("hidden"); openClean(); };
  const later = $("#cacheLater");
  if (later) later.onclick = () => {
    localStorage.setItem("xc_cache_remind_at", String(Date.now()));
    bar.classList.add("hidden");
  };
  $("#cacheOff").onclick = async () => {
    bar.classList.add("hidden");
    if (once)
      return toast("知道了。清理之前不会再提醒你，想早点看到提醒去「设置 → 维护 → 重置提醒」。", "ok", 8000);
    await api("/api/settings", { cache_remind_on: false });
    toast("已关闭提醒，想再打开去「设置 → 维护」", "ok", 6000);
    loadState();
  };
}
async function checkCache(fresh) {
  if (CACHE.running) return;
  CACHE.running = true;
  try {
    if (fresh || !CACHE.info) CACHE.info = (((await api("/api/state")) || {}).cache) || null;
    const c = CACHE.info, s = (S.st && S.st.settings) || {};
    if (!c || !c.total) return;
    if (s.cache_remind_on === false) return;
    const limit = (+s.cache_limit_mb || 1500) * 1048576;
    const every = Math.max(5, +s.cache_remind_min || 60) * 60000;
    // 掉回上限以下 = 清理过了，提醒重新武装（下次再超限还会提醒一次）
    if (c.total < limit) { localStorage.removeItem(WARN_KEY); return; }
    if (localStorage.getItem(WARN_KEY) === "1" && s.cache_remind_once !== false) return;
    const last = +localStorage.getItem("xc_cache_remind_at") || 0;
    if (Date.now() - last < every) return;
    localStorage.setItem("xc_cache_remind_at", String(Date.now()));
    if (s.cache_remind_once !== false) localStorage.setItem(WARN_KEY, "1");
    showCacheBar(c, s);
  } catch (e) { /* 忽略 */ } finally { CACHE.running = false; }
}
/* 清理缓存：先把明细拉回来，让用户自己勾选要清哪几项，不勾的绝不动 */
let CLEAN = null, CLEAN_SEL = {};
async function openClean() {
  CLEAN = null; CLEAN_SEL = {};
  modal(`<div class="mh"><span>清理缓存</span><button class="ghost icon" data-close>×</button></div>
    <div class="mb" id="cleanBody"><div class="hint" style="margin-top:0">正在统计缓存明细，第一次要几秒钟…</div></div>
    <div class="mf">
      <span class="dim" id="cleanSum" style="margin-right:auto;font-size:12.5px">请勾选要清理的项</span>
      <button class="btn" data-close>关闭</button>
      <button class="btn primary" id="cleanGo" disabled>清理选中的项</button>
    </div>`);
  let d = null;
  try { d = await api("/api/cache/detail"); } catch (e) { /* 下面统一提示 */ }
  if (!d || !d.ready) {
    $("#cleanBody").innerHTML = `<div class="hint" style="margin-top:0">统计失败${d && d.msg ? "：" + esc(d.msg) : ""}，关掉面板再打开一次试试。</div>`;
    return;
  }
  CLEAN = d;
  renderClean();
}

function renderClean() {
  const d = CLEAN;
  if (!d) return;
  const html = d.sections.map(sec => {
    const rows = sec.groups.length
      ? sec.groups.map(g => {
        CLEAN_SEL[g.id] = g.sel;
        return `<label class="clrow"><input type="checkbox" data-g="${esc(g.id)}">
          <span class="nm">${esc(g.label)}</span>
          <span class="dim">${fmtSize(g.size)} · ${esc(g.note)}</span></label>`;
      }).join("")
      : `<div class="hint" style="margin-top:0">这一类现在是空的。</div>`;
    return `<div class="clsec">
      <div class="clhead"><span class="nm">${esc(sec.label)}</span>
        <span class="dim">${fmtSize(sec.size)} · ${sec.files} 个文件</span>
        ${sec.groups.length ? `<button class="btn sm ghost" data-sec="${esc(sec.id)}">全选</button>` : ""}</div>
      <div class="hint" style="margin-top:6px">${esc(sec.note)}</div>
      ${rows}</div>`;
  }).join("");
  $("#cleanBody").innerHTML = `<div class="hint" style="margin-top:0">下面全是程序自己生成的临时文件，
    <b>不会动你的素材原文件</b>；想清哪几项就勾哪几项，没勾的一点都不会动。</div>${html}`;
  $$("#cleanBody input[type=checkbox]").forEach(cb => cb.onchange = cleanSum);
  $$("#cleanBody [data-sec]").forEach(b => b.onclick = () => {
    const cbs = $$("input[type=checkbox]", b.closest(".clsec"));
    const on = cbs.some(c => !c.checked);
    cbs.forEach(c => c.checked = on);
    cleanSum();
  });
  $("#cleanGo").onclick = doClean;
  cleanSum();
}

function findGroup(id) {
  for (const sec of (CLEAN ? CLEAN.sections : []))
    for (const g of sec.groups) if (g.id === id) return g;
  return null;
}

function cleanSum() {
  const on = $$("#cleanBody input[type=checkbox]:checked");
  let size = 0;
  on.forEach(c => { const g = findGroup(c.dataset.g); if (g) size += g.size || 0; });
  $("#cleanGo").disabled = !on.length;
  $("#cleanSum").textContent = on.length
    ? `已选 ${on.length} 项，约 ${fmtSize(size)}`
    : "请勾选要清理的项";
}

async function doClean() {
  const on = $$("#cleanBody input[type=checkbox]:checked");
  if (!on.length) return;
  const sels = on.map(c => CLEAN_SEL[c.dataset.g]).filter(Boolean);
  let size = 0; const names = [];
  on.forEach(c => {
    const g = findGroup(c.dataset.g);
    if (!g) return;
    size += g.size || 0;
    names.push("· " + g.label + "（" + fmtSize(g.size) + (g.size ? "" : "，不占空间") + "）");
  });
  let msg = `要清理这 ${on.length} 项，约 ${fmtSize(size)}：\n\n${names.join("\n")}\n\n`;
  if (sels.some(x => x.what === "nested"))
    msg += "「嵌套解压缓存」清掉后，这些模型要重新扫描素材库才能打开。\n";
  if (sels.some(x => x.what === "nested_stale"))
    msg += "「重复的旧记录」只删列表里的重复条目，不会删除任何文件。\n";
  msg += "不会动你的素材原文件，确定清理吗？";
  if (!confirm(msg)) return;
  const btn = $("#cleanGo");
  btn.disabled = true; btn.textContent = "正在清理…";
  try {
    const r = await api("/api/cleanup", { items: sels });
    toast((r.msg || "清理完成") + "。", "ok", 7000);
    closeModal();
    loadState(); checkCache(true);
  } catch (e) {
    toast("清理失败：" + e.message, "err", 7000);
    btn.disabled = false; btn.textContent = "清理选中的项";
  }
}

/* ---------------- 视图切换 ---------------- */
$$("#modesw button").forEach(b => b.onclick = () => setMode(b.dataset.mode));
function setMode(m) {
  if (location.hash !== "#" + m) history.replaceState(null, "", "#" + m);
  S.mode = m;
  $$("#modesw button").forEach(b => b.classList.toggle("on", b.dataset.mode === m));
  $("#sideLib").classList.toggle("hidden", m !== "lib");
  $("#sideBrowse").classList.toggle("hidden", m !== "browse");
  $("#tbLib").classList.toggle("hidden", m !== "lib");
  $("#tbBrowse").classList.toggle("hidden", m !== "browse");
  $("#crumbs").classList.toggle("hidden", m !== "browse");
  $("#btnScan").classList.toggle("hidden", m !== "lib");
  $("#btnPrefetch").classList.toggle("hidden", m !== "lib");
  $("#q").placeholder = m === "lib"
    ? "搜索素材名 / 分类 / 关键词 / 扩展名，如：吊灯、现代、.pdf（按 / 聚焦）"
    : "在当前文件夹里筛选文件名…（按 / 聚焦）";
  syncLoadedUI();
  S.sel.clear(); S.map = {}; syncSel();
  stop3D();
  $("#drawer").classList.add("hidden");
  if (m === "browse") {
    loadPlaces();
    const r0 = ((S.st && S.st.roots[0]) || {}).path || "D:\\";
    browse(S.dir || r0);
  }
  else if (S.loaded) { renderBrowse(); refreshSideStats(); }
  else reload();
  loadState();
}

/* ---------------- 侧栏 ---------------- */
async function loadState() {
  const st = await api("/api/state");
  S.st = st;
  const c = st.counts || {};
  const parts = Object.keys(c).sort((a, b) => c[b] - c[a])
    .map(k => `<span class="pill"><i class="dot" style="background:${kindColor(k)}"></i>${esc(kindLabel(k))} ${c[k]}</span>`).join("");
  $("#stats").innerHTML =
    `${parts}<div style="margin-top:6px">共索引 <b>${st.total}</b> 条 · 压缩包 <b>${st.archives}</b> 个<br>` +
    `收藏 ${st.favorites} · 缩略图 ${st.thumbs_ready} 已生成 / ${st.thumbs_pending} 待生成<br>` +
    `数据目录 ${esc(st.data_dir)}<br>剩余空间 <b>${fmtSize(st.disk_free)}</b><br>` +
    `<span class="dim">解压内核 ${st.seven_zip_bundled ? "已内嵌" : (st.seven_zip ? "外部" : "缺失")}` +
    ` · SKP 3D 看图 ${st.skp3d && st.skp3d.available ? "可用" : "不可用"}` +
    ` · SketchUp ${st.sketchup ? "已找到" : "未找到"}` +
    ` · ffmpeg ${st.ffmpeg ? "已就绪" : "未找到"}</span></div>`;
  renderRoots();
  if (st.cache) {
    CACHE.info = st.cache;
    if (!CACHE.ticked) { CACHE.ticked = 1; checkCache(false); }
  }
  const tb = $("#tbImgThumb");
  if (tb) tb.checked = !!(st.settings && st.settings.image_thumbs);
  return st;
}

function renderRoots() {
  const box = $("#roots");
  if (!box) return;
  const rs = (S.st && S.st.roots) || [];
  const norm = p => (p || "").replace(/[\\/]+$/, "").toLowerCase();
  const cur = norm(S.loaded);
  if (!rs.length) { box.innerHTML = `<div class="dim" style="font-size:12px">还没有素材目录</div>`; return; }
  const BS = String.fromCharCode(92);   // 反斜杠
  // 这个根目录是不是别的根目录的子目录？是的话缩进、淡化，看得出一层套一层
  const isChild = p => rs.some(r => { const a = norm(p), b = norm(r.path); return a !== b && a.indexOf(b + BS) === 0; });
  // 当前加载的排最前；根目录太多时先只显示前几个，其余折叠起来，别把左栏撑乱
  const list = rs.slice().sort((a, b) => (norm(a.path) === cur ? 0 : 1) - (norm(b.path) === cur ? 0 : 1));
  const LIMIT = 5;
  const showAll = !!S.rootsExpanded || list.length <= LIMIT;
  const shown = showAll ? list : list.slice(0, LIMIT);
  box.innerHTML = shown.map(r => {
    const on = cur && norm(r.path) === cur;
    return `<div class="root${on ? " on" : ""}${isChild(r.path) ? " sub" : ""}" data-root="${esc(r.path)}" title="${esc(r.path)}\n点一下 = 加载这个根目录">`
      + `<span>${esc(r.path)}</span><button data-rm="${esc(r.path)}" title="从素材库移除">×</button></div>`;
  }).join("");
  const rest = list.length - shown.length;
  if (rest > 0) box.insertAdjacentHTML("beforeend", `<button class="rootsMore" data-more="1">还有 ${rest} 个根目录，展开…</button>`);
  else if (list.length > LIMIT) box.insertAdjacentHTML("beforeend", `<button class="rootsMore" data-more="0">收起</button>`);
  $$("#roots .root").forEach(el => el.onclick = () => loadFolder(el.dataset.root));
  $$("#roots button[data-rm]").forEach(b => b.onclick = async e => {
    e.stopPropagation();
    if (!confirm("从素材库移除该目录？\n（不会删除磁盘文件，只是不再索引）")) return;
    if (S.loaded && norm(S.loaded) === norm(b.dataset.rm)) unloadFolder();
    await api("/api/roots", { action: "remove", path: b.dataset.rm });
    loadState(); loadKinds(); loadFacets();
  });
  const more = $("#roots .rootsMore");
  if (more) more.onclick = e => { e.stopPropagation(); S.rootsExpanded = more.dataset.more === "1"; renderRoots(); };
}

async function loadKinds() {
  if (S.loaded) return;                      // 加载了文件夹：左侧显示的是它的统计，别覆盖
  const d = await api("/api/kinds");
  S.kinds = d.kinds || [];
  S.kindMap = {}; S.kinds.forEach(k => S.kindMap[k.key] = k);
  const items = [["all", "全部文件", d.total, "#374151"], ["fav", "我的收藏", S.st ? S.st.favorites : 0, "#b45309"]]
    .concat(S.kinds.filter(k => k.count).map(k => [k.key, k.label, k.count, k.color]));
  $("#kinds").innerHTML = items.map(([k, label, n, col]) =>
    `<button data-kind="${k}" class="${S.kind === k ? "on" : ""}"><span class="kl"><i class="dot" style="background:${col}"></i>${esc(label)}</span><span class="n">${n ?? ""}</span></button>`).join("");
  $$("#kinds button").forEach(b => b.onclick = () => {
    S.kind = b.dataset.kind; S.offset = 0; S.sel.clear(); S.map = {}; syncSel();
    if (S.mode === "browse") { S.dirFilter = S.kind === "all" ? "" : S.kind; renderBrowse(); }
    else { renderKinds(); reload(); }
  });
  return d;
}
function renderKinds() { loadKinds(); }

async function loadFacets() {
  if (S.loaded) return;
  const f = await api("/api/facets");
  $("#catCount").textContent = f.categories.length;
  $("#cats").innerHTML = f.categories.map(c =>
    `<button data-cat="${esc(c.value)}" class="${S.category === c.value ? "on" : ""}"><span>${esc(c.value)}</span><span class="n">${c.count}</span></button>`).join("");
  $("#styles").innerHTML = f.styles.map(s =>
    `<button data-style="${esc(s.value)}" class="${S.style === s.value ? "on" : ""}">${esc(s.value)} <span class="n">${s.count}</span></button>`).join("")
    || `<span class="dim" style="font-size:12px">暂无风格标签</span>`;
  const wire = (sel, attr, key) => $$(sel).forEach(b => b.onclick = () => {
    const v = b.dataset[attr];
    const off = S[key] === v;
    S[key] = off ? "" : v;
    // 点分类 = 直接看这一组里的所有模型（分类本来就只统计模型），避免混进效果图
    if (key === "category" && !off) S.kind = "model";
    S.offset = 0; loadFacets(); reload();
  });
  wire("#cats button", "cat", "category");
  wire("#styles button", "style", "style");
  $("#catFilter").oninput = e => {
    const v = e.target.value.trim();
    $$("#cats button").forEach(b => b.style.display = !v || b.textContent.includes(v) ? "" : "none");
  };
  const dcf = $("#dirCatFilter");
  if (dcf && !dcf._wired) { dcf._wired = 1; dcf.oninput = () => refreshSideStats(); }
}

async function loadPlaces() {
  const d = await api("/api/places");
  $("#places").innerHTML = (d.places || []).map(p =>
    `<button data-p="${esc(p.path)}"><span>${esc(p.name)}</span><span class="n">${p.kind === "root" ? "库" : ""}</span></button>`).join("")
    || `<span class="dim" style="font-size:12px">—</span>`;
  $("#drives").innerHTML = (d.drives || []).map(p =>
    `<button data-p="${esc(p.path)}"><span>${esc(p.name)}${p.label ? " · " + esc(p.label) : ""}</span><span class="n">${fmtSize(p.free)}</span></button>`).join("");
  $$("#places button,#drives button").forEach(b => b.onclick = () => browse(b.dataset.p));
}

/* ---------------- 条目归一化 ---------------- */
function thumbLg(url) { return !url ? "" : url + (url.includes("?") ? "&" : "?") + "size=1600"; }
function libItem(a) {
  return {
    key: "a" + a.id, id: a.id, name: a.name || "", kind: a.kind || "other", ext: a.ext || "",
    size: a.size || 0, mtime: a.mtime || 0, is_dir: false, preview: a.preview || "none",
    thumb: "/api/thumb/" + a.id, raw: "/api/raw?id=" + a.id, text: "/api/text?id=" + a.id,
    source_path: a.source_path, inner: a.inner_path || "", category: a.category || "",
    style: a.style || "", favorite: a.favorite || 0, fav_at: a.fav_at || 0, render_id: a.render_id || 0,
    origin: a.origin || "", filename: (a.name || "") + (a.ext || ""), from: "lib",
    renderThumb: a.render_id ? "/api/thumb/" + a.render_id + "?size=1400&render=0" : "",
  };
}
function fileItem(e) {
  const q = "path=" + encodeURIComponent(e.path);
  return {
    key: "p" + e.path, name: e.name, kind: e.is_dir ? "folder" : (e.kind || "other"),
    ext: e.ext || "", size: e.size || 0, mtime: e.mtime || 0, is_dir: !!e.is_dir,
    preview: e.is_dir ? "folder" : (e.preview || "none"),
    thumb: e.is_dir ? "" : "/api/thumb-path?" + q, raw: "/api/raw?" + q, text: "/api/text?" + q,
    path: e.path, source_path: e.path, inner: "", filename: e.name, from: "fs",
    favorite: e.favorite || 0, asset_id: e.asset_id || 0, rel: e.rel,
  };
}

/* ---------------- 卡片 ---------------- */
// 图片缩略图开关：不勾（默认）时，图片不生成也不显示缩略图
function imgNoThumb(it) {
  const on = S.st && S.st.settings && S.st.settings.image_thumbs;
  return !!it && !it.is_dir && it.kind === "image" && !on;
}
// 卡片上到底显不显示缩略图：总开关关掉，或者这张是图片而且没开图片缩略图
function cardNoThumb(it) {
  const st = (S.st && S.st.settings) || {};
  if (st.show_thumbs === false) return true;   // 总开关关掉：连文件夹也走文字列表
  if (it.is_dir) return false;                 // 文件夹本来就只看图标
  return imgNoThumb(it);
}
// 没有缩略图可显示的卡片（图片关了缩略图、或总开关关掉）走紧凑列表行：
// 小图标在左，文件名和说明在右 —— 一屏能扫一堆名字，方便挑要打开哪个
const ROW_ICON = { model: "🧊", image: "🖼", psd: "🖼", cad: "📐", archive: "🗜",
  video: "🎬", audio: "🎵", doc: "📄", code: "📜", app: "⚙", other: "📄" };
function rowHtml(it, on) {
  const st = (S.st && S.st.settings) || {};
  const withPath = !!st.show_path && !it.is_dir;
  const fullPath = it.from === "lib" ? (it.source_path || "") : (it.path || "");
  const fullName = it.filename || it.name || "";
  const pathTxt = fullPath + (it.inner ? "  ↳ " + it.inner : "");
  const icon = it.is_dir ? (it.kind === "drive" ? "💽" : "📁") : (ROW_ICON[it.kind] || "📄");
  const relTxt = (it.rel === undefined || it.rel === null) ? ""
    : (it.rel ? "📁 " + it.rel + " · " : "📁 就在这个文件夹 · ");
  const right = withPath ? pathTxt
    : it.from === "lib" ? [it.category, it.origin || it.folder].filter(Boolean).join(" · ")
    : relTxt + (it.is_dir ? "文件夹" : fmtSize(it.size)) + " · " + fmtTime(it.mtime);
  const left = withPath ? (it.is_dir ? "文件夹" : fmtSize(it.size))
    : (it.is_dir ? "文件夹" : kindLabel(it.kind));
  const title = withPath ? fullName + "\n" + pathTxt : (it.name || "");
  return `<div class="card row${on}" data-key="${esc(it.key)}" title="${esc(title)}">
    <span class="ric">${icon}</span>
    <div class="rmeta">
      <div class="rname">${esc(withPath ? fullName : it.name)}</div>
      <div class="rsub"><span class="c">${esc(left)}</span><span class="o" title="${esc(right)}">${esc(right)}</span></div>
    </div>
    ${it.is_dir ? "" : `<span class="badge">${esc((it.ext || "").replace(".", "").toUpperCase() || "文件")}</span>`}
    ${it.is_dir ? "" : `<button class="favbtn${it.favorite ? " fav-on" : ""}" data-fav="1" title="${it.favorite ? "取消收藏" : "收藏到我的收藏"}">${it.favorite ? "★" : "☆"}</button>`}
  </div>`;
}
// 卡片上要不要写「真实文件名 + 所在文件夹的完整地址」
function showPathOn() {
  return !!(S.st && S.st.settings && S.st.settings.show_path);
}
// 设置里改了「卡片显示方式」后，原地把当前列表重画一遍（不用重新扫描、不丢滚动位置）
function redrawCards() {
  if (S.mode === "browse") { renderBrowse(); return; }
  if (!S.items || !S.items.length) return;
  $("#grid").innerHTML = S.items.map(cardHtml).join("");
  bindCards();
  syncSel();
}
// 这些格式浏览器能直接显示，详情里可以直接看原图
const IMG_DIRECT = /\.(jpe?g|jfif|png|bmp|gif|webp|ico|avif|svg)$/i;
function cardHtml(it) {
  const on = S.sel.has(it.key) ? " sel" : "";
  if (cardNoThumb(it)) return rowHtml(it, on);   // 没缩略图可看：走紧凑列表行
  const badge = it.is_dir ? "" : `<span class="badge">${esc((it.ext || "").replace(".", "").toUpperCase() || "文件")}</span>`;
  const thumb = it.is_dir
    ? `<div class="thumb ${it.kind === "drive" ? "drive" : "folder"}"><span class="fi">${it.kind === "drive" ? "💽" : "📁"}</span></div>`
    : `<div class="thumb"><img src="${it.thumb}" loading="lazy" onload="this.dataset.ok='1'" onerror="this.remove()" alt=""><span class="ph">${esc(kindLabel(it.kind))}</span>
        ${["image", "psd", "model"].includes(it.kind) ? "" : `<span class="kindtag">${esc(kindLabel(it.kind))}</span>`}${badge}</div>`;
  const favBtn = it.is_dir ? ""
    : `<button class="favbtn${it.favorite ? " fav-on" : ""}" data-fav="1" title="${it.favorite ? "取消收藏" : "收藏到我的收藏"}">${it.favorite ? "★" : "☆"}</button>`;
  const fullPath = it.from === "lib" ? (it.source_path || "") : (it.path || "");
  const fullName = it.filename || it.name || "";
  const withPath = showPathOn() && !it.is_dir;
  const pathTxt = fullPath + (it.inner ? "  ↳ " + it.inner : "");
  const sub = withPath
    ? `<span class="o full" title="${esc(pathTxt)}">${esc(pathTxt)}</span>`
    : it.from === "lib"
    ? `${it.category ? `<span class="c">${esc(it.category)}</span>` : ""}<span class="o" title="${esc(it.origin || "")}">${esc(it.origin || it.folder || "")}</span>`
    : `${it.is_dir ? `<span class="c">文件夹</span>` : `<span class="c">${esc(fmtSize(it.size))}</span>`}<span class="o">${esc((it.rel === undefined || it.rel === null ? "" : (it.rel ? it.rel + " · " : "就在这个文件夹 · ")) + fmtTime(it.mtime))}</span>`;
  return `<div class="card${on}" data-key="${esc(it.key)}">
    ${thumb}${favBtn}${it.kind === "model" ? `<span class="tag3d">3D</span>` : ""}
    <div class="meta"><div class="name" title="${esc(withPath ? (fullName + "\n" + pathTxt) : it.name)}">${esc(withPath ? fullName : it.name)}</div><div class="sub${withPath ? " pathmode" : ""}">${sub}</div></div>
    </div>`;
}
function bindCards(scope = "#grid") {
  $$(scope + " .card").forEach(c => {
    if (c.dataset.bound) return;
    c.dataset.bound = "1";
    const key = c.dataset.key;
    c.onclick = e => {
      const it = it2(key); if (!it) return;
      if (e.target.dataset.q) { quickAct(e.target.dataset.q, it); return; }
      const list = gridItems();
      const i = list.findIndex(x => x.key === key);
      if (e.shiftKey && S.lastIndex >= 0) {
        const [a, b] = [Math.min(i, S.lastIndex), Math.max(i, S.lastIndex)];
        for (let k = a; k <= b; k++) { S.sel.add(list[k].key); S.map[list[k].key] = list[k]; }
      } else if (e.ctrlKey || e.metaKey) {
        if (S.sel.has(key)) { S.sel.delete(key); delete S.map[key]; } else { S.sel.add(key); S.map[key] = it; }
        S.lastIndex = i;
      } else { S.sel.clear(); S.sel.add(key); S.map[key] = it; S.lastIndex = i; }
      syncSel(); openDrawer(it);
    };
    const fb = c.querySelector(".favbtn");
    if (fb) fb.onclick = e => { e.stopPropagation(); toggleFav(it2(key), fb); };
    c.ondblclick = e => { if (!e.target.dataset.q) quickAct(it2(key).is_dir ? "enter" : "open", it2(key)); };
  });
}
const it2 = key => S.map[key] || gridItems().find(x => x.key === key);

/* ---------------- 鼠标右键菜单（二级菜单） ----------------
   卡片上的「打开 / 定位 / 复制」按钮全部收进这里：在卡片上点右键弹出来，
   想选别的程序就进「打开方式 ▸」那个二级菜单。 */
let ctxBox = null;
function closeCtx() { if (ctxBox) { ctxBox.remove(); ctxBox = null; } }
function ctxMenuFor(it, many) {
  const lib = it.from === "lib", n = S.sel.size, R = [];
  const add = (act, label) => R.push({ act, label });
  const sep = () => { if (R.length && !R[R.length - 1].sep) R.push({ sep: 1 }); };
  add("open", many ? `用默认程序打开这 ${n} 项` : "打开");
  const subs = [{ act: "open", label: "用默认程序打开" },
                { act: "openas", label: "打开方式…（自己挑程序）" }];
  if (!many && it.kind === "image") subs.push({ act: "__viewer", label: "指定看图软件…" });
  R.push({ label: "打开方式", children: subs });
  add("reveal", "在资源管理器中定位");
  sep();
  add("copy", many ? `复制这 ${n} 项（到「浏览文件」里粘贴）` : "复制（到「浏览文件」里粘贴）");
  add("export", "复制到文件夹…");
  if (!lib) add("moveto", "移动到文件夹…");
  if (!lib) add("cut", "剪切");
  add("copyfull", "复制完整路径");
  sep();
  if (many) {
    add("fav", "☆ 收藏 / ★ 取消收藏（这些）");
    add("rename", "批量重命名…");
    add("extract", "解压选中的压缩包");
  } else {
    add("fav", it.favorite ? "★ 取消收藏" : "☆ 收藏到我的收藏");
    add("rename", "重命名…");
    if (it.inner || it.kind === "archive") add("extract", "解压…");
    if (!lib && it.is_dir) add("enter", "进入该文件夹");
    if (!lib && it.is_dir) add("paste", "粘贴到此处");
    if (!lib && !it.is_dir) add("zip", "压缩为 zip");
    if (!lib) add("recycle", "删除到回收站");
  }
  sep();
  add("__all", "全选本页");
  add("__none", "取消选择");
  return R;
}
function ctxHtml(rows) {
  return rows.map(r => {
    if (r.sep) return '<div class="msep"></div>';
    if (r.children)
      return `<div class="mi hasSub">${esc(r.label)}<span class="ar">›</span>
        <div class="msub">${r.children.map(c => `<div class="mi" data-ctx="${esc(c.act)}">${esc(c.label)}</div>`).join("")}</div></div>`;
    return `<div class="mi" data-ctx="${esc(r.act)}">${esc(r.label)}</div>`;
  }).join("");
}
function openCtx(x, y, it) {
  closeCtx();
  const many = S.sel.size > 1 && S.sel.has(it.key);
  ctxBox = document.createElement("div");
  ctxBox.className = "ctxmenu";
  ctxBox.innerHTML = (many ? `<div class="ctxhd">已选 ${S.sel.size} 项，下面的动作都作用于它们</div>` : "")
    + ctxHtml(ctxMenuFor(it, many));
  document.body.appendChild(ctxBox);
  const r = ctxBox.getBoundingClientRect();
  ctxBox.style.left = Math.max(6, Math.min(x, innerWidth - r.width - 8)) + "px";
  ctxBox.style.top = Math.max(6, Math.min(y, innerHeight - r.height - 8)) + "px";
  ctxBox.onclick = e => {
    const mi = e.target.closest(".mi[data-ctx]");
    if (!mi) return;
    const act = mi.dataset.ctx;
    closeCtx();
    if (act === "__all") return selectAll();
    if (act === "__none") { S.sel.clear(); S.map = {}; syncSel(); return; }
    if (act === "__viewer") return openViewerPicker();
    doAct(act, many ? undefined : it);
  };
}
$("#grid").addEventListener("contextmenu", e => {
  const c = e.target.closest(".card");
  if (!c) return;
  const it = it2(c.dataset.key);
  if (!it) return;
  e.preventDefault();
  if (!S.sel.has(it.key)) { S.sel.clear(); S.sel.add(it.key); S.map[it.key] = it; syncSel(); }
  openCtx(e.clientX, e.clientY, it);
});
document.addEventListener("click", e => { if (ctxBox && !ctxBox.contains(e.target)) closeCtx(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeCtx(); });
window.addEventListener("resize", closeCtx);
$("#grid").addEventListener("scroll", closeCtx);
function syncSel() {
  $$("#grid .card").forEach(c => c.classList.toggle("sel", S.sel.has(c.dataset.key)));
  $("#selbar").classList.toggle("hidden", S.sel.size === 0);
  $("#selCount").textContent = S.sel.size;
  renderSelActs();
  queueSideStats();
}
function selItems() { return [...S.sel].map(it2).filter(Boolean); }

/* ---------------- 收藏 ---------------- */
async function toggleFav(it, btn) {
  if (!it || it.is_dir) return;
  const val = it.favorite ? 0 : 1;
  const body = it.from === "lib" ? { ids: [it.id], value: val }
                                 : { paths: [it.path || it.source_path], value: val };
  let r;
  try { r = await api("/api/favorite", body); }
  catch (e) { return toast("收藏失败：" + e.message, "err", 7000); }
  if (!r.ok) return toast("这一项收藏不了（文件夹不能收藏，或所在位置没有权限）", "warn", 7000);
  const aid = (r.ids && r.ids[0]) || it.id || 0;
  it.favorite = val;
  it.fav_at = val ? (it.fav_at || Date.now() / 1000) : 0;
  if (aid) it.id = aid;
  if (btn) {
    btn.classList.toggle("fav-on", !!val);
    btn.textContent = val ? "★" : "☆";
    btn.title = val ? "取消收藏" : "收藏到我的收藏";
  }
  const db2 = $('#dBody [data-d="fav"]');
  if (db2 && S.cur && S.cur.key === it.key) db2.textContent = val ? "★ 取消收藏" : "☆ 收藏";
  refreshFavCount();
  toast(val ? `已加入我的收藏：${it.name}` : `已从我的收藏移除：${it.name}`, "ok", 2600);
  if (!val && isLib() && S.kind === "fav") {   // 在收藏夹里取消收藏，这张卡就该消失
    S.sel.delete(it.key); delete S.map[it.key]; syncSel(); reload();
  }
  return r;
}
async function refreshFavCount() {
  try { await loadState(); } catch (e) { return; }   // 顺便把左下角那行统计也刷新
  const b = $('#kinds button[data-kind="fav"] .n');
  if (b) b.textContent = S.st.favorites || 0;
}

/* ---------------- 素材库列表 ---------------- */
function params() {
  const p = new URLSearchParams({ q: S.q, sort: S.sort, offset: S.offset, limit: S.limit });
  if (S.kind === "fav") p.set("fav", 1);
  else if (S.kind && S.kind !== "all") p.set("kind", S.kind);
  if (S.category) p.set("category", S.category);
  if (S.style) p.set("style", S.style);
  if (S.onlyRender) p.set("has_render", 1);
  return p;
}
function crumbText() {
  const parts = [];
  const km = S.kindMap[S.kind];
  parts.push(S.kind === "all" ? "全部文件" : S.kind === "fav" ? "我的收藏" : (km ? km.label : S.kind));
  if (S.category) parts.push(S.category);
  if (S.style) parts.push(S.style);
  if (S.q) parts.push("“" + S.q + "”");
  return parts.join(" · ");
}
async function reload() {
  S.loading = true; S.offset = 0; S.sel.clear(); S.map = {}; syncSel();
  $("#crumb").textContent = crumbText();
  const d = await api("/api/assets?" + params().toString());
  S.total = d.total; S.items = d.items.map(libItem); S.items.forEach(i => S.map[i.key] = i);
  $("#grid").innerHTML = S.items.map(cardHtml).join("");
  bindCards();
  S.loading = false;
  emptyState("没有找到匹配的素材", "试试换个关键词，或点右上角「扫描素材库」");
  renderKinds();
}
async function loadMore() {
  if (S.loading || S.offset + S.limit >= S.total) return;
  S.loading = true;
  S.offset += S.limit;
  const p = params(); p.set("offset", S.offset);
  const d = await api("/api/assets?" + p.toString());
  const items = d.items.map(libItem);
  items.forEach(i => S.map[i.key] = i);
  S.items = S.items.concat(items);
  $("#grid").insertAdjacentHTML("beforeend", items.map(cardHtml).join(""));
  bindCards();
  S.loading = false;
}
function emptyState(title, sub) {
  const has = gridItems().length;
  const e = $("#empty");
  e.classList.toggle("hidden", !!has);
  if (!has) e.innerHTML = `<span class="big">🐞</span>${esc(title)}<br><span class="dim">${esc(sub || "")}</span>`;
}

/* ---------------- 浏览磁盘 ---------------- */
async function browse(path) {
  const old = S.dir;
  S.sel.clear(); S.map = {}; syncSel();
  $("#pathInput").value = path || "";
  let d;
  try {
    d = await api("/api/browse?path=" + encodeURIComponent(path || "") + "&hidden=" + S.showHidden);
  } catch (e) { toast("打开目录失败：" + e.message, "err"); if (old) $("#pathInput").value = old; return; }
  if (d.error) { toast("打不开：" + d.error, "err"); return; }
  if (old && d.path !== old) {
    S.dirFilter = "";                                  // 换了文件夹，类型筛选不再适用
    if (S.q.trim()) { S.q = ""; $("#q").value = ""; }   // 换文件夹就丢掉筛选词，否则「上一级后文件夹都不见了」
  }
  S.dir = d.path; S.dirData = d;
  if (S.loaded && S.mode === "lib") S.loaded = d.path;
  $("#pathInput").value = d.path;
  $("#crumbs").innerHTML = (d.crumbs || []).map((c, i) =>
    `${i ? '<span class="sep">›</span>' : ""}<button data-go="${esc(c.path)}">${esc(c.name)}</button>`).join("");
  $$("#crumbs button").forEach(b => b.onclick = () => browse(b.dataset.go));
  renderBrowse();
  refreshSideStats();
}
function sortDirItems(items) {
  const by = S.sortDir;
  return items.slice().sort((a, b) => {
    if (by === "size") return (b.size || 0) - (a.size || 0);
    if (by === "new") return (b.mtime || 0) - (a.mtime || 0);
    if (by === "kind") return (a.kind || "").localeCompare(b.kind || "") || a.name.localeCompare(b.name, "zh");
    return a.name.localeCompare(b.name, "zh", { numeric: true });
  });
}
function renderBrowse() {
  const wasSearch = !!S.searchRes;
  S.searchRes = null; S.searchFor = "";
  if (wasSearch) {
    S.dirFilter = "";                     // 搜索时筛的类型对文件夹没意义，退回时清掉
    if (S.mode === "lib" && S.loaded) setTimeout(refreshSideStats, 0);
  }
  const d = S.dirData; if (!d) return;
  const dirs = (d.dirs || []).map(e => fileItem(Object.assign({}, e, { is_dir: true })));
  const files = (d.files || []).map(fileItem);
  const q = S.q.trim().toLowerCase();
  const nameHit = o => !q || o.name.toLowerCase().includes(q);
  // 类型筛选只管文件：文件夹永远显示，不然筛一下文件夹全没了、一层层点不下去
  const fileHit = o => nameHit(o) && (!S.dirFilter || o.kind === S.dirFilter);
  S.dirItems = sortDirItems(dirs.filter(nameHit)).concat(sortDirItems(files.filter(fileHit)));
  S.dirItems.forEach(i => S.map[i.key] = i);
  $("#grid").innerHTML = S.dirItems.map(cardHtml).join("");
  bindCards();
  $("#crumb").textContent = d.path;
  const n = S.dirItems.length;
  const raw = (d.dirs || []).length + (d.files || []).length;
  if (!n) {
    if (raw) emptyState(`这 ${raw} 项都被筛选条件挡住了`, "清掉搜索框里的字、或点左侧「全部文件」就能看见");
    else emptyState("这个文件夹是空的", "");
  } else emptyState("", "");
  $("#selCount").textContent = S.sel.size;
}
// 左侧「类型 / 分类」什么时候跟着走：浏览模式，或者素材库里加载了文件夹
const sideStatsOn = () => S.mode === "browse" || (S.mode === "lib" && !!S.loaded);
// 加载了根目录后，搜索框 = 在这个文件夹里（含子文件夹）递归找文件名
async function applyFsSearch() {
  const kw = S.q.trim();
  if (!kw) { S.searchRes = null; S.searchFor = ""; renderBrowse(); return; }
  const scope = S.dir || S.loaded;
  const seq = ++S.searchSeq;
  S.searchFor = kw;
  $("#crumb").textContent = "正在搜 “" + kw + "” …";
  let d = null;
  try {
    d = await api("/api/fs/search?path=" + encodeURIComponent(scope)
                  + "&q=" + encodeURIComponent(kw) + "&hidden=" + S.showHidden);
  } catch (e) {
    if (seq === S.searchSeq) { toast("搜索失败：" + e.message, "err"); renderBrowse(); }
    return;
  }
  if (seq !== S.searchSeq || S.q.trim() !== kw) return;   // 关键词又变了，丢掉这次结果
  S.searchRes = d;
  renderSearch();
}

function renderSearch() {
  const d = S.searchRes;
  if (!d) { renderBrowse(); return; }
  S.sel.clear(); S.map = {};
  const dirs = (d.items || []).filter(e => e.is_dir).map(fileItem);
  const files = (d.items || []).filter(e => !e.is_dir).map(fileItem);
  const flt = o => o.is_dir || !S.dirFilter || o.kind === S.dirFilter;
  S.dirItems = sortDirItems(dirs.filter(flt)).concat(sortDirItems(files.filter(flt)));
  S.dirItems.forEach(i => S.map[i.key] = i);
  $("#grid").innerHTML = S.dirItems.map(cardHtml).join("");
  bindCards();
  syncSel();
  renderSearchKinds();
  const n = S.dirItems.length;
  $("#crumb").textContent = "在 " + (d.path || S.loaded) + " 里搜 “" + S.searchFor + "” · 命中 " + n + " 项"
    + (S.dirFilter ? "（只看一类）" : "") + (d.truncated ? "，结果太多只列了前面这些" : "");
  if (n) $("#empty").classList.add("hidden");
  else emptyState("没搜到", "少打几个字或换个词；搜的是文件名，包含子文件夹");
}

// 搜索时左侧「类型」= 命中结果里的类型统计（点一下只在结果里筛）
function renderSearchKinds() {
  const d = S.searchRes;
  if (!d) return;
  const items = d.items || [];
  const counts = {};
  items.forEach(e => { if (!e.is_dir) { const k = e.kind || "other"; counts[k] = (counts[k] || 0) + 1; } });
  const list = [["", "全部", items.length, "#374151"]]
    .concat(Object.keys(counts).sort((a, b) => counts[b] - counts[a])
      .map(k => [k, kindLabel(k), counts[k], kindColor(k)]));
  $("#dirKinds").innerHTML = list.map(([k, label, n, col]) =>
    `<button data-k="${k}" class="${S.dirFilter === k ? "on" : ""}"><i class="dot" style="background:${col}"></i>${esc(label)} <span class="n">${n}</span></button>`).join("");
  $$("#dirKinds button").forEach(b => b.onclick = () => { S.dirFilter = b.dataset.k; refreshList(); });
  if ($("#dirKindPath")) $("#dirKindPath").textContent = "搜“" + S.searchFor + "”的结果";
}

function dirStatTarget() {
  // 「选中单个文件夹」就统计那个文件夹；没选就是当前打开的这个
  if (!sideStatsOn() || S.sel.size !== 1) return null;
  const one = S.map[Array.from(S.sel)[0]];
  return one && one.is_dir ? one : null;
}
function statKey() {
  const p = dirStatTarget();
  return p ? "S:" + p.path : "D:" + (S.dir || "");
}
let STATSEQ = 0, STATKEY = "";
function queueSideStats() {
  if (!sideStatsOn()) return;
  if (statKey() === STATKEY) return;
  clearTimeout(queueSideStats._t);
  queueSideStats._t = setTimeout(() => { if (statKey() !== STATKEY) refreshSideStats(); }, 120);
}
async function refreshSideStats() {
  if (S.searchRes) return;              // 正在看搜索结果：左侧类型归搜索管
  const seq = ++STATSEQ;
  const pick = dirStatTarget();
  const target = pick ? pick.path : (S.dir || "");
  STATKEY = statKey();
  if (!target) {
    $("#dirKinds").innerHTML = ""; $("#dirCats").innerHTML = "";
    if ($("#dirCatCount")) $("#dirCatCount").textContent = "";
    if ($("#dirKindPath")) $("#dirKindPath").textContent = "";
    return;
  }
  let d = null, g = null;
  try {
    d = (target === S.dir && S.dirData)
      ? S.dirData
      : await api("/api/browse?path=" + encodeURIComponent(target) + "&hidden=" + S.showHidden + "&limit=1");
    g = await api("/api/fs/groups?path=" + encodeURIComponent(target) + "&hidden=" + S.showHidden);
  } catch (e) { /* 打不开就按空处理 */ }
  if (seq !== STATSEQ) return;
  renderDirKinds(d, pick ? pick.name : "");
  renderDirCats(g, pick ? pick.name : "");
}
function renderDirKinds(d, picked) {
  d = d || S.dirData;
  const counts = (d && d.counts) || {};
  let totalFiles = 0;
  Object.keys(counts).forEach(k => { totalFiles += counts[k]; });
  const nm = (d && d.path) ? (d.path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || d.path) : "";
  if ($("#dirKindPath")) $("#dirKindPath").textContent = picked ? ("选中 " + picked) : nm;
  const items = [["", "全部", totalFiles, "#374151"]]
    .concat(Object.keys(counts).sort((a, b) => counts[b] - counts[a]).map(k => [k, kindLabel(k), counts[k], kindColor(k)]));
  $("#dirKinds").innerHTML = items.map(([k, label, n, col]) =>
    `<button data-k="${k}" class="${S.dirFilter === k ? "on" : ""}"><i class="dot" style="background:${col}"></i>${esc(label)} <span class="n">${n}</span></button>`).join("");
  $$("#dirKinds button").forEach(b => b.onclick = async () => {
    const pick = dirStatTarget();
    if (pick && b.dataset.k === "") { S.sel.clear(); S.map = {}; syncSel(); return; }
    if (pick) { await browse(pick.path); S.dirFilter = b.dataset.k; refreshList(); refreshSideStats(); }
    else { S.dirFilter = b.dataset.k; renderDirKinds(); refreshList(); }
  });
}

/* 浏览模式左侧「分类」：跟着你选中 / 打开的文件夹走，列出里面的子文件夹，点一下进去 */
function renderDirCats(g, picked) {
  const box = $("#dirCats");
  if (!box) return;
  const groups = (g && g.groups) || [];
  S.dirCats = groups;
  const badge = $("#dirCatCount");
  if (badge) badge.textContent = (picked ? "选中 " + picked + " · " : "") + (groups.length ? groups.length + " 个" : "");
  const kw = (($("#dirCatFilter") || {}).value || "").trim().toLowerCase();
  const shown = kw ? groups.filter(x => x.name.toLowerCase().includes(kw)) : groups;
  if (!shown.length) {
    box.innerHTML = '<div class="dim" style="font-size:12px;padding:4px 2px">'
      + (groups.length ? "没有匹配的分类" : "这个文件夹里没有子文件夹，文件都直接列在右边了") + "</div>";
    return;
  }
  box.innerHTML = shown.map(x =>
    '<button data-catgo="' + esc(x.path) + '" title="' + esc(x.path) + '">'
    + '<span class="kl">' + (x.dirs ? "📂" : "📁") + " " + esc(x.name) + "</span>"
    + '<span class="n">' + x.files + "</span></button>").join("")
    + (g && g.truncated
        ? '<div class="dim" style="font-size:11px;padding:4px 2px">文件夹太多，只统计了前面几个。点右上角「刷新」可重算。</div>'
        : "");
  $$("#dirCats button").forEach(b => b.onclick = () => browse(b.dataset.catgo));
}

/* ---------------- 详情抽屉 ---------------- */
function pvHtml(it) {
  if (it.is_dir) {
    return `<div class="pv"><div class="thumb folder" style="height:150px;display:grid;place-items:center"><span class="fi" style="font-size:52px">📁</span></div>
      <div class="lab">${esc(it.name)}</div></div>`;
  }
  const t = it.thumb, r = it.raw, tl = thumbLg(t);
  const bar = `<div class="pvbar"><button data-pv="raw">原图 / 原件</button>`
    + `<button data-pv="open">用看图软件打开</button>`
    + `<button data-pv="pickviewer">指定看图软件…</button>`
    + `<button data-pv="openas">换打开方式…</button></div>`;
  switch (it.preview) {
    case "model3d":
      return `<div class="pv v3dpv"><div id="pv3d" class="v3d">
          <div class="v3load"><div class="spin"></div><div id="pv3dMsg">正在准备 3D 预览…</div></div>
        </div>
        <div class="v3db">
          <button data-3d="wire">线框</button>
          <button data-3d="box">包围盒</button>
          <button data-3d="grid">地面网格</button>
          <button data-3d="reset">重置视角</button>
          <button data-3d="wide">放大面板</button>
          <button data-pv="open">用 SketchUp 打开</button>
        </div></div>
        <div class="v3dinfo" id="pv3dInfo"></div>`;
    case "image": case "svg": {
      // 图片缩略图关了之后，详情里直接加载原图（比缩略图清楚，也省一次生成）
      const direct = (imgNoThumb(it) && it.ext && IMG_DIRECT.test(it.ext)) ? it.raw : "";
      return `<div class="pv"><img id="pvImg" src="${direct || tl || t}" alt="" onerror="this.src='${t}'">${bar}
        <div class="lab">${esc(it.filename)} · 点「原图」看原始分辨率</div></div>`;
    }
    case "video":
      return `<div class="pv"><video src="${r}" controls preload="metadata"></video><div class="lab">视频预览</div></div>`;
    case "audio":
      return `<div class="pv"><div class="aud"><img src="${t}" onerror="this.remove()"><audio src="${r}" controls></audio></div>
        <div class="lab">音频预览</div></div>`;
    case "pdf":
      return `<div class="pv"><iframe src="${r}" loading="lazy"></iframe><div class="lab">PDF 预览（可滚动 / 缩放）</div></div>`;
    case "text":
      return `<div class="pv"><pre class="txt" id="pvText">正在读取…</pre><div class="lab">文本预览（只读，可滚动）</div></div>`;
    case "archive":
      return `<div class="pv" style="padding:10px"><div id="pvArc" class="arclist">正在读取压缩包…</div>
        <div class="lab">压缩包内容</div></div>`;
    case "font":
      return `<div class="pv"><img src="${t}" alt="">${bar}<div class="lab">字体预览 · 双击可安装/查看</div></div>`;
    default:
      return `<div class="pv"><img src="${t}" alt="">${bar}
        <div class="lab">${esc(kindLabel(it.kind))} · 该类型没有内置预览，可直接用默认程序打开</div></div>`;
  }
}
function drawerActs(it) {
  const a = [];
  a.push(["open", "用默认程序打开", "primary"]);
  a.push(["openas", "打开方式…", ""]);
  a.push(["reveal", "在资源管理器中定位", ""]);
  if (it.from === "lib") {
    a.push(["export", "复制到文件夹…", ""]);
    a.push(["copy", "复制（到「浏览文件」里粘贴）", ""]);
    a.push(["fav", it.favorite ? "★ 取消收藏" : "☆ 收藏", "ghost"]);
    a.push(["rename", "重命名…", "ghost"]);
    if (it.inner || it.kind === "archive") a.push(["extract", "解压", "ghost"]);
  } else {
    a.push(["export", "复制到文件夹…", ""]);
    a.push(["moveto", "移动到文件夹…", "ghost"]);
    a.push(["copy", "复制", ""]);
    a.push(["cut", "剪切", ""]);
    a.push(["rename", "重命名…", "ghost"]);
    if (!it.is_dir) a.push(["zip", "压缩为 zip", "ghost"]);
    if (it.kind === "archive") a.push(["extract", "解压到…", "ghost"]);
    if (!it.is_dir) a.push(["fav", it.favorite ? "★ 取消收藏" : "☆ 收藏", "ghost"]);
    a.push(["recycle", "删除到回收站", "ghost"]);
  }
  a.push(["copyfull", "复制完整路径", "ghost"]);
  if (it.from === "fs" && it.is_dir) a.push(["enter", "进入该文件夹", "ghost"]);
  return a.map(([act, l, c]) => `<button class="btn ${c}" data-d="${act}">${esc(l)}</button>`).join("");
}
async function openDrawer(it) {
  if (!it) return;
  stop3D();                     // 上一张的 3D 画布即将被替换，先释放
  S.cur = it;
  $("#drawer").classList.remove("hidden");
  $("#drawer").classList.toggle("wide", it.preview === "model3d");
  $("#dTitle").textContent = it.is_dir ? "文件夹" : kindLabel(it.kind);
  const kv = it.from === "lib"
    ? `<dt>名称</dt><dd>${esc(it.name)}</dd>
       <dt>文件名</dt><dd>${esc(it.filename)}</dd>
       <dt>类型</dt><dd>${esc(kindLabel(it.kind))}${it.ext ? " · " + esc(it.ext) : ""}</dd>
       <dt>分类</dt><dd>${esc(it.category || "-")}${it.style ? " · " + esc(it.style) : ""}</dd>
       <dt>大小</dt><dd>${fmtSize(it.size)}</dd>
       <dt>修改时间</dt><dd>${esc(fmtTime(it.mtime))}</dd>`
    : `<dt>名称</dt><dd>${esc(it.name)}</dd>
       <dt>类型</dt><dd>${esc(kindLabel(it.kind))}${it.ext ? " · " + esc(it.ext) : ""}</dd>
       <dt>大小</dt><dd>${it.is_dir ? "文件夹" : fmtSize(it.size)}</dd>
       <dt>修改时间</dt><dd>${esc(fmtTime(it.mtime))}</dd>`;
  const pathTxt = it.from === "lib"
    ? esc(it.source_path) + (it.inner ? "<br>↳ " + esc(it.inner) : "")
    : esc(it.path);
  $("#dBody").innerHTML = `${pvHtml(it)}
    <div class="dacts">${drawerActs(it)}</div>
    <dl class="kv">${kv}</dl>
    <div class="pathbox">${pathTxt}</div>
    <div id="dExtra"></div>`;
  $$("#dBody [data-d]").forEach(b => b.onclick = () => doAct(b.dataset.d, it));
  $$("#dBody [data-pv]").forEach(b => b.onclick = () => {
    if (b.dataset.pv === "raw") {
      const im = $("#pvImg");
      if (im) { im.src = it.raw + (it.raw.includes("?") ? "&" : "?") + "t=" + Date.now(); toast("正在载入原图…"); }
    } else if (b.dataset.pv === "pickviewer") openViewerPicker();
    else doAct(b.dataset.pv, it);
  });
  if (it.preview === "text") {
    try {
      const d = await api(it.text);
      const el = $("#pvText");
      if (el) el.textContent = d.text + (d.truncated ? "\n\n…（文件较大，仅显示开头部分）" : "");
    } catch (e) { const el = $("#pvText"); if (el) el.textContent = "读取失败：" + e.message; }
  }
  $$("#dBody [data-3d]").forEach(b => b.onclick = () => v3Act(b.getAttribute("data-3d")));
  if (it.preview === "model3d") start3D(it);
  if (it.preview === "archive") loadArchiveList(it);   // 素材库里的压缩包也能展开看
  if (it.is_dir && it.from === "fs") loadDirStats(it);
  if (it.from === "lib") loadDrawerExtra(it);
}
async function loadArchiveList(it) {
  const box = $("#pvArc"); if (!box) return;
  const arc = it.path || it.source_path;      // 素材库条目用 source_path
  if (!arc) { box.textContent = "找不到这个压缩包的位置"; return; }
  try {
    const d = await api("/api/archive-entries?path=" + encodeURIComponent(arc));
    box.innerHTML = `<div class="it" style="background:#f4f6f8"><span class="nm"><b>${d.total}</b> 个文件</span>
        <span class="acts"><button data-arc="all">全部解压到…</button></span></div>` +
      d.items.slice(0, 400).map(e => `<div class="it"><span class="nm" title="${esc(e.inner)}">${esc(e.inner)}</span>
        <span class="sz">${fmtSize(e.size)}</span>
        <span class="acts">${/\.(jpg|jpeg|png|gif|webp|bmp|tif|tiff|svg)$/i.test(e.inner) || /\.skp$/i.test(e.inner)
          ? `<button data-arc-inner="${esc(e.inner)}">预览</button>` : ""}
        <button data-arc-one="${esc(e.inner)}">取出…</button></span></div>`).join("");
    box.querySelectorAll("[data-arc-one]").forEach(b => b.onclick = async () => {
      const dest = await pickFolder("把「" + b.dataset.arcOne.split("/").pop() + "」解压到哪个文件夹？");
      if (!dest) return;
      const r = await api("/api/archive-extract-one", { archive: arc, inner: b.dataset.arcOne, dest });
      toast(r.ok ? "已解压到 " + dest : "解压失败：" + r.msg, r.ok ? "ok" : "err");
    });
    box.querySelectorAll("[data-arc-inner]").forEach(b => b.onclick = () => {
      const inner = b.dataset.arcInner;
      const ext = inner.slice(inner.lastIndexOf(".")).toLowerCase();
      const url = "/api/raw?path=" + encodeURIComponent(arc) + "&inner=" + encodeURIComponent(inner);
      if (ext === ".skp") {
        // 包里的模型直接在小虫里转着看
        if (!arc.toLowerCase().endsWith(".zip")) {
          return toast("这个格式的压缩包要先「取出…」再预览 3D（zip 才能直读）", "warn", 6000);
        }
        $("#drawer").classList.add("wide");
        $("#dBody").insertAdjacentHTML("afterbegin",
          pvHtml({ preview: "model3d", from: "fs", name: inner }) + '<div style="height:8px"></div>');
        $$("#dBody [data-3d]").forEach(x => x.onclick = () => v3Act(x.getAttribute("data-3d")));
        start3D({ from: "fs", path: arc, inner: inner });
        return;
      }
      if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff"].includes(ext.slice(1)))
        window.open(url, "_blank");
      else toast("这个类型先「取出…」再看", "warn", 5000);
    });
    const all = box.querySelector("[data-arc=all]");
    if (all) all.onclick = () => doAct("extract", it);
  } catch (e) { box.textContent = "读取失败：" + e.message; }
}
async function loadDirStats(it) {
  const box = $("#dExtra"); if (!box) return;
  box.innerHTML = `<div class="hint">正在统计文件夹大小…</div>`;
  try {
    const d = await api("/api/fs/stats", { path: it.path });
    box.innerHTML = `<div class="hint">共 <b>${d.files}</b> 个文件，合计 <b>${fmtSize(d.size)}</b>${d.partial ? "（统计超时，为部分结果）" : ""}</div>`;
  } catch (e) { box.innerHTML = ""; }
}
async function loadDrawerExtra(it) {
  const box = $("#dExtra"); if (!box || !it.id) return;
  try {
    const a = await api("/api/asset/" + it.id);
    let h = "";
    if (a.collection_images && a.collection_images.length)
      h += `<h4 style="font-size:11px;color:var(--dim);letter-spacing:.08em;margin:0 0 8px">该合集的效果图 / 预览图（${a.collection_images.length}）</h4>
        <div class="sib">${a.collection_images.map(s => `<img src="/api/thumb/${s.id}" title="${esc(s.name)}" data-big="${s.id}">`).join("")}</div>`;
    if (a.siblings && a.siblings.length)
      h += `<h4 style="font-size:11px;color:var(--dim);letter-spacing:.08em;margin:14px 0 8px">同一组合的其它模型（${a.siblings.length}）</h4>
        <div class="sib">${a.siblings.map(s => `<img src="/api/thumb/${s.id}" title="${esc(s.name)}" data-open="${s.id}">`).join("")}</div>`;
    box.innerHTML = h;
    $$("#dExtra .sib img").forEach(i => i.onclick = () => {
      if (i.dataset.big) { const im = $("#pvImg"); if (im) im.src = `/api/thumb/${i.dataset.big}?size=1400&render=0`; }
      else if (i.dataset.open) {
        const s = (a.siblings || []).find(x => x.id === +i.dataset.open);
        if (s) openDrawer(Object.assign(libItem({ id: s.id, name: s.name, kind: "model", render_id: s.render_id, preview: "image" }), {}));
      }
    });
  } catch (e) { /* ignore */ }
}
$("#dClose").onclick = () => { $("#drawer").classList.add("hidden"); };
window.addEventListener("beforeunload", () => { try { stop3D(); } catch (e) {} });

/* ---------------- 选择条与动作 ---------------- */
const LIB_ACTS = [
  ["open", "打开", "primary"], ["openas", "打开方式", ""], ["reveal", "定位", ""],
  ["export", "复制到文件夹…", ""], ["copy", "复制", ""], ["cut", "剪切", ""],
  ["fav", "☆ 收藏", "ghost"], ["rename", "批量重命名", "ghost"],
  ["extract", "解压", "ghost"], ["copyfull", "复制路径", "ghost"],
];
const FS_ACTS = [
  ["open", "打开", "primary"], ["openas", "打开方式", ""], ["reveal", "定位", ""],
  ["export", "复制到…", ""], ["moveto", "移动到…", ""],
  ["copy", "复制", ""], ["cut", "剪切", ""], ["paste", "粘贴到此处", ""],
  ["rename", "重命名", "ghost"], ["zip", "压缩为 zip", "ghost"], ["extract", "解压", "ghost"],
  ["recycle", "删除到回收站", "ghost"], ["newfolder", "新建文件夹", "ghost"], ["copyfull", "复制路径", "ghost"],
];
function actLabel(a, l) {          // 「收藏」按钮跟着选中项状态变文案
  if (a !== "fav") return l;
  const items = selItems();
  return items.length && items.every(x => x.favorite) ? "★ 取消收藏" : "☆ 收藏";
}
function renderSelActs() {
  const list = isLib() ? LIB_ACTS : FS_ACTS;
  $("#selacts").innerHTML = list.map(([a, l, c]) => `<button class="btn ${c}" data-act="${a}">${esc(actLabel(a, l))}</button>`).join("");
  $$("#selacts button").forEach(b => b.onclick = () => doAct(b.dataset.act));
}
async function quickAct(act, it) {
  if (act === "enter") return browse(it.path);
  if (act === "open" || act === "reveal") return doAct(act, it);
  if (act === "export") return it.from === "lib" ? openExport([it.id]) : copyTo([it], false);
  if (act === "moveto") return copyTo([it], true);
  if (act === "copy") return doAct("copy", it);
}
async function doAct(act, one) {
  const items = one ? [one] : selItems();
  const libs = items.filter(x => x.from === "lib"), fss = items.filter(x => x.from === "fs");
  const ids = libs.map(x => x.id), paths = fss.map(x => x.path);
  try {
    if (act === "enter") return browse(one.path);
    if (act === "open" || act === "openas" || act === "reveal") {
      if (!items.length) return toast("先选中文件", "warn");
      let ok = 0, errs = [], progs = [];
      for (const x of items) {
        const r = x.from === "lib"
          ? await api("/api/open", { id: x.id, mode: act })
          : await api("/api/fs/open", { path: x.path, mode: act });
        if (r.ok) {
          ok++;
          if (act === "open" && r.msg) progs.push(shortProg(r.msg));
        } else errs.push(r.msg || "失败");
      }
      const why = errs.length ? "：" + errs[0] : "";
      const uniq = [...new Set(progs)];
      const prog = uniq.length
        ? "（" + uniq.slice(0, 2).join(" / ") + (uniq.length > 2 ? " 等" : "") + "）" : "";
      const staged = ok && items.some(x => x.from === "lib" && x.inner)
        ? "，压缩包里的文件已先取到暂存目录" : "";
      toast(act === "reveal" ? `已在资源管理器中定位 ${ok} 项${why}`
            : (ok ? `已打开 ${ok} 项${prog}${staged}${why}` : `打不开${why}`),
            ok ? "ok" : "err", errs.length ? 9000 : 4200);
      return;
    }
    if (act === "export") {
      if (fss.length && !libs.length) return copyTo(fss, false);
      if (fss.length) return copyTo(fss, false);
      if (!ids.length) return toast("请选择素材库里的素材", "warn");
      return openExport(ids);
    }
    if (act === "moveto") {
      if (!fss.length) return toast("请选择磁盘上的文件或文件夹", "warn");
      return copyTo(fss, true);
    }
    if (act === "copy" || act === "cut") {
      if (!items.length) return toast("先选中文件", "warn");
      let p = paths, tip = "";
      if (libs.length) {
        const r = await api("/api/stage", { ids });
        if (!r.ok) return toast("提取失败：" + r.msg, "err");
        p = r.files.concat(paths);
        tip = `（${libs.length} 个库内素材已提取到暂存目录）`;
      }
      const r = await api("/api/clip", { paths: p, mode: act === "cut" ? "move" : "copy" });
      S.clip = { paths: r.paths, mode: r.mode };
      toast(`已${act === "cut" ? "剪切" : "复制"} ${r.ok} 项${tip}。`
            + `接着切到「浏览文件」打开目标文件夹，点工具栏的「粘贴」（或按 Ctrl+V）。`
            + `想直接一步到位，也可以选中后点「复制到…」。`, "ok", 10000);
      return;
    }
    if (act === "paste") {
      if (!S.dir) return toast("先选一个目标文件夹：左边挑一个磁盘/文件夹，或点工具栏的「浏览…」", "warn", 9000);
      const c = await api("/api/clip").catch(() => null) || S.clip;
      S.clip = c;
      if (!c.paths || !c.paths.length)
        return toast("剪贴板是空的：先选中文件点「复制」或「剪切」，再回到目标文件夹里点「粘贴」", "warn", 9000);
      const r = await api("/api/fs/paste", { dest: S.dir });
      if (r.errors && r.errors.length) return toast("粘贴不了：" + r.errors[0], "err", 8000);
      toast(`正在${r.mode === "move" ? "移动" : "复制"} ${r.ok} 项到当前文件夹…`, "ok");
      pollJobs(true); setTimeout(() => browse(S.dir), 1500);
      return;
    }
    if (act === "fav" || act === "favoff") {
      const sel = items.filter(x => !x.is_dir);
      if (!sel.length) return toast("先选中要收藏的文件（文件夹不能收藏）", "warn");
      const allFav = sel.every(x => x.favorite);
      const val = act === "favoff" ? 0 : (allFav ? 0 : 1);
      const body = { value: val };
      const lids = sel.filter(x => x.from === "lib").map(x => x.id);
      const fps = sel.filter(x => x.from === "fs").map(x => x.path || x.source_path);
      if (lids.length) body.ids = lids;
      if (fps.length) body.paths = fps;
      let r;
      try { r = await api("/api/favorite", body); }
      catch (e) { return toast("收藏失败：" + e.message, "err", 7000); }
      if (!r.ok) return toast("收藏失败：这些项目可能没有权限", "err", 7000);
      const keys = new Set(sel.map(x => x.key));
      const pset = new Set(sel.map(x => (x.path || "").toLowerCase()));
      [S.items, S.dirItems].forEach(L => L.forEach(y => { if (keys.has(y.key)) y.favorite = val; }));
      ((S.dirData || {}).files || []).forEach(f => {
        if (pset.has((f.path || "").toLowerCase())) f.favorite = val;
      });
      const miss = r.missed || 0;
      toast(val ? `已收藏 ${r.ok} 项${miss ? `（${miss} 项没能收藏）` : ""}`
                : `已取消收藏 ${r.ok} 项`, "ok");
      refreshFavCount();
      if (S.cur && sel.some(x => x.key === S.cur.key)) {
        const db2 = $('#dBody [data-d="fav"]');
        if (db2) db2.textContent = val ? "★ 取消收藏" : "☆ 收藏";
      }
      if (isLib()) {
        if (!val && S.kind === "fav") { S.sel.clear(); S.map = {}; S.cur = null; reload(); }
        else {
          // 就地更新星星，不刷新整页：不跳滚动条、选中状态也不丢（可以再按 F 取消）
          $$("#grid .card").forEach(c => {
            if (!keys.has(c.dataset.key)) return;
            const b = c.querySelector(".favbtn");
            if (!b) return;
            b.classList.toggle("fav-on", !!val);
            b.textContent = val ? "★" : "☆";
            b.title = val ? "取消收藏" : "收藏到我的收藏";
          });
          syncSel();
        }
      } else renderBrowse();
      return;
    }
    if (act === "rename") {
      if (!items.length) return toast("先选中文件", "warn");
      return libs.length ? openRename(ids) : openFsRename(fss);
    }
    if (act === "extract") {
      const zips = items.map(x => x.source_path || x.path).filter(p => p && /\.[0-9a-z]{2,4}$/i.test(p));
      const uniq = [...new Set(zips)];
      if (!uniq.length) return toast("没找到可解压的压缩包", "warn");
      return openExtract(uniq);
    }
    if (act === "zip") { if (!paths.length) return toast("请选择磁盘上的文件", "warn"); return openZip(paths); }
    if (act === "recycle") {
      if (!paths.length) return toast("请选择磁盘上的文件", "warn");
      if (!confirm(`把 ${paths.length} 项移到回收站？\n\n（可在 Windows 回收站里还原，不是永久删除）`)) return;
      const r = await api("/api/fs/recycle", { paths });
      toast(`已移到回收站 ${r.ok}/${r.total || paths.length}${r.errors && r.errors.length ? "：" + r.errors[0] : ""}`, r.errors && r.errors.length ? "warn" : "ok");
      S.sel.clear(); S.map = {}; syncSel();
      if (S.mode === "browse") browse(S.dir); else reload();
      return;
    }
    if (act === "newfolder") return mkdirPrompt();
    if (act === "copyfull") {
      const t = items.map(x => x.from === "lib" ? (x.source_path + (x.inner ? "\\" + x.inner : "")) : x.path).join("\r\n");
      if (!t) return;
      navigator.clipboard.writeText(t).then(() => toast("路径已复制", "ok"), () => toast("复制失败", "err"));
      return;
    }
  } catch (e) { toast("操作失败：" + e.message, "err"); }
}

/* ---------------- 文件夹 / 重命名小工具 ---------------- */
async function mkdirPrompt() {
  if (!S.dir) return toast("先打开一个文件夹", "warn");
  const name = prompt("新建文件夹名称：", "新建文件夹");
  if (!name) return;
  const r = await api("/api/fs/mkdir", { parent: S.dir, name });
  toast(r.ok ? "已创建：" + r.path.split("\\").pop() : "创建失败：" + r.msg, r.ok ? "ok" : "err");
  if (r.ok) browse(S.dir);
}
function pickFolder(title) { return api("/api/pick-folder", { title }).then(r => r.path || "").catch(() => ""); }
/* 复制 / 移动到指定文件夹：库内素材走导出，磁盘文件走系统复制 */
async function copyTo(items, move) {
  if (!items.length) return toast("先选中要" + (move ? "移动" : "复制") + "的东西", "warn");
  const dest = await pickFolder(move ? "把这些移动到哪个文件夹？" : "复制到哪个文件夹？");
  if (!dest) return;
  const libs = items.filter(x => x.from === "lib"), fss = items.filter(x => x.from === "fs");
  const verb = move ? "移动" : "复制";
  let ok = 0, errs = [];
  toast(`正在${verb}到 ${dest} …`, "ok", 2500);
  try {
    if (libs.length) {
      const r = await api("/api/export", { ids: libs.map(x => x.id), dest, bundle: true });
      ok += r.ok || 0;
      if (r.errors && r.errors.length) errs = errs.concat(r.errors);
    }
    if (fss.length) {
      const r = await api("/api/fs/copy", { paths: fss.map(x => x.path), dest, move });
      if (r.errors && r.errors.length) errs = errs.concat(r.errors);
      else { ok += r.ok || 0; pollJobs(true); }
    }
  } catch (e) { errs.push(e.message); }
  toast(`${verb}到「${dest}」：成功 ${ok} 个${errs.length ? "，失败 " + errs.length + " 个：" + errs[0] : ""}`,
        errs.length ? "warn" : "ok", errs.length ? 10000 : 6000);
  if (S.mode === "browse" && S.dir) setTimeout(() => browse(S.dir), 1400);
}
async function openFsRename(items) {
  if (!items.length) return;
  modal(`<div class="mh"><span>重命名（${items.length} 项）</span><button class="ghost icon" data-close>×</button></div>
    <div class="mb">
      <div class="fldrow">
        <div class="fld"><label>查找</label><input id="fFind" placeholder="要替换掉的文字"></div>
        <div class="fld"><label>替换为</label><input id="fRepl"></div>
        <div class="fld"><label>前缀</label><input id="fPre"></div>
        <div class="fld"><label>后缀</label><input id="fSuf"></div>
        <div class="fld"><label>序号起始</label><input id="fNum" type="number" value="1"></div>
        <div class="fld"><label>序号位数</label><input id="fPad" type="number" value="2"></div>
        <div class="fld"><label>序号连接符</label><input id="fSep" value="-"></div>
        <div class="fld"><label>大小写</label><select id="fCase"><option value="keep">不变</option><option value="lower">全小写</option><option value="upper">全大写</option></select></div>
        <div class="fld"><label>选项</label><label class="chk"><input type="checkbox" id="fNumOn"> 加序号</label>
          <label class="chk"><input type="checkbox" id="fExt" checked> 保留扩展名</label></div>
      </div>
      <div id="fPv"></div>
    </div>
    <div class="mf"><button class="btn" data-close>取消</button><button class="btn primary" id="fGo">开始重命名</button></div>`);
  const rules = () => ({
    find: $("#fFind").value, repl: $("#fRepl").value, prefix: $("#fPre").value, suffix: $("#fSuf").value,
    numbering: $("#fNumOn").checked, num_start: +$("#fNum").value || 1, num_pad: +$("#fPad").value || 2,
    num_sep: $("#fSep").value, case: $("#fCase").value,
  });
  const calc = () => {
    const r = rules();
    return items.map((it, i) => {
      const dot = it.name.lastIndexOf(".");
      const ext = dot > 0 ? it.name.slice(dot) : "";
      let stem = dot > 0 ? it.name.slice(0, dot) : it.name;
      if (r.find) stem = stem.split(r.find).join(r.repl);
      if (r.case === "lower") stem = stem.toLowerCase();
      if (r.case === "upper") stem = stem.toUpperCase();
      if (r.numbering) stem = (stem || "") + r.num_sep + String(r.num_start + i).padStart(r.num_pad, "0");
      stem = r.prefix + stem + r.suffix;
      return { it, name: stem + ext };
    });
  };
  const pv = () => {
    const rows = calc();
    $("#fPv").innerHTML = `<table class="pv-table"><tr><th>原名</th><th>新名称</th></tr>` +
      rows.map(r => `<tr class="${r.name === r.it.name ? "" : "chg"}"><td>${esc(r.it.name)}</td><td>${esc(r.name)}</td></tr>`).join("") + `</table>`;
  };
  ["fFind", "fRepl", "fPre", "fSuf", "fNum", "fPad", "fSep", "fCase", "fNumOn"].forEach(id => $("#" + id).oninput = pv);
  pv();
  $("#fGo").onclick = async () => {
    const rows = calc().filter(r => r.name && r.name !== r.it.name);
    if (!rows.length) return toast("没有需要改名的项目", "warn");
    closeModal();
    let ok = 0, errs = [];
    for (const r of rows) {
      const res = await api("/api/fs/rename", { path: r.it.path, name: r.name });
      if (res.ok) ok++; else errs.push(r.it.name + "：" + res.msg);
    }
    toast(`改名完成 ${ok}/${rows.length}${errs.length ? "，失败：" + errs[0] : ""}`, errs.length ? "warn" : "ok", 7000);
    if (ok) { if (S.mode === "browse") browse(S.dir); else reload(); }
  };
}
async function openExtract(paths) {
  const dest = await pickFolder("解压到哪个文件夹？（取消则解压到压缩包旁边）");
  const per = confirm("每个压缩包解压到独立子文件夹？\n\n【确定】各自一个文件夹（推荐）\n【取消】全部平铺到同一文件夹");
  const del = confirm("解压后删除原压缩包？\n\n【确定】删除原压缩包\n【取消】保留（推荐）");
  const r = await api("/api/extract", { paths, dest: dest || "", per_folder: per, delete_source: del });
  toast(`解压完成 ${r.ok}/${paths.length}${r.errors && r.errors.length ? "，失败：" + r.errors[0] : ""}`, r.errors && r.errors.length ? "warn" : "ok", 7000);
  if (S.mode === "browse") browse(S.dir);
}
async function openZip(paths) {
  const base = paths[0].replace(/\\[^\\]+$/, "");
  modal(`<div class="mh"><span>压缩为 zip（${paths.length} 项）</span><button class="ghost icon" data-close>×</button></div>
    <div class="mb">
      <div class="fld"><label>保存到文件夹</label><div class="row"><input id="zDest" style="flex:1" value="${esc(base)}">
        <button class="btn" id="zPick">浏览…</button></div></div>
      <div class="fld" style="margin-top:10px"><label>压缩包名称</label><input id="zName" value="${esc(paths.length === 1 ? paths[0].replace(/^.+\\(.+?)(\.[^.]+)?$/, "$1") : "打包")}"></div>
      <div class="hint">压缩完成后会在上面这个文件夹里生成 .zip 文件。</div>
    </div>
    <div class="mf"><button class="btn" data-close>取消</button><button class="btn primary" id="zGo">开始压缩</button></div>`);
  $("#zPick").onclick = async () => { const p = await pickFolder("压缩包保存到哪个文件夹？"); if (p) $("#zDest").value = p; };
  $("#zGo").onclick = async () => {
    const dest = $("#zDest").value.trim(), name = $("#zName").value.trim() || "打包";
    closeModal();
    const r = await api("/api/fs/zip", { paths, dest, name });
    toast(r.ok ? "正在压缩…" : (r.msg || "失败"), r.ok ? "ok" : "err");
    pollJobs(true);
    setTimeout(() => { if (S.mode === "browse") browse(S.dir); }, 1500);
  };
}

/* ---------------- 弹窗：导出 / 批量重命名 / 设置 ---------------- */
function modal(html) {
  $("#modalBox").innerHTML = html;
  $("#modal").classList.remove("hidden");
  $$("#modalBox [data-close]").forEach(b => b.onclick = closeModal);
  $("#modal").onclick = e => { if (e.target.id === "modal") closeModal(); };
}
function closeModal() { $("#modal").classList.add("hidden"); $("#modalBox").innerHTML = ""; }

async function openViewerPicker() {
  let d = { items: [], current: "" };
  try { d = await api("/api/viewers"); } catch (e) { /* ignore */ }
  const items = d.items || [];
  if (!items.length)
    return toast("本机没扫到可用的看图软件。可以点图片上的「换打开方式…」，用 Windows 的对话框挑一个。", "warn", 9000);
  modal(`<div class="mh"><span>选一个看图软件</span><button class="ghost icon" data-close>×</button></div>
    <div class="mb">
      <div class="hint" style="margin-top:0">选好之后，图片都用它打开（「设置 → 文件关联」里也能改）。
        找不到想用的？先点下面「自己选一个程序…」。</div>
      ${items.map(it => `<div class="clrow"><span class="nm">${esc(it.name)}</span>
        <span class="dim">${esc(it.exe)}${it.note ? " · " + esc(it.note) : ""}</span>
        ${it.exe === d.current ? '<span class="pill">当前</span>' : ""}
        <button class="btn primary sm" data-vw="${esc(it.exe)}">用这个</button></div>`).join("")}
      <div class="dacts" style="margin-top:12px">
        <button class="btn" id="vwBrowse">自己选一个程序…</button>
        <button class="btn ghost" id="vwSys">跟 Windows 默认一致</button>
      </div>
    </div>
    <div class="mf"><button class="btn" data-close>取消</button></div>`);
  $$("#modalBox [data-vw]").forEach(b => b.onclick = async () => {
    await api("/api/settings", { image_viewer: b.dataset.vw });
    closeModal();
    toast("看图软件设好了，点图片的「用看图软件打开」就能用。", "ok", 7000);
    loadState();
  });
  $("#vwSys").onclick = async () => {
    await api("/api/settings", { image_viewer: "" });
    closeModal(); toast("已改成跟 Windows 默认一致", "ok", 6000); loadState();
  };
  $("#vwBrowse").onclick = async () => {
    const p2 = await api("/api/pick-file", { title: "选一个看图软件（exe）", ext: ".exe" })
      .then(r => r.path || "").catch(() => "");
    if (!p2) return;
    await api("/api/settings", { image_viewer: p2 });
    closeModal(); toast("看图软件设好了：" + p2, "ok", 7000); loadState();
  };
}

async function openExport(ids) {
  const st = S.st || {};
  const def = localStorage.getItem("xc_export_dest") || ((st.data_dir || "") + "\\导出");
  modal(`<div class="mh"><span>复制到文件夹（${ids.length} 项）</span><button class="ghost icon" data-close>×</button></div>
    <div class="mb">
      <div class="fld"><label>目标文件夹（复制到这里）</label>
        <div class="row"><input id="dest" style="flex:1" value="${esc(def)}" placeholder="例如 D:\\项目\\某小区\\素材">
        <button class="btn" id="pick">浏览…</button></div></div>
      <label class="chk" style="margin-top:10px"><input type="checkbox" id="bundle" checked> 连压缩包内同目录的贴图 / 说明文件一起复制</label>
      <div class="hint">文件名用索引里整理过的名称（已去掉卖家广告词、修复乱码），重名会自动加序号。<br>
      复制出来的文件可以直接用 SketchUp / 其它软件打开；压缩包里的文件会自动解出来。</div>
    </div>
    <div class="mf"><button class="btn" data-close>取消</button><button class="btn primary" id="go">开始复制</button></div>`);
  $("#pick").onclick = async () => { const p = await pickFolder("复制到哪个文件夹？"); if (p) $("#dest").value = p; };
  $("#go").onclick = async () => {
    const dest = $("#dest").value.trim();
    if (!dest) return toast("先点「浏览…」选一个目标文件夹", "warn", 8000);
    closeModal(); toast(`正在复制到 ${dest} …`, "ok", 2500);
    const r = await api("/api/export", { ids, dest, bundle: $("#bundle") && $("#bundle").checked });
    if (r.ok) localStorage.setItem("xc_export_dest", dest);
    toast(`已复制到「${dest}」：成功 ${r.ok} 个${r.errors && r.errors.length ? "，失败 " + r.errors.length + " 个（" + r.errors[0] + "）" : ""}`,
      r.errors && r.errors.length ? "warn" : "ok", 8000);
  };
}

async function openRename(ids) {
  modal(`<div class="mh"><span>批量重命名（${ids.length} 项）</span><button class="ghost icon" data-close>×</button></div>
    <div class="mb">
      <div class="fldrow">
        <div class="fld"><label>查找</label><input id="rFind" placeholder="要替换掉的文字"></div>
        <div class="fld"><label>替换为</label><input id="rRepl"></div>
        <div class="fld"><label>前缀</label><input id="rPre"></div>
        <div class="fld"><label>后缀</label><input id="rSuf"></div>
        <div class="fld"><label>序号起始</label><input id="rNum" type="number" value="1"></div>
        <div class="fld"><label>序号位数</label><input id="rPad" type="number" value="2"></div>
        <div class="fld"><label>序号连接符</label><input id="rSep" value="-"></div>
        <div class="fld"><label>大小写</label><select id="rCase"><option value="keep">不变</option><option value="lower">全小写</option><option value="upper">全大写</option></select></div>
        <div class="fld"><label>选项</label>
          <label class="chk"><input type="checkbox" id="rNumOn"> 加序号</label>
          <label class="chk"><input type="checkbox" id="rMoji" checked> 修复乱码</label>
          <label class="chk"><input type="checkbox" id="rAds" checked> 去广告词</label>
          <label class="chk"><input type="checkbox" id="rDisp" checked> 使用整理后的名称</label></div>
      </div>
      <div id="rPv"><div class="hint">正在生成预览…</div></div>
    </div>
    <div class="mf"><button class="btn" data-close>取消</button><button class="btn primary" id="rGo">开始重命名</button></div>`);
  const rules = () => ({
    find: $("#rFind").value, replace: $("#rRepl").value, prefix: $("#rPre").value, suffix: $("#rSuf").value,
    numbering: $("#rNumOn").checked, num_start: +$("#rNum").value || 1, num_pad: +$("#rPad").value || 2,
    num_sep: $("#rSep").value, case: $("#rCase").value,
    fix_moji: $("#rMoji").checked, drop_ads: $("#rAds").checked, use_display: $("#rDisp").checked,
  });
  let rows = [];
  const pv = async () => {
    const d = await api("/api/rename/preview", { ids, rules: rules() });
    rows = d.rows || [];
    $("#rPv").innerHTML = `<table class="pv-table"><tr><th>类型</th><th>新名称</th><th>原名</th><th>来源</th></tr>` +
      rows.slice(0, 300).map(r => `<tr class="${r.changed ? "chg" : ""}"><td class="t">${r.source_type === "archive" ? "包内" : "磁盘"}</td>
        <td>${esc(r.new)}</td><td class="t">${esc(r.old)}</td><td class="t">${esc((r.origin || r.source_path || "").slice(-40))}</td></tr>`).join("") +
      `</table><div class="hint">共 ${rows.length} 项，其中 ${rows.filter(r => r.changed).length} 项会改名。<br>
      磁盘文件直接改名；压缩包内的模型用 7-Zip 改写（.rar 只读，无法改包内名称）。改完可以撤销。</div>`;
  };
  let t = 0;
  ["rFind", "rRepl", "rPre", "rSuf", "rNum", "rPad", "rSep", "rCase", "rNumOn", "rMoji", "rAds", "rDisp"].forEach(id => {
    $("#" + id).oninput = () => { clearTimeout(t); t = setTimeout(pv, 350); };
  });
  $("#rGo").onclick = async () => {
    const n = rows.filter(r => r.changed).length;
    if (!n) return toast("没有需要改名的项目", "warn");
    closeModal(); toast("正在改名…");
    const r = await api("/api/rename/apply", { ids, rules: rules() });
    toast(`改名完成 ${r.ok} 个${r.errors && r.errors.length ? "，失败 " + r.errors.length + "：" + r.errors[0] : ""}`,
      r.errors && r.errors.length ? "warn" : "ok", 7000);
    window._lastBatch = r.batch;
    reload();
  };
  pv();
}

async function openSettings() {
  const st = S.st || await api("/api/state");
  const s = st.settings || {};
  const hist = await api("/api/rename/history").catch(() => ({ items: [] }));
  const vw = st.viewer || {};
  const vwPhotoOk = !!vw.photo_builtin;
  const vwText = vw.kind === "photo" ? "Windows 自带的照片查看器"
    : vw.kind === "exe" ? shortProg(vw.exe) + "（" + vw.exe + "）"
    : "跟 Windows 默认一致（" + (vw.system_default || "未知") + "）";
  modal(`<div class="mh"><span>设置</span><button class="ghost icon" data-close>×</button></div>
    <div class="tabs">
      <button class="tab on" data-tab="tab1">常规</button>
      <button class="tab" data-tab="tab2">文件关联</button>
      <button class="tab" data-tab="tab3">维护</button>
    </div>
    <div class="mb">
      <section id="tab1">
      <h4 style="font-size:11px;color:var(--dim);letter-spacing:.08em;margin:2px 0 8px">关掉界面窗口的时候</h4>
      <label class="chk"><input type="radio" name="closeAct" value="tray" ${s.close_action === "quit" ? "" : "checked"}>
        隐藏到任务栏（推荐）：后台继续跑，右下角托盘留个小虫图标，双击图标就回来</label>
      <label class="chk"><input type="radio" name="closeAct" value="quit" ${s.close_action === "quit" ? "checked" : ""}>
        直接退出程序：窗口一关，后台服务也一起关掉（下次双击图标重新启动）</label>
      <div class="hint" style="margin:2px 0 14px">这里只管点窗口右上角「×」的动作，跟「维护 → 退出程序」不是一回事。<br>
        当前：<b>${s.close_action === "quit" ? "关窗口 = 退出程序"
          : ((st.ui && st.ui.tray) ? "关窗口 = 隐藏到任务栏（托盘图标已就位）" : "关窗口 = 隐藏到任务栏（托盘图标正在启动）")}</b>
        ${s.close_action === "quit" ? "" : "　托盘图标上右键还有「打开界面 / 退出程序」"}</div>
      <dl class="kv">
        <dt>程序位置</dt><dd>${esc(location.origin)}</dd>
        <dt>数据目录</dt><dd>${esc(st.data_dir)}</dd>
        <dt>缩略图缓存</dt><dd>${esc(st.thumb_dir)}</dd>
        <dt>索引条数</dt><dd>${st.total}</dd>
        <dt>缩略图</dt><dd>${st.thumbs_ready} 已生成 / ${st.thumbs_pending} 待生成</dd>
        <dt>剩余空间</dt><dd>${fmtSize(st.disk_free)}</dd>
        <dt>解压内核</dt><dd>${st.seven_zip_bundled ? "已内嵌 7-Zip（无需另装）" : "未内嵌"} · ${esc(st.seven_zip || "未找到")}</dd>
        <dt>SKP 3D 看图</dt><dd>${st.skp3d && st.skp3d.available
          ? "可用 · " + esc(st.skp3d.sk_dir || "")
          : '<span style="color:#b45309">不可用</span> · ' + esc((st.skp3d && st.skp3d.why) || "")}</dd>
        <dt>SKP 打开方式</dt><dd>${st.sketchup
          ? "SketchUp 2026 · " + esc(st.sketchup)
          : '<span style="color:#b45309">未找到 SketchUp.exe</span>'
            + (st.sketchup_assoc ? '<span class="dim"> · 注册表原本指向 ' + esc(st.sketchup_assoc) + "</span>" : "")}</dd>
      </dl>
      <div class="fldrow">
        <div class="fld"><label>7-Zip 程序路径</label><input id="set7z" value="${esc(st.seven_zip || "")}"></div>
        <div class="fld"><label>ffmpeg 程序路径（视频缩略图用，可留空）</label><input id="setFF" value="${esc(st.ffmpeg || "")}"></div>
        <div class="fld"><label>SketchUp 程序路径（打开 .skp 用，留空自动查找）</label><input id="setSU" value="${esc(s.sketchup_exe || "")}" placeholder="C:\\Program Files\\SketchUp\\SketchUp 2026\\SketchUp\\SketchUp.exe"></div>
        <div class="fld"><label>缩略图最长边（像素）</label><input id="setPx" type="number" value="${s.thumb_max_px || 720}"></div>
        <div class="fld"><label>并行线程数</label><input id="setWk" type="number" value="${s.workers || 8}"></div>
        <div class="fld"><label>单个图片索引上限（MB）</label><input id="setImg" type="number" value="${s.image_max_mb || 30}"></div>
        <div class="fld"><label>单文件索引上限（MB）</label><input id="setMax" type="number" value="${s.max_file_mb || 2048}"></div>
        <div class="fld"><label>文本预览长度（KB）</label><input id="setTxt" type="number" value="${s.text_preview_kb || 256}"></div>
        <div class="fld"><label>索引选项</label>
          <label class="chk"><input type="checkbox" id="setAll" ${s.index_all_files ? "checked" : ""}> 收录任意类型文件</label>
          <label class="chk"><input type="checkbox" id="setInArc" ${s.index_inside_archives ? "checked" : ""}> 索引压缩包内部文件</label>
          <label class="chk"><input type="checkbox" id="setImgOn" ${s.index_images ? "checked" : ""}> 索引压缩包内图片</label></div>
      </div>
      <h4 style="font-size:11px;color:var(--dim);letter-spacing:.08em;margin:18px 0 8px">列表卡片显示</h4>
      <label class="chk"><input type="checkbox" id="setThumbs" ${s.show_thumbs === false ? "" : "checked"}>
        显示缩略图（总开关）</label>
      <label class="chk"><input type="checkbox" id="setShowPath" ${s.show_path ? "checked" : ""}>
        显示文件地址和名字</label>
      <div class="hint" style="margin:0 0 14px">
        · <b>显示缩略图</b>：不勾 = 所有卡片都变成文字列表（小图标 + 文件名），翻得快也好找；
          只是不显示，缓存照旧管着，再勾回来立刻恢复。<br>
        · <b>图片显示缩略图</b>：已经挪到列表工具栏，「只看带效果图」旁边，勾一下立刻生效。<br>
        · <b>显示文件地址和名字</b>：勾上 = 卡片上写真实文件名和它所在的完整文件夹，方便在硬盘里找。<br>
        · 用鼠标在卡片上<b>点右键</b>：打开 / 打开方式 / 定位 / 复制 / 重命名 / 解压 都在那个菜单里。</div>
      </section>
      <section id="tab2" class="hidden">
      <h4 style="font-size:11px;color:var(--dim);letter-spacing:.08em;margin:18px 0 8px">文件类型默认程序</h4>
      <div class="hint" style="margin:0 0 10px">双击文件时按这里的设置打开；没配的扩展名用 Windows 默认程序。
        .skp 就算不配，也会自动去找 SketchUp。</div>
      <div class="owlist" id="owList"></div>
      <div class="hint" id="owAuto" style="margin:8px 0 10px"></div>
      <div class="fldrow" style="grid-template-columns:1fr 1.4fr">
        <div class="fld"><label>扩展名（多个用逗号隔开）</label><input id="owExt" placeholder=".psd 或 .jpg,.png"></div>
        <div class="fld"><label>程序路径</label><input id="owExe" placeholder="点下面「选择程序…」或「读系统默认」"></div>
      </div>
      <div class="dacts owacts">
        <button class="btn" id="owPick">选择程序…</button>
        <button class="btn" id="owSys">读系统默认</button>
        <button class="btn primary" id="owAdd">添加 / 更新</button>
        <span class="dim owtip">加完点右下角「保存设置」才会生效</span>
      </div>
      <div class="hint">常见对照：.skp → SketchUp　.psd → Photoshop　.dwg → CAD　.zip / .rar / .7z → 内嵌 7-Zip　.mp4 → 播放器<br>      规则按扩展名匹配（不分大小写）；程序路径失效会自动回退到 Windows 默认程序。</div>
      <h4 style="font-size:11px;color:var(--dim);letter-spacing:.08em;margin:20px 0 8px">看图软件（图片通用，建议设一个）</h4>
      <div class="hint" style="margin:0 0 10px">图片「打开」时用哪个软件，就看这里。设好之后，在小虫里点图片的「用看图软件打开」直接用它，
        不会再出现「点了没反应」。<br>当前：<b>${esc(vwText)}</b></div>
      <div class="dacts">
        <button class="btn primary" id="vwPick">换一个看图软件…</button>
        <button class="btn" id="vwAuto">跟 Windows 默认一致</button>
        <button class="btn ghost" id="vwPhoto"${vwPhotoOk ? "" : " disabled"}>用 Windows 自带照片查看器</button>
      </div>
      </section>
      <section id="tab3" class="hidden">
      <div class="dacts" style="margin-top:2px">
        <button class="btn" id="openData">打开数据目录</button>
        <button class="btn" id="openThumb">打开缩略图缓存</button>
        <button class="btn ghost" id="rebuild">重新扫描全部素材</button>
        <button class="btn ghost" id="cleanStage">清理预览/暂存缓存</button>
        <button class="btn ghost" id="cleanThumb">清理缩略图…（按类型挑）</button>
        <button class="btn ghost" id="cleanNested">清理嵌套解压…（按分类挑）</button>
        <button class="btn" id="cleanPanel">清理缓存面板…</button>
        <button class="btn ghost" id="quit">退出程序</button>
      </div>
      <h4 style="font-size:11px;color:var(--dim);letter-spacing:.08em;margin:18px 0 8px">定时提醒清理缓存</h4>
      <label class="chk"><input type="checkbox" id="setRemind" ${s.cache_remind_on === false ? "" : "checked"}> 定时提醒我清理缓存</label>
      <label class="chk"><input type="checkbox" id="setOnce" ${s.cache_remind_once === false ? "" : "checked"}> 只提醒一次（清理过之后再提醒）</label>
      <div class="fldrow" style="grid-template-columns:1fr 1fr">
        <div class="fld"><label>隔多久提醒一次（分钟）</label><input id="setRmin" type="number" value="${+s.cache_remind_min || 60}"></div>
        <div class="fld"><label>缓存超过多少 MB 才提醒</label><input id="setRmb" type="number" value="${+s.cache_limit_mb || 1500}"></div>
      </div>
      <div class="hint">当前缓存占用：<b>${fmtSize((st.cache || {}).total || 0)}</b>${(st.cache || {}).ready === false ? "（正在统计…）" : ""}
        　提醒状态：<b>${localStorage.getItem("xc_cache_warned") === "1" ? "已提醒过" : "还没提醒过"}</b>
        <button class="link" id="cleanOpen">打开清理面板</button>
        <button class="link" id="remindReset">重置提醒</button><br>
        勾了「只提醒一次」之后，同一次超限只弹一次；等缓存清理到上限以下会自动重新武装。
        想固定间隔反复提醒，就把它取消勾选。</div>
      <h4 style="font-size:11px;color:var(--dim);letter-spacing:.08em;margin:16px 0 8px">重命名记录（可撤销）</h4>
      <div>${(hist.items || []).slice(0, 6).map(h =>
        `<div class="row" style="margin-bottom:6px"><span class="pill">${esc(h.batch)}</span>
          <span class="dim">成功 ${h.ok || 0} / 共 ${h.n}</span>
          <button class="btn sm" data-undo="${esc(h.batch)}">撤销这批</button></div>`).join("") || `<span class="dim">暂无记录</span>`}</div>
      <div class="hint">本程序只在本机运行（127.0.0.1），不会把任何文件上传到网络。<br>
      缩略图与预览缓存都在数据目录里，可以随时清理，会自动重新生成。</div>
      </section>
    </div>
    <div class="mf"><button class="btn" data-close>关闭</button><button class="btn primary" id="setSave">保存设置</button></div>`);
  /* ---- 设置页签 ---- */
  $$("#modalBox .tab").forEach(b => b.onclick = () => {
    $$("#modalBox .tab").forEach(x => x.classList.toggle("on", x === b));
    $$("#modalBox .mb section").forEach(sec => sec.classList.toggle("hidden", sec.id !== b.dataset.tab));
    const mbx = $("#modalBox .mb"); if (mbx) mbx.scrollTop = 0;
  });
  /* ---- 文件类型默认程序：本地维护 ow 表，点「保存设置」才落盘 ---- */
  const ow = {}, owOk = {};
  (st.open_with || []).forEach(r => { ow[r.ext] = r.exe; owOk[r.ext] = r.ok; });
  const renderOw = () => {
    const keys = Object.keys(ow).sort();
    $("#owList").innerHTML = keys.length ? keys.map(e => {
      const p2 = ow[e], bad = owOk[e] === false;
      return `<div class="owrow${bad ? " bad" : ""}"><code>${esc(e)}</code>` +
        `<span class="p" title="${esc(p2)}">${esc(p2)}${bad ? "　（文件不存在，会回退到系统默认）" : ""}</span>` +
        `<button class="ghost icon" data-ow="${esc(e)}" title="删除">×</button></div>`;
    }).join("") : `<div class="dim" style="font-size:12px;margin-bottom:8px">还没有设置，全部走 Windows 默认程序</div>`;
    $$("#owList [data-ow]").forEach(b => b.onclick = () => {
      delete ow[b.dataset.ow]; delete owOk[b.dataset.ow]; renderOw();
    });
  };
  renderOw();
  const bi = st.builtin_open_with || [];
  if ($("#owAuto")) $("#owAuto").innerHTML = bi.length
    ? "自动认到的默认程序：" + bi.map(r =>
        `<code>${esc((r.exts || [r.ext]).join(" / "))}</code> → <b>${esc(r.name)}</b>` +
        `<span class="dim">（${esc(r.exe)}）</span>`).join("　")
      + "<br>上面没单独配过的，就按这个打开；上面配了就按上面配的。"
    : "";
  const owFirstExt = () => ($("#owExt").value.split(",")[0] || "").trim();
  $("#owPick").onclick = async () => {
    const p2 = await api("/api/pick-file",
      { title: "选择用来打开的程序", ext: owFirstExt() || ".exe" })
      .then(r => r.path || "").catch(() => "");
    if (p2) { $("#owExe").value = p2; toast("已选中：" + p2, "ok"); }
  };
  $("#owSys").onclick = async () => {
    const ext = owFirstExt();
    if (!ext) return toast("先填一个扩展名，例如 .psd", "warn");
    const d = await api("/api/assoc?ext=" + encodeURIComponent(ext)).catch(() => null);
    if (!d || !d.registered)
      return toast(`系统里查不到 ${ext} 的默认程序（有些类型由系统商店应用处理），请点「选择程序…」手动指定`, "warn", 7000);
    $("#owExe").value = d.exe || d.registered;
    toast(d.exe ? "已读到系统默认程序" : `注册表登记的是 ${d.registered}，但这个文件不存在`, d.exe ? "ok" : "warn", 7000);
  };
  $("#owAdd").onclick = () => {
    const exts = $("#owExt").value.split(",").map(x => x.trim()).filter(Boolean);
    const exe = $("#owExe").value.trim();
    if (!exts.length) return toast("先填扩展名", "warn");
    if (!exe) return toast("先填程序路径（可用「选择程序…」或「读系统默认」）", "warn", 6000);
    exts.forEach(x => {
      const e = (x.startsWith(".") ? x : "." + x).toLowerCase();
      ow[e] = exe; owOk[e] = undefined;
    });
    $("#owExt").value = ""; $("#owExe").value = "";
    renderOw();
    toast(`已加 ${exts.length} 条，点「保存设置」生效`, "ok");
  };
  $("#vwPick").onclick = () => openViewerPicker();
  $("#vwAuto").onclick = async () => {
    await api("/api/settings", { image_viewer: "" });
    toast("已改成跟 Windows 默认一致", "ok", 6000); loadState(); closeModal(); openSettings();
  };
  $("#vwPhoto").onclick = async () => {
    await api("/api/settings", { image_viewer: "@photoviewer" });
    toast("已改成 Windows 自带照片查看器", "ok", 6000); loadState(); closeModal(); openSettings();
  };
  $("#cleanPanel").onclick = () => openClean();
  $("#cleanOpen").onclick = () => openClean();
  const syncOnce = () => {
    const on = $("#setOnce").checked;
    $("#setRmin").disabled = on;
    $("#setRmin").style.opacity = on ? .5 : 1;
  };
  $("#setOnce").onchange = syncOnce;
  syncOnce();
  $("#remindReset").onclick = () => {
    localStorage.removeItem(WARN_KEY);
    localStorage.removeItem("xc_cache_remind_at");
    toast("提醒已重置，下一次检查缓存时就会重新判断。", "ok", 6000);
    checkCache(true);
  };
  $("#openData").onclick = () => api("/api/open-folder", { path: st.data_dir });
  $("#openThumb").onclick = () => api("/api/open-folder", { path: st.thumb_dir });
  $("#rebuild").onclick = () => { closeModal(); $("#btnScan").click(); };
  $("#setSave").onclick = async () => {
    await api("/api/settings", {
      seven_zip: $("#set7z").value.trim(), ffmpeg: $("#setFF").value.trim(),
      sketchup_exe: $("#setSU").value.trim(),
      thumb_max_px: +$("#setPx").value || 720, workers: +$("#setWk").value || 8,
      image_max_mb: +$("#setImg").value || 30, max_file_mb: +$("#setMax").value || 2048,
      text_preview_kb: +$("#setTxt").value || 256,
      index_all_files: $("#setAll").checked, index_inside_archives: $("#setInArc").checked,
      index_images: $("#setImgOn").checked,
      cache_remind_on: $("#setRemind").checked,
      cache_remind_once: $("#setOnce").checked,
      cache_remind_min: Math.max(5, +$("#setRmin").value || 60),
      cache_limit_mb: Math.max(10, +$("#setRmb").value || 1500),
      close_action: (document.querySelector('input[name="closeAct"]:checked') || {}).value || "tray",
      show_thumbs: $("#setThumbs").checked,
      show_path: $("#setShowPath").checked,
      open_with: ow,
    });
    if ($("#setRemind").checked && s.cache_remind_on === false) localStorage.removeItem("xc_cache_remind_at");
    if ($("#setOnce").checked && s.cache_remind_once === false) localStorage.removeItem(WARN_KEY);
    const idxChanged = ($("#setAll").checked !== !!s.index_all_files)
                    || ($("#setInArc").checked !== !!s.index_inside_archives)
                    || ($("#setImgOn").checked !== !!s.index_images);
    const showChanged = ($("#setThumbs").checked !== (s.show_thumbs !== false))
                     || ($("#setShowPath").checked !== !!s.show_path);
    toast("设置已保存" + (idxChanged ? "，索引选项改动要重新扫描才生效" : "")
      + (showChanged ? "；卡片显示方式已更新" : ""), "ok");
    await loadState();
    if (showChanged) redrawCards();
  };
  $$("#modalBox [data-undo]").forEach(b => b.onclick = async () => {
    if (!confirm("撤销这批重命名？")) return;
    const r = await api("/api/rename/undo", { batch: b.dataset.undo });
    toast(`已撤销 ${r.ok} 项${r.errors && r.errors.length ? "，失败：" + r.errors[0] : ""}`, r.ok ? "ok" : "warn");
    closeModal(); reload();
  });
  const clean = async what => {
    const r = await api("/api/cleanup", { what });
    toast(r.msg + "，会自动重新生成。", "ok", 6000);
    loadState();
  };
  $("#cleanStage").onclick = () => {
    if (confirm("清理「预览 / 暂存缓存」？\n\n这些只是打开文件时复制出来的临时副本，删掉不影响素材。")) clean("stage");
  };
  $("#cleanThumb").onclick = () => openClean();
  $("#cleanNested").onclick = () => openClean();
  $("#quit").onclick = () => {
    api("/api/shutdown", {});
    document.body.innerHTML = '<div style="display:grid;place-items:center;height:100vh;color:#6b7280;font:14px sans-serif">小虫管理器已退出，可直接关闭本页。</div>';
  };
}

/* ---------------- 任务进度 ---------------- */
async function pollJobs(immediate) {
  try {
    const d = await api("/api/jobs");
    const box = $("#jobs");
    const list = (d.jobs || []).filter(j => j.status === "running" || Date.now() / 1000 - (j.ended || 0) < 12);
    box.classList.toggle("hidden", !list.length);
    box.innerHTML = list.map(j => {
      const pct = j.total ? Math.round(j.done / j.total * 100) : 0;
      const label = { scan: "扫描资源库", prefetch: "生成缩略图", extract: "解压", rename: "重命名", paste: "复制/移动", zip: "压缩打包" }[j.kind] || j.kind;
      return `<div class="job"><div class="t"><span>${label} ${j.status === "running" ? "" : "（" + (j.status === "done" ? "完成" : j.status === "warn" ? "有失败" : "出错") + "）"}</span>
        <span>${j.total ? j.done + "/" + j.total : ""}</span></div>
        <div class="bar"><i style="width:${j.status === "running" ? pct : 100}%"></i></div>
        <div class="dim" style="font-size:11.5px">${esc((j.msg || "").slice(0, 70))}</div></div>`;
    }).join("");
    const busy = list.some(j => j.status === "running");
    if (busy) setTimeout(pollJobs, 1500);
    else if (window._wasBusy) { loadState(); if (isLib()) { loadKinds(); loadFacets(); reload(); }
                  else if (S.loaded) { renderBrowse(); refreshSideStats(); } }
    window._wasBusy = busy;
  } catch (e) { /* ignore */ }
}
setInterval(pollJobs, 4000);
setInterval(() => checkCache(true), 5 * 60 * 1000);   // 每 5 分钟看一眼缓存，到点了就提醒

/* ---------------- 素材库：加载文件夹（分类 / 统计 / 管理 / 浏览） ----------------
   点了「加载文件夹…」之后：右边只显示这个文件夹里的东西；左边「类型 / 分类」换成浏览模式那套
   （类型＝它的文件类型统计，分类＝它下面的子文件夹），选中一个子文件夹还会统计那个子文件夹。
   右键菜单、批量操作条、复制/剪切/重命名/删除/解压照常能用。 */
// 「📂 加载根目录…」：列出素材库目录里的根目录，挑一个加载（点左边那一行也行）
function openRootMenu(ax, ay) {
  closeCtx();
  const rs = (S.st && S.st.roots) || [];
  if (!rs.length) { toast("还没有素材目录，先点「+ 添加素材目录」", "warn", 6000); return; }
  ctxBox = document.createElement("div");
  ctxBox.className = "ctxmenu";
  ctxBox.innerHTML = '<div class="ctxhd">选择要加载的根目录（素材库目录里的）</div>'
    + rs.map(r => '<div class="mi" data-rootload="' + esc(r.path) + '">📂 ' + esc(r.path) + '</div>').join("")
    + '<div class="msep"></div>'
    + '<div class="mi" data-rootload="__all">全部素材（不筛选根目录）</div>'
    + '<div class="mi" data-rootpick="1">其他文件夹…（临时看看，不加进素材库）</div>';
  document.body.appendChild(ctxBox);
  const r = ctxBox.getBoundingClientRect();
  ctxBox.style.left = Math.max(6, Math.min(ax, innerWidth - r.width - 8)) + "px";
  ctxBox.style.top = Math.max(6, Math.min(ay, innerHeight - r.height - 8)) + "px";
  ctxBox.onclick = e => {
    const mi = e.target.closest(".mi");
    if (!mi) return;
    const p = mi.dataset.rootload, pick = mi.dataset.rootpick;
    closeCtx();
    if (pick) { pickFolder("选择要加载的文件夹").then(q => { if (q) loadFolder(q); }); return; }
    if (!p || p === "__all") unloadFolder();
    else loadFolder(p);
  };
}

function syncSideSections() {
  const m = S.mode, on = (m === "lib" && !!S.loaded);
  const t = (id, hide) => { const e = $(id); if (e) e.classList.toggle("hidden", hide); };
  // 素材库里加载了文件夹：左栏只留「素材库目录 / 加载文件夹」，类型、分类换成浏览模式那套
  // （统计跟着这个文件夹走，选中某个子文件夹就统计那个文件夹）
  t("#secLibKinds", on); t("#secLibCats", on);
  t("#secPlaces", on); t("#secDrives", on);
  t("#secDirKinds", false); t("#secDirCats", false);
  $("#sideBrowse").classList.toggle("hidden", m !== "browse" && !on);
}
function syncLoadedUI() {
  syncSideSections();
  renderRoots();
  if (S.mode !== "lib") return;
  const on = !!S.loaded;
  const lb = $("#loadedBox");
  if (lb) lb.classList.toggle("hidden", !on);
  $("#tbLib").classList.toggle("hidden", on);
  $("#tbBrowse").classList.toggle("hidden", !on);
  $("#crumbs").classList.toggle("hidden", !on);
  $("#styleBox").classList.toggle("hidden", on);
  const q = $("#q");
  if (q) q.placeholder = on
    ? "在这个文件夹（含子文件夹）里搜文件名…（按 / 聚焦）"
    : "搜索素材名 / 分类 / 关键词 / 扩展名，如：吊灯、现代、.pdf（按 / 聚焦）";
  if (!on) { $("#loadedRow").innerHTML = ""; return; }
  const nm = S.loaded.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || S.loaded;
  $("#loadedRow").innerHTML =
    '<div class="root loaded" title="' + esc(S.loaded) + '"><span>📂 ' + esc(nm) + '</span>'
    + '<button data-unload="1" title="回到素材库">×</button></div>';
  const b = $("#loadedRow [data-unload]");
  if (b) b.onclick = unloadFolder;
}

// 加载一个文件夹：右侧变成它的内容，左侧类型/分类都按它算
async function loadFolder(path) {
  if (!path) return;
  if (S.mode !== "lib") setMode("lib");
  const before = S.dirData, keep = S.loaded;
  S.loaded = path; S.dirFilter = "";
  await browse(path);
  if (S.dirData === before) {            // 没换成新目录 = 打不开（browse 已经提示过）
    S.loaded = keep; syncLoadedUI(); loadKinds(); loadFacets(); reload();
    if (!keep || keep === path) saveLoadedRoot("");   // 上次记住的目录没了，别再记
    return;
  }
  syncLoadedUI();
  refreshSideStats();
  saveLoadedRoot(S.loaded);              // 记住：下次打开程序自动回到这里
}

// 记住 / 忘掉「上次加载的根目录」（存在设置里，关了程序也在）
function saveLoadedRoot(p) {
  try { api("/api/settings", { loaded_root: p || "" }); } catch (e) { /* 记不住也不影响用 */ }
}
function savedLoadedRoot() {
  const p = ((S.st && S.st.settings) || {}).loaded_root;
  return typeof p === "string" ? p.trim() : "";
}

// 回到素材库（只看全库）
function unloadFolder() {
  if (!S.loaded) return;
  S.loaded = ""; S.dirFilter = ""; S.dir = ""; S.dirData = null; S.dirItems = [];
  S.searchRes = null; S.searchFor = ""; S.q = ""; $("#q").value = "";
  S.sel.clear(); S.map = {}; syncSel();
  syncLoadedUI();
  saveLoadedRoot("");                    // 点了「×」= 以后别自动加载了
  loadKinds(); loadFacets(); reload();
}

/* ---------------- 工具条 ---------------- */
function selectAll() {
  const list = gridItems();
  list.forEach(i => { S.sel.add(i.key); S.map[i.key] = i; });
  syncSel();
}
const debounce = (fn, ms = 300) => { let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); }; };
const onSearch = debounce(() => {
  if (isLib()) { S.offset = 0; reload(); }
  else if (S.mode === "lib" && S.loaded) applyFsSearch();   // 加载了根目录：在它里面（含子文件夹）递归搜
  else renderBrowse();
}, 280);
// 当前列表该用哪套渲染：搜索结果 or 文件夹内容
function refreshList() { return S.searchRes ? renderSearch() : renderBrowse(); }

$("#q").oninput = e => { S.q = e.target.value; onSearch(); };
$("#qclear").onclick = () => {
  $("#q").value = ""; S.q = "";
  isLib() ? reload() : renderBrowse();           // renderBrowse 会看到「刚才在搜索」并收尾
};
$("#sort").onchange = e => { S.sort = e.target.value; S.offset = 0; reload(); };
$("#onlyRender").onchange = e => { S.onlyRender = e.target.checked ? 1 : 0; S.offset = 0; reload(); };
// 图片缩略图开关（就在「只看带效果图」旁边）：勾了图片才生成/显示缩略图，不勾就出文字列表、点开看原图
$("#tbImgThumb").onchange = async e => {
  const on = e.target.checked;
  try { await api("/api/settings", { image_thumbs: on }); }
  catch (err) { e.target.checked = !on; return toast("保存失败：" + err.message, "err"); }
  if (S.st && S.st.settings) S.st.settings.image_thumbs = on;
  toast(on
    ? "图片缩略图已打开：翻到哪张就补哪张。想一次性全生成，点右上角「生成缩略图」。"
    : "图片缩略图已关闭：图片改成文字列表，点开直接看原图，省缓存也更快。", "ok", 9000);
  if (isLib()) reload(); else renderBrowse();
  loadState();
};
function setZoom(v) {
  document.documentElement.style.setProperty("--card", v + "px");
  $("#zoom").value = v; $("#zoom2").value = v;
  localStorage.setItem("xc_zoom", v);
}
$("#zoom").oninput = e => setZoom(e.target.value);
$("#zoom2").oninput = e => setZoom(e.target.value);
$("#btnSelectAll").onclick = selectAll;
$("#btnSelectAll2").onclick = selectAll;
$("#selClear").onclick = () => { S.sel.clear(); S.map = {}; syncSel(); };
$("#sortDir").onchange = e => { S.sortDir = e.target.value; renderBrowse(); };
$("#showHidden").onchange = e => { S.showHidden = e.target.checked ? 1 : 0; if (S.dir) browse(S.dir); };
$("#btnReload").onclick = () => { if (S.dir) browse(S.dir); else loadState(); };
$("#btnUp").onclick = () => { const p = S.dirData && S.dirData.parent; if (p) browse(p); else toast("已经是顶层了", "warn"); };
$("#btnMkdir").onclick = mkdirPrompt;
$("#btnPaste").onclick = () => doAct("paste");
$("#btnPickDir").onclick = async () => { const p = await pickFolder("选择要打开的文件夹"); if (p) browse(p); };
$("#pathInput").onkeydown = e => {
  if (e.key === "Enter") {
    const v = e.target.value.trim().replace(/^"|"$/g, "");
    if (v) browse(v);
  }
};
$("#btnScan").onclick = async e => {
  const force = e.shiftKey;
  if (force && !confirm("全量重建索引？\n\n会重新读取所有压缩包内的文件清单，耗时较长（本机约 10 秒）。")) return;
  const r = await api("/api/scan", { force });
  if (!r.ok) return toast(r.msg || "启动失败", "err");
  toast(force ? "开始全量重建索引…" : "开始扫描…", "ok");
  pollJobs(true);
};
$("#btnPrefetch").onclick = async () => {
  const r = await api("/api/prefetch", {});
  toast("开始后台生成缩略图，边看边生成即可。", "ok");
  pollJobs(true);
};
$("#btnSettings").onclick = openSettings;
$("#btnLoadDir").onclick = e => {
  e.stopPropagation();          // 别让 document 上「点别处关菜单」的监听立刻把它关掉
  const r = e.currentTarget.getBoundingClientRect();
  openRootMenu(r.left, r.bottom + 4);
};
$("#btnAddRoot").onclick = async () => {
  const p = await pickFolder("选择要加入素材库的文件夹");
  if (!p) return;
  const r = await api("/api/roots", { action: "add", path: p });
  if (!r.ok) return toast(r.msg || "添加失败", "err");
  toast("已添加：" + p + "，正在扫描…", "ok", 6000);
  await loadState();
  await api("/api/scan", {});
  pollJobs(true);
  loadPlaces();
};


/* ---------------- 内嵌 SKP 3D 看图（three.js） ----------------
   后端用 SketchUp C API 把 .skp 读成小虫自己的 XCM3 网格，
   这里解析后用 three.js 显示。整条链路不打开 SketchUp 界面。 */
const V3 = { ready: false, pend: null, r: null, sc: null, cam: null, ctrl: null,
             mesh: null, mat: null, box: null, grid: null, ro: null, sig: "" };

function loadScriptOnce(src) {
  return new Promise((ok, no) => {
    const el = document.createElement("script");
    el.src = src;
    el.onload = () => ok();
    el.onerror = () => no(new Error("载入 " + src + " 失败"));
    document.head.appendChild(el);
  });
}
function ensure3D() {
  if (V3.ready) return Promise.resolve();
  if (!V3.pend) V3.pend = loadScriptOnce("/static/vendor/three.min.js")
    .then(() => loadScriptOnce("/static/vendor/OrbitControls.js"))
    .then(() => { V3.ready = true; })
    .catch(e => { V3.pend = null; throw e; });
  return V3.pend;
}
function stop3D() {
  if (V3.r) { try { V3.r.setAnimationLoop(null); } catch (e) {} }
  if (V3.ro) { try { V3.ro.disconnect(); } catch (e) {} }
  if (V3.ctrl) { try { V3.ctrl.dispose(); } catch (e) {} }
  if (V3.sc) V3.sc.traverse(o => {
    if (o.geometry) { try { o.geometry.dispose(); } catch (e) {} }
    const ms = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
    ms.forEach(m => { try { m.dispose(); } catch (e) {} });
  });
  if (V3.r) { try { V3.r.dispose(); } catch (e) {} }
  V3.r = V3.sc = V3.cam = V3.ctrl = V3.mesh = V3.mat = V3.box = V3.grid = V3.ro = null;
  V3.sig = "";
  const host = $("#pv3d");
  if (host) [...host.querySelectorAll("canvas")].forEach(c => c.remove());
}

/* XCM3 解析：头 + 归一化坐标/法线/颜色 + 索引 */
function parseXCM3(buf) {
  if (buf.byteLength < 52) throw new Error("预览数据为空");
  const dv = new DataView(buf);
  const magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3));
  if (magic !== "XCM3") throw new Error("预览数据格式不正确");
  const ver = dv.getUint32(4, true);
  const n = dv.getUint32(8, true), ni = dv.getUint32(12, true), wide = dv.getUint32(16, true);
  if (!n || !ni) throw new Error("这个模型里没有可显示的几何体（可能全是线条/组件）");
  const min = [0, 1, 2].map(i => dv.getFloat32(20 + i * 4, true));
  const max = [0, 1, 2].map(i => dv.getFloat32(32 + i * 4, true));
  const view = dv.getFloat32(44, true);
  const dim = ver >= 2 ? dv.getFloat32(48, true) : view;
  let o = ver >= 2 ? 52 : 48;
  const pos = new Float32Array(buf, o, n * 3); o += n * 12;
  const nrm = new Float32Array(buf, o, n * 3); o += n * 12;
  const col = new Uint8Array(buf, o, n * 4); o += n * 4;
  const idx = wide ? new Uint32Array(buf, o, ni) : new Uint16Array(buf, o, ni);
  return { n, ni, wide, ver, min, max, dim, view, pos, nrm, col, idx };
}

function v3FitView(cam, ctrl, min, max) {
  /* 按模型真实包围盒算一个刚好的机位，别让模型缩在角落里 */
  const THREE = window.THREE;
  const b = new THREE.Box3(new THREE.Vector3(min[0], min[1], min[2]),
                           new THREE.Vector3(max[0], max[1], max[2]));
  const c = b.getCenter(new THREE.Vector3());
  const sz = b.getSize(new THREE.Vector3());
  const t = Math.tan(cam.fov * Math.PI / 360);
  const asp = cam.aspect || 1.6;
  const dist = Math.max((sz.y / 2) / t, (sz.x / 2) / (t * asp), (sz.z / 2) / t, 0.4) * 1.5;
  const dir = new THREE.Vector3(0.58, 0.46, 0.79).normalize();
  const pos = c.clone().add(dir.multiplyScalar(dist));
  cam.near = Math.max(dist * 0.004, 0.001);
  cam.far = dist * 30;
  cam.position.copy(pos);
  cam.updateProjectionMatrix();
  if (ctrl) { ctrl.target.copy(c); ctrl.update(); }
  V3.home = { pos: pos.toArray(), target: c.toArray() };
}
function v3Mark() {
  $$("#dBody [data-3d]").forEach(b => {
    const k = b.getAttribute("data-3d");
    let on = false;
    if (k === "wire") on = !!(V3.mat && V3.mat.wireframe);
    if (k === "box") on = !!(V3.box && V3.box.visible);
    if (k === "grid") on = !!(V3.grid && V3.grid.visible);
    if (k === "wide") on = $("#drawer").classList.contains("wide");
    b.classList.toggle("on", on);
  });
}
function v3Act(act) {
  if (act === "reset" && V3.cam && V3.ctrl) {
    const h = V3.home || { pos: [1.75, 1.28, 2.35], target: [0, 0, 0] };
    V3.cam.position.set(h.pos[0], h.pos[1], h.pos[2]);
    V3.ctrl.target.set(h.target[0], h.target[1], h.target[2]);
    V3.ctrl.update();
  } else if (act === "wire" && V3.mat) {
    V3.mat.wireframe = !V3.mat.wireframe;
    V3.mat.needsUpdate = true;
  } else if (act === "box" && V3.box) {
    V3.box.visible = !V3.box.visible;
  } else if (act === "grid" && V3.grid) {
    V3.grid.visible = !V3.grid.visible;
  } else if (act === "wide") {
    $("#drawer").classList.toggle("wide");
    setTimeout(() => window.dispatchEvent(new Event("resize")), 60);
  }
  v3Mark();
}

function waitJob(jid, onMsg) {
  return new Promise((ok, no) => {
    const tick = async () => {
      let j;
      try { j = await api("/api/job/" + jid); } catch (e) { return no(e); }
      if (onMsg && j.msg) onMsg(j.msg);
      if (j.status === "running") return setTimeout(tick, 400);
      if (j.status === "error") return no(new Error(j.msg || "转换失败"));
      ok(j);
    };
    tick();
  });
}

async function start3D(it) {
  const msg = $("#pv3dMsg"), info = $("#pv3dInfo");
  const say = t => { if (msg) msg.innerHTML = t; };
  const s3 = ((S.st || {}).skp3d) || {};
  if (info) info.textContent = "";
  if (!s3.available) {
    const sp = $("#pv3d .spin"); if (sp) sp.remove();
    say(`<b>内置 3D 看图暂时用不了</b>${esc(s3.why || "没有找到 SketchUp 的读图组件")}<br>
      <span class="dim">装好 SketchUp（2016 及以上）后重新打开小虫管理器即可自动识别；<br>
      也可以把 SketchUp SDK 的 SketchUpAPI.dll 放到程序目录的 bin\\sketchup 里。</span>`);
    return;
  }
  try {
    say("正在载入 3D 引擎…");
    await ensure3D();
  } catch (e) {
    say("<b>3D 引擎载入失败</b>" + esc(e.message || e));
    return;
  }
  const payload = it.from === "lib" ? { id: it.id } : { path: it.path, inner: it.inner || "" };
  const qs = it.from === "lib"
    ? "id=" + it.id
    : "path=" + encodeURIComponent(it.path) + "&inner=" + encodeURIComponent(it.inner || "");
  try {
    const r = await api("/api/model3d/prepare", payload);
    if (!r.cached) {
      say("正在读取模型几何，请稍候…<br><span class=\"dim\">大模型可能要十几秒，之后会缓存，下次秒开</span>");
      await waitJob(r.job, m => say("正在生成 3D 预览…<br><span class=\"dim\">" + esc(m) + "</span>"));
    } else {
      say("读取缓存…");
    }
    say("正在载入网格…");
    const res = await fetch("/api/model3d?" + qs);
    if (!res.ok) throw new Error((await res.text()).slice(0, 300));
    mount3D(await res.arrayBuffer(), info, it);
  } catch (e) {
    say("<b>生成 3D 预览失败</b>" + esc(e.message || e));
  }
}

function mount3D(buf, info, it) {
  const THREE = window.THREE;
  const host = $("#pv3d");
  if (!host || !THREE) return;
  let g;
  try { g = parseXCM3(buf); } catch (e) { $("#pv3dMsg").innerHTML = esc(e.message); return; }
  stop3D();
  const old = host.querySelector(".v3load"); if (old) old.remove();
  const w = host.clientWidth || 600, h = host.clientHeight || 420;

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(w, h, false);
  renderer.setClearColor(0xe8edef, 1);

  const scene = new THREE.Scene();
  const cam = new THREE.PerspectiveCamera(42, w / h, 0.01, 500);

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(g.pos, 3));
  geo.setAttribute("normal", new THREE.BufferAttribute(g.nrm, 3));
  geo.setAttribute("color", new THREE.BufferAttribute(g.col, 4, true));
  geo.setIndex(new THREE.BufferAttribute(g.idx, 1));
  geo.computeBoundingSphere();

  const mat = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide });
  const mesh = new THREE.Mesh(geo, mat);
  scene.add(mesh);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a9aa1, 1.0));
  scene.add(new THREE.AmbientLight(0xffffff, 0.26));
  const d1 = new THREE.DirectionalLight(0xffffff, 0.72); d1.position.set(1.4, 2.0, 1.6);
  const d2 = new THREE.DirectionalLight(0xffffff, 0.34); d2.position.set(-1.6, 0.7, -1.4);
  scene.add(d1); scene.add(d2);

  const box = new THREE.Box3Helper(
    new THREE.Box3(new THREE.Vector3(g.min[0], g.min[1], g.min[2]),
                   new THREE.Vector3(g.max[0], g.max[1], g.max[2])), 0x0d7a63);
  box.visible = false; scene.add(box);
  const grid = new THREE.GridHelper(4, 8, 0xa9bcc3, 0xd4dee2);
  grid.position.y = g.min[1]; grid.visible = false; scene.add(grid);

  host.appendChild(renderer.domElement);
  const ctrl = new THREE.OrbitControls(cam, renderer.domElement);
  ctrl.enableDamping = true;
  ctrl.dampingFactor = 0.08;
  ctrl.minDistance = 0.4;
  ctrl.maxDistance = 30;
  v3FitView(cam, ctrl, g.min, g.max);

  V3.r = renderer; V3.sc = scene; V3.cam = cam; V3.ctrl = ctrl;
  V3.mesh = mesh; V3.mat = mat; V3.box = box; V3.grid = grid;

  renderer.setAnimationLoop(() => { ctrl.update(); renderer.render(scene, cam); });

  V3.ro = new ResizeObserver(() => {
    const w2 = host.clientWidth, h2 = host.clientHeight;
    if (!w2 || !h2 || !V3.r) return;
    V3.cam.aspect = w2 / h2;
    V3.cam.updateProjectionMatrix();
    V3.r.setSize(w2, h2, false);
  });
  V3.ro.observe(host);

  if (info) {
    const fmtL = v => v >= 1 ? v.toFixed(2) + " m" : (v * 100).toFixed(0) + " cm";
    const d = g.dim || 0;
    let extra = "";
    if (d > (g.view || 0) * 1.3) extra = "（模型里还有飘在很远的零散几何，视角按主体自动取景）";
    info.innerHTML = `三角面 <b>${(g.ni / 3).toLocaleString()}</b> · 顶点 ${g.n.toLocaleString()} · ` +
      `实际最长边 <b>${fmtL(d)}</b>${extra}` +
      `<br><span class="dim">左键旋转 · 滚轮缩放 · 右键平移（按真实比例显示）</span>`;
  }
  v3Mark();
}

/* ---------------- 快捷键 ---------------- */
window.addEventListener("keydown", e => {
  const inInput = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName);
  if (e.key === "/" && !inInput) { e.preventDefault(); $("#q").focus(); return; }
  if (e.key === "Escape") {
    if (!$("#modal").classList.contains("hidden")) closeModal();
    else { $("#drawer").classList.add("hidden"); stop3D(); }
    S.sel.clear(); S.map = {}; syncSel();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && !inInput) { e.preventDefault(); selectAll(); return; }
  if (inInput) return;
  if (e.key.toLowerCase() === "f" && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault(); doAct("fav"); return;
  }
  if (S.mode === "browse") {
    if (e.key === "Delete") { e.preventDefault(); doAct("recycle"); }
    else if (e.key === "F2") { e.preventDefault(); doAct("rename"); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c") { e.preventDefault(); doAct("copy"); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "x") { e.preventDefault(); doAct("cut"); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v") { e.preventDefault(); doAct("paste"); }
    else if (e.key === "Backspace") { e.preventDefault(); $("#btnUp").click(); }
  } else if (e.key === "F2") { e.preventDefault(); doAct("rename"); }
});

/* ---------------- 外链 / 前进后退切模式 ---------------- */
window.addEventListener("hashchange", () => {
  const m = location.hash === "#browse" ? "browse" : "lib";
  if (m !== S.mode) setMode(m);
});

/* ---------------- 首屏 ---------------- */
const io = new IntersectionObserver(es => {
  if (es[0].isIntersecting && isLib() && S.offset + S.limit < S.total) loadMore();
}, { rootMargin: "700px" });
io.observe($("#sentinel"));

(async () => {
  setZoom(localStorage.getItem("xc_zoom") || 190);
  await loadState();
  await loadKinds();
  await loadFacets();
  const backLoaded = savedLoadedRoot();
  if (location.hash === "#browse") setMode("browse");
  else if (backLoaded) await loadFolder(backLoaded);     // 上次加载的根目录，自动回去
  else await reload();
  pollJobs();
  if (!S.st.total) {
    toast("素材库还是空的：点右上角「扫描素材库」开始建立索引，或直接切到「浏览文件」逛磁盘。", "warn", 9000);
  } else if (S.st.thumbs_pending > 200) {
    api("/api/prefetch", {});
    toast(`正在后台生成缩略图（待生成 ${S.st.thumbs_pending} 张），边看边生成即可。`, "ok", 6000);
    pollJobs(true);
  }
})();