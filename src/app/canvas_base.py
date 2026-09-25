"""matplotlib 画布基类：中文字体、Qt 嵌入、导航工具栏。"""

from __future__ import annotations

import matplotlib
from matplotlib.backends.backend_qtagg import (FigureCanvasQTAgg,
                                               NavigationToolbar2QT)
from matplotlib.figure import Figure
from matplotlib.text import Annotation
from PySide6.QtWidgets import QVBoxLayout, QWidget

# 中文字体：按优先级找系统里第一个可用的
_CJK_CANDIDATES = ["Microsoft YaHei", "微软雅黑", "SimHei", "黑体",
                   "Noto Sans CJK SC", "Source Han Sans SC", "DejaVu Sans"]
matplotlib.rcParams["font.sans-serif"] = _CJK_CANDIDATES
matplotlib.rcParams["axes.unicode_minus"] = False   # 负号不显示成方块
matplotlib.rcParams["figure.autolayout"] = False

# 界面配色（与界面整体风格一致）
COLOR_BED = "#5F5E5A"        # 断面地面线
COLOR_WATER = "#378ADD"      # 水面填充
COLOR_DESIGN = "#185FA5"     # 设计水位
COLOR_PLUS = "#0F6E56"       # 加高水位
COLOR_DISASTER = "#A32D2D"   # 成灾水位
COLOR_THALWEG = "#993C1D"    # 深泓点
COLOR_PEAK = "#854F0B"       # 岸顶
COLOR_TURN = "#534AB7"       # 转折点
COLOR_ZONE = "#B5D4F4"       # 分区底色带
COLOR_WARN = "#E24B4A"       # 异常段
COLOR_PICK = "#378ADD"       # 拾取 / 编辑时的高亮


class MplCanvas(FigureCanvasQTAgg):
    """一个带单张坐标轴的画布。"""

    def __init__(self, parent=None, width: float = 7.0, height: float = 5.0, dpi: int = 100):
        self.fig = Figure(figsize=(width, height), dpi=dpi, facecolor="white")
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)


def place_hover_note(ax, text: str, x: float, y: float,
                     color: str = "#185FA5", fc: str = "#E6F1FB",
                     ec: str = "#85B7EB",
                     rad: float = 16.0, dy: float = 18.0) -> object:
    """在数据点 (x, y) 旁放一个悬停提示框。最终位置由 `PlotPanel.redraw()` 校正。

    为什么不能简单地一直往右偏
    --------------------------
    提示框的默认位置是测点右侧（`xytext=(正数, 正数)`）。测点靠右时框会溢出到
    轴外，而 `PlotPanel.redraw()` 用的是 `fig.tight_layout()`——它会把**所有
    可见 artist 的包围盒**算进去，于是为了给框腾地方，**压缩绘图区**。

    实测（`tools/probe_hover_bbox.py`，断面 secA-6，画布 636 px）：
    悬停第 10 个测点（起点距 33.22 m）时轴宽 551 → 401 px，**窄了 27%**；
    移开鼠标又弹回来，表现为"画布一闪一闪地缩"。

    修法有两层，缺一不可：

    1. `PlotPanel._exclude_hover_notes()`：把框排除出 tight_layout 的
       包围盒计算——这是"画布变窄"的直接原因。
    2. `PlotPanel._place_hover_notes()`：在 tight_layout **之后**，
       按框的实际屏幕宽度决定摆哪边（右边放不下就翻左侧）。

    ⚠ 第 2 步**故意不在这里做**。本函数是在 `_refresh_plot()` 中途被调用的，
    那时轴还是**上一次布局**的几何：`ax.get_window_extent()` 给的是旧矩形
    （实测 draw 前 573 px、draw 后 613 px，差 40 px）。拿旧矩形判断
    "右边放不下"，结果是**每个点都判成溢出**、全部翻到左侧，比不翻还难看。
    等 tight_layout 跑完再量，才是这一帧真正的轴矩形。

    参数
    ----
    rad : 框与测点的水平间距（点）。翻到左侧时镜像使用。
    dy  : 框与测点的垂直间距（点），正值向上。
    """
    ann = ax.annotate(text, (x, y), textcoords="offset points",
                      xytext=(rad, dy), fontsize=9, color=color,
                      bbox=dict(boxstyle="round,pad=0.45", fc=fc, ec=ec, lw=0.8),
                      zorder=10)
    # gid 里带上原始偏移，供 `_place_hover_notes` 反复校正时回到基准
    ann.set_gid(f"hover-note:{rad:.1f}:{dy:.1f}")
    return ann


class PlotPanel(QWidget):
    """画布 + 导航工具栏的组合面板，供三个视图继承。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = MplCanvas(self)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.canvas)

    @property
    def ax(self):
        return self.canvas.ax

    def clear(self):
        self.ax.clear()

    def redraw(self):
        self._exclude_hover_notes()          # 必须早于 tight_layout
        self.canvas.fig.tight_layout()
        self._place_hover_notes()            # 必须晚于 tight_layout
        self.canvas.draw_idle()

    def _hover_notes(self) -> list:
        """当前轴上的悬停提示框。靠 `place_hover_note` 打的 gid 识别。

        ⚠ 不能用 `t.xycoords` 判断：那是 `Annotation` 才有的属性，
        `ax.text()` 建出的 `Text` 没有，直接取会 `AttributeError`。
        """
        return [t for t in self.ax.texts
                if isinstance(t, Annotation)
                and str(t.get_gid() or "").startswith("hover-note:")]

    def _exclude_hover_notes(self):
        """把悬停提示框排除出 tight_layout 的包围盒计算。

        ⚠ 这是"提示框一出现画布就变窄"的直接原因，不是可选项。
        `tight_layout()` 会把所有可见 artist 的包围盒算进去；悬停框在测点
        靠右时会溢出轴外，于是它为了腾地方压缩绘图区（实测挤掉 27%）。
        设 `set_in_layout(False)` 之后，框仍照常绘制、只是不参与布局。

        只排除悬停框；`ax.text()` 加的说明文字不在其列——那些本来就该
        参与 tight_layout，否则会被裁掉。
        """
        for t in self._hover_notes():
            t.set_in_layout(False)

    def _place_hover_notes(self):
        """把每个悬停提示框摆到轴的边界之内。**必须在 tight_layout 之后调用。**

        规则：默认在测点右上方；右边放不下就翻到左侧，上面放不下就压到下方。
        用屏幕坐标判断——横向几十米、纵向几米，数据坐标下的"距离"
        和眼睛看到的完全不是一回事。

        ⚠ 单位：`xytext` 用**点**，`get_window_extent()` 返回**像素**，
        相差 dpi/72 倍（dpi=100 时 1.39）。混用会把偏移算成几百点、
        把框甩到轴外，而且看起来"确实翻到左边了"，很难发现算错了。
        """
        notes = self._hover_notes()
        if not notes:
            return
        try:
            renderer = self.canvas.get_renderer()
        except Exception:                                   # pragma: no cover
            return
        k = 72.0 / self.canvas.fig.dpi
        ax_bb = self.ax.get_window_extent(renderer)
        for t in notes:
            try:
                _tag, rad_s, dy_s = str(t.get_gid()).split(":")
                rad, dy = float(rad_s), float(dy_s)
            except Exception:                               # pragma: no cover
                rad, dy = 16.0, 18.0
            # 先回到基准偏移再量，否则会以上次的校正结果为起点反复累积
            t.set_position((rad, dy))
            bb = t.get_window_extent(renderer)
            dx = 0.0
            if bb.x1 > ax_bb.x1:                    # 右边放不下 -> 翻到左侧
                dx = -(rad + bb.width * k)
            elif bb.x0 < ax_bb.x0:                  # 左边也放不下 -> 贴右边界
                dx = (ax_bb.x1 - bb.x1) * k
            dy2 = 0.0
            if bb.y1 > ax_bb.y1:                    # 顶到上边界 -> 压到下方
                dy2 = -(dy + bb.height * k)
            elif bb.y0 < ax_bb.y0:
                dy2 = (ax_bb.y0 - bb.y0) * k
            if dx or dy2:
                t.set_position((rad + dx, dy + dy2))

    def draw_placeholder(self, text: str = "请先载入数据"):
        self.ax.clear()
        self.ax.text(0.5, 0.5, text, ha="center", va="center",
                     transform=self.ax.transAxes, fontsize=13, color="#888780")
        self.ax.set_axis_off()
        self.canvas.draw_idle()
