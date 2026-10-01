"""当前断面的参数精调面板（Q12）。

批量填写已经移到顶部菜单栏的「批量填写」里；这里只负责**单个断面**的精调。
参数集 xlsx 当前不提供，后期接入 Excel 导入后，导入结果同样落在 Section.params 上，
仍然可以在本面板单个覆盖。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QGroupBox, QLabel,
                              QPushButton, QVBoxLayout, QWidget)

from core import params as P
from core.model import Section

from .widgets import make_spin

NAN_TEXT = "—"


class ParamPanel(QWidget):
    """单断面参数编辑面板。改动后发出 changed 信号，由主窗口触发重算。"""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sections: list[Section] = []
        self.current: Section | None = None

        self.grp_one = QGroupBox("当前断面参数")
        self.lbl_name = QLabel(NAN_TEXT)
        self.o_rough = make_spin(0.001, 0.2, 0.005, 3)
        self.o_slope = make_spin(0.00001, 0.5, 0.0005, 5)
        self.o_q = make_spin(0.0, 1e6, 10.0, 2)
        self.o_main = make_spin(0.001, 0.2, 0.005, 3)
        self.o_left = make_spin(0.001, 0.2, 0.005, 3)
        self.o_right = make_spin(0.001, 0.2, 0.005, 3)

        self.o_rough.setToolTip("当前断面的综合糙率 n")
        self.o_slope.setToolTip("当前断面的河道比降 S")
        self.o_q.setToolTip("当前断面的设计流量 Qs (m³/s)")

        self.o_use_zone = QCheckBox("分区糙率分开填写（主槽 / 左滩 / 右滩）")
        self.o_use_zone.setToolTip("勾选后可分别为左滩、主槽、右滩设置不同的糙率")
        self.o_use_zone.toggled.connect(self._toggle_zone)

        self.o_main.setToolTip("主槽的糙率 n")
        self.o_left.setToolTip("左滩的糙率 n")
        self.o_right.setToolTip("右滩的糙率 n")

        self.btn_one = QPushButton("应用到当前断面")
        self.btn_one.setToolTip("将上述参数写入当前选中的断面，并重新计算")
        self.btn_one.clicked.connect(self._apply_one)

        fo = QFormLayout()
        fo.addRow("断面编号", self.lbl_name)
        fo.addRow("糙率 n", self.o_rough)
        fo.addRow("比降 S", self.o_slope)
        fo.addRow("设计流量 Qs", self.o_q)
        fo.addRow("", self.o_use_zone)
        fo.addRow("主槽糙率", self.o_main)
        fo.addRow("左滩糙率", self.o_left)
        fo.addRow("右滩糙率", self.o_right)
        fo.addRow("", self.btn_one)
        self.grp_one.setLayout(fo)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)

        self.lbl_hint = QLabel("批量填写请用顶部菜单栏的「批量填写」。")
        self.lbl_hint.setStyleSheet("color:#888780;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.grp_one)
        lay.addWidget(self.lbl_hint)
        lay.addWidget(self.lbl_status)
        lay.addStretch(1)

        self._toggle_zone(False)
        self._set_one_enabled(False)

    # ---------------- 状态 ----------------
    def set_sections(self, sections: list[Section]):
        self.sections = sections
        self._update_status()

    def set_current(self, sec: Section | None):
        self.current = sec
        self._set_one_enabled(sec is not None)
        if sec is None:
            self.lbl_name.setText(NAN_TEXT)
            return
        self.lbl_name.setText(sec.name)
        p = sec.params
        for widget, value in ((self.o_rough, p.roughness), (self.o_slope, p.slope),
                              (self.o_q, p.design_q), (self.o_main, p.roughness_main),
                              (self.o_left, p.roughness_left),
                              (self.o_right, p.roughness_right)):
            widget.setValue(value if (value is not None and value == value) else 0.0)
        has_zone = any(v is not None and v == v for v in
                       (p.roughness_main, p.roughness_left, p.roughness_right))
        self.o_use_zone.setChecked(has_zone)
        self._toggle_zone(has_zone)

    def _set_one_enabled(self, on: bool):
        for w in (self.o_rough, self.o_slope, self.o_q, self.o_use_zone,
                  self.o_main, self.o_left, self.o_right, self.btn_one):
            w.setEnabled(on)

    def _toggle_zone(self, on: bool):
        self.o_main.setEnabled(on)
        self.o_left.setEnabled(on)
        self.o_right.setEnabled(on)
        if not on:
            for w in (self.o_main, self.o_left, self.o_right):
                w.setValue(0.0)

    def set_zone_ui(self, on: bool) -> None:
        """只改「分区糙率」复选框与三个输入框的**显示状态**，不碰数据。

        供「批量填写」窗口的分区开关同步过来用（用户要求的单向同步）。
        不能直接调 `_toggle_zone`——它在取消勾选时会把三个输入框**清零**，
        那等于顺手改了界面上的值；而这里只是让两处显示一致，
        真正写数据仍要用户点「应用到当前断面」。
        """
        self.o_use_zone.blockSignals(True)
        self.o_use_zone.setChecked(on)
        self.o_use_zone.blockSignals(False)
        for w in (self.o_main, self.o_left, self.o_right):
            w.setEnabled(on)

    def refresh_status(self):
        """外部（如批量填写后）也可调用，刷新「还有几个断面没填」的提示。"""
        self._update_status()

    def _update_status(self):
        if not self.sections:
            self.lbl_status.setText("")
            return
        miss = P.missing_params(self.sections)
        total = len(self.sections)
        if miss:
            shown = "、".join(miss[:10]) + ("…" if len(miss) > 10 else "")
            self.lbl_status.setText(
                f"<span style='color:#A32D2D'>本条线上参数未填：{len(miss)}/{total} 个断面"
                f"<br>{shown}</span>")
        else:
            self.lbl_status.setText(
                f"<span style='color:#0F6E56'>本条线参数已齐（{total} 个断面）</span>")

    # ---------------- 动作 ----------------
    def _apply_one(self):
        if self.current is None:
            return
        fields = dict(roughness=self.o_rough.value(),
                      slope=self.o_slope.value(),
                      design_q=self.o_q.value())
        if self.o_use_zone.isChecked():
            fields.update(roughness_main=self.o_main.value(),
                          roughness_left=self.o_left.value(),
                          roughness_right=self.o_right.value())
        else:
            fields.update(roughness_main=None, roughness_left=None,
                          roughness_right=None)
        P.set_one(self.current, **fields)
        self._update_status()
        self.changed.emit()
