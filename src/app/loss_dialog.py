"""逐断面局部水头损失系数（收缩 / 扩张）面板。

为什么是"批量表格"而不是一排数字框
----------------------------------
局部损失系数通常**整条河段用同一对值**，只有桥、涵、急缩急扩才需要单独调。
所以交互照「批量填写」那套：一张表 + 支持从 Excel 粘贴一列/多列，
整组统一改时在 Excel 拉一列贴进来即可。

三条设计约定（都与"默认值"有关，改之前先读）
--------------------------------------------
1. **空格 = 用线级默认**（默认当前是 0.1 / 0.3）。空格不写 `loss_overrides` 的键，
   所以以后默认值变了，这些断面会跟着变。
   ⚠ 反过来：一旦把默认值填进格子并「应用」，就变成**显式覆盖**，
     以后改默认它不再跟随——这正是「填入默认值」按钮要带提示的原因。
2. **参数存在本断面、作用于"本断面 → 下游断面"这一跨**（HEC-RAS 约定）。
   于是**最下游断面的值永远用不到**，状态列会直接写「无下游，不生效」。
3. **空格的灰字是占位提示，不是内容**（见 `_DefaultHintDelegate`）。
   这样既能看见默认值是多少，又不会被"预填"物化。

收缩/扩张用哪个由引擎按流速水头自动判定（下游流速水头大 → 收缩），
用户不需要指定方向，只要填两个数。
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QStyledItemDelegate, QTableWidgetItem, QVBoxLayout,
)

from core.model import HydroConfig, Project

from .dialogs import PasteTable

#: 列号
COL_NAME, COL_CONTRACT, COL_EXPAND, COL_STATE = range(4)

#: 取值范围（下限统一为 0：负系数等于给水头**加**能量，物理上不成立）
LIMITS = {COL_CONTRACT: 0.5, COL_EXPAND: 1.0}


class _DefaultHintDelegate(QStyledItemDelegate):
    """空格子里用灰字画出"将采用的默认值"，但它**不是单元格内容**。

    如果改成把默认值真的填进单元格，用户一点「应用」就把默认值物化成了
    显式覆盖，之后改默认再也不影响这些断面——这条坑很隐蔽，所以宁可多写
    二十行 delegate。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hints: dict[int, str] = {}

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.data(Qt.DisplayRole):
            return
        txt = self.hints.get(index.column())
        if not txt:
            return
        painter.save()
        painter.setPen(QColor("#9AA0A6"))
        painter.drawText(option.rect.adjusted(6, 0, -6, 0),
                         Qt.AlignLeft | Qt.AlignVCenter, txt)
        painter.restore()


class LossDialog(QDialog):
    """局部损失系数面板：只显示当前组，改动点「应用」才写回。"""

    #: (线索引, 新的 HydroConfig)
    applied = Signal(int, HydroConfig)
    #: 面板里换组 -> 主界面左侧跟着切
    groupChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("局部损失系数（收缩 / 扩张）")
        self.setModal(False)
        self.resize(640, 560)

        self.project: Project | None = None
        self.infos: dict = {}
        self.line_index = 0
        self._group_busy = False
        self._ds_name: str | None = None

        layout = QVBoxLayout(self)

        # ---------------- 顶部：当前组 ----------------
        grp_row = QHBoxLayout()
        grp_row.addWidget(QLabel("当前组"))
        self.cmb_group = QComboBox()
        self.cmb_group.setMinimumWidth(260)
        self.cmb_group.currentIndexChanged.connect(self._on_group_combo)
        grp_row.addWidget(self.cmb_group, 1)
        layout.addLayout(grp_row)

        hint = QLabel(
            "留空 = 使用默认值。收缩/扩张由程序按流速水头自动判定，不需要指定方向。<br>"
            "系数作用于<b>本断面到下游断面</b>这一跨，因此<b>最下游断面的值不生效</b>。"
            "<br>两列可直接从 Excel 粘贴（Ctrl+V）。")
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.RichText)
        hint.setStyleSheet("color:#5F5E5A;")
        layout.addWidget(hint)

        # ---------------- 表格 ----------------
        self.table = PasteTable(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["断面名", "收缩系数", "扩张系数", "状态"])
        self.table.readonly_cols = {COL_NAME, COL_STATE}
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(COL_NAME, 220)
        self.table.setColumnWidth(COL_CONTRACT, 110)
        self.table.setColumnWidth(COL_EXPAND, 110)
        self._hint = _DefaultHintDelegate(self)
        self.table.setItemDelegate(self._hint)
        layout.addWidget(self.table, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color:#185FA5;")
        layout.addWidget(self.lbl_status)

        # ---------------- 底部 ----------------
        self.btn_fill = QPushButton("填入默认值")
        self.btn_fill.setToolTip(
            "把本组所有空行填成当前的默认值。\n"
            "注意：填满并「应用」后，这些断面就变成了显式覆盖值——\n"
            "以后若调整默认值，它们不会再跟着变。只想个别调整就别用这个按钮。")
        self.btn_fill.clicked.connect(self._fill_defaults)

        self.btn_clear = QPushButton("清空本组覆盖")
        self.btn_clear.setToolTip("把本组所有格子清空，全部回到默认值（点「应用」后生效）")
        self.btn_clear.clicked.connect(self._clear_all)

        self.btn_apply = QPushButton("应用")
        self.btn_apply.setDefault(True)
        self.btn_apply.clicked.connect(self._on_apply)

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.hide)

        foot = QHBoxLayout()
        foot.addWidget(self.btn_fill)
        foot.addWidget(self.btn_clear)
        foot.addStretch(1)
        foot.addWidget(btn_close)
        foot.addWidget(self.btn_apply)
        layout.addLayout(foot)

        self.btn_apply.setEnabled(False)

    # ---------------- 上下文（由主窗口推送）----------------

    def set_context(self, project: Project | None, infos: dict, line_index: int):
        """推入工程 / 地形结果 / 当前组。

        同一组重复推送时**不重建表格**——否则用户正在编辑器里敲的数
        会被主界面的一次刷新抹掉（`_refresh_manual_panels` 触发很频繁）。
        """
        line_changed = (line_index != self.line_index)
        project_changed = (project is not self.project)
        self.project = project
        self.infos = infos or {}
        self.line_index = max(line_index, 0)
        self._sync_group_combo()
        if project_changed or line_changed:
            self._reload()

    def popup(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _current_line(self):
        if self.project is None:
            return None
        try:
            return self.project.profile_lines[self.line_index]
        except IndexError:
            return None

    def _sync_group_combo(self):
        self._group_busy = True
        try:
            self.cmb_group.clear()
            if self.project is not None:
                for ln in self.project.profile_lines:
                    self.cmb_group.addItem(f"{ln.name}（{len(ln.sections)} 个断面）")
            if 0 <= self.line_index < self.cmb_group.count():
                self.cmb_group.setCurrentIndex(self.line_index)
        finally:
            self._group_busy = False

    def _on_group_combo(self, index: int):
        if self._group_busy or index < 0:
            return
        self.line_index = index
        self._reload()
        self.groupChanged.emit(index)

    # ---------------- 数据 <-> 表格 ----------------

    def _reload(self):
        ln = self._current_line()
        self.table.setRowCount(0)
        self.lbl_status.setText("")
        if ln is None:
            self._ds_name = None
            self.btn_apply.setEnabled(False)
            return

        cfg = ln.hydro_config if ln.hydro_config is not None else HydroConfig()
        overrides = dict(getattr(cfg, "loss_overrides", None) or {})
        self._hint.hints = {COL_CONTRACT: f"{cfg.c_contract:g}",
                            COL_EXPAND: f"{cfg.c_expand:g}"}
        self._ds_name = self._find_ds_name(ln, cfg)

        self.table.setRowCount(len(ln.sections))
        for r, sec in enumerate(ln.sections):
            name_item = QTableWidgetItem(sec.name)
            name_item.setFlags(Qt.ItemIsEnabled)          # 只读
            self.table.setItem(r, COL_NAME, name_item)

            pair = overrides.get(sec.name)
            for col, val in ((COL_CONTRACT, pair[0] if pair else None),
                             (COL_EXPAND, pair[1] if pair else None)):
                item = QTableWidgetItem("" if val is None else f"{val:g}")
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, col, item)

            state = QTableWidgetItem("")
            state.setFlags(Qt.ItemIsEnabled)              # 只读
            self.table.setItem(r, COL_STATE, state)

        self._refresh_states()
        self.btn_apply.setEnabled(True)

    def _find_ds_name(self, ln, cfg) -> str | None:
        """找出"最下游断面"的名字，用来在状态列标出"该行不生效"。

        判定要复用 `_downstream_index`（含手动指定的下游断面），否则会和
        实际推算方向对不上。地形结果缺失时返回 None（只是不标注，不影响功能）。
        """
        try:
            from core.hydro1d import _downstream_index
            infos = [self.infos[s.name] for s in ln.sections]
            idx = _downstream_index(ln.sections, infos, cfg)
        except Exception:                                  # noqa: BLE001
            return None
        if 0 <= idx < len(ln.sections):
            return ln.sections[idx].name
        return None

    def _has_value(self, row: int) -> bool:
        for col in (COL_CONTRACT, COL_EXPAND):
            item = self.table.item(row, col)
            if item is not None and item.text().strip():
                return True
        return False

    def _refresh_states(self):
        ln = self._current_line()
        if ln is None:
            return
        for r, sec in enumerate(ln.sections):
            item = self.table.item(r, COL_STATE)
            if item is None:
                continue
            if self._ds_name is not None and sec.name == self._ds_name:
                item.setText("无下游，不生效")
                item.setForeground(QColor("#9AA0A6"))
            elif self._has_value(r):
                item.setText("已覆盖")
                item.setForeground(QColor("#185FA5"))
            else:
                item.setText("默认")
                item.setForeground(QColor("#5F5E5A"))

    # ---------------- 按钮 ----------------

    def _defaults(self) -> tuple[float, float]:
        ln = self._current_line()
        cfg = ln.hydro_config if ln is not None and ln.hydro_config is not None \
            else HydroConfig()
        return cfg.c_contract, cfg.c_expand

    def _fill_defaults(self):
        dc, de = self._defaults()
        for r in range(self.table.rowCount()):
            for col, val in ((COL_CONTRACT, dc), (COL_EXPAND, de)):
                item = self.table.item(r, col)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(r, col, item)
                if not item.text().strip():
                    item.setText(f"{val:g}")
        self._refresh_states()
        self.lbl_status.setText(
            "已把默认值填入空行。注意：点「应用」后这些断面将变成显式覆盖值。")

    def _clear_all(self):
        for r in range(self.table.rowCount()):
            for col in (COL_CONTRACT, COL_EXPAND):
                item = self.table.item(r, col)
                if item is not None:
                    item.setText("")
        self._refresh_states()
        self.lbl_status.setText("已清空本组所有格子，点「应用」后全部回到默认值。")

    def _read_cell(self, row: int, col: int, sec_name: str,
                   label: str, errors: list[str]):
        item = self.table.item(row, col)
        txt = item.text().strip() if item is not None else ""
        if txt == "":
            return None
        try:
            val = float(txt)
        except ValueError:
            errors.append(f"{sec_name} · {label}：{txt} 不是数字")
            return None
        if val < 0 or val > LIMITS[col]:
            errors.append(
                f"{sec_name} · {label}：{val:g} 超出允许范围 0 ~ {LIMITS[col]:g}")
            return None
        return val

    def _on_apply(self):
        ln = self._current_line()
        if ln is None:
            return

        errors: list[str] = []
        rows = []
        for r, sec in enumerate(ln.sections):
            c = self._read_cell(r, COL_CONTRACT, sec.name, "收缩系数", errors)
            e = self._read_cell(r, COL_EXPAND, sec.name, "扩张系数", errors)
            rows.append((sec.name, c, e))
        if errors:
            head = "\n".join(errors[:10])
            more = f"\n……共 {len(errors)} 处问题" if len(errors) > 10 else ""
            QMessageBox.warning(self, "无法应用", head + more)
            return

        base = ln.hydro_config if ln.hydro_config is not None else HydroConfig()
        dc, de = base.c_contract, base.c_expand
        overrides = {}
        for name, c, e in rows:
            if c is None and e is None:
                continue                    # 留空 = 不写键 = 用线级默认
            overrides[name] = (dc if c is None else c,
                               de if e is None else e)

        self.applied.emit(self.line_index,
                          replace(base, loss_overrides=overrides))
        self._refresh_states()
        self.lbl_status.setText(
            f"已应用到「{ln.name}」：{len(overrides)} 个断面有单独设定，"
            f"其余 {len(ln.sections) - len(overrides)} 个用默认值。")
