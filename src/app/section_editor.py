"""横断面编辑器：左侧测点表格 + 右侧可拖动的断面图（新窗口，非模态）。

交互约定
--------
* **双向同步**：改表格立刻重画；拖图上测点立刻改表格。
* **拖动默认只改高程**：起点距一旦被横向误拖，图上看着只是"挪了一点"，
  实际整条起点距序列已经被改坏，极难自查。需要改起点距时取消「只改高程」。
* **表格默认只读**：`QTableWidget` 的 `editTriggers` 默认是
  `DoubleClicked | EditKeyPressed | AnyKeyPressed`——那个 `AnyKeyPressed`
  意味着选中单元格后**随便敲一个键就进编辑态**。对本窗口来说这是事故：
  想按键盘+鼠标多选，却把某个高程改掉了，而表格本身才两列数字，看不出来。
  所以默认 `NoEditTriggers`，要在表格里录入必须显式勾选「允许键入 / 粘贴」。
* **录入的主角是 Excel 粘贴**：批量改几十个点的高程，靠双击一个个敲不现实。
  粘贴走**整批校验、一次性写入**——中间有一格不是合法数字就整批放弃，
  几何数据改一半比改错了更难查。
* **松手才重算**：拖动中每帧都跑一遍曼宁公式会掉帧，所以拖动过程只重画，
  松开鼠标才发 `edited` 让主窗口重算。表格录入按批次，一次 `edited`。

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
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from core import edit as edit_mod
from core.model import Section

from .canvas_base import (COLOR_BED, COLOR_PICK, PlotPanel, place_hover_note)

#: 吸附半径（屏幕像素）
SNAP_PX = 16.0

COL_NO, COL_S, COL_Z = 0, 1, 2

#: 撤销栈深度上限。断面点数本来就不多，多留几步没有成本。
UNDO_LIMIT = 50

#: 解锁后允许进入编辑态的方式。**故意不含 `AnyKeyPressed`**——
#: 那是"选中某格后随便敲个键就改写数据"的元凶，本窗口默认就是防它。
TRIGGERS_EDITABLE = QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed

HINT_LOCKED = ("表格当前只读（防止误敲键盘改坏测点）。要键入或粘贴 Excel 数据，"
               "先勾选左下角「允许键入 / 粘贴」；图上拖动改高程任何时候都能用。")
HINT_UNLOCKED = ("两列都能双击（或 Enter / F2）修改；Ctrl+V 粘贴 Excel 数据，"
                 "从当前选中行往下铺开。粘贴前会整批校验，有一格不对就整批取消。")


class PointTable(QTableWidget):
    """测点表格：把 Ctrl+V 从 Qt 手里拿回来自己处理。

    为什么必须自己写：Qt 默认的 Ctrl+V 把整段剪贴板塞进**一个**单元格，
    而 Excel 复制出来的是制表符分隔的文本（列间 `\\t`、行间 `\\n`），
    必须按行列铺开才行。批量填写对话框里的 `PasteTable` 也是这个套路。

    Enter / Return / F2 统一在这里触发编辑：Qt 默认在
    `NoEditTriggers` 下的行为取决于平台，不如显式调用 `edit()`。
    单元格不可编辑时 `edit()` 自己会返回 False，不用再判一遍。
    """

    def __init__(self, parent=None, on_paste=None):
        super().__init__(parent)
        self._on_paste = on_paste

    def keyPressEvent(self, event):
        if self._on_paste is not None and event.matches(QKeySequence.Paste):
            self._on_paste()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_F2):
            idx = self.currentIndex()
            if idx.isValid():
                self.edit(idx)
                event.accept()
                return
        super().keyPressEvent(event)


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
        #: 是否跟随主界面的断面选择变化（窗口开着时主界面换断面，这里自动跟）。
        #: 用户在确认框里选「否」时置 False 并保持，直到重新打开窗口；
        #: 见 `follow()`。
        self.follow_selection = True

        self._drag_idx: int | None = None
        self._hover_idx: int | None = None
        self._drag_dirty = False
        self._loading = False               # 回填表格时屏蔽 itemChanged

        self.lbl_head = QLabel("—")
        self.lbl_head.setStyleSheet("font-size:13px; font-weight:500;")

        # ---- 左：测点表格 ----
        # 默认一个字也不让改：`setEditTriggers(NoEditTriggers)` 是必须的，
        # 只看 item 的 `ItemIsEditable` 标志没用——默认 editTriggers 里有
        # AnyKeyPressed，选中后敲任何键都会改写数据。见 HINT_LOCKED。
        self.table = PointTable(on_paste=self._paste_from_clipboard)
        self.table.setColumnCount(3)
        self.table.setRowCount(0)
        self.table.setHorizontalHeaderLabels(["#", "起点距 (m)", "高程 (m)"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_NO, QHeaderView.ResizeToContents)
        for c in (COL_S, COL_Z):
            self.table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._on_cell_changed)

        # 「允许键入 / 粘贴」是表格录入的总闸（键入和粘贴都归它管），
        # 「只改高程」只管**拖动**。两个复选各管一段，所以文案必须写清范围，
        # 否则用户会以为是同一个开关。勾选本项即两列都可以改，不用再动另一个。
        self.chk_type = QCheckBox("允许键入 / 粘贴")
        self.chk_type.setChecked(False)
        self.chk_type.setToolTip(
            "默认不勾：表格只读，避免误敲键盘把测点改坏。\n"
            "勾选后起点距、高程两列都能双击（或 Enter / F2）修改，"
            "也能把 Excel 复制的整块数据 Ctrl+V 粘进来。\n"
            "注意它只管表格；图上拖动受下面那个复选控制。")
        self.chk_type.toggled.connect(self._apply_edit_mode)

        self.chk_lock = QCheckBox("拖动时只改高程")
        self.chk_lock.setChecked(True)
        self.chk_lock.setToolTip(
            "只影响**图上拖动**。勾选时拖测点只改高程，起点距锁死；"
            "取消后可横向拖动改起点距，平面坐标 x/y 会按断面走向同步重算。\n"
            "表格里改起点距不需要取消这一项。")

        self.btn_insert = QPushButton("插入测点")
        self.btn_insert.setToolTip("在当前行之后插入一个测点（形态不变，只是加点）")
        self.btn_insert.clicked.connect(self._insert)
        self.btn_delete = QPushButton("删除测点")
        self.btn_delete.clicked.connect(self._delete)
        self.btn_paste = QPushButton("粘贴 Excel 数据")
        self.btn_paste.setToolTip("把剪贴板里的数据粘到当前选中行开始的两列"
                                  "（等价于 Ctrl+V）。需先勾选「允许键入 / 粘贴」")
        self.btn_paste.clicked.connect(self._paste_from_clipboard)
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
        row_chk = QHBoxLayout()
        row_chk.addWidget(self.chk_type)
        row_chk.addWidget(self.chk_lock)
        row_chk.addStretch(1)
        lay_l.addLayout(row_chk)
        row = QHBoxLayout()
        for b in (self.btn_paste, self.btn_insert, self.btn_delete):
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

        self.lbl_note = QLabel(HINT_LOCKED)
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
        self.btn_paste.setEnabled(False)

    # ---------------- 外部接口 ----------------
    @property
    def has_unsaved_changes(self) -> bool:
        """相对「打开这个断面时」有没有改动过。

        用来决定切断面时要不要先问一句。注意判据是**与 `_base` 比对**而不是
        "撤销栈非空"：用户改完又按了「还原」，撤销栈里仍有历史，
        但断面已经回到原样，这时不该再拦着不让切。
        """
        if self.sec is None or self._base is None:
            return False
        return edit_mod.snapshot(self.sec) != self._base

    def set_context(self, sec: Section | None, chainage: float | None = None):
        """主窗口把要编辑的断面推进来。

        打开窗口时调一次；窗口开着时主界面换断面也会再调（跟随选择）。
        两种情形共用，所以这里**重置**全部会话状态：撤销/重做清空、
        `_base` 重取——撤销栈跨断面会让人撤销到别的断面上去。
        """
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
            self._set_note(HINT_LOCKED)
            return
        self._base = edit_mod.snapshot(sec)
        self._cur = edit_mod.snapshot(sec)
        self._fill_table()
        self._apply_edit_mode()
        self._refresh_plot()
        self._update_head()

    def follow(self, sec: Section | None, chainage: float | None = None) -> bool:
        """主界面换了断面：这个窗口要不要跟过去。

        返回 True 表示已经跟过去（或本来就该跟），False 表示用户选择留在
        原断面。留在原断面时 `follow_selection` 置 False——**这一条是必须的**：
        否则主界面「行号没变」之类的路径会反复进来问同一句话。
        `follow_selection` 会在下次 `_open_section_editor()` 里复位。

        有未保存改动时先问一句。编辑是**实时生效**的（改一下主窗口就重算并标脏），
        所以"切走"并不会丢数据——这里拦一下是防用户误以为切走会丢而不敢切，
        也防他真的以为切走 = 放弃改动。
        """
        if self.sec is sec or not self.follow_selection:
            return True
        if self.isVisible() and self.has_unsaved_changes:
            ans = QMessageBox.question(
                self, "切换断面",
                f"【{self.sec.name}】已有改动（改动已实时生效并保存到工程）。\n\n"
                f"切换到【{sec.name if sec is not None else '（无）'}】后，"
                f"本窗口的撤销/重做历史将清空，"
                f"【{self.sec.name}】的改动不受影响。\n\n确定切换吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans != QMessageBox.Yes:
                self.follow_selection = False
                self._set_note(f"已留在【{self.sec.name}】。"
                               f"关掉本窗口再打开即可切到别的断面。")
                return False
        self.set_context(sec, chainage)
        return True

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
        # ⚠ 这里故意**不**预选 (0, 高程)：预选 + editTriggers 里的
        # AnyKeyPressed = 打开窗口后手一抖就改掉第一个测点。
        # 没有选中格时粘贴默认从第一行起点距列开始，一样好用。

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

    def _apply_edit_mode(self):
        """按「允许键入 / 粘贴」刷新表格的可编辑性，并同步提示文案。

        三处必须一起给，少一处都会让用户以为"勾选了还是不能改"：

        1. 整表的 `editTriggers`——决定用户能不能**进入**编辑态；
        2. 每个 item 的 `ItemIsEditable`——决定 `edit()` 会不会被拒绝；
        3. 粘贴按钮的可用性——否则 Ctrl+V 之外的入口没有任何暗示。

        ⚠ 表格能否录入**只看** `chk_type`，与 `chk_lock` 无关（那个只管拖动）。
        """
        editable = self.chk_type.isChecked()
        self.table.setEditTriggers(
            TRIGGERS_EDITABLE if editable else QTableWidget.NoEditTriggers)
        self.btn_paste.setEnabled(editable)
        self._loading = True
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            self._apply_row_flags(i)
        self.table.blockSignals(False)
        self._loading = False
        self._set_note(HINT_UNLOCKED if editable else HINT_LOCKED)

    def _apply_row_flags(self, i: int):
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        editable = self.chk_type.isChecked()
        for col in (COL_S, COL_Z):
            it = self.table.item(i, col)
            if it is None:
                continue
            it.setFlags(flags if not editable else flags | Qt.ItemIsEditable)

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
            v = float(item.text().strip())
        except ValueError:
            self._set_note(f"第 {row + 1} 行填的不是数字，已还原。")
            self._sync_row(row)
            return
        # `float("nan")` / `float("inf")` 都能解析成功，必须另外拦：
        # core.edit.set_z 不做有限性检查，NaN 一旦进模型，
        # 后面所有插值、面积、曼宁公式都会静默变成 NaN。
        if not np.isfinite(v):
            self._set_note(f"第 {row + 1} 行是无效数值（NaN / 无穷大），已还原。")
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

    # ---------------- 从 Excel 粘贴 ----------------
    def _paste_from_clipboard(self) -> int:
        """把剪贴板里的 Excel 数据按行列铺开写入。返回写入的数值个数。

        锚点：当前选中格。行 = 选中行往下，列 = 选中列往右；
        选中格落在「#」列时自动挪到起点距列——否则用户选中整行再粘贴，
        第一列数据会被 `#` 列吞掉。

        三条刻意的选择：

        1. **整批校验、一次性写入**。只要有一格不是合法数字就整批放弃，
           断面一个点都不动。几何改一半（前 20 点改了、后 10 点没改）
           比改错了更难发现，而且在图上只表现为"形状怪怪的"。
        2. **空格 = 这一格不动**，不算错。Excel 复制常带空单元格/尾随空列。
        3. **多出来的行数忽略并明确告知**，不自动追加测点。
           自动加点听上去贴心，但新点的高程/起点距可能没粘全，
           弄出一个畸形断面比少粘几行糟得多；要加点请先用「插入测点」。

        起点距仍走 `edit.set_s`，被夹到第 2 条规则之外的值会回报去处。
        """
        sec = self.sec
        if sec is None:
            return 0
        # 粘贴同样归「允许键入 / 粘贴」管：它一次性覆盖几十个格子，
        # 比手误改一格危险得多，不能藏在一个看不见的快捷键后面。
        if not self.chk_type.isChecked():
            self._set_note("表格当前只读：要粘贴 Excel 数据，"
                           "请先勾选左下角「允许键入 / 粘贴」。")
            return 0

        text = QApplication.clipboard().text()
        if not text.strip():
            self._set_note("剪贴板是空的，先从 Excel 里复制起点距 / 高程。")
            return 0

        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while lines and not lines[-1].strip():      # 去掉末尾空行
            lines.pop()

        n = self.table.rowCount()
        r0 = max(self.table.currentRow(), 0)
        c0 = max(self.table.currentColumn(), COL_S)

        vals: list[tuple[int, int, float]] = []
        bad: list[str] = []
        ignored = 0
        for i, line in enumerate(lines):
            r = r0 + i
            if r >= n:
                ignored = len(lines) - i
                break
            for j, cell in enumerate(line.split("\t")):
                c = c0 + j
                if c > COL_Z:
                    break
                t = cell.strip()
                if not t:
                    continue
                try:
                    v = float(t)
                except ValueError:
                    bad.append(f"第 {r + 1} 行「{t}」")
                    continue
                if not np.isfinite(v):
                    bad.append(f"第 {r + 1} 行「{t}」")
                    continue
                vals.append((r, c, v))

        if bad:
            shown = "、".join(bad[:5])
            more = f"，另有 {len(bad) - 5} 处" if len(bad) > 5 else ""
            self._set_note(f"粘贴已取消：{len(bad)} 个格子不是有效数字"
                           f"（{shown}{more}）。断面未做任何改动。")
            return 0
        if not vals:
            self._set_note("剪贴板里没有可用的数值。")
            return 0

        # 整体撤销一步：粘贴是"一个动作"，不能粘了 60 个数要撤 60 次
        self._push_undo(self._cur)
        rows = sorted({r for r, _c, _v in vals})
        clamped: list[int] = []
        max_dev = 0.0
        for r, c, v in sorted(vals):
            if c == COL_Z:
                edit_mod.set_z(sec, r, v)
            else:
                got = edit_mod.set_s(sec, r, v)
                if abs(got - v) > 1e-9:
                    clamped.append(r + 1)
                    max_dev = max(max_dev, abs(got - v))
        self._cur = edit_mod.snapshot(sec)
        self._fill_table()
        self._apply_edit_mode()
        self._refresh_plot()
        self._update_head()
        self.edited.emit()                      # 整批只重算一次

        lo, hi = min(rows) + 1, max(rows) + 1
        span = f"第 {lo} 行" if lo == hi else f"第 {lo}–{hi} 行"
        msg = f"已粘贴 {len(vals)} 个数据（{span}）。"
        if clamped:
            msg += (f"其中 {len(clamped)} 个起点距被夹进相邻点之间"
                    f"（最大挪了 {max_dev:.2f} m）——起点距必须严格递增，"
                    f"越界的值不会被采纳。")
        if ignored:
            msg += (f"剪贴板还剩 {ignored} 行没地方放（当前 {n} 个测点），"
                    f"已忽略；要增加测点请先用「插入测点」。")
        self._set_note(msg)
        return len(vals)

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
        self._apply_edit_mode()
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
        self._apply_edit_mode()
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
        self._apply_edit_mode()
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
            # 用共用定位函数：测点靠右时框自动翻到左侧，避免溢出轴外把
            # 绘图区挤窄（见 canvas_base.place_hover_note 的说明）
            place_hover_note(
                ax,
                f"第 {i + 1} 点　起点距 {s[i]:.2f} m　高程 {z[i]:.2f} m",
                s[i], z[i], rad=12.0, dy=16.0)

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
