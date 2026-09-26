# -*- coding: utf-8 -*-
"""生成程序图标：小虫管理器（文件夹 + 小虫）。"""
import os
import shutil
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "小虫.ico")
FONTS = [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
         r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\seguisb.ttf",
         r"C:\Windows\Fonts\arialbd.ttf"]


def font(size):
    for f in FONTS:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                pass
    return ImageFont.load_default()


def build(size=256):
    S = size * 4
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # 圆角底 + 竖向渐变
    grad = Image.new("RGB", (1, S))
    gd = ImageDraw.Draw(grad)
    top, bot = (26, 176, 138), (9, 84, 68)
    for y in range(S):
        t = y / max(1, S - 1)
        gd.point((0, y), fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    grad = grad.resize((S, S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=255)
    im.paste(grad, (0, 0), mask)

    white = (255, 255, 255, 255)
    d = ImageDraw.Draw(im, "RGBA")
    # 文件夹
    d.rounded_rectangle([S * 0.13, S * 0.40, S * 0.87, S * 0.82], radius=S * 0.055, fill=white)
    d.rounded_rectangle([S * 0.13, S * 0.28, S * 0.47, S * 0.46], radius=S * 0.04, fill=white)
    d.rectangle([S * 0.13, S * 0.38, S * 0.47, S * 0.46], fill=white)
    # 文件夹上的小虫（用底色画，形成镂空感）
    bug = (9, 84, 68, 255)
    lw = int(S * 0.019)
    d.ellipse([S * 0.47, S * 0.53, S * 0.71, S * 0.75], fill=bug)
    d.ellipse([S * 0.53, S * 0.44, S * 0.65, S * 0.56], fill=bug)
    for sx, sy, ex, ey in ((0.47, 0.57, 0.40, 0.51), (0.47, 0.64, 0.39, 0.64),
                           (0.48, 0.71, 0.41, 0.77), (0.71, 0.57, 0.78, 0.51),
                           (0.71, 0.64, 0.79, 0.64), (0.70, 0.71, 0.77, 0.77)):
        d.line([S * sx, S * sy, S * ex, S * ey], fill=bug, width=lw)
    d.line([S * 0.56, S * 0.45, S * 0.51, S * 0.38], fill=bug, width=lw)
    d.line([S * 0.62, S * 0.45, S * 0.67, S * 0.38], fill=bug, width=lw)
    im = im.resize((size, size), Image.LANCZOS)
    im.save(OUT, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    st = os.path.join(HERE, "static")
    os.makedirs(st, exist_ok=True)
    shutil.copy2(OUT, os.path.join(st, "小虫.ico"))
    im.save(os.path.join(st, "icon.png"))
    print("icon ->", OUT)


if __name__ == "__main__":
    build()