from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QFormLayout, QGroupBox
)
from PySide6.QtCore import Signal
from core.model import HydroConfig

class Hydro1DDialog(QDialog):
    # 当参数改变且用户点击应用时触发此信号
    config_applied = Signal(HydroConfig)

    def __init__(self, parent=None, initial_config=None):
        super().__init__(parent)
        self.setWindowTitle("一维水面线推算")
        self.setModal(False)
        self.resize(300, 250)

        self.config = initial_config or HydroConfig()

        layout = QVBoxLayout(self)

        form_layout = QFormLayout()

        self.regime_combo = QComboBox()
        self.regime_combo.addItems(["亚临界流 (subcritical)", "超临界流 (supercritical)", "混合流 (mixed)"])
        # Map values
        self.regimes = ["subcritical", "supercritical", "mixed"]
        try:
            idx = self.regimes.index(self.config.regime)
            self.regime_combo.setCurrentIndex(idx)
        except ValueError:
            pass

        form_layout.addRow("流态:", self.regime_combo)

        self.boundary_kind_combo = QComboBox()
        self.boundary_kind_combo.addItems(["正常水深 (normal)", "已知水位 (ws)", "临界水深 (critical)"])
        self.b_kinds = ["normal", "ws", "critical"]

        self.boundary_val_edit = QLineEdit()
        self.boundary_val_edit.setPlaceholderText("可为空。正常水深填比降，已知水位填高程。")
        self.boundary_val_edit.setToolTip("边界值为边界断面的比降（normal）或高程（ws）。留空使用断面自身参数。")
        self.boundary_val_edit.setAccessibleName("边界值输入")
        self.boundary_val_edit.setAccessibleDescription("正常水深模式下输入比降，已知水位模式下输入高程。")

        try:
            idx = self.b_kinds.index(self.config.boundary_kind)
            self.boundary_kind_combo.setCurrentIndex(idx)
        except ValueError:
            pass

        if self.config.boundary_value == self.config.boundary_value:  # not NaN
            self.boundary_val_edit.setText(str(self.config.boundary_value))

        form_layout.addRow("边界类型:", self.boundary_kind_combo)
        form_layout.addRow("边界值:", self.boundary_val_edit)

        self.q_edit = QLineEdit()
        self.q_edit.setPlaceholderText("流量(m³/s)。留空则用断面设计流量。")
        self.q_edit.setToolTip("输入用于推算的恒定流量。如果不填，将使用河段最上游断面的设计流量。")
        self.q_edit.setAccessibleName("推算流量输入")
        self.q_edit.setAccessibleDescription("输入覆盖设计流量的数值，留空使用断面原参数。")

        if self.config.q_override is not None:
            self.q_edit.setText(str(self.config.q_override))

        form_layout.addRow("流量:", self.q_edit)

        layout.addLayout(form_layout)

        btn_layout = QHBoxLayout()
        self.apply_btn = QPushButton("推算")
        self.apply_btn.clicked.connect(self._on_apply)
        btn_layout.addStretch()
        btn_layout.addWidget(self.apply_btn)

        layout.addLayout(btn_layout)

        self.boundary_kind_combo.currentIndexChanged.connect(self._on_bkind_changed)
        self._on_bkind_changed(self.boundary_kind_combo.currentIndex())

    def _on_bkind_changed(self, idx):
        kind = self.b_kinds[idx]
        if kind == "critical":
            self.boundary_val_edit.setEnabled(False)
            self.boundary_val_edit.clear()
        else:
            self.boundary_val_edit.setEnabled(True)

    def _on_apply(self):
        self.config.regime = self.regimes[self.regime_combo.currentIndex()]
        self.config.boundary_kind = self.b_kinds[self.boundary_kind_combo.currentIndex()]

        val_str = self.boundary_val_edit.text().strip()
        if not val_str:
            self.config.boundary_value = float("nan")
        else:
            try:
                self.config.boundary_value = float(val_str)
            except ValueError:
                self.config.boundary_value = float("nan")

        q_str = self.q_edit.text().strip()
        if not q_str:
            self.config.q_override = None
        else:
            try:
                self.config.q_override = float(q_str)
            except ValueError:
                self.config.q_override = None

        self.config_applied.emit(self.config)
