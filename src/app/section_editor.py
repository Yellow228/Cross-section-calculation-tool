"""横断面编辑器：左侧测点表格 + 右侧可拖动的断面图（新窗口，非模态）。

交互约定
--------
* **双向同步**：改表格立刻重画；拖图上测点立刻改表格。
* **默认只改高程**：起点距一旦被横向误拖，图上看着只是"挪了一点"，
  实际整条起点距序列已经被改坏，极难自查。需要改起点距时取消底部勾选。
* **松手才重算**：拖动中每帧都跑一遍曼宁公式会掉帧，所以拖动过程只重画，
  松开鼠标才发 `edited` 让主窗口重算。表格改完是单次事件，直接重算。

⚠ 与 `section_view.py` 共用两条经验
-----------------------------------
1. 导航工具栏处于平移/缩放模式时会**吃掉左键点击**，
   所以按下前必须判 `_toolbar_idle()`，否则表现为"点了没反应"。
2. 吸附用屏幕像素距离，不能拿数据坐标比——横向几十米、纵向几米，
   数据坐标下的"距离"完全不是眼睛看到的距离。
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog,
                               QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from core import edit as edit_mod
from core.model import Section

from .canvas_base import COLOR_BED, COLOR_PICK, PlotPanel

#: 吸附半径（屏幕像素）
SNAP_PX = 16.0

COL_NO, COL_S, COL_Z = 0, 1, 2

#: 撤销栈深度上限。断面点数本来就不多，多留几步没有成本。
UNDO_LIMIT = 50

HINT = ("拖动图上的测点即可修改；改表格同样立即生效。"
        "灰色虚线是打开时的原始断面，便于对照。")


class SectionEditorDialog(QDialog):
    """编辑单个横断面的测点。

    只认主窗口推进来的那一个 `Section` 对象，**原地修改**它。
    主窗口负责重算与标脏，这里只发 `edited` / `notice`。
    """

    #: 几何已改动，主窗口需要重算（重算结果 + 桩号 + 标脏）
    edited = Signal()
    #: 需要告诉用户的话（例如"手动深泓点被清空了"），交给主窗口状态栏
    notice = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑横断面")
        self.resize(980, 640)

        self.sec: Section | None = None
        self._chainage: float | None = None
        self._base: dict | None = None      # 打开时的快照（"还原"用）
        self._cur: dict | None = None       # 最近一次提交后的状态（撤销用）
        self._undo: list[dict] = []
        self._redo: list[dict] = []

        self._drag_idx: int | None = None
        self._hover_idx: int | None = None
        self._drag_dirty = False
        self._loading = False               # 回填表格时屏蔽 itemChanged

        self.lbl_head = QLabel("—")
        self.lbl_head.setStyleSheet("font-size:13px; font-weight:500;")

        # ---- 左：测点表格 ----
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "起点距 (m)", "高程 (m)"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_NO, QHeaderView.ResizeToContents)
        for c in (COL_S, COL_Z):
            self.table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._on_cell_changed)

        self.chk_lock = QCheckBox("只改高程（锁定起点距）")
        self.chk_lock.setChecked(True)
        self.chk_lock.setToolTip(
            "勾选时拖动只改高程；取消后可同时改起点距，"
            "平面坐标 x/y 会按断面走向同步重算")
        self.chk_lock.toggled.connect(self._apply_lock)

        self.btn_insert = QPushButton("插入测点")
        self.btn_insert.setToolTip("在当前行之后插入一个测点（形态不变，只是加点）")
        self.btn_insert.clicked.connect(self._insert)
        self.btn_delete = QPushButton("删除测点")
        self.btn_delete.clicked.connect(self._delete)
        self.btn_undo = QPushButton("撤销")
        self.btn_undo.clicked.connect(self._undo_step)
        self.btn_redo = QPushButton("重做")
        self.btn_redo.clicked.connect(self._redo_step)
        self.btn_reset = QPushButton("还原")
        self.btn_reset.setToolTip("回到打开本窗口时的状态")
        self.btn_reset.clicked.connect(self._reset_all)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.close)

        left = QWidget()
        lay_l = QVBoxLayout(left)
        lay_l.setContentsMargins(0, 0, 0, 0)
        lay_l.addWidget(self.table, 1)
        lay_l.addWidget(self.chk_lock)
        row = QHBoxLayout()
        for b in (self.btn_insert, self.btn_delete):
            row.addWidget(b)
        lay_l.addLayout(row)

        # ---- 右：断面图 ----
        self.plot = PlotPanel(self)
        c = self.plot.canvas
        c.mpl_connect("motion_notify_event", self._on_motion)
        c.mpl_connect("button_press_event", self._on_press)
        c.mpl_connect("button_release_event", self._on_release)

        self.split = QSplitter(Qt.Horizontal)
        self.split.addWidget(left)
        self.split.addWidget(self.plot)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([300, 660])

        self.lbl_note = QLabel(HINT)
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet("color:#888780; font-size:12px;")

        row2 = QHBoxLayout()
        for b in (self.btn_undo, self.btn_redo, self.btn_reset, self.btn_close):
            row2.addWidget(b)

        lay = QVBoxLayout(self)
        lay.addWidget(self.lbl_head)
        lay.addWidget(self.split, 1)
        lay.addWidget(self.lbl_note)
        lay.addLayout(row2)

        self.plot.draw_placeholder("请先在左侧选中一个横断面")

    # ---------------- 外部接口 ----------------
    def set_context(self, sec: Section | None, chainage: float | None = None):
        """主窗口在弹出前把当前断面推进来。"""
        self.sec = sec
        self._chainage = chainage
        self._drag_idx = None
        self._hover_idx = None
        self._drag_dirty = False
        self._undo.clear()
        self._redo.clear()
        if sec is None:
            self._base = self._cur = None
            self.table.setRowCount(0)
            self.plot.draw_placeholder("请先在左侧选中一个横断面")
            self.lbl_head.setText("—")
            self._set_note(HINT)
            return
        self._base = edit_mod.snapshot(sec)
        self._cur = edit_mod.snapshot(sec)
        self._fill_table()
        self._apply_lock()
        self._refresh_plot()
        self._update_head()

    def popup(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def set_chainage(self, chainage: float | None):
        """主窗口重算桩号后把新值推回来（只更新标题，不动数据）。"""
        self._chainage = chainage
        self._update_head()

    # ---------------- 表格 ----------------
    def _fill_table(self):
        sec = self.sec
        if sec is None:
            return
        self._loading = True
        self.table.blockSignals(True)
        self.table.setRowCount(sec.n_points)
        for i in range(sec.n_points):
            self._set_row(i)
        self.table.blockSignals(False)
        self._loading = False
        if sec.n_points:
            self.table.setCurrentCell(0, COL_Z)

    def _set_row(self, i: int):
        """写第 i 行。**不带** blockSignals，调用方自己负责。"""
        sec = self.sec
        it_no = QTableWidgetItem(str(i + 1))
        it_no.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        it_no.setForeground(QColor("#888780"))
        it_s = QTableWidgetItem(f"{sec.s[i]:.2f}")
        it_z = QTableWidgetItem(f"{sec.z[i]:.2f}")
        self.table.setItem(i, COL_NO, it_no)
        self.table.setItem(i, COL_S, it_s)
        self.table.setItem(i, COL_Z, it_z)
        self._apply_row_flags(i)

    def _apply_lock(self):
        """「只改高程」勾上时，起点距列整列只读。"""
        self._loading = True
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            self._apply_row_flags(i)
        self.table.blockSignals(False)
        self._loading = False

    def _apply_row_flags(self, i: int):
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        for col in (COL_S, COL_Z):
            it = self.table.item(i, col)
            if it is None:
                continue
            locked = (col == COL_S) and self.chk_lock.isChecked()
            it.setFlags(flags if locked else flags | Qt.ItemIsEditable)

    def _sync_row(self, i: int):
        """把第 i 个测点的当前值回填表格（拖动时用）。"""
        if self.sec is None or not (0 <= i < self.table.rowCount()):
            return
        self._loading = True
        self.table.blockSignals(True)
        it_s = self.table.item(i, COL_S)
        it_z = self.table.item(i, COL_Z)
        if it_s is not None:
            it_s.setText(f"{self.sec.s[i]:.2f}")
        if it_z is not None:
            it_z.setText(f"{self.sec.z[i]:.2f}")
        self.table.blockSignals(False)
        self._loading = False

    def _on_cell_changed(self, item):
        if self._loading or self.sec is None:
            return
        row, col = item.row(), item.column()
        if col not in (COL_S, COL_Z):
            return
        try:
            v = float(item.text())
        except ValueError:
            self._set_note(f"第 {row + 1} 行填的不是数字，已还原。")
            self._sync_row(row)
            return
        if v != v:
            self._sync_row(row)
            return
        self._push_undo(self._cur)
        if col == COL_Z:
            edit_mod.set_z(self.sec, row, v)
        else:
            applied = edit_mod.set_s(self.sec, row, v)
            if abs(applied - v) > 1e-9:
                self._set_note(
                    f"第 {row + 1} 点的起点距被夹到 {applied:.2f} m——"
                    f"起点距必须严格递增，不能越过相邻测点。")
        self._cur = edit_mod.snapshot(self.sec)
        self._sync_row(row)
        self._refresh_plot()
        self.edited.emit()

    # ---------------- 增删测点 ----------------
    def _insert(self):
        if self.sec is None:
            return
        row = self.table.currentRow()
        at = (row + 1) if row >= 0 else self.sec.n_points
        self._push_undo(self._cur)
        idx = edit_mod.insert_point(self.sec, at)
        self._cur = edit_mod.snapshot(self.sec)
        self._fill_table()
        self._apply_lock()
        self.table.setCurrentCell(idx, COL_Z)
        self._refresh_plot()
        self._update_head()
        self.edited.emit()
        self._set_note(f"已在第 {idx + 1} 个位置插入测点（共 "
                       f"{self.sec.n_points} 点）。")

    def _delete(self):
        if self.sec is None:
            return
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先在表格里选中要删除的测点。")
            return
        if self.sec.n_points <= 2:
            QMessageBox.information(self, "提示",
                                    "至少要保留 2 个测点，不能再删了。")
            return
        self._push_undo(self._cur)
        cleared = edit_mod.delete_point(self.sec, row)
        self._cur = edit_mod.snapshot(self.sec)
        self._fill_table()
        self._apply_lock()
        if row < self.table.rowCount():
            self.table.setCurrentCell(row, COL_Z)
        self._refresh_plot()
        self._update_head()
        self.edited.emit()
        if cleared:
            msg = ("删除的正是被指定的测点，以下手动设定已清空、改回自动："
                   + "、".join(cleared))
            self._set_note(msg)
            self.notice.emit(msg)
        else:
            self._set_note(f"已删除第 {row + 1} 个测点（剩 "
                           f"{self.sec.n_points} 点）。")

    # ---------------- 撤销 / 重做 / 还原 ----------------
    def _push_undo(self, snap):
        if snap is None:
            return
        self._undo.append(snap)
        if len(self._undo) > UNDO_LIMIT:
            self._undo.pop(0)
        self._redo.clear()

    def _apply_snapshot(self, snap):
        if self.sec is None or snap is None:
            return
        edit_mod.restore(self.sec, snap)
        self._cur = edit_mod.snapshot(self.sec)
        self._fill_table()
        self._apply_lock()
        self._refresh_plot()
        self._update_head()
        self.edited.emit()

    def _undo_step(self):
        if not self._undo or self.sec is None:
            return
        self._redo.append(edit_mod.snapshot(self.sec))
        self._apply_snapshot(self._undo.pop())

    def _redo_step(self):
        if not self._redo or self.sec is None:
            return
        self._undo.append(edit_mod.snapshot(self.sec))
        self._apply_snapshot(self._redo.pop())

    def _reset_all(self):
        if self.sec is None or self._base is None:
            return
        self._push_undo(edit_mod.snapshot(self.sec))
        self._apply_snapshot(self._base)
        self._set_note("已还原到打开本窗口时的状态。")

    # ---------------- 图上拖动 ----------------
    def _toolbar_idle(self) -> bool:
        try:
            return not self.plot.toolbar.mode
        except Exception:                   # pragma: no cover
            return True

    def _nearest_point(self, event) -> int | None:
        if self.sec is None or event.x is None or event.y is None:
            return None
        s = np.asarray(self.sec.s, dtype=float)
        z = np.asarray(self.sec.z, dtype=float)
        if s.size == 0:
            return None
        pts = self.plot.ax.transData.transform(np.column_stack([s, z]))
        d = np.hypot(pts[:, 0] - event.x, pts[:, 1] - event.y)
        i = int(np.argmin(d))
        return i if d[i] <= SNAP_PX else None

    def _on_motion(self, event):
        if self.sec is None or not self._toolbar_idle():
            return
        if self._drag_idx is not None:
            if event.xdata is None or event.ydata is None:
                return
            i = self._drag_idx
            if not self._drag_dirty:
                self._push_undo(self._cur)
                self._drag_dirty = True
            if self.chk_lock.isChecked():
                edit_mod.set_z(self.sec, i, event.ydata)
            else:
                edit_mod.set_s(self.sec, i, event.xdata)
                edit_mod.set_z(self.sec, i, event.ydata)
            self._sync_row(i)
            self._refresh_plot()
            return
        idx = self._nearest_point(event)
        if idx != self._hover_idx:
            self._hover_idx = idx
            self._refresh_plot()

    def _on_press(self, event):
        if self.sec is None or event.button != 1 or event.x is None:
            return
        if not self._toolbar_idle():
            self._set_note("导航工具栏正在平移/缩放，先退出该模式才能拖动测点。")
            return
        # 用正在吸附的那一个：用户眼睛看到的是它，按下去就该是它
        idx = self._hover_idx
        if idx is None:
            idx = self._nearest_point(event)
        if idx is None:
            return
        self._drag_idx = idx
        self._drag_dirty = False
        self._refresh_plot()

    def _on_release(self, event):
        if self._drag_idx is None:
            return
        self._drag_idx = None
        if self._drag_dirty:
            self._cur = edit_mod.snapshot(self.sec)
            self._drag_dirty = False
            self.edited.emit()
        self._refresh_plot()

    # ---------------- 绘制 ----------------
    def _refresh_plot(self):
        sec = self.sec
        if sec is None:
            self.plot.draw_placeholder("请先在左侧选中一个横断面")
            return
        ax = self.plot.ax
        ax.clear()
        ax.set_axis_on()

        s = np.asarray(sec.s, dtype=float)
        z = np.asarray(sec.z, dtype=float)

        # 原始断面做对照：改了哪里一眼看得出来。
        # 不画水位线——拖动过程中结果还没重算，画出来会让人误以为水位不变。
        base = self._base
        if base is not None and len(base["s"]) == len(s):
            ax.plot(base["s"], base["z"], color="#B4B2A9", lw=1.2, ls="--",
                    zorder=1, label="原始断面")
        ax.plot(s, z, color=COLOR_BED, lw=1.8, zorder=3, label="当前断面")
        ax.plot(s, z, ls="none", marker="o", ms=5.0, mfc="white",
                mec="#888780", mew=1.1, zorder=5)

        i = self._drag_idx if self._drag_idx is not None else self._hover_idx
        if i is not None and 0 <= i < len(s):
            ax.plot([s[i]], [z[i]], marker="o", ms=9.0, color=COLOR_PICK,
                    mec="white", mew=1.6, zorder=8)
            ax.annotate(f"第 {i + 1} 点　起点距 {s[i]:.2f} m　高程 {z[i]:.2f} m",
                        (s[i], z[i]), textcoords="offset points", xytext=(12, 16),
                        fontsize=9, color="#185FA5",
                        bbox=dict(boxstyle="round,pad=0.45", fc="#E6F1FB",
                                  ec="#85B7EB", lw=0.8), zorder=9)

        ax.set_xlabel("起点距 (m)")
        ax.set_ylabel("高程 (m)")
        title = f"{sec.name}　{len(s)} 个测点"
        ch = self._chainage
        if ch is not None and ch == ch:
            title += f"　桩号 {ch:.1f} m"
        if sec.has_manual:
            title += "　（含手动设定）"
        ax.set_title(title)
        ax.grid(True, alpha=0.25, ls=":")
        ax.legend(loc="upper right", fontsize=9, framealpha=0.95)
        self.plot.redraw()

    # ---------------- 杂项 ----------------
    def _update_head(self):
        sec = self.sec
        if sec is None:
            self.lbl_head.setText("—")
            return
        ch = self._chainage
        txt = f"编辑横断面：{sec.name}"
        if ch is not None and ch == ch:
            txt += f"　桩号 {ch:.1f} m"
        txt += f"　共 {sec.n_points} 个测点"
        self.lbl_head.setText(txt)

    def _set_note(self, text: str):
        self.lbl_note.setText(text)
