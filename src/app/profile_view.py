"""沿河纵剖面：河底线 + 设计水面线 + 加高水面线 + 成灾水位线。

本视图**没有自己的选择控件**——直接跟随左侧「纵断面线」列表当前选中的那条重绘。
多条纵断面线（多条河流）在左侧列表里切换即可，不必在下拉框里再选一遍。

横轴里程来自「纵断面」块与横断面的交点位置，是实测精度。
"""

from __future__ import annotations

import numpy as np
from PySide6.QtWidgets import QVBoxLayout, QWidget

from core.model import Project, SectionResult, TerrainInfo

from .canvas_base import (COLOR_BED, COLOR_DESIGN, COLOR_DISASTER, COLOR_PLUS,
                          PlotPanel)


class ProfileView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Project | None = None
        self.results: dict[str, SectionResult] = {}
        self.infos: dict[str, TerrainInfo] = {}
        self.line_index = 0
        self.cfg = None   # 主窗口在创建/载入工程后注入（见 main_window）

        self.plot = PlotPanel(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.plot)

        self.plot.draw_placeholder()

    # ---------------- 外部接口 ----------------
    def set_data(self, project: Project | None,
                 results: dict[str, SectionResult],
                 infos: dict[str, TerrainInfo],
                 line_index: int | None = None):
        self.project = project
        self.results = results
        self.infos = infos
        if line_index is not None:
            self.line_index = line_index
        self.refresh()

    def set_line(self, index: int):
        """由主窗口在左侧纵断面线列表切换时调用。"""
        self.line_index = max(0, index)
        self.refresh()

    # ---------------- 绘制 ----------------
    def refresh(self):
        if self.project is None or not self.project.profile_lines:
            self.plot.draw_placeholder()
            return

        idx = min(max(self.line_index, 0), len(self.project.profile_lines) - 1)
        line = self.project.profile_lines[idx]

        ax = self.plot.ax
        ax.clear()
        ax.set_axis_on()

        # 河底线（来自「纵断面」块）
        prof = line.profile
        if prof is not None and prof.chainage:
            ch = np.asarray(prof.chainage, dtype=float)
            z = np.asarray(prof.z, dtype=float)
            order = np.argsort(ch)
            ax.plot(ch[order], z[order], color=COLOR_BED, lw=1.8, zorder=3,
                    label="河底深泓线（纵断面实测）")
            ax.fill_between(ch[order], z[order], float(z.min()) - 2,
                            color="#D3D1C7", alpha=0.35, zorder=1)

        # 各横断面处的水位
        chs, hs, h1s, dz, names = [], [], [], [], []
        for sec, c in zip(line.sections, line.chainage or []):
            res = self.results.get(sec.name)
            info = self.infos.get(sec.name)
            if res is None or info is None or c != c:
                continue
            chs.append(c)
            hs.append(res.design_level)
            h1s.append(res.design_level_plus)
            dz.append(info.disaster_level)
            names.append(sec.name)

        if chs:
            o = np.argsort(np.asarray(chs, dtype=float))
            xs = np.asarray(chs, dtype=float)[o]
            ax.plot(xs, np.asarray(hs, dtype=float)[o], color=COLOR_DESIGN, lw=1.6,
                    marker="o", ms=5, zorder=4, label="设计水面线 Hs")
            # 加高水面线：仅当 raise_enabled 时绘制（关闭则连同图例一起隐藏）。
            cfg = getattr(self, "cfg", None)
            raise_on = (cfg is not None) and cfg.raise_enabled
            h1arr = np.asarray(h1s, dtype=float)[o]
            if raise_on and np.isfinite(h1arr).any():
                lab = (f"加高水面线 (Hs+{cfg.raise_level:g})"
                       if cfg is not None else "加高水面线 Hs+1")
                ax.plot(xs, h1arr, color=COLOR_PLUS, lw=1.4,
                        ls="-.", marker="s", ms=4, zorder=4, label=lab)
            ax.plot(xs, np.asarray(dz, dtype=float)[o], color=COLOR_DISASTER, lw=1.4,
                    ls=":", marker="*", ms=8, zorder=4, label="成灾水位")
            for x, y, nm in zip(xs, np.asarray(hs, dtype=float)[o],
                                np.asarray(names, dtype=object)[o]):
                ax.annotate(str(nm), (x, y), textcoords="offset points",
                            xytext=(0, 8), ha="center", fontsize=8.5,
                            color=COLOR_DESIGN)

        ax.set_xlabel("里程 / 桩号 (m)")
        ax.set_ylabel("高程 (m)")

        origin_txt = {"start": "首点", "end": "末点", "lowest": "最低点"}.get(
            (prof.origin if prof else "lowest"), "")
        title = (f"纵剖面　{line.name}　（第 {idx + 1}/{len(self.project.profile_lines)} 条，"
                 f"桩号原点＝{origin_txt}，{len(line.sections)} 个横断面）")
        if chs:
            title += f"\n横断面里程 {min(chs):.0f} ~ {max(chs):.0f} m"
        ax.set_title(title)
        ax.grid(True, alpha=0.25, ls=":")

        if chs or prof is not None:
            ax.legend(fontsize=9, loc="best", framealpha=0.9)

        self.plot.redraw()
