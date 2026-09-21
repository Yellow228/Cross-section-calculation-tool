"""matplotlib 画布基类：中文字体、Qt 嵌入、导航工具栏。"""

from __future__ import annotations

import matplotlib
from matplotlib.backends.backend_qtagg import (FigureCanvasQTAgg,
                                               NavigationToolbar2QT)
from matplotlib.figure import Figure
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
        self.canvas.fig.tight_layout()
        self.canvas.draw_idle()

    def draw_placeholder(self, text: str = "请先载入数据"):
        self.ax.clear()
        self.ax.text(0.5, 0.5, text, ha="center", va="center",
                     transform=self.ax.transAxes, fontsize=13, color="#888780")
        self.ax.set_axis_off()
        self.canvas.draw_idle()
