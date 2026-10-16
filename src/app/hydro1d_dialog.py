"""一维水面线推算设置窗口。

对齐 ZoneDialog / _ManualBase 的「选组」交互：
- 顶部下拉框选择纵断面线（当前组），窗口打开期间**跟随主界面左侧选中的线**；
- 每条线的推算配置存在各自的 `ProfileLine.hydro_config` 里，切组即加载；
- 点「推算」写回**当前选中组**的配置并触发主窗口重算——
  信号携带线索引，主窗口不再用闭包捕获打开时的行号
  （旧实现切线后点推算会把结果写到错误的线上）。
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QFormLayout
)
from PySide6.QtCore import Signal
from core.model import HydroConfig, Project


class Hydro1DDialog(QDialog):
    # 面板里换组 -> 主界面左侧跟着切（与 ZoneDialog/_ManualBase 同款）
    groupChanged = Signal(int)
    # (线索引, 配置)：用户点「推算」时发出
    config_applied = Signal(int, HydroConfig)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("一维水面线推算")
        self.setModal(False)
        self.resize(380, 280)

        self.project: Project | None = None
        self.line_index = 0
        self._group_busy = False    # 防止程序性切组触发 _on_group_combo

        layout = QVBoxLayout(self)

        # ---------------- 顶部：选择纵断面线（当前组）----------------
        grp_row = QHBoxLayout()
        grp_row.addWidget(QLabel("当前组"))
        self.cmb_group = QComboBox()
        self.cmb_group.setMinimumWidth(220)
        self.cmb_group.currentIndexChanged.connect(self._on_group_combo)
        grp_row.addWidget(self.cmb_group, 1)
        layout.addLayout(grp_row)

        # ---------------- 表单区 ----------------
        form_layout = QFormLayout()

        self.regime_combo = QComboBox()
        self.regime_combo.addItems(
            ["亚临界流 (subcritical)", "超临界流 (supercritical)", "混合流 (mixed)"])
        self.regimes = ["subcritical", "supercritical", "mixed"]
        form_layout.addRow("流态:", self.regime_combo)

        self.boundary_kind_combo = QComboBox()
        self.boundary_kind_combo.addItems(
            ["正常水深 (normal)", "已知水位 (ws)", "临界水深 (critical)"])
        self.b_kinds = ["normal", "ws", "critical"]
        form_layout.addRow("边界类型:", self.boundary_kind_combo)

        self.boundary_val_edit = QLineEdit()
        self.boundary_val_edit.setPlaceholderText("可为空。正常水深填比降，已知水位填高程；已知水位留空时按正常水深推算。")
        self.boundary_val_edit.setToolTip(
            "边界值为边界断面的比降（normal）或高程（ws）。留空使用断面自身参数。")
        self.boundary_val_edit.setAccessibleName("边界值输入")
        self.boundary_val_edit.setAccessibleDescription(
            "正常水深模式下输入比降，已知水位模式下输入高程。")
        form_layout.addRow("边界值:", self.boundary_val_edit)

        self.q_edit = QLineEdit()
        self.q_edit.setPlaceholderText("流量(m³/s)。留空则用断面设计流量。")
        self.q_edit.setToolTip(
            "输入用于推算的恒定流量。如果不填，将使用河段最上游断面的设计流量。")
        self.q_edit.setAccessibleName("推算流量输入")
        self.q_edit.setAccessibleDescription(
            "输入覆盖设计流量的数值，留空使用断面原参数。")
        form_layout.addRow("流量:", self.q_edit)

        # 下游断面 / 流向
        self.cmb_downstream = QComboBox()
        self.cmb_downstream.addItems(
            ["自动（河底最低点）", "首个断面为下游", "末个断面为下游", "指定断面…"])
        self.ds_kinds = ["lowest", "first", "last", "name"]
        self.cmb_downstream.setToolTip(
            "决定推算方向（RASlite 约定：下游断面获得最大里程）。默认按河底最低点自动判定；"
            "若中游有深潭或河道平缓导致误判，可手动指定下游边界断面。")
        form_layout.addRow("下游断面:", self.cmb_downstream)

        self.cmb_downstream_sec = QComboBox()
        self.cmb_downstream_sec.setEnabled(False)
        form_layout.addRow("", self.cmb_downstream_sec)

        layout.addLayout(form_layout)

        # ---------------- 底部 ----------------
        btn_layout = QHBoxLayout()
        self.apply_btn = QPushButton("推算")
        self.apply_btn.clicked.connect(self._on_apply)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.hide)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        btn_layout.addWidget(self.apply_btn)
        layout.addLayout(btn_layout)

        self.boundary_kind_combo.currentIndexChanged.connect(self._on_bkind_changed)
        self._on_bkind_changed(self.boundary_kind_combo.currentIndex())
        self.cmb_downstream.currentIndexChanged.connect(self._on_downstream_changed)

    # ---------------- 上下文（由主窗口推送，单一真相）----------------

    def set_project(self, project: Project | None):
        """推入工程并重建组下拉框（打开窗口 / 载入数据后调用）。"""
        self.project = project
        self._reload_groups()

    def set_current_line(self, index: int):
        """主窗口左侧换线时同步跟随（程序性切换，不触发回环）。"""
        self.line_index = max(index, 0)
        if 0 <= self.line_index < self.cmb_group.count():
            self._group_busy = True
            try:
                self.cmb_group.setCurrentIndex(self.line_index)
            finally:
                self._group_busy = False
        self._load_current_config()

    def popup(self):
        """显示并置前（不阻塞）。"""
        self.show()
        self.raise_()
        self.activateWindow()

    # ---------------- 组切换 ----------------

    def _reload_groups(self):
        self._group_busy = True
        try:
            self.cmb_group.clear()
            if self.project is not None:
                for ln in self.project.profile_lines:
                    self.cmb_group.addItem(
                        f"{ln.name}（{len(ln.sections)} 个断面）")
            if 0 <= self.line_index < self.cmb_group.count():
                self.cmb_group.setCurrentIndex(self.line_index)
        finally:
            self._group_busy = False
        self.apply_btn.setEnabled(self.cmb_group.count() > 0)
        self._load_current_config()

    def _on_group_combo(self, index: int):
        if self._group_busy or index < 0:
            return
        self.line_index = index
        self._load_current_config()
        # 让主界面左侧跟着切（ setCurrentRow 会经 _refresh_manual_panels
        # 回推 set_current_line，那里是程序性同步、不会再触发本槽，无回环）
        self.groupChanged.emit(index)

    # ---------------- 配置 <-> 控件 ----------------

    def _current_line(self):
        if self.project is None:
            return None
        try:
            return self.project.profile_lines[self.line_index]
        except IndexError:
            return None

    def _load_current_config(self):
        """把当前组的 hydro_config 刷到控件上。"""
        ln = self._current_line()
        cfg = ln.hydro_config if ln is not None and ln.hydro_config is not None \
            else HydroConfig()

        self.regime_combo.setCurrentIndex(
            self.regimes.index(cfg.regime)
            if cfg.regime in self.regimes else 0)
        self.boundary_kind_combo.setCurrentIndex(
            self.b_kinds.index(cfg.boundary_kind)
            if cfg.boundary_kind in self.b_kinds else 0)

        if cfg.boundary_value == cfg.boundary_value:    # not NaN
            self.boundary_val_edit.setText(str(cfg.boundary_value))
        else:
            self.boundary_val_edit.clear()

        if cfg.q_override is not None:
            self.q_edit.setText(str(cfg.q_override))
        else:
            self.q_edit.clear()

        self.cmb_downstream.setCurrentIndex(self.ds_kinds.index(cfg.downstream_kind)
            if cfg.downstream_kind in self.ds_kinds else 0)

        self.cmb_downstream_sec.clear()
        if ln is not None:
            for s in ln.sections:
                self.cmb_downstream_sec.addItem(s.name)
            if cfg.downstream_kind == "name" and cfg.downstream_name:
                idx = self.cmb_downstream_sec.findText(cfg.downstream_name)
                if idx >= 0:
                    self.cmb_downstream_sec.setCurrentIndex(idx)

        self._on_bkind_changed(self.boundary_kind_combo.currentIndex())
        self._on_downstream_changed(self.cmb_downstream.currentIndex())

    def _collect_config(self) -> HydroConfig:
        """把控件读成 HydroConfig（非法数字按留空处理，不弹错——推算时再校验）。"""
        val_str = self.boundary_val_edit.text().strip()
        try:
            boundary_value = float(val_str) if val_str else float("nan")
        except ValueError:
            boundary_value = float("nan")

        q_str = self.q_edit.text().strip()
        try:
            q_override = float(q_str) if q_str else None
        except ValueError:
            q_override = None

        kind = self.ds_kinds[self.cmb_downstream.currentIndex()]
        name = (self.cmb_downstream_sec.currentText().strip()
                if kind == "name" and self.cmb_downstream_sec.count() > 0 else None)

        return HydroConfig(
            regime=self.regimes[self.regime_combo.currentIndex()],
            boundary_kind=self.b_kinds[self.boundary_kind_combo.currentIndex()],
            boundary_value=boundary_value,
            q_override=q_override,
            downstream_kind=kind,
            downstream_name=name,
        )

    # ---------------- 动作 ----------------

    def _on_downstream_changed(self, idx):
        self.cmb_downstream_sec.setEnabled(self.ds_kinds[idx] == "name")

    def _on_bkind_changed(self, idx):
        kind = self.b_kinds[idx]
        if kind == "critical":
            self.boundary_val_edit.setEnabled(False)
            self.boundary_val_edit.clear()
        else:
            self.boundary_val_edit.setEnabled(True)

    def _on_apply(self):
        ln = self._current_line()
        if ln is None:
            return
        self.config_applied.emit(self.line_index, self._collect_config())
