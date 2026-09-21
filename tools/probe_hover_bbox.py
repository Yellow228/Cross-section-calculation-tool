"""临时探针：量出"悬停提示框把画布绘图区挤窄"的实际幅度。

不靠猜。构造真实的编辑器窗口，在断面上从左到右依次悬停每个实测测点，
每停一个点就记一次轴在画布中的位置（transAxes 的像素宽度）与画布自身宽度。
若提示框（annotation，靠右时会溢出到轴外）真的进入了 tight_layout 的
包围盒计算，就会看到：悬停靠右的点时轴变窄，鼠标移开后又恢复。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication          # noqa: E402

app = QApplication(sys.argv)

from app.main_window import MainWindow              # noqa: E402

win = MainWindow()
win.data_dir = os.path.join(ROOT, "data")
win._load()

win.lst_lines.setCurrentRow(0)
win.lst_secs.setCurrentRow(0)
win._refresh_current_views()
sec = win._current_section()
win.act_edit_sec.trigger()
ed = win.dlg_edit
ed.plot.canvas.draw()

canvas = ed.plot.canvas
ax = ed.plot.ax


def axes_px():
    """(轴像素宽, 轴左边界, 画布像素宽)"""
    bbox = ax.get_window_extent()
    return bbox.width, bbox.x0, canvas.width()


print(f"断面 {sec.name}，{sec.n_points} 个测点")
print(f"画布宽度 = {canvas.width()} px")
print()

base = None
rows = []
for i in range(sec.n_points):
    px, py = ax.transData.transform((sec.s[i], sec.z[i]))
    ev = type("Ev", (), {})()
    ev.x, ev.y, ev.button = float(px), float(py), None
    ev.xdata, ev.ydata, ev.key = sec.s[i], sec.z[i], None
    ed._on_motion(ev)               # 悬停：会触发 _refresh_plot + redraw
    canvas.draw()                   # draw_idle 是异步的，量之前必须同步 draw 一次
    w, x0, cw = axes_px()
    if base is None:
        # 先把 hover 清掉量一次"干净"状态作为基准
        ed._hover_idx = None
        ed._refresh_plot()
        canvas.draw()
        base_w, base_x0, _ = axes_px()
        ed._on_motion(ev)
        canvas.draw()
        w, x0, cw = axes_px()
    rows.append((i + 1, round(sec.s[i], 2), round(w, 1), round(x0, 1)))

print(f"基准（无提示框）：轴宽 {base_w:.1f} px，左边界 {base_x0:.1f} px")
print()
print(f"{'测点':>4} {'起点距':>8} {'轴宽 px':>9} {'Δ轴宽':>8} {'轴左边界':>9}")
for no, s, w, x0 in rows:
    print(f"{no:>4} {s:>8.2f} {w:>9.1f} {w - base_w:>+8.1f} {x0:>9.1f}")

narrow = [r for r in rows if r[2] < base_w - 0.5]
print()
if narrow:
    worst = min(narrow, key=lambda r: r[2])
    print(f"⚠ 有 {len(narrow)}/{len(rows)} 个测点在悬停时把轴挤窄了；"
          f"最严重：第 {worst[0]} 点（起点距 {worst[1]}），"
          f"轴宽 {worst[2]:.1f} px，比基准窄 {base_w - worst[2]:.1f} px"
          f"（{(base_w - worst[2]) / base_w * 100:.1f}%）")
else:
    print("未观察到轴被挤窄")

print()
print("--- 提示框是否溢出到轴外 ---")
# ⚠ 不能用 ax.texts[-1] 取"刚加的那个框"：图里图标/标注多起来之后它不准。
# 取"锚点正好落在被测点数据坐标上的 Annotation"。
#
# ⚠ 也不能在 _refresh_plot() 之后再 draw()：draw 里会再跑一次 tight_layout，
#   轴的位置随之改变，此时读到的 get_window_extent 是拿旧坐标系算的，
#   会得出"框远远溢出"的假结论。_refresh_plot() 内部已经 draw_idle 过，
#   直接量即可。
from matplotlib.text import Annotation                # noqa: E402

for i in (0, sec.n_points // 2, sec.n_points - 1):
    ed._hover_idx = i
    ed._refresh_plot()
    target = (sec.s[i], sec.z[i])
    ann = None
    for t in ax.texts:
        if isinstance(t, Annotation) and t.xy == target:
            ann = t
    if ann is None:
        print(f"第 {i + 1} 点：没找到对应的提示框")
        continue
    bb = ann.get_window_extent(canvas.get_renderer())
    ax_bb = ax.get_window_extent(canvas.get_renderer())
    pos = ann.get_position()
    side = "右" if pos[0] > 0 else "左"
    fits = ax_bb.x0 <= bb.x0 and bb.x1 <= ax_bb.x1
    print(f"第 {i + 1:>2} 点：框摆在测点{side}侧（offset={pos[0]:.0f}pt），"
          f"框 [{bb.x0:.0f}, {bb.x1:.0f}]，轴 [{ax_bb.x0:.0f}, {ax_bb.x1:.0f}]，"
          f"{'完全在轴内 ✓' if fits else '仍有溢出 ✗'}")
