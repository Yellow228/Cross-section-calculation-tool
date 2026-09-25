"""生成程序图标：一个简约的河道横断面（V 形河谷 + 水位线）。

用几何绘制而非生成式图片，保证 16×16 到 256×256 全部清晰、无噪点。

产物：
    src/app/assets/icon.ico   多尺寸图标（16/24/32/48/64/128/256）
    src/app/assets/icon.png   256×256 预览图
    src/app/assets/icon_preview.png  各尺寸横向排开，便于检查小尺寸辨识度

**当前采用的设计是 `c`「金边」**（默认值即它，直接跑 `python tools/make_icon.py`
就生成它）。其余变体保留在 `--variant` 里，方便日后再比选或微调。

配色变体（`--variant`）
----------------------
    none  原始：深蓝底 + 白断面线 + 浅蓝水面（不含任何浅黄）
    a     「暖阳」：天空加一个浅黄太阳（大尺寸带光线），断面略往右下让位
    b     「金水」：水面附近一条浅黄高光带，水位线以上仍是深蓝
    c     「金边」：沿蓝底内缘描一圈浅黄 ← **当前采用**
    all   把四种都渲染到 `--outdir`，供挑选

⚠ 四种变体**共用同一份几何**（TERRAIN / WATER_Y / 圆角参数），只改配色与
  装饰元素。改几何时只需改一处——本项目踩过"同一东西两处定义，改一处漏一处"。

⚠⚠ 改完**务必**验一条：`none` 变体重新渲染出来，必须与已装的
   `src/app/assets/icon.png` **逐像素一致**。原设计就是正在分发的那个图标，
   重构 draw() 很容易顺手把它改坏。做法见 `.workbuddy/memory/2026-09-25.md`。

⚠ 换图标后 exe 里的图标**不会自动更新**，必须重新打包才生效（见 CLAUDE.md 坑 #5 / #6）。
"""

from __future__ import annotations

import argparse
import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "src", "app", "assets")

# —— 配色（深蓝底 + 白描边 + 浅蓝水面 + 浅黄点缀）——
BG = (24, 95, 165, 255)        # #185FA5
LINE = (255, 255, 255, 255)    # 白
WATER = (181, 212, 244, 255)   # #B5D4F4
YELLOW = (255, 255, 102, 255)  # #FFFF66  浅黄点缀

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

# —— 断面在图标内的整体偏移 ——
# 往右下挪一点，给左上角的太阳让出整块天空。只作用于断面（含水面），
# 不影响太阳本身——所以 to_px 不参与偏移，另有 to_px_sec 负责。
# ⚠ 别挪多：岸顶本来就离圆角边不远，右移过多右边会顶到圆角，
#   下移过多谷底会贴底边，整体变成"右下角塞满、左上角空一片"。
SEC_SHIFT_X, SEC_SHIFT_Y = 0.042, 0.028

# —— 变体 A：太阳（左上角）——
# 半径是试出来的：0.072 在 16px 下只剩一个灰点，0.098 又大到压住断面。
# 移到左上角后视觉重量本来就变轻了，取 0.070。
SUN_CX, SUN_CY, SUN_R = 0.235, 0.170, 0.070
SUN_RAY_R0, SUN_RAY_R1 = 0.092, 0.115
SUN_RAYS = 8

# —— 变体 B：水面高光带 ——
# 浅蓝水层相对真实水位下移这么多，露出的一圈浅黄就是"水面附近"的带子。
# 太小会看不见（16px 下 0.09 才约 1.4 px），太大就成了"黄水"。
WATER_BAND = 0.09

# —— 变体 C：金边 ——
# 沿蓝底圆角矩形的**内缘**描一圈浅黄。
# ⚠ 必须向内描：Pillow 的 `outline + width` 是往包围盒内侧画的，正好保住
#   PAD 那圈透明外边距。若改成"外面套一圈黄"，图标在任务栏里会比同排的
#   其它图标显大一圈。
# 厚度是折中：16px 下 0.050 只有约 0.8 px（勉强能看出染色），再薄就没了；
#   256px 下 12.8 px，已经接近"边框"而非"细线"。想更精致可降到 0.035。
RING_W = 0.050


def _px(x: float, y: float) -> tuple[float, float]:
    return x * S, y * S


def _water_polygon(level: float = WATER_Y) -> list[tuple[float, float]]:
    """水面以下的过水区域（按给定水位线 level 裁切）。

    传不同的 level 可以得到"水位更高/更低"时的过水区，变体 B 正是用两次调用
    叠出"水面高光带"——先铺一层浅黄（全水深），再在上面铺浅蓝（水位下移 BAND），
    露出的那圈浅黄刚好是水面附近的一带。**不用做多边形裁剪**，省掉一堆边界情况。
    """
    pts: list[tuple[float, float]] = []
    for i in range(len(TERRAIN) - 1):
        (x1, y1), (x2, y2) = TERRAIN[i], TERRAIN[i + 1]
        if y1 >= level:
            pts.append((x1, y1))
        if (y1 - level) * (y2 - level) < 0:              # 跨越水位线 -> 插值
            t = (level - y1) / (y2 - y1)
            pts.append((x1 + t * (x2 - x1), level))
        if i == len(TERRAIN) - 2 and y2 >= level:
            pts.append((x2, y2))
    # 沿水位线闭合
    if pts:
        pts.append((pts[0][0], level))
    return [_px(*p) for p in pts]


def draw(size: int, variant: str = "none") -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = PAD * size
    d.rounded_rectangle([pad, pad, size - pad, size - pad],
                        radius=RADIUS * size, fill=BG)

    # 变体 C：沿蓝底内缘描一圈浅黄。放在断面之前画——万一断面线压到边缘，
    # 让白线盖在黄边上（白线是主体，不能被压掉）。
    if variant == "c":
        rad = RADIUS * size - RING_W * size      # 内缩后圆角半径同步减小
        d.rounded_rectangle([pad, pad, size - pad, size - pad],
                            radius=max(1, int(rad)), outline=YELLOW,
                            width=max(1, int(RING_W * size)))

    def to_px(p):
        # 把 0~1 的形状坐标映射到图标内区（不偏移，太阳用它）
        inner = 1.0 - 2 * PAD
        return ((p[0] * inner + PAD) * size, (p[1] * inner + PAD) * size)

    def to_px_sec(p):
        # 断面（含水面）专用：变体 A 上叠整体偏移，给左上角的太阳让出天空。
        # 只对 A 生效——none 要保持"改动前"的样子，才能拿来当对比基线。
        if variant == "a":
            return to_px((p[0] + SEC_SHIFT_X, p[1] + SEC_SHIFT_Y))
        return to_px(p)

    # 变体 B：水面附近一条浅黄高光带。做法是叠两层水——
    # 先按真实水位铺浅黄，再把"水位下移 BAND"的过水区铺浅蓝盖上去，
    # 露出来的那圈浅黄正好贴着水面。不需要做多边形裁剪。
    if variant == "b":
        wp_y = [to_px_sec((x / S, y / S)) for x, y in _water_polygon(WATER_Y)]
        if len(wp_y) >= 3:
            d.polygon(wp_y, fill=YELLOW)

    water_level = WATER_Y + (WATER_BAND if variant == "b" else 0.0)

    # 水面填充
    wp = [to_px_sec((x / S, y / S)) for x, y in _water_polygon(water_level)]
    if len(wp) >= 3:
        d.polygon(wp, fill=WATER)

    # 水位线：始终画在**真实**水位 WATER_Y 上，横向跨度取该水位的过水区
    # （变体 B 的浅蓝层是下移过的，跨度更窄，不能拿它算跨度）
    xs = [p[0] for p in (to_px_sec((x / S, y / S))
                         for x, y in _water_polygon(WATER_Y))]
    if xs:
        y = to_px_sec((0, WATER_Y))[1]
        d.line([(min(xs), y), (max(xs), y)], fill=LINE,
               width=max(1, int(0.028 * size)))

    # 断面地面线
    tpts = [to_px_sec(p) for p in TERRAIN]
    d.line(tpts, fill=LINE, width=max(1, int(0.055 * size)),
           joint="curve")
    r = 0.026 * size
    for p in (tpts[0], tpts[-1]):
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=LINE)

    # 变体 A：天空里的浅黄太阳（画在最上层，压住蓝色背景）
    if variant == "a":
        cx, cy = to_px((SUN_CX, SUN_CY))
        rr = SUN_R * size
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=YELLOW)
        # 光线在小尺寸上会糊成一团，只在 ≥32 px 时画
        if size >= 32:
            r0, r1 = SUN_RAY_R0 * size, SUN_RAY_R1 * size
            w = max(1, int(0.016 * size))
            for k in range(SUN_RAYS):
                a = 2 * math.pi * k / SUN_RAYS
                d.line([(cx + r0 * math.cos(a), cy + r0 * math.sin(a)),
                        (cx + r1 * math.cos(a), cy + r1 * math.sin(a))],
                       fill=YELLOW, width=w)

    return img


def _sheet(frames: list[Image.Image], sizes: list[int]) -> Image.Image:
    """各尺寸横向排开，方便肉眼检查小尺寸下是否还认得出。"""
    width = sum(f.width for f in frames) + 12 * len(frames)
    sheet = Image.new("RGBA", (width, 268), (255, 255, 255, 255))
    x = 6
    for f in frames:
        sheet.alpha_composite(f, (x, 6 + (256 - f.width) // 2))
        x += f.width + 12
    return sheet


SIZES = [16, 24, 32, 48, 64, 128, 256]
VARIANT_LABELS = {"none": "原始_无浅黄", "a": "A_暖阳", "b": "B_金水",
                  "c": "C_金边"}


def render(variant: str, out_dir: str) -> Image.Image:
    master = draw(S, variant)
    os.makedirs(out_dir, exist_ok=True)
    prefix = "" if out_dir == OUT_DIR else VARIANT_LABELS[variant] + "_"

    master.resize((256, 256), Image.LANCZOS).save(
        os.path.join(out_dir, f"{prefix}icon.png"))
    sheet = _sheet([master.resize((s, s), Image.LANCZOS) for s in SIZES], SIZES)
    sheet.save(os.path.join(out_dir, f"{prefix}icon_preview.png"))
    return master


def main() -> None:
    ap = argparse.ArgumentParser(description="生成程序图标")
    ap.add_argument("--variant", default="c",
                    choices=["none", "a", "b", "c", "all"],
                    help="配色变体（默认 c 金边，即当前采用的设计）；"
                         "all 会把四种都渲染出来供比选")
    ap.add_argument("--outdir", default=OUT_DIR,
                    help="输出目录（默认写进 src/app/assets）")
    a = ap.parse_args()

    if a.variant != "all":
        os.makedirs(a.outdir, exist_ok=True)
        master = draw(S, a.variant)
        prefix = "" if a.outdir == OUT_DIR else VARIANT_LABELS[a.variant] + "_"

        master.resize((256, 256), Image.LANCZOS).save(
            os.path.join(a.outdir, f"{prefix}icon.png"))
        _sheet([master.resize((s, s), Image.LANCZOS) for s in SIZES], SIZES).save(
            os.path.join(a.outdir, f"{prefix}icon_preview.png"))

        # ⚠ 必须从 1024 的主图保存，让 Pillow 逐级降采样。
        #   曾经误用 frames[0]（16×16）当源，结果 .ico 里只剩 16×16 一档
        #   （656 字节），exe 也就只嵌到一个尺寸。
        if a.outdir == OUT_DIR:
            master.save(os.path.join(OUT_DIR, "icon.ico"),
                        format="ICO", sizes=[(s, s) for s in SIZES])
            print(f"已生成 {os.path.join(OUT_DIR, 'icon.ico')}"
                  f"（变体 {a.variant}，{len(SIZES)} 档）")
        else:
            print(f"已生成 {a.outdir} 下的预览（变体 {a.variant}）")
        return

    # all：全部渲染，另出一张并列对比图
    order = ("none", "a", "b", "c")
    os.makedirs(a.outdir, exist_ok=True)
    masters = {v: render(v, a.outdir) for v in order}
    for v in order:
        lab = VARIANT_LABELS[v]
        print(f"  {lab}: {os.path.join(a.outdir, lab + '_icon_preview.png')}")

    # 并列对比：每变体一列，两行（256 与 32）
    rows = [256, 32]
    cell = 256
    n = len(order)
    comp = Image.new("RGBA", (cell * n + 10 * (n + 1),
                              cell * len(rows) + 10 * (len(rows) + 1)),
                     (255, 255, 255, 255))
    for r, sz in enumerate(rows):
        for c, v in enumerate(order):
            f = masters[v].resize((sz, sz), Image.LANCZOS)
            comp.alpha_composite(f, (10 + c * (cell + 10) + (cell - sz) // 2,
                                     10 + r * (cell + 10) + (cell - sz) // 2))
    comp.save(os.path.join(a.outdir, "对比_256与32.png"))
    print(f"  对比图: {os.path.join(a.outdir, '对比_256与32.png')}")


if __name__ == "__main__":
    main()
