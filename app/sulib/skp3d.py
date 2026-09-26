# -*- coding: utf-8 -*-
"""小虫管理器 —— 内嵌 SketchUp(.skp) 3D 看图。

用本机安装的 SketchUp 官方 C API（SketchUpAPI.dll）直接读几何，转成小虫自己的
轻量二进制网格（XCM3），交给浏览器里的 three.js 显示。不打开 SketchUp 界面。

- 只读，不改原文件
- 自动按“面积覆盖率”挑重要面，控制预览体积
- 顶点焊接 + 法线量化，保住硬边又压缩数据
"""
import ctypes as C
import hashlib
import os
import shutil
import struct
import tempfile
import threading
import time

SU_ERROR_NONE = 0
INCH_TO_M = 0.0254
SKP3D_REV = 6
MAGIC = b"XCM3"
MAX_FACES = 160000
AREA_KEEP = 0.98
MAX_DEPTH = 14
SEARCH_ROOTS = [r"C:\Program Files\SketchUp", r"D:\Program Files\SketchUp",
                r"C:\Program Files (x86)\SketchUp", r"D:\SketchUp"]


class SUPoint3D(C.Structure):
    _fields_ = [("x", C.c_double), ("y", C.c_double), ("z", C.c_double)]


class SUVector3D(C.Structure):
    _fields_ = [("x", C.c_double), ("y", C.c_double), ("z", C.c_double)]


class SUColor(C.Structure):
    _fields_ = [("red", C.c_ubyte), ("green", C.c_ubyte), ("blue", C.c_ubyte),
                ("alpha", C.c_ubyte)]


class SUTransformation(C.Structure):
    _fields_ = [("values", C.c_double * 16)]


class SUBoundingBox3D(C.Structure):
    _fields_ = [("min", SUPoint3D), ("max", SUPoint3D)]


_lock = threading.RLock()
_api = None
_state = {"dir": "", "why": "", "missing": []}


# ------------------------------------------------------------------ 装载
def bundled_dirs():
    """程序目录里自带的 SketchUp SDK 位置（有的话优先用，做到零依赖）。"""
    out = []
    try:
        from . import config
        out = [config.BIN_DIR / "sketchup", config.DATA_DIR / "sketchup"]
    except Exception:
        pass
    return out


def find_sketchup_dir():
    for d in bundled_dirs():
        if os.path.isfile(str(d / "SketchUpAPI.dll")):
            return str(d)
    cands = []
    for root in SEARCH_ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            for name in os.listdir(root):
                d = os.path.join(root, name, "SketchUp")
                if os.path.isfile(os.path.join(d, "SketchUpAPI.dll")):
                    cands.append(d)
        except Exception:
            pass
    cands.sort(reverse=True)
    return cands[0] if cands else ""


class _Api:
    def __init__(self, dll_dir):
        self.dir = dll_dir
        self.missing = []
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(dll_dir)
        self.dll = C.WinDLL(os.path.join(dll_dir, "SketchUpAPI.dll"))
        d = self.dll

        def f(name, restype, *args):
            try:
                fn = getattr(d, name)
            except AttributeError:
                self.missing.append(name)
                return None
            fn.restype = restype
            fn.argtypes = list(args)
            return fn

        V, P = C.c_void_p, C.POINTER(C.c_void_p)
        SZ, PSZ = C.c_size_t, C.POINTER(C.c_size_t)
        self.SUInitialize = f("SUInitialize", None)
        self.SUTerminate = f("SUTerminate", None)
        self.ModelCreateFromFile = f("SUModelCreateFromFile", C.c_int, P, C.c_char_p)
        self.ModelRelease = f("SUModelRelease", C.c_int, P)
        self.ModelGetEntities = f("SUModelGetEntities", C.c_int, V, P)
        self.ModelGetVersion = f("SUModelGetVersion", C.c_int, V, C.POINTER(C.c_int),
                                 C.POINTER(C.c_int), C.POINTER(C.c_int))
        self.ModelGetName = f("SUModelGetName", C.c_int, V, P)
        self.ModelGetDescription = f("SUModelGetDescription", C.c_int, V, P)
        for key, nm in (("layers", "SUModelGetNumLayers"), ("materials", "SUModelGetNumMaterials"),
                        ("scenes", "SUModelGetNumScenes"),
                        ("components", "SUModelGetNumComponentDefinitions")):
            setattr(self, "Num_" + key, f(nm, C.c_int, V, PSZ))
        self.ModelGetUnits = f("SUModelGetUnits", C.c_int, V, C.POINTER(C.c_int))
        self.ModelGetLengthFormatter = f("SUModelGetLengthFormatter", C.c_int, V, P)
        self.LengthFormatterGetLengthString = f("SULengthFormatterGetLengthString", C.c_int,
                                                V, C.c_double, C.c_bool, P)
        self.LengthFormatterRelease = f("SULengthFormatterRelease", C.c_int, P)
        self.EntGetBoundingBox = f("SUEntitiesGetBoundingBox", C.c_int, V, C.POINTER(SUBoundingBox3D))
        self.StringGetUTF8 = f("SUStringGetUTF8", C.c_int, V, SZ, C.c_char_p, PSZ)
        self.StringRelease = f("SUStringRelease", C.c_int, P)
        self.EntNumFaces = f("SUEntitiesGetNumFaces", C.c_int, V, PSZ)
        self.EntGetFaces = f("SUEntitiesGetFaces", C.c_int, V, SZ, P, PSZ)
        self.EntNumInstances = f("SUEntitiesGetNumInstances", C.c_int, V, PSZ)
        self.EntGetInstances = f("SUEntitiesGetInstances", C.c_int, V, SZ, P, PSZ)
        self.EntNumGroups = f("SUEntitiesGetNumGroups", C.c_int, V, PSZ)
        self.EntGetGroups = f("SUEntitiesGetGroups", C.c_int, V, SZ, P, PSZ)
        self.InstGetTransform = f("SUComponentInstanceGetTransform", C.c_int, V,
                                  C.POINTER(SUTransformation))
        self.InstGetDefinition = f("SUComponentInstanceGetDefinition", C.c_int, V, P)
        self.DefGetEntities = f("SUComponentDefinitionGetEntities", C.c_int, V, P)
        self.GroupGetEntities = f("SUGroupGetEntities", C.c_int, V, P)
        self.GroupGetTransform = f("SUGroupGetTransform", C.c_int, V, C.POINTER(SUTransformation))
        self.FaceGetAreaWithTransform = f("SUFaceGetAreaWithTransform", C.c_int, V,
                                          C.POINTER(SUTransformation), C.POINTER(C.c_double))
        self.FaceGetFrontMaterial = f("SUFaceGetFrontMaterial", C.c_int, V, P)
        self.MaterialGetColor = f("SUMaterialGetColor", C.c_int, V, C.POINTER(SUColor))
        self.MeshHelperCreate = f("SUMeshHelperCreate", C.c_int, P, V)
        self.MeshHelperRelease = f("SUMeshHelperRelease", C.c_int, P)
        self.MeshNumVertices = f("SUMeshHelperGetNumVertices", C.c_int, V, PSZ)
        self.MeshNumTriangles = f("SUMeshHelperGetNumTriangles", C.c_int, V, PSZ)
        self.MeshVertices = f("SUMeshHelperGetVertices", C.c_int, V, SZ, C.POINTER(SUPoint3D), PSZ)
        self.MeshIndices = f("SUMeshHelperGetVertexIndices", C.c_int, V, SZ,
                             C.POINTER(C.c_size_t), PSZ)
        self.MeshNormals = f("SUMeshHelperGetNormals", C.c_int, V, SZ, C.POINTER(SUVector3D), PSZ)

    def string(self, ref):
        if not ref or not self.StringGetUTF8:
            return ""
        buf = C.create_string_buffer(4096)
        n = C.c_size_t()
        try:
            if self.StringGetUTF8(ref, 4096, buf, C.byref(n)) != SU_ERROR_NONE:
                return ""
            return buf.value.decode("utf-8", "replace")
        except Exception:
            return ""


def available():
    """本机是否有可用的 SketchUp C API。"""
    global _api
    if _api is not None:
        return True
    d = find_sketchup_dir()
    if not d:
        _state["why"] = "没有在本机找到 SketchUp 安装目录（需要 SketchUpAPI.dll）"
        return False
    try:
        _api = _Api(d)
        if _api.SUInitialize:
            _api.SUInitialize()
        _state["dir"] = d
        _state["missing"] = list(_api.missing)
        return True
    except Exception as e:
        _api = None
        _state["why"] = "加载 SketchUpAPI.dll 失败：%s" % e
        return False


def status():
    return {"available": available(), "sk_dir": _state.get("dir", ""),
            "why": _state.get("why", ""), "rev": SKP3D_REV}


# ------------------------------------------------------------------ 矩阵
IDENT = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def _mat(values, col_major):
    v = list(values)
    if col_major:
        v = [v[0], v[4], v[8], v[12], v[1], v[5], v[9], v[13],
             v[2], v[6], v[10], v[14], v[3], v[7], v[11], v[15]]
    return v


def _mul(a, b):
    return [sum(a[i * 4 + k] * b[k * 4 + j] for k in range(4))
            for i in range(4) for j in range(4)]


def _detect_order(api, entities, depth=0):
    if depth > 6 or not entities or not api.EntNumInstances:
        return None
    ni = C.c_size_t()
    if api.EntNumInstances(entities, C.byref(ni)) != SU_ERROR_NONE or not ni.value:
        return None
    insts = (C.c_void_p * ni.value)()
    cnt = C.c_size_t()
    if api.EntGetInstances(entities, ni.value, insts, C.byref(cnt)) != SU_ERROR_NONE:
        return None
    for i in range(cnt.value):
        if not insts[i]:
            continue
        t = SUTransformation()
        if api.InstGetTransform(insts[i], C.byref(t)) != SU_ERROR_NONE:
            continue
        v = list(t.values)
        tail = abs(v[12]) + abs(v[13]) + abs(v[14])
        side = abs(v[3]) + abs(v[7]) + abs(v[11])
        if tail > side:
            return True
        if side > tail:
            return False
        d = C.c_void_p()
        if api.InstGetDefinition(insts[i], C.byref(d)) == SU_ERROR_NONE and d.value:
            e2 = C.c_void_p()
            if api.DefGetEntities(d, C.byref(e2)) == SU_ERROR_NONE and e2.value:
                got = _detect_order(api, e2, depth + 1)
                if got is not None:
                    return got
    return None


def _collect(api, entities, m, out, depth, col_major, budget):
    """收集 (face, matrix)，按面积挑重要的面，控制在 budget 个以内。"""
    if depth > MAX_DEPTH or not entities or len(out) >= budget:
        return
    nf = C.c_size_t()
    if api.EntNumFaces(entities, C.byref(nf)) == SU_ERROR_NONE and nf.value:
        n = min(int(nf.value), max(0, budget - len(out)))
        if n:
            faces = (C.c_void_p * int(nf.value))()
            cnt = C.c_size_t()
            if api.EntGetFaces(entities, int(nf.value), faces, C.byref(cnt)) == SU_ERROR_NONE:
                for i in range(min(cnt.value, n)):
                    if faces[i]:
                        out.append((faces[i], m))
    ni = C.c_size_t()
    if api.EntNumInstances(entities, C.byref(ni)) == SU_ERROR_NONE and ni.value:
        insts = (C.c_void_p * ni.value)()
        cnt = C.c_size_t()
        if api.EntGetInstances(entities, ni.value, insts, C.byref(cnt)) == SU_ERROR_NONE:
            for i in range(cnt.value):
                if not insts[i]:
                    continue
                t = SUTransformation()
                if api.InstGetTransform(insts[i], C.byref(t)) != SU_ERROR_NONE:
                    continue
                d = C.c_void_p()
                if api.InstGetDefinition(insts[i], C.byref(d)) != SU_ERROR_NONE or not d.value:
                    continue
                e2 = C.c_void_p()
                if api.DefGetEntities(d, C.byref(e2)) == SU_ERROR_NONE and e2.value:
                    _collect(api, e2, _mul(m, _mat(t.values, col_major)), out, depth + 1,
                             col_major, budget)
    if api.EntNumGroups and api.EntGetGroups:
        ng = C.c_size_t()
        if api.EntNumGroups(entities, C.byref(ng)) == SU_ERROR_NONE and ng.value:
            gs = (C.c_void_p * ng.value)()
            cnt = C.c_size_t()
            if api.EntGetGroups(entities, ng.value, gs, C.byref(cnt)) == SU_ERROR_NONE:
                for i in range(cnt.value):
                    g = gs[i]
                    if not g:
                        continue
                    t = SUTransformation()
                    sub = m
                    if api.GroupGetTransform(g, C.byref(t)) == SU_ERROR_NONE:
                        sub = _mul(m, _mat(t.values, col_major))
                    e2 = C.c_void_p()
                    if api.GroupGetEntities(g, C.byref(e2)) == SU_ERROR_NONE and e2.value:
                        _collect(api, e2, sub, out, depth + 1, col_major, budget)


# ------------------------------------------------------------------ 三角形化
class _Mesh:
    def __init__(self):
        self.pos, self.nrm, self.col, self.idx = [], [], [], []
        self.faces = self.tris = self.skipped = 0


def _face_mesh(api, face, m, mesh):
    helper = C.c_void_p()
    if api.MeshHelperCreate(C.byref(helper), face) != SU_ERROR_NONE or not helper.value:
        mesh.skipped += 1
        return
    try:
        cnt = C.c_size_t()
        nv = C.c_size_t()
        if api.MeshNumVertices(helper, C.byref(nv)) != SU_ERROR_NONE or not nv.value \
                or nv.value > 4000000:
            mesh.skipped += 1
            return
        verts = (SUPoint3D * nv.value)()
        if api.MeshVertices(helper, nv.value, verts, C.byref(cnt)) != SU_ERROR_NONE or not cnt.value:
            mesh.skipped += 1
            return
        n = int(cnt.value)
        norms = (SUVector3D * n)()
        ok_n = (api.MeshNormals(helper, n, norms, C.byref(cnt)) == SU_ERROR_NONE
                and int(cnt.value) == n)
        nt = C.c_size_t()
        if api.MeshNumTriangles(helper, C.byref(nt)) != SU_ERROR_NONE or not nt.value:
            mesh.skipped += 1
            return
        ni = min(int(nt.value), 4000000)
        idx = (C.c_size_t * (ni * 3))()
        if api.MeshIndices(helper, ni * 3, idx, C.byref(cnt)) != SU_ERROR_NONE:
            mesh.skipped += 1
            return
        ni = min(ni, int(cnt.value) // 3)
        if ni <= 0:
            mesh.skipped += 1
            return
        col = (232, 232, 236, 255)
        mat = C.c_void_p()
        if api.FaceGetFrontMaterial and \
                api.FaceGetFrontMaterial(face, C.byref(mat)) == SU_ERROR_NONE and mat.value:
            c = SUColor()
            if api.MaterialGetColor and api.MaterialGetColor(mat, C.byref(c)) == SU_ERROR_NONE:
                col = (c.red, c.green, c.blue, c.alpha if c.alpha else 255)
        base = len(mesh.pos)
        m0, m1, m2, m3 = m[0], m[1], m[2], m[3]
        m4, m5, m6, m7 = m[4], m[5], m[6], m[7]
        m8, m9, m10, m11 = m[8], m[9], m[10], m[11]
        for i in range(n):
            pt = verts[i]
            mesh.pos.append(((m0 * pt.x + m1 * pt.y + m2 * pt.z + m3) * INCH_TO_M,
                             (m4 * pt.x + m5 * pt.y + m6 * pt.z + m7) * INCH_TO_M,
                             (m8 * pt.x + m9 * pt.y + m10 * pt.z + m11) * INCH_TO_M))
            if ok_n:
                nn = norms[i]
                nx = m0 * nn.x + m1 * nn.y + m2 * nn.z
                ny = m4 * nn.x + m5 * nn.y + m6 * nn.z
                nz = m8 * nn.x + m9 * nn.y + m10 * nn.z
                L = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
                mesh.nrm.append((nx / L, ny / L, nz / L))
            else:
                mesh.nrm.append((0.0, 0.0, 1.0))
            mesh.col.append(col)
        for i in range(ni * 3):
            v = int(idx[i])
            mesh.idx.append(base + (v if 0 <= v < n else 0))
        mesh.tris += ni
        mesh.faces += 1
    finally:
        api.MeshHelperRelease(C.byref(helper))


def robust_box(pos, lo_p=0.002, hi_p=0.998):
    """稳健包围盒：按分位点取，避免个别飘在外面的点把整个包围盒撑大。"""
    n = len(pos)
    if not n:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    mn, mx = [], []
    for i in range(3):
        v = sorted(p[i] for p in pos)
        mn.append(v[min(n - 1, int(n * lo_p))])
        mx.append(v[min(n - 1, int(n * hi_p))])
    return tuple(mn), tuple(mx)


def _weld(mesh, eps):
    """顶点焊接：位置 + 法线方向 + 颜色 都相同才合并，保住硬边。"""
    pos, nrm, col, idx = mesh.pos, mesh.nrm, mesh.col, mesh.idx
    if not pos:
        return
    key = {}
    remap = [0] * len(pos)
    np_, nn_, nc_ = [], [], []
    q = 1.0 / max(eps, 1e-9)
    for i, p in enumerate(pos):
        n = nrm[i]
        k = (int(p[0] * q), int(p[1] * q), int(p[2] * q),
             int(n[0] * 24), int(n[1] * 24), int(n[2] * 24), col[i])
        j = key.get(k)
        if j is None:
            j = len(np_)
            key[k] = j
            np_.append(p); nn_.append(n); nc_.append(col[i])
        remap[i] = j
    mesh.pos, mesh.nrm, mesh.col = np_, nn_, nc_
    mesh.idx = [remap[v] for v in idx]


def build_mesh(path, max_faces=MAX_FACES, area_keep=AREA_KEEP, weld=True):
    """读 .skp，返回 (_Mesh, info)。"""
    with _lock:
        if not available():
            raise RuntimeError(_state.get("why") or "SketchUp C API 不可用")
        api = _api
        model = C.c_void_p()
        r = api.ModelCreateFromFile(C.byref(model), path.encode("utf-8"))
        if r != SU_ERROR_NONE or not model.value:
            raise RuntimeError("SketchUp 打不开这个文件（错误码 %d）" % r)
        try:
            info = {}
            a, b, c = C.c_int(), C.c_int(), C.c_int()
            if api.ModelGetVersion and api.ModelGetVersion(model, C.byref(a), C.byref(b),
                                                           C.byref(c)) == SU_ERROR_NONE:
                info["skp_version"] = "%d.%d.%d" % (a.value, b.value, c.value)
            for key, fn in (("skp_name", api.ModelGetName), ("skp_desc", api.ModelGetDescription)):
                if not fn:
                    continue
                ref = C.c_void_p()
                if fn(model, C.byref(ref)) == SU_ERROR_NONE and ref.value:
                    info[key] = api.string(ref)
                    api.StringRelease(C.byref(ref))
            for k in ("layers", "materials", "scenes", "components"):
                fn = getattr(api, "Num_" + k, None)
                n = C.c_size_t()
                if fn and fn(model, C.byref(n)) == SU_ERROR_NONE:
                    info[k] = int(n.value)
            ent = C.c_void_p()
            if api.ModelGetEntities(model, C.byref(ent)) != SU_ERROR_NONE or not ent.value:
                raise RuntimeError("读取模型根节点失败")
            col_major = _detect_order(api, ent)
            if col_major is None:
                col_major = True
            t0 = time.time()
            raw = []
            _collect(api, ent, IDENT, raw, 0, col_major, 3000000)
            total_faces = len(raw)
            if not total_faces:
                raise RuntimeError("这个模型里没有面（可能是空文件或只有图纸信息）")
            info["faces_total"] = total_faces
            # 按面积排序，保留 98% 面积 / 最多 max_faces 个面
            areas = []
            tr = SUTransformation()
            for f, m in raw:
                tr.values = (C.c_double * 16)(*m)
                a2 = C.c_double()
                if api.FaceGetAreaWithTransform(f, C.byref(tr), C.byref(a2)) == SU_ERROR_NONE:
                    areas.append((a2.value, f, m))
                else:
                    areas.append((0.0, f, m))
            areas.sort(key=lambda x: -x[0])
            tot = sum(x[0] for x in areas) or 1.0
            keep, acc = [], 0.0
            for a2, f, m in areas:
                if len(keep) >= max_faces:
                    break
                keep.append((f, m))
                acc += a2
                if acc / tot >= area_keep and len(keep) >= 2000:
                    break
            info["faces_used"] = len(keep)
            bb = SUBoundingBox3D()
            if api.EntGetBoundingBox and api.EntGetBoundingBox(ent, C.byref(bb)) == SU_ERROR_NONE:
                info["bbox_in"] = [[round(v, 4) for v in (bb.min.x, bb.min.y, bb.min.z)],
                                   [round(v, 4) for v in (bb.max.x, bb.max.y, bb.max.z)]]
            if info.get("bbox_in"):
                lo, hi = info["bbox_in"]
                info["dim_m"] = [round((hi[i] - lo[i]) * INCH_TO_M, 3) for i in range(3)]
            info["units"] = "英寸"
            mesh = _Mesh()
            for f, m in keep:
                _face_mesh(api, f, m, mesh)
            if not mesh.idx:
                raise RuntimeError("模型里没有可显示的三角面")
            if weld and mesh.pos:
                rmn, rmx = robust_box(mesh.pos)
                diag = max(rmx[i] - rmn[i] for i in range(3)) or 1.0
                _weld(mesh, diag * 2e-4)
            info["seconds"] = round(time.time() - t0, 2)
            info["col_major"] = col_major
            return mesh, info
        finally:
            api.ModelRelease(C.byref(model))


# ------------------------------------------------------------------ 写出
def write_xcm3(path, mesh, model_size_m=0.0):
    pos, nrm, col, idx = mesh.pos, mesh.nrm, mesh.col, mesh.idx
    n = len(pos)
    if not n or not idx:
        raise RuntimeError("没有可显示的几何体")
    xs = [p[0] for p in pos]; ys = [p[1] for p in pos]; zs = [p[2] for p in pos]
    mn = (min(xs), min(ys), min(zs))
    mx = (max(xs), max(ys), max(zs))
    rmn, rmx = robust_box(pos)
    full = max(mx[i] - mn[i] for i in range(3))
    rob = max(rmx[i] - rmn[i] for i in range(3))
    if rob > 0 and rob < full * 0.6:
        # 有个别飘在很远的零散几何，取景按主体部分，别让主体缩成一个小点
        mn, mx = rmn, rmx
    ctr = ((mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2)
    size = max(mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]) or 1.0
    sc = 2.0 / size
    pb = bytearray()
    nb = bytearray()
    cb = bytearray()
    for i in range(n):
        p = pos[i]
        pb += struct.pack("<3f", (p[0] - ctr[0]) * sc, (p[1] - ctr[1]) * sc, (p[2] - ctr[2]) * sc)
        nb += struct.pack("<3f", *nrm[i])
        cb += struct.pack("<4B", *col[i])
    imax = max(idx)
    wide = 1 if imax >= 65536 else 0
    ib = bytearray()
    if wide:
        for v in idx:
            ib += struct.pack("<I", v)
    else:
        for v in idx:
            ib += struct.pack("<H", v)
    head = bytearray(MAGIC)
    head += struct.pack("<IIII", 2, n, len(idx), wide)   # ver 2：末尾多一个「模型真实尺寸」
    head += struct.pack("<6f", (mn[0] - ctr[0]) * sc, (mn[1] - ctr[1]) * sc, (mn[2] - ctr[2]) * sc,
                        (mx[0] - ctr[0]) * sc, (mx[1] - ctr[1]) * sc, (mx[2] - ctr[2]) * sc)
    head += struct.pack("<ff", size,            # 取景尺寸（米）
                        model_size_m or size)   # 模型自身包围盒最长边（米）
    blob = bytes(head) + bytes(pb) + bytes(nb) + bytes(cb) + bytes(ib)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, path)
    return {"bytes": len(blob), "verts": n, "tris": mesh.tris, "faces": mesh.faces,
            "skipped": mesh.skipped, "view_size_m": round(size, 3),
            "model_size_m": round(model_size_m or size, 3),
            "dim_m": [round(mx[i] - mn[i], 3) for i in range(3)]}


def cache_path(data_dir, src, inner, mtime, size):
    key = "%s|%s|%.0f|%d|%d|%d|%d" % (src.lower(), (inner or "").lower(), mtime, size,
                                      SKP3D_REV, MAX_FACES, int(AREA_KEEP * 100))
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]
    d = os.path.join(data_dir, "model3d")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, h + ".xcm3")


def _stat_cache(path):
    """缓存命中也把顶点/面数/尺寸读出来，界面照样能显示。"""
    st = {"cached": True, "bytes": os.path.getsize(path)}
    try:
        with open(path, "rb") as f:
            head = f.read(52)
        if head[:4] == MAGIC:
            n, ni, wide = struct.unpack_from("<III", head, 8)
            mn = struct.unpack_from("<3f", head, 20)
            mx = struct.unpack_from("<3f", head, 32)
            view_m, model_m = struct.unpack_from("<2f", head, 44)
            st.update(verts=n, tris=ni // 3, faces=ni // 3, skipped=0,
                      view_size_m=round(view_m, 3), model_size_m=round(model_m, 3),
                      dim_m=[round(mx[i] - mn[i], 3) for i in range(3)])
    except Exception:
        pass
    return st


def build(data_dir, src, inner, mtime, size):
    """把 .skp（含 zip 内的）转成 XCM3，带缓存。返回 (路径, 统计, 是否命中缓存)。"""
    out = cache_path(data_dir, src, inner, mtime, size)
    if os.path.isfile(out) and os.path.getsize(out) > 64:
        return out, _stat_cache(out), True
    real, tmp_dir = src, None
    if inner:
        from . import archives
        if archives.archive_kind(src) not in ("zip", "tar"):
            raise RuntimeError("压缩包里的模型目前只支持 zip / tar，请先用「解压」取出再预览")
        # 走 archives.open_inner，能正确处理中文名（含老式 GBK zip）
        fh = archives.open_inner(src, inner)
        if fh is None:
            raise RuntimeError("压缩包里找不到这个模型：%s" % os.path.basename(inner))
        tmp_dir = tempfile.mkdtemp(prefix="xcm_skp_")
        real = os.path.join(tmp_dir, os.path.basename(inner.replace("\\", "/")) or "model.skp")
        try:
            with fh, open(real, "wb") as w:
                shutil.copyfileobj(fh, w, 1 << 20)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
    try:
        mesh, info = build_mesh(real)
        st = write_xcm3(out, mesh, max(info.get("dim_m") or [0]) or 0.0)
        st.update(info)
        return out, st, False
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
