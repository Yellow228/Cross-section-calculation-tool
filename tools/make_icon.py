"""生成程序图标：一个简约的河道横断面（V 形河谷 + 水位线）。

用几何绘制而非生成式图片，保证 16×16 到 256×256 全部清晰、无噪点。

产物：
    src/app/assets/icon.ico   多尺寸图标（16/24/32/48/64/128/256）
    src/app/assets/icon.png   256×256 预览图
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "src", "app", "assets")

# —— 配色（取自界面主题，深蓝底 + 白描边 + 浅蓝水面）——
BG = (24, 95, 165, 255)        # #185FA5
LINE = (255, 255, 255, 255)    # 白
WATER = (181, 212, 244, 255)   # #B5D4F4

S = 1024                       # 先画大图，再降采样，得到平滑边缘
PAD = 0.085                    # 圆角矩形的内边距比例
RADIUS = 0.22                  # 圆角半径比例

# —— 断面形状（归一化坐标，y 向下）——
# 左右岸坡下探到谷底，形成典型的河道横断面
TERRAIN = [
    (0.13, 0.30),
    (0.28, 0.625),
    (0.42, 0.735),
    (0.58, 0.735),
    (0.72, 0.625),
    (0.87, 0.30),
]
WATER_Y = 0.525                # 水位线（低于两岸顶、高于谷底）


def _px(x: float, y: float) -> tuple[float, float]:
    return x * S, y * S


def _water_polygon() -> list[tuple[float, float]]:
    """把断面线高于水位的部分裁掉，得到水面以下的过水区域。"""
    pts: list[tuple[float, float]] = []
    for i in range(len(TERRAIN) - 1):
        (x1, y1), (x2, y2) = TERRAIN[i], TERRAIN[i + 1]
        if y1 >= WATER_Y:
            pts.append((x1, y1))
        if (y1 - WATER_Y) * (y2 - WATER_Y) < 0:          # 跨越水位线 -> 插值
            t = (WATER_Y - y1) / (y2 - y1)
            pts.append((x1 + t * (x2 - x1), WATER_Y))
        if i == len(TERRAIN) - 2 and y2 >= WATER_Y:
            pts.append((x2, y2))
    # 沿水位线闭合
    if pts:
        pts.append((pts[0][0], WATER_Y))
    return [_px(*p) for p in pts]


def draw(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = PAD * size
    d.rounded_rectangle([pad, pad, size - pad, size - pad],
                        radius=RADIUS * size, fill=BG)

    def to_px(p):
        # 把 0~1 的形状坐标映射到图标内区
        inner = 1.0 - 2 * PAD
        return ((p[0] * inner + PAD) * size, (p[1] * inner + PAD) * size)

    # 水面填充
    wp = [to_px((x / S, y / S)) for x, y in _water_polygon()]
    if len(wp) >= 3:
        d.polygon(wp, fill=WATER)

    # 水位线
    xs = [p[0] for p in wp]
    if xs:
        y = to_px((0, WATER_Y))[1]
        d.line([(min(xs), y), (max(xs), y)], fill=LINE,
               width=max(1, int(0.028 * size)))

    # 断面地面线
    tpts = [to_px(p) for p in TERRAIN]
    d.line(tpts, fill=LINE, width=max(1, int(0.055 * size)),
           joint="curve")
    r = 0.026 * size
    for p in (tpts[0], tpts[-1]):
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=LINE)

    return img


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    master = draw(S)
    png = master.resize((256, 256), Image.LANCZOS)
    png.save(os.path.join(OUT_DIR, "icon.png"))

    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [master.resize((s, s), Image.LANCZOS) for s in sizes]

    # ⚠ 必须从 1024 的主图保存，让 Pillow 逐级降采样。
    #   曾经误用 frames[0]（16×16）当源，结果 .ico 里只剩 16×16 一档
    #   （656 字节），exe 也就只嵌到一个尺寸。
    master.save(os.path.join(OUT_DIR, "icon.ico"),
                format="ICO", sizes=[(s, s) for s in sizes])
    # 各尺寸横向排开，方便肉眼检查小尺寸下是否还认得出
    sheet = Image.new("RGBA", (sum(sizes) + 12 * len(sizes), 268), (255, 255, 255, 255))
    x = 6
    for f in frames:
        sheet.alpha_composite(f, (x, 6 + (256 - f.width) // 2))
        x += f.width + 12
    sheet.save(os.path.join(OUT_DIR, "icon_preview.png"))

    print(f"已生成 {os.path.join(OUT_DIR, 'icon.ico')}")


if __name__ == "__main__":
    main()
