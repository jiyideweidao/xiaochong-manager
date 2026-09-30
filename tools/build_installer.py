# -*- coding: utf-8 -*-
r"""小虫管理器 一键打包：PyInstaller + Inno Setup

一条命令做四件事：生成安装附加文件 → PyInstaller 打包 → 生成 Inno Setup 脚本 → 编译安装包。

用法：
    python tools/build_installer.py

可选环境变量：
    XC_SRC   源码目录（默认取本脚本的上一级）
    XC_OUT   安装包输出目录（默认 tools/安装包）

产物：
    程序目录   tools/dist/小虫管理器/
    安装包     tools/安装包/小虫管理器-<版本>-安装包.exe
"""
import io
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("XC_SRC") or os.path.dirname(HERE)   # 仓库根目录
APP = os.path.join(SRC, "app")
_venv_py = os.path.join(SRC, ".venv", "Scripts", "python.exe")
PY = _venv_py if os.path.isfile(_venv_py) else sys.executable
BUILD = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "dist")
EXTRA = os.path.join(HERE, "extra")
OUTPUT_DIR = os.environ.get("XC_OUT") or os.path.join(HERE, "安装包")
APP_NAME = "小虫管理器"
APP_VER = "1.2.1"
def _find_iscc() -> str:
    """找 Inno Setup 6 的编译器（装在哪都可能）。"""
    for p in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
              r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
              r"C:\Program Files\Inno Setup 6\ISCC.exe"):
        if os.path.isfile(p):
            return p
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe")


ISCC = _find_iscc()

README = """小虫管理器 %s —— 使用说明
=================================

一、这是什么
    一个跑在你电脑本地的资源管理器 + 素材库。
    界面是一个独立的程序窗口（没有地址栏、没有标签页），文件都在你自己的硬盘上，
    不上传、不联网（只监听本机 127.0.0.1）。

二、怎么开始
    1) 双击桌面上的「小虫管理器」图标（或开始菜单里的同名项）。
    2) 程序会在后台启动本地服务，然后弹出界面窗口。
    3) 已经开着的时候再双击一次图标 -> 直接把已经打开的窗口提到你眼前，
       不会多开第二个窗口，也不会重启后台服务（连点两下也一样）。
    3) 顶栏两个模式：
       「素材库」：按类型 / 分类 / 风格浏览已索引的素材，可搜关键词、扩展名。
       「浏览文件」：像资源管理器一样逛整个磁盘，双击进文件夹，任何文件都能打开。

三、常用功能
    · 查找        搜索框按 文件名 / 分类 / 关键词 / .扩展名 过滤（按 / 快速聚焦）
    · 多选        Ctrl 点击加选、Shift 点击连选、Ctrl+A 全选、点空白处或 Esc 取消
    · 复制粘贴    Ctrl+C / Ctrl+X / Ctrl+V（程序内部复制，到「浏览文件」里粘贴），可跨文件夹、跨盘
    · 批量改名    选中多个后按 F2，支持查找替换 / 加前后缀 / 编号，先预览再执行
    · 压缩解压    选中条的「压缩为 zip」「解压」；压缩包能直接展开看里面有什么
    · 缩略图      图片、PSD、SketchUp、CAD、PDF、视频、音频、字体、文本、代码 都能预览
    · 3D 看图     选中 .skp，详情里直接转着看（线框 / 包围盒 / 真实尺寸），不打开 SketchUp
    · 分类        点左侧任一分类标签 = 只看这一组的模型，卡片带缩略图，点开即可 3D 预览
    · 收藏        卡片右上角的 ☆ 点一下就收藏（变成金色 ★），再点一下取消；
                  左侧「我的收藏」一键只看收藏过的文件，还能按「收藏时间」排序；
                  选中多个后按 F 键（或点「☆ 收藏」）批量收藏
                  「浏览文件」里看中的文件也能收藏，会自动补进素材库索引
    · 打开        双击 = 用合适的程序打开；每种格式都可以单独指定程序（见下面）
                  「打开方式」= 临时换一个程序打开
                  打开后右下角会写清「用了哪个程序」，程序窗口会自动提到最前
    · 复制到…     选中后点「复制到…」/「移动到…」，直接挑一个目标文件夹一步到位
                  （也可以 Ctrl+C，切到「浏览文件」打开目标文件夹，再按 Ctrl+V）
    · 看图软件    点图片右上方的「指定看图软件…」，从本机已装的看图程序里挑一个。
                  指定之后图片一定用那个软件打开，不会再出现「点了没反应」
    · 删除        Delete 键删到回收站（可还原）
    · 其它        定位、复制完整路径、文件夹大小统计、新建文件夹 都在界面上

四、给每种文件格式单独指定程序
    点右上角齿轮「设置」，弹窗分三个页签：常规 / 文件关联 / 维护。
    切到「文件关联」页签：
      1) 在「扩展名」里填 .psd（多个用逗号隔开，如 .jpg,.png）
      2) 点「读系统默认」自动填上系统当前的默认程序，或点「选择程序…」手动挑
      3) 点「添加 / 更新」
      4) 点右下角「保存设置」生效（这个按钮一直贴在弹窗底部）
    没配的扩展名照旧用 Windows 默认程序；配了但程序不在了会自动回退。
    打开成功后右下角会提示实际用的是哪个程序。

    同一个页签里还有「看图软件」（所有图片通用）：
      · 点「换一个看图软件…」，从本机已装的看图软件里挑（含 Windows 自带的照片查看器）
      · 设好之后，点图片的「用看图软件打开」就直接用它；
        也可以点图片上的「指定看图软件…」就地设置
      · 想恢复成 Windows 默认，点「跟 Windows 默认一致」

五、关掉窗口算不算退出（自己选）
    · 设置 → 常规 →「关掉界面窗口的时候」，两个选项：
        - 隐藏到任务栏（默认）：后台继续跑（缩略图、索引不用重新加载），
          任务栏右下角托盘留着一个小虫图标，双击图标就回来；
          右键图标还有「打开界面 / 退出程序」。
        - 直接退出程序：窗口一关，后台服务也一起关掉；下次双击桌面图标重新启动。
    · 想马上退出程序：设置 → 维护 →「退出程序」，
      或双击安装目录里的「停止小虫管理器.bat」，或右键托盘图标 →「退出程序」。

六、内嵌能力（不需要另装任何东西）
    · 解压：zip / tar / tar.gz / 7z / rar / iso 等 —— 程序自带 7-Zip 内核（bin\\7z.exe），
             zip 和 tar 系列走内置标准库，开箱即用。
    · 3D 看图：.skp 由程序内置的 SketchUp 读图接口解析，会自动识别本机装好的
             SketchUp（2016 及以上都行）。本机没装 SketchUp 时，把 SketchUp SDK 里的
             SketchUpAPI.dll 放到 安装目录\\_internal\\bin\\sketchup\\ 一样能用。
             转换结果缓存到 data\\model3d，同一个模型第二次打开秒开。
    · 打开 .skp：优先用本机装好的 SketchUp（自动在常见安装目录和系统关联里找，
             也可以在「设置 → SketchUp 程序路径」里手动指定）。找不到时给出中文
             提示而不是报错，且仍可以用内置 3D 看图直接看模型。
    · 压缩包里的 .skp 也能直接 3D 预览（zip 直读；rar/7z 先「解压」取出即可）。
    · 打开 .dwg / .dxf：自动用本机装好的「CAD 快速看图」（在常见安装目录和系统关联
              里找）；本机没装就照旧走 Windows 默认程序。「设置 → 文件关联」里能看到
              它认到了哪个程序，也能单独改成别的。

七、常见问题
    · 视频 / 音频缩略图需要 ffmpeg（可选，没有也不影响其它功能）。
    · 「设置」里可调缩略图尺寸、并行线程、索引选项，以及素材目录。
    · 图片（.jpg / .png 这类）默认不生成也不显示缩略图：列表里图片只占一个
      「点开看原图」的格子，点开在右侧直接看原图 —— 更清楚，也不用等生成、不占缓存。
      想恢复成显示缩略图：设置 → 常规 → 勾上「图片显示缩略图」（勾完无需重扫）。
    · 首次启动若素材库是空的，程序会自动在后台扫描一次。
    · 双击 .skp 提示「找不到 SketchUp」：说明本机没装 SketchUp，或者装得不完整
      （系统关联指向的 SketchUp.exe 不存在）。到「设置」里手动填上 SketchUp.exe
      路径，或重装 / 修复 SketchUp；不修也能用内置 3D 看图看模型。
    · 界面打不开时：先确认没有别的程序占用 8765 端口（旧版「SU素材管家」也会占它）。
    · 缓存提醒：程序会定时看一眼缓存占用，超过上限就在底部弹一条提醒。
      点「挑着清理…」会先列出缓存明细——嵌套解压按分类、缩略图按文件类型、
      预览暂存和 3D 缓存按多久没用过——勾哪项清哪项，没勾的一点都不会动。
      提醒的开关、间隔、体积上限都在「设置 → 维护」里调，默认「只提醒一次」
      （清到上限以下会自动重新武装；也可以在里面点「重置提醒」）。
      清理不会动你的素材文件，缩略图以后会按需自动重新生成。

八、目录说明
    安装目录\\小虫管理器.exe      主程序（双击即用）
    安装目录\\启动小虫管理器.bat  等同于双击主程序
    安装目录\\停止小虫管理器.bat  关掉后台服务
    安装目录\\_internal\\bin       内嵌的 7-Zip（解压内核）
    安装目录\\data\\index.db      素材索引数据库
    安装目录\\data\\thumbs        缩略图缓存（删掉会自动重新生成）
    安装目录\\data\\model3d       3D 预览缓存（删掉会自动重新生成）
    安装目录\\data\\nested        压缩包嵌套解压缓存（可在清理面板里按分类挑着删）
    安装目录\\data\\config.json   设置（素材目录、7-Zip / ffmpeg / SketchUp 路径等）
    安装目录\\data\\ui           界面窗口自带的缓存（删掉无影响，会自动重建）

    卸载时 data 目录会保留，重新安装后索引与缩略图继续可用。
""" % APP_VER

ISS = r'''; 小虫管理器 安装脚本（由 打包.py 自动生成，UTF-8 with BOM）
#define AppName "%(name)s"
#define AppVer "%(ver)s"
#define SrcDir "%(src)s"
#define ExtraDir "%(extra)s"

[Setup]
AppId={{8F3A2C41-7B6D-4E92-A1C5-2D9E4B7F1A30}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher=小虫工作室
AppPublisherURL=http://127.0.0.1:8765/
DefaultDirName={code:GetDefaultDir}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=%(out)s
OutputBaseFilename=%(name)s-%(ver)s-安装包
SetupIconFile=%(icon)s
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\\小虫管理器.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion=%(ver)s.0
VersionInfoCompany=小虫工作室
VersionInfoDescription=小虫管理器 安装程序
VersionInfoProductName=小虫管理器
VersionInfoProductVersion=%(ver)s
AllowNoIcons=yes
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "cn"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
Source: "{#SrcDir}\\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ExtraDir}\\data\\config.json"; DestDir: "{app}\\data"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "{#ExtraDir}\\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ExtraDir}\\启动小虫管理器.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ExtraDir}\\停止小虫管理器.bat"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\\data"

[Icons]
Name: "{autoprograms}\\{#AppName}"; Filename: "{app}\\小虫管理器.exe"; IconFilename: "{app}\\小虫管理器.exe"; Comment: "本地资源管理器 · 素材库"
Name: "{autodesktop}\\{#AppName}"; Filename: "{app}\\小虫管理器.exe"; IconFilename: "{app}\\小虫管理器.exe"; Tasks: desktopicon; Comment: "本地资源管理器 · 素材库"

[Run]
Filename: "{app}\\小虫管理器.exe"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\\taskkill.exe"; Parameters: "/IM 小虫管理器.exe /F"; Flags: runhidden; RunOnceId: "KillApp"

[Code]
function GetDefaultDir(Param: String): String;
begin
  if DirExists('D:\\') then
    Result := 'D:\\小虫管理器'
  else
    Result := ExpandConstant('{autopf}\\小虫管理器');
end;

function InitializeSetup(): Boolean;
var
  R: Integer;
begin
  Exec(ExpandConstant('{sys}\\taskkill.exe'), '/IM 小虫管理器.exe /F', '',
       SW_HIDE, ewWaitUntilTerminated, R);
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  R: Integer;
begin
  if CurUninstallStep = usUninstall then
    Exec(ExpandConstant('{sys}\\taskkill.exe'), '/IM 小虫管理器.exe /F', '',
         SW_HIDE, ewWaitUntilTerminated, R);
end;
'''


def write_extra():
    shutil.rmtree(EXTRA, ignore_errors=True)
    os.makedirs(os.path.join(EXTRA, "data"), exist_ok=True)
    # roots 留空：别人的电脑上没有你的素材目录；首次运行用 config.py 里的通用默认值
    cfg = {"roots": [], "seven_zip": "",
           "ffmpeg": "", "thumb_max_px": 720, "workers": 8, "index_images": True,
           "image_max_mb": 30, "index_all_files": True, "max_file_mb": 2048,
           "index_inside_archives": True, "text_preview_kb": 256, "use_3d": True,
           "sketchup_exe": ""}
    io.open(os.path.join(EXTRA, "data", "config.json"), "w", encoding="utf-8").write(
        json.dumps(cfg, ensure_ascii=False, indent=2))
    io.open(os.path.join(EXTRA, "使用说明.txt"), "w", encoding="utf-8").write(README)
    io.open(os.path.join(EXTRA, "启动小虫管理器.bat"), "w", encoding="gbk",
            newline="\r\n").write('@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\n'
                                  'start "" "%~dp0小虫管理器.exe"\r\nexit\r\n')
    io.open(os.path.join(EXTRA, "停止小虫管理器.bat"), "w", encoding="gbk",
            newline="\r\n").write('@echo off\r\nchcp 65001 >nul\r\n'
                                  'echo 正在关闭 小虫管理器 ...\r\n'
                                  'taskkill /IM 小虫管理器.exe /F >nul 2>nul\r\n'
                                  'echo 已关闭。\r\ntimeout /t 2 >nul\r\nexit\r\n')
    print("[1/4] 已生成安装附加文件 ->", EXTRA)


def run_pyinstaller():
    if os.path.isdir(BUILD):
        shutil.rmtree(BUILD, ignore_errors=True)
    if os.path.isdir(DIST):
        shutil.rmtree(DIST, ignore_errors=True)
    cmd = [PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--noconsole",
           "--name", APP_NAME, "--icon", os.path.join(APP, "小虫.ico"),
           "--distpath", DIST, "--workpath", BUILD, "--specpath", HERE,
           "--paths", APP,
           "--add-data", os.path.join(APP, "static") + ";static",
           "--add-data", os.path.join(APP, "小虫.ico") + ";.",
           "--add-data", os.path.join(APP, "bin") + ";bin",
           "--collect-all", "pypdfium2",
           "--copy-metadata", "fastapi", "--copy-metadata", "starlette",
           "--copy-metadata", "uvicorn", "--copy-metadata", "pydantic"]
    for m in ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
              "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
              "uvicorn.protocols.http.httptools_impl",
              "uvicorn.protocols.websockets.auto",
              "uvicorn.protocols.websockets.websockets_impl",
              "uvicorn.protocols.websockets.wsproto_impl",
              "uvicorn.lifespan.on", "uvicorn.lifespan.off",
              "PIL._tkinter_finder", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont"]:
        cmd += ["--hidden-import", m]
    cmd += [os.path.join(APP, "start.py")]
    print("[2/4] 正在用 PyInstaller 打包程序 ...")
    r = subprocess.run(cmd, cwd=SRC, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    if r.returncode:
        sys.exit("PyInstaller 失败，退出码 %s" % r.returncode)
    print("      完成 ->", os.path.join(DIST, APP_NAME))


def compile_installer():
    iss = os.path.join(HERE, "小虫管理器.iss")
    io.open(iss, "w", encoding="utf-8-sig", newline="\r\n").write(ISS % {
        "name": APP_NAME, "ver": APP_VER, "src": os.path.join(DIST, APP_NAME),
        "extra": EXTRA, "out": OUTPUT_DIR, "icon": os.path.join(APP, "小虫.ico")})
    print("[3/4] 已生成 Inno Setup 脚本 ->", iss)
    if not os.path.isfile(ISCC):
        sys.exit("找不到 ISCC.exe（Inno Setup 6）。装法：winget install JRSoftware.InnoSetup")
    r = subprocess.run([ISCC, iss])
    if r.returncode:
        sys.exit("Inno Setup 编译失败，退出码 %s" % r.returncode)
    out = os.path.join(OUTPUT_DIR, "%s-%s-安装包.exe" % (APP_NAME, APP_VER))
    print("[4/4] 安装包 ->", out, "(%.1f MB)" % (os.path.getsize(out) / 1048576.0))


if __name__ == "__main__":
    write_extra()
    run_pyinstaller()
    compile_installer()
    print("\n全部完成。")
