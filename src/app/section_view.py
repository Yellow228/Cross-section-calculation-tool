"""断面形态图：地面线 + 水位填充 + 关键点标注 + 分区 + 水位滑块 + 图上拾取。

拾取模式（Q14）说明
------------------
「分区调节」与「成灾水位」两个面板都不提供数值输入，一切靠**在图上点测点**。
本视图承担拾取：进入模式后把全部实测测点画成可点的小圈，
鼠标移动时吸附到最近的测点并显示它的起点距 / 高程 / 测点序号，
左键点击即发出 `picked(目标, 索引)`。

⚠ 只能落在**实测测点**上，没有测点之间的位置。
   分区边界与深泓点在程序里都是"测点索引"，落在两点之间表达不出来；
   成灾水位也约定取自某个测点的高程，否则会出现界面上无法还原的状态。
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from core.geom import section_geom, wetted_polygons
from core.model import Section, SectionResult, TerrainInfo

from .canvas_base import (COLOR_BED, COLOR_DESIGN, COLOR_DISASTER, COLOR_PEAK,
                          COLOR_PICK, COLOR_PLUS, COLOR_THALWEG, COLOR_TURN,
                          COLOR_WARN, COLOR_WATER, PlotPanel)

#: 拾取目标 -> 中文名。键同时用作信号参数，务必与主窗口/面板保持一致。
PICK_LABELS = {
    "thalweg": "深泓点",
    "left": "左边界",
    "right": "右边界",
    "disaster": "成灾水位",
}

#: 吸附半径（屏幕像素）。太小则难点中，太大则容易选错相邻测点。
PICK_SNAP_PX = 14.0

COLOR_AUTO_MARK = "#888780"


class SectionView(QWidget):
    #: 拾取到测点：(目标, 测点索引)。目标取值见 PICK_LABELS 的键。
    picked = Signal(str, int)
    #: 用户按 Esc 或右键取消了拾取
    pickCancelled = Signal()
    #: 点到了图上，但没落在任何实测测点附近——不是静默，要给出提示。
    #  否则用户只会反复点、怀疑功能坏了，却不知道是没点准。
    pickMissed = Signal()
    #: 拾取模式开关变化（供面板同步提示条）
    pickModeChanged = Signal(object)   # str | None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sec: Section | None = None
        self.res: SectionResult | None = None
        self.info: TerrainInfo | None = None
        self.cfg = None   # 主窗口在创建/载入工程后注入（见 main_window）

        self._pick_mode: str | None = None
        self._hover_idx: int | None = None
        self._pick_cids: list[int] = []
        self._cursor_before = None

        self.plot = PlotPanel(self)

        # —— 水位滑块 ——
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(1000)
        self.slider.setValue(0)
        self.slider.valueChanged.connect(self._on_slider)
        self.lbl_level = QLabel("预览水位: —")
        self.chk_marks = QCheckBox("显示关键点与分区")
        self.chk_marks.setChecked(True)
        self.chk_marks.toggled.connect(lambda _: self.refresh())

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("水位预览"))
        ctrl.addWidget(self.slider, 1)
        ctrl.addWidget(self.lbl_level)
        ctrl.addWidget(self.chk_marks)

        btn_reset = QCheckBox("对齐到设计水位")
        btn_reset.setChecked(True)
        btn_reset.toggled.connect(self._reset_level)
        self.chk_reset = btn_reset
        ctrl.addWidget(btn_reset)

        # —— 拾取提示条（平时隐藏）——
        self.lbl_pick = QLabel("")
        self.lbl_pick.setWordWrap(True)
        self.lbl_pick.setStyleSheet(
            "background:#E6F1FB; color:#185FA5; padding:5px 8px; border-radius:5px;")
        self.lbl_pick.setVisible(False)
        self.btn_pick_cancel = QPushButton("取消拾取 (Esc)")
        self.btn_pick_cancel.setVisible(False)
        self.btn_pick_cancel.clicked.connect(self.cancel_pick)

        pick_row = QHBoxLayout()
        pick_row.addWidget(self.lbl_pick, 1)
        pick_row.addWidget(self.btn_pick_cancel)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.plot, 1)
        lay.addLayout(pick_row)
        lay.addLayout(ctrl)

        self.plot.draw_placeholder()

    # ---------------- 外部接口 ----------------
    def set_data(self, sec: Section | None, res: SectionResult | None,
                 info: TerrainInfo | None, chainage: float | None = None):
        self.sec, self.res, self.info = sec, res, info
        self._chainage = chainage
        if sec is None or info is None:
            self.plot.draw_placeholder()
            return
        self._reset_level(True)

    # ---------------- 拾取模式 ----------------
    @property
    def pick_mode(self) -> str | None:
        return self._pick_mode

    def begin_pick(self, mode: str) -> bool:
        """进入拾取模式。返回 False 表示当前没有断面、进不去。"""
        if mode not in PICK_LABELS or self.sec is None or self.info is None:
            return False
        # ⚠ 必须先把导航工具栏从平移/缩放模式退回空闲。
        #   工具栏处于这两种模式时会**吃掉左键点击**，但 motion 事件照样发出来，
        #   于是表现成"悬浮吸附还在、点击却毫无反应"——用户几乎不可能自查出来
        #   （光标只变成小手）。所以进入拾取时强制复位。
        self._reset_toolbar()
        self._pick_mode = mode
        self._hover_idx = None
        self._pick_cids = []
        c = self.plot.canvas
        self._pick_cids = [
            c.mpl_connect("motion_notify_event", self._on_motion),
            c.mpl_connect("button_press_event", self._on_click),
            c.mpl_connect("key_press_event", self._on_key),
        ]
        self._cursor_before = c.cursor()
        c.setCursor(Qt.CrossCursor)
        self.lbl_pick.setText(
            f"拾取模式：正在拾取【{PICK_LABELS[mode]}】—— 在图上点击一个测点即可，"
            f"按 Esc 或右键取消。只能落在实测测点上，鼠标靠近会自动吸附。")
        self.lbl_pick.setVisible(True)
        self.btn_pick_cancel.setVisible(True)
        self.pickModeChanged.emit(mode)
        self.refresh()
        return True

    def cancel_pick(self, notify: bool = True):
        if self._pick_mode is None:
            return
        mode = self._pick_mode
        self._pick_mode = None
        self._hover_idx = None
        for cid in self._pick_cids:
            try:
                self.plot.canvas.mpl_disconnect(cid)
            except Exception:                       # pragma: no cover
                pass
        self._pick_cids = []
        if self._cursor_before is not None:
            self.plot.canvas.setCursor(self._cursor_before)
            self._cursor_before = None
        self.lbl_pick.setVisible(False)
        self.btn_pick_cancel.setVisible(False)
        self.pickModeChanged.emit(None)
        if notify:
            self.pickCancelled.emit()
        if self.sec is not None:
            self.refresh()

    # ---------------- 拾取事件 ----------------
    def _toolbar_idle(self) -> bool:
        """导航工具栏处于平移/缩放模式时不吃点击。

        否则用户想缩放一下看看，左键一按就被当成拾取，白白改掉一个值。
        （`begin_pick` 里已强制复位过，这里只是兜底。）
        """
        try:
            return not self.plot.toolbar.mode
        except Exception:                           # pragma: no cover
            return True

    def _reset_toolbar(self):
        """把导航工具栏从平移/缩放模式退回空闲；本就空闲则什么都不做。

        `NavigationToolbar2.mode` 在不同 matplotlib 版本里形态不一
        （老版本是字符串 `"pan"`，3.6+ 是 `_Mode.PAN`），所以这里按
        **名字**判断而不比较值。
        """
        tb = self.plot.toolbar
        try:
            mode = getattr(tb, "mode", None)
            if not mode:
                return
            name = (getattr(mode, "name", None) or str(mode)).upper()
            if "PAN" in name:
                tb.pan()                            # 再调一次 = 退出该模式
            elif "ZOOM" in name:
                tb.zoom()
        except Exception:                           # pragma: no cover
            pass

    def _nearest_point(self, event) -> int | None:
        """屏幕坐标 -> 最近测点索引；超出吸附半径返回 None。"""
        if self.sec is None or event.x is None or event.y is None:
            return None
        ax = self.plot.ax
        s = np.asarray(self.sec.s, dtype=float)
        z = np.asarray(self.sec.z, dtype=float)
        if s.size == 0:
            return None
        # 一次性把全部测点变换到屏幕坐标，避免逐个 transform 的开销
        pts = ax.transData.transform(np.column_stack([s, z]))
        d = np.hypot(pts[:, 0] - event.x, pts[:, 1] - event.y)
        i = int(np.argmin(d))
        return i if d[i] <= PICK_SNAP_PX else None

    def _on_motion(self, event):
        if self._pick_mode is None:
            return
        idx = self._nearest_point(event)
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.refresh()

    def _on_click(self, event):
        if self._pick_mode is None:
            return
        if event.button == 3:                       # 右键：取消
            self.cancel_pick()
            return
        if event.button != 1 or not self._toolbar_idle():
            return
        # 优先用**当前正在吸附**的那一个：用户眼睛看到的是它，点下去就该是它。
        # 重新算一遍在"悬浮→点击"之间若图形被 tight_layout 轻微重排过，
        # 会算到相邻测点上去，表现为"点 A 却选中了 B"。
        idx = self._hover_idx
        if idx is None:
            idx = self._nearest_point(event)
        if idx is None:
            # 不静默：告诉用户没点准，并保持拾取模式让他再点一次
            self.pickMissed.emit()
            return
        mode = self._pick_mode
        self.cancel_pick(notify=False)
        self.picked.emit(mode, idx)

    def _on_key(self, event):
        if self._pick_mode is not None and event.key == "escape":
            self.cancel_pick()

    # ---------------- 水位 ----------------
    def _reset_level(self, on: bool):
        if not on or self.res is None or self.info is None:
            return
        lo, hi = self.info.dmin, self.info.zymin
        if hi <= lo:
            return
        target = self.res.design_level
        if not (lo <= target <= hi):
            target = self.res.design_level_plus
        if not (lo <= target <= hi):
            target = lo + (hi - lo) * 0.5
        self.slider.setValue(int(round((target - lo) / (hi - lo) * 1000)))
        self.refresh()

    def _current_level(self) -> float:
        if self.info is None:
            return 0.0
        lo, hi = self.info.dmin, self.info.zymin
        return lo + (hi - lo) * self.slider.value() / 1000.0

    def _on_slider(self, _):
        self.refresh()

    # ---------------- 绘制 ----------------
    def refresh(self):
        if self.sec is None:
            self.plot.draw_placeholder()
            return

        sec, res, info = self.sec, self.res, self.info
        H = self._current_level()
        ax = self.plot.ax
        ax.clear()
        ax.set_axis_on()

        s = np.asarray(sec.s, dtype=float)
        z = np.asarray(sec.z, dtype=float)

        # 地面线
        ax.plot(s, z, color=COLOR_BED, lw=1.8, zorder=4, label="断面地面线")

        # 水面填充：用真正的插值交点围出过水区域。
        # 曾用 fill_between(s, minimum(z,H), H, where=z<=H)——minimum 会把岸上地形
        # 截断成水位高度，下边界随之变成"截断后的地形"，水面就越过真实交点一路
        # 漫到最外侧采样点，看起来两岸都被淹了。
        polys = wetted_polygons(sec.s, sec.z, H)
        for k, poly in enumerate(polys):
            ax.fill([p[0] for p in poly], [p[1] for p in poly],
                    color=COLOR_WATER, alpha=0.32, zorder=2,
                    label="过水断面" if k == 0 else None)
            for pt in (poly[0], poly[-1]):
                ax.plot(pt[0], H, marker="o", ms=4.5, color=COLOR_WATER,
                        mec="white", mew=0.8, zorder=5)
        ax.axhline(H, color=COLOR_WATER, lw=1.2, ls="-", alpha=0.85, zorder=3,
                   label=f"预览水位 {H:.2f} m")

        if res is not None:
            self._draw_levels(ax, sec, res, info)

        if self.chk_marks.isChecked() or self._pick_mode is not None:
            self._draw_marks(ax, sec, info)

        if self._pick_mode is not None:
            self._draw_pick_layer(ax, sec)

        # 信息框只保留"当前水位下的断面几何量"四项（用户要求）。
        # 分区糙率、手动设定一览等都不在这里显示——那些信息在「分区调节」面板
        # 与状态表里，且断面图本身已用不同记号标出了边界与转折点。
        #
        # ⚠ 信息框是 matplotlib 的 ax.text 画的，**不解析 HTML**：
        #   里面放 <span style=...> / <b> 会原样显示成标签文字（踩过）。
        A, P, B = section_geom(sec.s, sec.z, H)
        txt = (f"当前水位 {H:.2f} m\n"
               f"过水面积 A={A:.2f} m²\n"
               f"湿周 P={P:.2f} m\n"
               f"顶宽 B={B:.2f} m")

        ax.text(0.015, 0.97, txt, transform=ax.transAxes, va="top", ha="left",
                fontsize=10, color="#2C2C2A",
                bbox=dict(boxstyle="round,pad=0.5", fc="#F1EFE8", ec="#B4B2A9", lw=0.6))

        # 无单调告警标记
        if res is not None and res.warnings:
            ax.text(0.985, 0.03, "\n".join(res.warnings), transform=ax.transAxes,
                    va="bottom", ha="right", fontsize=9, color=COLOR_WARN,
                    bbox=dict(boxstyle="round,pad=0.4", fc="#FCEBEB", ec=COLOR_WARN, lw=0.6))

        ax.set_xlabel("起点距 (m)")
        ax.set_ylabel("高程 (m)")
        chainage = getattr(self, "_chainage", None)
        title = f"断面 {sec.name}"
        if chainage is not None and chainage == chainage:
            title += f"　桩号 {chainage:.1f} m"
        title += f"　[{ '复式断面' if len(info.zones) > 1 else '单断面' }]"
        # 标题里保留"含手动设定"标记：信息框虽然只显示当前水位下的四项几何量，
        # 但"这个断面有人工干预"是影响结果解读的关键信息，标在标题上不占地方。
        if sec.has_manual:
            title += "　（含手动设定）"
        ax.set_title(title)
        ax.grid(True, alpha=0.25, ls=":")
        self.lbl_level.setText(f"预览水位: {H:.2f} m")
        self.plot.redraw()

    # ---------------- 绘制：水位线 ----------------
    def _draw_levels(self, ax, sec, res, info):
        """设计水位 / 加高水位 / 成灾水位。

        成灾水位用线型区分手动与自动：手动是实线并标注"（手动）"。
        两种都画成同一颜色，因为它们的物理含义相同，只是来源不同。

        加高水位线是否绘制由 cfg.raise_enabled 控制（默认开）；关闭后只隐藏
        这条线，不影响计算结果（Hs1 仍 = Hs + 加高幅度）。标签动态显示幅度。
        """
        cfg = getattr(self, "cfg", None)
        raise_on = (cfg is not None) and cfg.raise_enabled
        raise_lab = (f"加高水位 (Hs+{cfg.raise_level:g})"
                     if cfg is not None else "加高水位 Hs+1")

        manual_dis = sec.disaster_idx_manual is not None
        rows = [(res.design_level, COLOR_DESIGN, "设计水位 Hs", "--")]
        if raise_on:
            rows.append((res.design_level_plus, COLOR_PLUS, raise_lab, "-."))
        if info.disaster_level == info.disaster_level:
            if manual_dis:
                rows.append((info.disaster_level, COLOR_DISASTER, "成灾水位（手动）", "-"))
            else:
                rows.append((info.disaster_level, COLOR_DISASTER, "成灾水位", ":"))
        for y, c, lab, ls in rows:
            if y == y:
                ax.axhline(y, color=c, lw=1.3 if manual_dis and c == COLOR_DISASTER else 1.1,
                           ls=ls, alpha=0.9, zorder=1, label=lab)

    # ---------------- 绘制：关键点与分区 ----------------
    def _draw_marks(self, ax, sec, info):
        s = np.asarray(sec.s, dtype=float)
        z = np.asarray(sec.z, dtype=float)
        n = len(s)

        # —— 分区边界 ——
        # 决定分区（进而决定各分区糙率取值）。**默认与转折点重合**，
        # 所以自动情况下这里只画一条虚竖线，看起来与以前一样。
        # 人工指定后画实线 + 实心方块，与转折点区分开（Q15：两者可以不同）。
        #
        # 注意竖线画在**边界点**上，不能用分区起点索引（zones[k][0]）——
        # 分区为了共享边界点是 {1:i1, i1:i2, i2:n} 的写法，第二个分区的起点是
        # i1+1，直接拿来画线会比边界点偏出 0.7~8.5 m。
        left_manual = sec.zone_manual and sec.zone_left is not None
        right_manual = sec.zone_manual and sec.zone_right is not None
        for idx, is_manual, lab in ((info.zone_left_idx, left_manual, "左边界"),
                                    (info.zone_right_idx, right_manual, "右边界")):
            if idx is None or not (0 <= idx < n):
                continue
            if is_manual:
                ax.axvline(s[idx], color=COLOR_PICK, lw=1.7, ls="-",
                           alpha=0.9, zorder=1)
                ax.plot(s[idx], z[idx], marker="s", ms=8.5, color=COLOR_PICK,
                        mec="white", mew=1.0, zorder=7)
                ax.annotate(lab + "（手动）", (s[idx], z[idx]),
                            textcoords="offset points", xytext=(0, -18),
                            ha="center", fontsize=9, color="#185FA5")
            else:
                ax.axvline(s[idx], color="#85B7EB", lw=1.0, ls="--",
                           alpha=0.9, zorder=1)

        # —— 转折点：**永远自动、只读**，成灾水位由它决定 ——
        # 与分区边界是两个概念，所以两者可能落在不同位置，各自画各自的。
        for idx, lab in ((info.left_turn_idx, "左转折"),
                         (info.right_turn_idx, "右转折")):
            if idx is None or not (0 <= idx < n):
                continue
            ax.plot(s[idx], z[idx], marker="v", ms=9, color=COLOR_TURN,
                    mec="white", mew=1.0, zorder=6)
            ax.annotate(lab, (s[idx], z[idx]), textcoords="offset points",
                        xytext=(0, 12), ha="center", fontsize=9, color=COLOR_TURN)

        # —— 深泓点 ——
        di = info.dmin_idx
        th_manual = sec.thalweg_manual is not None
        if 0 <= di < n:
            if th_manual:
                ax.axvline(s[di], color=COLOR_THALWEG, lw=1.4, ls="-",
                           alpha=0.85, zorder=1)
            ax.plot(s[di], z[di], marker="s" if th_manual else "o", ms=8,
                    color=COLOR_THALWEG, mec="white", mew=1.0, zorder=6)
            ax.annotate("深泓点（手动）" if th_manual else "深泓点",
                        (s[di], z[di]), textcoords="offset points",
                        xytext=(0, -16), ha="center", fontsize=9, color=COLOR_THALWEG)

        # —— 断面真实最低测点（仅当深泓点被手动改到别处时才画，供对照）——
        ai = info.dmin_auto_idx
        if th_manual and 0 <= ai < n and ai != di:
            ax.plot(s[ai], z[ai], marker="o", ms=8, mfc="white",
                    mec=COLOR_AUTO_MARK, mew=1.6, zorder=6)
            ax.annotate("最低测点", (s[ai], z[ai]), textcoords="offset points",
                        xytext=(0, -16), ha="center", fontsize=9,
                        color=COLOR_AUTO_MARK)

        # —— 岸顶 ——
        for idx, lab in ((info.zmax_idx, "左岸顶"), (info.ymax_idx, "右岸顶")):
            if idx is not None and 0 <= idx < n:
                ax.plot(s[idx], z[idx], marker="^", ms=8, color=COLOR_PEAK,
                        mec="white", mew=1.0, zorder=6)
                ax.annotate(lab, (s[idx], z[idx]), textcoords="offset points",
                            xytext=(0, 12), ha="center", fontsize=9, color=COLOR_PEAK)

        # —— 成灾水位点 ——
        xi = info.disaster_idx
        if 0 <= xi < n:
            manual_dis = sec.disaster_idx_manual is not None
            ax.plot(s[xi], z[xi], marker="D" if manual_dis else "*",
                    ms=9 if manual_dis else 14, color=COLOR_DISASTER,
                    mec="white", mew=1.0, zorder=7)

    # ---------------- 绘制：拾取图层 ----------------
    def _draw_pick_layer(self, ax, sec):
        s = np.asarray(sec.s, dtype=float)
        z = np.asarray(sec.z, dtype=float)
        if s.size == 0:
            return

        # 全部实测测点画成可点的小圈
        ax.plot(s, z, ls="none", marker="o", ms=5.5, mfc="white",
                mec=COLOR_AUTO_MARK, mew=1.1, zorder=8)

        hi = self._hover_idx
        if hi is None or not (0 <= hi < len(s)):
            return
        ax.axvline(s[hi], color=COLOR_PICK, lw=1.0, ls=":", alpha=0.8, zorder=7)
        ax.plot([s[hi]], [z[hi]], marker="o", ms=8.5, color=COLOR_PICK,
                mec="white", mew=1.6, zorder=9)
        txt = (f"起点距 {s[hi]:.1f} m　高程 {z[hi]:.2f} m\n"
               f"点击设为{PICK_LABELS[self._pick_mode]}（第 {hi + 1} 个测点）")
        ax.annotate(txt, (s[hi], z[hi]), textcoords="offset points",
                    xytext=(14, 18), fontsize=9, color="#185FA5",
                    bbox=dict(boxstyle="round,pad=0.45", fc="#E6F1FB",
                              ec="#85B7EB", lw=0.8), zorder=10)
