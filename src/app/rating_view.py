"""交互式水位–流量关系曲线：悬停读数 + 多断面叠加 + 非单调异常段标记。"""

from __future__ import annotations

import numpy as np
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from core.model import Section, SectionResult, TerrainInfo

from .canvas_base import (COLOR_DESIGN, COLOR_DISASTER, COLOR_WARN, PlotPanel)

CURVE_COLORS = ["#185FA5", "#0F6E56", "#993C1D", "#534AB7", "#854F0B",
                "#993556", "#3B6D11", "#185FA5"]


class RatingView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[tuple[Section, SectionResult, TerrainInfo]] = []
        self.current_key: str | None = None
        self._curves: list[dict] = []

        self.plot = PlotPanel(self)
        self.annot = None

        self.chk_overlay = QCheckBox("叠加显示全部断面")
        self.chk_overlay.toggled.connect(lambda _: self.refresh())
        self.chk_abnormal = QCheckBox("标记非单调异常段")
        self.chk_abnormal.setChecked(True)
        self.chk_abnormal.toggled.connect(lambda _: self.refresh())
        self.lbl_hint = QLabel("提示：鼠标在曲线上移动可读取 H / Q / A / P / B")

        ctrl = QHBoxLayout()
        ctrl.addWidget(self.chk_overlay)
        ctrl.addWidget(self.chk_abnormal)
        ctrl.addStretch(1)
        ctrl.addWidget(self.lbl_hint)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.plot, 1)
        lay.addLayout(ctrl)

        self.plot.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.plot.draw_placeholder()

    # ---------------- 外部接口 ----------------
    def set_data(self, items: list[tuple[Section, SectionResult, TerrainInfo]],
                 current_key: str | None):
        self.items = items
        self.current_key = current_key
        self.refresh()

    # ---------------- 绘制 ----------------
    def refresh(self):
        if not self.items:
            self.plot.draw_placeholder()
            return

        ax = self.plot.ax
        ax.clear()
        ax.set_axis_on()
        self._curves = []

        chosen = self.items if self.chk_overlay.isChecked() else [
            it for it in self.items if it[0].name == self.current_key] or self.items

        plotted = 0
        for k, (sec, res, info) in enumerate(chosen):
            if not res.hvec:
                continue
            # 参数没填时 Q 全是 NaN，画不出曲线，直接跳过并给出提示
            if not any(q == q for q in res.qvec):
                continue
            plotted += 1
            c = CURVE_COLORS[k % len(CURVE_COLORS)]
            lw = 2.0 if sec.name == self.current_key else 1.1
            alpha = 1.0 if (sec.name == self.current_key or len(chosen) <= 3) else 0.45
            ax.plot(res.qvec, res.hvec, color=c, lw=lw, alpha=alpha, label=sec.name)

            # 异常段（Q 下降）
            if self.chk_abnormal.isChecked():
                for i in range(1, len(res.qvec)):
                    if res.qvec[i] < res.qvec[i - 1]:
                        ax.plot(res.qvec[i - 1:i + 1], res.hvec[i - 1:i + 1],
                                color=COLOR_WARN, lw=3.0, alpha=0.8,
                                solid_capstyle="round", zorder=5)

            self._curves.append({
                "name": sec.name, "color": c,
                "q": np.asarray(res.qvec, dtype=float),
                "h": np.asarray(res.hvec, dtype=float),
                "a": np.asarray(res.avec, dtype=float),
                "p": np.asarray(res.pvec, dtype=float),
                "b": np.asarray(res.bvec, dtype=float),
            })

            # 设计点 / 成灾点：标记全画，但只有当前断面写文字，
            # 否则多断面叠加时标注会糊成一片
            is_current = (sec.name == self.current_key) or len(chosen) == 1

            if sec.params.design_q == sec.params.design_q and res.design_level == res.design_level:
                ax.plot([sec.params.design_q], [res.design_level], marker="o", ms=7,
                        color=COLOR_DESIGN, mec="white", mew=1.0, zorder=6)
                if is_current:
                    ax.annotate(f"{sec.name}\n设计 Q={sec.params.design_q:g}  H={res.design_level:.2f}",
                                (sec.params.design_q, res.design_level),
                                textcoords="offset points", xytext=(10, -22),
                                fontsize=9, color=COLOR_DESIGN)
            if info.disaster_level == info.disaster_level and res.disaster_flow == res.disaster_flow:
                ax.plot([res.disaster_flow], [info.disaster_level], marker="*", ms=12,
                        color=COLOR_DISASTER, mec="white", mew=1.0, zorder=6)
                if is_current:
                    ax.annotate(f"成灾 H={info.disaster_level:.2f}  Q={res.disaster_flow:.1f}",
                                (res.disaster_flow, info.disaster_level),
                                textcoords="offset points", xytext=(10, 6),
                                fontsize=9, color=COLOR_DISASTER)

        if plotted == 0:
            ax.text(0.5, 0.5, "参数（糙率 / 比降 / 设计流量）尚未填写，无法生成曲线\n"
                              "请在左下角「参数面板」中填写并应用",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=12, color="#A32D2D")
            ax.set_axis_off()
            self.plot.redraw()
            return

        self.annot = ax.annotate("", xy=(0, 0), xytext=(14, 14),
                                 textcoords="offset points", fontsize=9,
                                 bbox=dict(boxstyle="round,pad=0.45",
                                           fc="#F1EFE8", ec="#B4B2A9", lw=0.6),
                                 arrowprops=dict(arrowstyle="->", color="#5F5E5A", lw=0.8))
        self.annot.set_visible(False)

        ax.set_xlabel("流量 Q (m³/s)")
        ax.set_ylabel("水位 H (m)")
        ax.set_title("水位–流量关系曲线" + ("（全部断面叠加）" if len(chosen) > 1 else ""))
        ax.grid(True, alpha=0.25, ls=":")
        if len(chosen) > 1:
            ax.legend(fontsize=9, loc="lower right", framealpha=0.9)
        self.plot.redraw()

    # ---------------- 悬停读数 ----------------
    def _on_motion(self, event):
        if self.annot is None or not self._curves or event.inaxes is not self.plot.ax:
            if self.annot is not None:
                self.annot.set_visible(False)
                self.plot.canvas.draw_idle()
            return

        best = None
        for cv in self._curves:
            d = np.hypot((cv["q"] - event.xdata), (cv["h"] - event.ydata) * 20)
            i = int(np.argmin(d))
            if best is None or d[i] < best[0]:
                best = (d[i], cv, i)

        if best is None or best[0] > 60:
            self.annot.set_visible(False)
            self.plot.canvas.draw_idle()
            return

        _, cv, i = best
        self.annot.xy = (cv["q"][i], cv["h"][i])
        self.annot.set_text(f"{cv['name']}\nH = {cv['h'][i]:.2f} m\nQ = {cv['q'][i]:.2f} m³/s\n"
                            f"A = {cv['a'][i]:.2f} m²\nP = {cv['p'][i]:.2f} m\nB = {cv['b'][i]:.2f} m")
        self.annot.set_visible(True)
        self.plot.canvas.draw_idle()
