"""顶部菜单栏弹出的两个设置面板。

设计要点
--------
1. **非模态**（`show()` 而非 `exec()`）：调阈值时要能一边改一边看右侧三张图跟着变，
   模态窗口会把交互挡死。
2. 改动**立即生效**，不需要「确定」按钮——与原来的面板行为一致，
   避免"改了没点确定所以没生效"的困惑。
3. 两个窗口都是**单例**：反复点菜单项只是把它显示出来并置前，
   不会越点越多、也不会丢掉已填的值。
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                              QComboBox, QDialog, QFormLayout, QGridLayout,
                              QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                              QMessageBox, QPushButton, QRadioButton,
                              QTableWidget, QTableWidgetItem, QTabWidget,
                              QVBoxLayout, QWidget)

from core.config import Config
from core.model import Section
from core.rating import hvec_row_counts
from core.slope import MODE_LABELS, SLOPE_MODES, propose_slopes
from core import version as version_mod

from .widgets import make_spin


class SettingsDialog(QDialog):
    """计算设置：断面模式 / 水位步长 / 桩号原点 / 转折点阈值 / CSV 编码。

    分三个信号：
      - `changed`            任何计算项变化 -> 整体重算
      - `thresholdsChanged`  仅阈值变化 -> 重算并报出"影响了多少个断面"
      - `exportChanged`      **仅导出项**变化 -> 不重算（编码只影响写文件）
    三者分开是为了避免"改个编码把 30 个断面全重算一遍"这种无谓开销。
    """

    changed = Signal()
    thresholdsChanged = Signal()
    exportChanged = Signal()

    # 界面下拉项 -> Config.csv_encoding
    CSV_ENCODINGS = ["utf-8-sig", "utf-8", "gbk"]

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("计算设置")
        self.setMinimumWidth(460)

        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["复式断面（分区求和）", "单断面（整体一次算）"])
        self.cmb_mode.setCurrentIndex(0 if cfg.compound_mode else 1)
        self.cmb_mode.setToolTip(
            "复式断面：按转折点分区，各分区独立算 A/P/B/Q 后求和，顶宽取逐段累加。\n"
            "单断面：不分区，整断面一次算完，顶宽取水面左右交点跨度。")
        self.cmb_mode.currentIndexChanged.connect(lambda _: self.changed.emit())

        self.spin_dh = make_spin(0.01, 5.0, 0.05, 3, cfg.dH)
        self.spin_dh.setToolTip("水位–流量曲线的水位步长（m）。越小越精细，曲线点越多。")
        self.spin_dh.valueChanged.connect(lambda _: self.changed.emit())

        self.cmb_origin = QComboBox()
        self.cmb_origin.addItems(["最低点（默认）", "首点", "末点"])
        self.cmb_origin.setCurrentIndex(
            {"lowest": 0, "start": 1, "end": 2}.get(cfg.chainage_origin, 0))
        self.cmb_origin.setToolTip(
            "纵剖面里程的桩号原点取在哪一端。\n"
            "「最低点」= 高程最低的断面为桩号 0，其余取到它的沿程距离。")
        self.cmb_origin.currentIndexChanged.connect(lambda _: self.changed.emit())

        self.spin_steep = make_spin(0.01, 10.0, 0.05, 2, cfg.steep_slope)
        self.spin_steep.setToolTip(
            "岸坡转折点识别的第一阶段阈值（原 MATLAB 参数 apxl，默认 0.4）。\n"
            "从深泓点向两岸扫描，先要遇到斜率大于此值的陡坡，\n"
            "才进入第二阶段去找转折点。调小会更早认定陡坡。")
        self.spin_steep.valueChanged.connect(
            lambda _: self.thresholdsChanged.emit())

        self.spin_turn = make_spin(0.001, 1.0, 0.01, 3, cfg.turn_slope)
        self.spin_turn.setToolTip(
            "岸坡转折点识别的第二阶段阈值（原 MATLAB 参数 zzdxl，默认 0.05）。\n"
            "遇到陡坡之后，再遇到斜率小于此值（或高程开始下降）的点，\n"
            "该点即为岸坡转折点、其高程即成灾水位。")
        self.spin_turn.valueChanged.connect(
            lambda _: self.thresholdsChanged.emit())

        self.btn_reset = QPushButton("恢复默认阈值 (0.4 / 0.05)")
        self.btn_reset.clicked.connect(self.reset_thresholds)

        # ---- 加高水位（原 Hs+1，现可配置：幅度 + 是否绘制）----
        self.chk_raise = QCheckBox("显示加高水位线（Hs + 幅度）")
        self.chk_raise.setChecked(cfg.raise_enabled)
        self.chk_raise.setToolTip(
            "是否在断面图 / 沿河纵剖面图上绘制「加高水位」参考线（原 Hs+1）。\n"
            "关闭后只隐藏这条线及其图例；加高幅度仍参与计算，\n"
            "左右岸水面交点（Zbs/Ybs）依旧按 Hs + 加高幅度 算，结果不变。")
        self.chk_raise.toggled.connect(lambda _: self.changed.emit())

        self.spin_raise = make_spin(0.0, 20.0, 0.1, 2, cfg.raise_level)
        self.spin_raise.setToolTip(
            "设计水位之上的安全加高幅度（m）。\n"
            "加高水位 = 设计水位 + 此幅度；原程序固定为 1.0（百年一遇加 1 m），\n"
            "现可自由设定。该幅度**始终参与计算**，影响左右岸水面交点。")
        self.spin_raise.valueChanged.connect(lambda _: self.changed.emit())

        form_raise = QFormLayout()
        form_raise.addRow("", self.chk_raise)
        form_raise.addRow("加高幅度 (m)", self.spin_raise)
        grp_raise = QGroupBox("加高水位（原 Hs+1）")
        grp_raise.setLayout(form_raise)

        form = QFormLayout()
        form.addRow("断面模式", self.cmb_mode)
        form.addRow("水位步长 dH", self.spin_dh)
        form.addRow("桩号原点", self.cmb_origin)
        form.addRow("陡坡阈值 (apxl)", self.spin_steep)
        form.addRow("缓坡阈值 (zzdxl)", self.spin_turn)
        form.addRow("", self.btn_reset)
        grp_calc = QGroupBox("计算")
        grp_calc.setLayout(form)

        # ---- 导出设置 ----
        self.cmb_enc = QComboBox()
        self.cmb_enc.addItems([
            "UTF-8 带 BOM（Excel 推荐）",
            "UTF-8 无 BOM",
            "GBK（旧版 / 特定平台）",
        ])
        self.cmb_enc.setCurrentIndex(
            self.CSV_ENCODINGS.index(cfg.csv_encoding)
            if cfg.csv_encoding in self.CSV_ENCODINGS else 0)
        self.cmb_enc.setToolTip(
            "导出 CSV 文件的字符编码。\n"
            "「UTF-8 带 BOM」：中文 Windows 的 Excel / WPS 打开时能正确识别，\n"
            "   中文表头与断面名正常显示（默认）。\n"
            "「UTF-8 无 BOM」：更通用，但 Excel 双击打开会显示乱码。\n"
            "「GBK」：仅在旧版 Excel 或特定下游平台不接受 BOM 时使用。\n"
            "此选项只影响导出文件的写法，不触发重算。")
        self.cmb_enc.currentIndexChanged.connect(
            lambda _: self.exportChanged.emit())

        form_out = QFormLayout()
        form_out.addRow("CSV 编码", self.cmb_enc)
        grp_out = QGroupBox("导出")
        grp_out.setLayout(form_out)

        self.lbl_hint = QLabel(
            "提示：本窗口为非模态，可拖到一侧，改动会立即反映到右侧三张图。")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#5F5E5A;")

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.hide)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_close)

        lay = QVBoxLayout(self)
        lay.addWidget(grp_calc)
        lay.addWidget(grp_raise)
        lay.addWidget(grp_out)
        lay.addWidget(self.lbl_hint)
        lay.addLayout(row)

    # ---------------- 行为 ----------------
    def reset_thresholds(self):
        self.spin_steep.setValue(0.4)
        self.spin_turn.setValue(0.05)
        self.thresholdsChanged.emit()

    def sync_to(self, cfg: Config) -> None:
        """把界面上的值写回配置对象。"""
        cfg.compound_mode = (self.cmb_mode.currentIndex() == 0)
        cfg.dH = self.spin_dh.value()
        cfg.chainage_origin = ["lowest", "start", "end"][self.cmb_origin.currentIndex()]
        cfg.steep_slope = self.spin_steep.value()
        cfg.turn_slope = self.spin_turn.value()
        cfg.raise_enabled = self.chk_raise.isChecked()
        cfg.raise_level = self.spin_raise.value()
        cfg.csv_encoding = self.CSV_ENCODINGS[self.cmb_enc.currentIndex()]

    def sync_from(self, cfg: Config) -> None:
        """把配置的值刷到界面上（打开工程文件后必须做，否则界面与实际不符）。

        必须 blockSignals：否则设置值会逐项触发 changed / thresholdsChanged，
        引起一连串重算，状态栏也会被刷屏。
        """
        widgets = (self.cmb_mode, self.spin_dh, self.cmb_origin,
                   self.spin_steep, self.spin_turn, self.chk_raise,
                   self.spin_raise, self.cmb_enc)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.cmb_mode.setCurrentIndex(0 if cfg.compound_mode else 1)
            self.spin_dh.setValue(cfg.dH)
            self.cmb_origin.setCurrentIndex(
                {"lowest": 0, "start": 1, "end": 2}.get(cfg.chainage_origin, 0))
            self.spin_steep.setValue(cfg.steep_slope)
            self.spin_turn.setValue(cfg.turn_slope)
            self.chk_raise.setChecked(cfg.raise_enabled)
            self.spin_raise.setValue(cfg.raise_level)
            idx = (self.CSV_ENCODINGS.index(cfg.csv_encoding)
                   if cfg.csv_encoding in self.CSV_ENCODINGS else 0)
            self.cmb_enc.setCurrentIndex(idx)
        finally:
            for w in widgets:
                w.blockSignals(False)

    def popup(self):
        """显示并置前（不阻塞）。"""
        self.show()
        self.raise_()
        self.activateWindow()


def _fmt(v, digits: int) -> str:
    """数值 -> 表格文本。空值（None / NaN）显示为空白，表示「不改动该字段」。"""
    if v is None:
        return ""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    return f"{x:.{digits}f}" if math.isfinite(x) else ""


def _parse(table: QTableWidget, r: int, c: int) -> float:
    """表格单元格 -> 数值。空白或非法一律返回 NaN（应用时会被跳过）。"""
    it = table.item(r, c)
    if it is None:
        return float("nan")
    txt = it.text().strip()
    if not txt:
        return float("nan")
    try:
        return float(txt)
    except ValueError:
        return float("nan")


class PasteTable(QTableWidget):
    """支持从 Excel 直接粘贴的表格。

    为什么必须自己处理：Qt 默认的 Ctrl+V 会把整段剪贴板文本塞进**一个**单元格。
    而 Excel 复制出来的是制表符分隔的文本（列间 `\\t`、行间 `\\n`），
    这里重写为：从当前选中格开始按行列铺开。

    只读列（断面名、桩号）会被跳过——**粘错列不会破坏数据**。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.readonly_cols: set[int] = set()
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(26)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Paste):
            self.paste_from_clipboard()
            event.accept()
            return
        super().keyPressEvent(event)

    def paste_from_clipboard(self) -> int:
        """把剪贴板内容按行列铺开写入。返回写入的单元格数。"""
        text = QApplication.clipboard().text()
        if not text:
            return 0

        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while lines and lines[-1].strip() == "":     # 去掉末尾空行
            lines.pop()
        if not lines:
            return 0

        r0 = max(self.currentRow(), 0)
        c0 = max(self.currentColumn(), 0)
        written = 0
        for i, line in enumerate(lines):
            r = r0 + i
            if r >= self.rowCount():
                break
            for j, cell in enumerate(line.split("\t")):
                c = c0 + j
                if c >= self.columnCount():
                    break
                if c in self.readonly_cols:
                    continue
                v = cell.strip()
                item = self.item(r, c)
                if item is None:
                    if v == "":
                        continue
                    item = QTableWidgetItem()
                    self.setItem(r, c, item)
                item.setText(v)
                written += 1
        self.mark_invalid()
        return written

    def mark_invalid(self) -> list[str]:
        """把解析不出数字的格子标红，返回问题描述（供应用前拦截）。"""
        bad: list[str] = []
        for r in range(self.rowCount()):
            name_item = self.item(r, 0)
            name = name_item.text() if name_item else f"第 {r + 1} 行"
            for c in range(self.columnCount()):
                if c in self.readonly_cols:
                    continue
                it = self.item(r, c)
                if it is None:
                    continue
                txt = it.text().strip()
                if txt == "":
                    it.setData(Qt.BackgroundRole, None)
                    continue
                try:
                    float(txt)
                except ValueError:
                    it.setBackground(QColor("#FBE3E3"))
                    head = self.horizontalHeaderItem(c)
                    bad.append(f"{name} · {head.text() if head else c}：{txt}")
                else:
                    it.setData(Qt.BackgroundRole, None)
        return bad


class BatchDialog(QDialog):
    """批量填写：三个页签（流量 / 比降 / 糙率），只列出**当前组**的断面。

    设计要点
    --------
    1. **只显示当前组**：主界面切换纵断面线时表格跟着换；窗口里也能下拉换组，
       两边双向同步。
    2. **表格即编辑区**：打开时用断面的当前参数填好，改完点「应用到当前组」写回。
       空单元格表示**不改动该字段**（不会把参数清空）。
    3. **糙率分区由整组一个开关控制**：勾选后表格变成主槽/左滩/右滩三列；
       取消勾选时把三个分区糙率清回 None（表示该组用统一糙率）。
    4. **不做「只填空白」**：表格已经显示当前值，所见即所得；
       再加一个"只填空白"会与"表格显示当前值"自相矛盾。
    5. **支持从 Excel 粘贴**：见 `PasteTable`，一次可粘一列或多列。
       想给整组填同一个值，在 Excel 里拉好一列粘进来即可。
    """

    #: 参数：是否应用到全部纵断面线（False = 只当前组）
    applyRequested = Signal(bool)
    #: 窗口内换组时发出，让主界面跟着切
    groupChanged = Signal(int)
    #: 分区开关变化，供主界面「当前断面参数」面板同步显示
    zoneChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量填写")
        self.resize(800, 580)
        self.setMinimumWidth(700)

        self.project = None
        self.line_index = 0

        # ---- 当前组 ----
        self.cmb_group = QComboBox()
        self.cmb_group.currentIndexChanged.connect(self._on_group_combo)
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color:#5F5E5A;")

        top = QHBoxLayout()
        top.addWidget(QLabel("当前组"))
        top.addWidget(self.cmb_group, 1)
        top.addWidget(self.lbl_count)
        follow = QLabel("表格只列出当前组，主界面切换分组时会跟着变")
        follow.setStyleSheet("color:#888780; font-size:12px;")
        top.addWidget(follow)

        # ---- 三个页签 ----
        self.t_q, self.t_s, self.t_n = PasteTable(), PasteTable(), PasteTable()
        for t in (self.t_q, self.t_s, self.t_n):
            t.readonly_cols = {0, 1}

        page_q = QWidget()
        lq = QVBoxLayout(page_q)
        lq.setContentsMargins(6, 8, 6, 6)
        lq.addWidget(self.t_q)

        self.btn_from_profile = QPushButton("用纵断面推算填充")
        self.btn_from_profile.setToolTip(
            "按纵断面实测数据推算本组的平均比降并填入表格（默认约翰斯通-克罗斯法）。\n"
            "推算结果是整组共用一个值，填入后仍需点「应用到当前组」才会写进参数。")
        self.btn_from_profile.clicked.connect(self.fill_slope_from_profile)
        note_slope = QLabel("默认约翰斯通-克罗斯法，整组共用一个值")
        note_slope.setStyleSheet("color:#888780; font-size:12px;")
        row_s = QHBoxLayout()
        row_s.addWidget(self.btn_from_profile)
        row_s.addWidget(note_slope)
        row_s.addStretch(1)
        page_s = QWidget()
        ls = QVBoxLayout(page_s)
        ls.setContentsMargins(6, 8, 6, 6)
        ls.addLayout(row_s)
        ls.addWidget(self.t_s)

        self.chk_zone = QCheckBox("分区填写（主槽 / 左滩 / 右滩）")
        self.chk_zone.setToolTip(
            "勾选后本组按主槽/左滩/右滩分别填糙率；取消勾选则整组用统一糙率\n"
            "（写回时会把三个分区糙率清空）。")
        self.chk_zone.toggled.connect(self._on_zone_toggled)
        page_n = QWidget()
        ln_ = QVBoxLayout(page_n)
        ln_.setContentsMargins(6, 8, 6, 6)
        ln_.addWidget(self.chk_zone)
        ln_.addWidget(self.t_n)

        self.tabs = QTabWidget()
        self.tabs.addTab(page_q, "流量")
        self.tabs.addTab(page_s, "比降")
        self.tabs.addTab(page_n, "糙率")

        # ---- 提示与结果 ----
        self.lbl_hint = QLabel(
            "提示：可以直接从 Excel 复制一列（或多列）粘贴进表格——"
            "点中目标单元格后 Ctrl+V，会按行列铺开。留空的单元格表示不改动该项。")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#5F5E5A; font-size:12px;")

        self.lbl_result = QLabel("")
        self.lbl_result.setWordWrap(True)

        # ---- 底部按钮 ----
        self.btn_reload = QPushButton("重新载入当前值")
        self.btn_reload.setToolTip("丢弃表格里的编辑，重新从断面的当前参数填充")
        self.btn_reload.clicked.connect(self.refresh_from_params)

        self.btn_apply = QPushButton("应用到当前组")
        self.btn_apply.setToolTip("把表格里的值写回当前组的断面参数")
        self.btn_apply.clicked.connect(lambda: self._emit_apply(False))

        self.btn_apply_all = QPushButton("应用到全部组")
        self.btn_apply_all.setToolTip(
            "把本组的值套用到**所有纵断面线**的断面。\n"
            "因为各组断面数不同，需要本组的值为一个统一值（各项都相同）。")
        self.btn_apply_all.clicked.connect(lambda: self._emit_apply(True))

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.hide)

        bottom = QHBoxLayout()
        bottom.addWidget(self.btn_reload)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_apply)
        bottom.addWidget(self.btn_apply_all)
        bottom.addWidget(btn_close)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(self.lbl_hint)
        lay.addLayout(bottom)
        lay.addWidget(self.lbl_result)

    # ---------------- 上下文 ----------------
    def _current_line(self):
        if self.project is None or not self.project.profile_lines:
            return None
        i = max(0, min(self.line_index, len(self.project.profile_lines) - 1))
        return self.project.profile_lines[i]

    def set_context(self, project, line_index: int = 0) -> None:
        """由主窗口调用：设置工程与当前组并刷新（打开窗口时用）。"""
        self.project = project
        self.cmb_group.blockSignals(True)
        self.cmb_group.clear()
        if project:
            for ln in project.profile_lines:
                self.cmb_group.addItem(f"{ln.name}（{len(ln.sections)} 个断面）")
        self.cmb_group.blockSignals(False)
        self.set_group(line_index)

    def set_group(self, index: int) -> None:
        """主界面切换分组时调用（同步下拉框 + 刷新表格）。"""
        if self.project is None:
            return
        n = len(self.project.profile_lines)
        self.line_index = max(0, min(index, n - 1)) if n else 0
        self.cmb_group.blockSignals(True)
        self.cmb_group.setCurrentIndex(self.line_index)
        self.cmb_group.blockSignals(False)
        self.refresh_from_params()

    def _on_group_combo(self, index: int) -> None:
        if self.project is None or index < 0:
            return
        self.line_index = index
        self.refresh_from_params()
        self.groupChanged.emit(index)        # 让主界面跟着切

    def zone_enabled(self) -> bool:
        return self.chk_zone.isChecked()

    # ---------------- 填表 ----------------
    def _rows(self) -> list[tuple[str, str, object]]:
        """当前组各断面的 (名称, 桩号文本, params)。"""
        ln = self._current_line()
        if ln is None:
            return []
        out = []
        for k, sec in enumerate(ln.sections):
            ch = ln.chainage[k] if k < len(ln.chainage) else float("nan")
            out.append((sec.name, "" if ch != ch else f"{ch:.1f}", sec.params))
        return out

    def refresh_from_params(self) -> None:
        """用断面的当前参数重建三页表格（丢弃未应用的编辑）。"""
        rows = self._rows()
        self.lbl_count.setText(f"{len(rows)} 个断面" if rows else "")
        enabled = bool(rows)
        for b in (self.btn_apply, self.btn_apply_all, self.btn_from_profile):
            b.setEnabled(enabled)

        self._setup_table(
            self.t_q, ["断面", "桩号 (m)", "设计流量 Qs (m³/s)"],
            [(n, c, [_fmt(p.design_q, 2)]) for n, c, p in rows])
        self._setup_table(
            self.t_s, ["断面", "桩号 (m)", "比降 S"],
            [(n, c, [_fmt(p.slope, 5)]) for n, c, p in rows])

        # 分区开关初值：组内只要有一个断面填了分区糙率就默认勾选，
        # 这样已存在的数据不会被"默认关掉"、一应用就被清空。
        any_zone = any(
            any(v is not None and v == v
                for v in (p.roughness_main, p.roughness_left, p.roughness_right))
            for _n, _c, p in rows)
        if self.chk_zone.isChecked() != any_zone:
            self.chk_zone.blockSignals(True)      # 灰显与同步在下面统一做
            self.chk_zone.setChecked(any_zone)
            self.chk_zone.blockSignals(False)

        self._refresh_roughness_table(rows)
        self._apply_zone_grey(any_zone)
        self.zoneChanged.emit(any_zone)          # 让主界面面板跟着显示

    def _refresh_roughness_table(self, rows=None) -> None:
        """糙率页固定 5 列：断面 | 桩号 | 糙率 n | 左滩 n | 右滩 n。

        **列数恒定**——分区复选框只控制后两列是否可编辑（灰显），
        与主界面「当前断面参数」的 `setEnabled` 表达方式一致，
        不会出现"勾一下列就变了"导致两处对不上。

        第 3 列既是统一糙率也是主槽糙率：两者本来就该是同一个值
        （用户确认），单式断面时它就是整断面糙率。
        """
        rows = self._rows() if rows is None else rows
        data = []
        for n, c, p in rows:
            main = p.roughness
            if not (main == main):          # NaN -> 退而用主槽糙率
                main = p.roughness_main
            data.append((n, c, [_fmt(main, 3),
                                _fmt(p.roughness_left, 3),
                                _fmt(p.roughness_right, 3)]))
        self._setup_table(
            self.t_n, ["断面", "桩号 (m)", "糙率 n", "左滩 n", "右滩 n"], data)

    def _apply_zone_grey(self, on: bool) -> None:
        """不勾选分区时把左滩/右滩两列置灰、设为不可编辑。

        只改**显示**，不动值——方便勾回来时不用重填。
        真正的清空发生在「应用」时：写回会把三个分区糙率设成 None
        （与主界面 `_apply_one` 不勾选时的行为一致）。
        """
        grey = QColor("#909090")
        bg = QColor("#F1EFE8")
        for r in range(self.t_n.rowCount()):
            for c in (3, 4):
                it = self.t_n.item(r, c)
                if it is None:
                    continue
                if on:
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable
                                | Qt.ItemIsEditable)
                    it.setData(Qt.ForegroundRole, None)
                    it.setData(Qt.BackgroundRole, None)
                else:
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    it.setForeground(grey)
                    it.setBackground(bg)

    def _setup_table(self, table: PasteTable, headers: list[str],
                     rows: list[tuple[str, str, list[str]]]) -> None:
        table.clear()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        for i, (name, chs, vals) in enumerate(rows):
            it = QTableWidgetItem(name)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            table.setItem(i, 0, it)
            it2 = QTableWidgetItem(chs)
            it2.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            it2.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(i, 1, it2)
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(i, 2 + j, cell)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(headers)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)

    # ---------------- 糙率分区切换 ----------------
    def _on_zone_toggled(self, checked: bool) -> None:
        """分区开关：只切换左滩/右滩两列的可编辑性（灰显）。

        **不改列数、不动值**——列数恒定才能与主界面「当前断面参数」的
        `setEnabled` 表达方式对得上。切换后把状态发给主界面，两处显示一致。
        """
        self._apply_zone_grey(checked)
        self.zoneChanged.emit(checked)

    # ---------------- 用纵断面推算填充比降 ----------------
    def fill_slope_from_profile(self) -> None:
        """按纵断面实测数据推算本组比降并填入比降页（不直接改参数）。"""
        ln = self._current_line()
        if ln is None or self.project is None:
            return
        try:
            props, _ = propose_slopes(self.project, mode="jc")
        except Exception as e:                       # pragma: no cover
            QMessageBox.warning(self, "推算失败", str(e))
            return
        value = None
        for p in props:
            if p.line == ln.name:
                value = None if p.bad else p.proposed
                break
        if value is None:
            QMessageBox.information(
                self, "无法推算",
                f"「{ln.name}」没有可用的纵断面推算结果（可能缺纵断面数据或整段倒坡）。")
            return

        txt = _fmt(value, 5)
        self.tabs.setCurrentIndex(1)
        for r in range(self.t_s.rowCount()):
            it = self.t_s.item(r, 2)
            if it is not None:
                it.setText(txt)
        self.set_result(f"已按纵断面推算填入比降 {txt}（点「应用到当前组」写回）。")

    # ---------------- 读取与应用 ----------------
    def collect(self) -> dict:
        """读表格 -> {断面名: {字段: 值}}。空白或非法一律为 NaN（应用时跳过）。

        糙率第 3 列（糙率 n）**既是统一糙率也是主槽糙率**——两者本来就该同值，
        所以勾选分区时两个字段都写这一个值，主界面两个框也不会出现分叉。
        """
        rows = self._rows()
        zone = self.chk_zone.isChecked()
        out: dict[str, dict] = {}
        for i, (name, _ch, _p) in enumerate(rows):
            main = _parse(self.t_n, i, 2)
            rec = {
                "design_q": _parse(self.t_q, i, 2),
                "slope": _parse(self.t_s, i, 2),
                "roughness": main,
            }
            if zone:
                rec["roughness_main"] = main
                rec["roughness_left"] = _parse(self.t_n, i, 3)
                rec["roughness_right"] = _parse(self.t_n, i, 4)
            else:
                # 不勾选分区：写回时会把三个分区糙率清成 None，这里填 NaN 即可
                rec["roughness_main"] = float("nan")
                rec["roughness_left"] = float("nan")
                rec["roughness_right"] = float("nan")
            out[name] = rec
        return out

    def _emit_apply(self, to_all: bool) -> None:
        bad: list[str] = []
        for t in (self.t_q, self.t_s, self.t_n):
            bad += t.mark_invalid()
        if bad:
            QMessageBox.warning(
                self, "有无法识别的数值",
                "下面这些单元格不是数字，请改正后再应用：\n\n" +
                "\n".join(bad[:8]) + ("\n…" if len(bad) > 8 else ""))
            return
        self.applyRequested.emit(to_all)

    def set_result(self, text: str, ok: bool = True) -> None:
        color = "#0F6E56" if ok else "#A32D2D"
        self.lbl_result.setText(f"<span style='color:{color}'>{text}</span>")

    def popup(self):
        self.show()
        self.raise_()
        self.activateWindow()


class SlopeDialog(QDialog):
    """按纵断面推算比降。

    设计取舍
    --------
    1. **先出建议值表，不自动改参数** —— 比降直接决定流量，改错了图上看不出来，
       必须让用户核对后再应用。表里同时给出「当前值」和「建议值」，
       以及每条线的河段范围/落差/R²，便于判断推算是否可信。
    2. **两种口径并列，不设默认** —— 实测数据上二者可差数倍
       （ypc6 相邻段 0.0029 vs 滑动窗口 0.0109），必须由用户选。
    3. **倒坡/无效的建议值标红并拒绝写入** —— 曼宁公式里是 sqrt(S)，
       S ≤ 0 会直接算不出流量；悄悄写进去的后果是整条曲线报废。
    """

    applied = Signal(bool)      # 参数：only_missing

    COLS = ["纵断面线", "断面", "起点距/m", "桩号/m", "纵断面高程/m",
            "当前比降", "建议比降", "差异"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("按纵断面推算比降")
        self.resize(980, 580)

        self.project = None
        self.proposals: list = []
        self.estimates: list = []

        # ---------------- 口径 ----------------
        self.rb_jc = QRadioButton(MODE_LABELS["jc"])
        self.rb_end = QRadioButton(MODE_LABELS["endpoints"])
        self.rb_lsq = QRadioButton(MODE_LABELS["lsq"])
        self.rb_jc.setChecked(True)              # Q13 确认：默认用教材标准方法
        self.rb_jc.setToolTip(
            "Johnstone & Cross (1949)，教材标准的平均纵比降算法。\n"
            "S = [ Σ L_i·√S_i / Σ L_i ]²\n"
            "把河段按纵断面相邻测点分成子段，对 √S 做长度加权平均再平方。\n"
            "因为曼宁公式 Q ∝ √S，这样得到的等效比降在**输水能力**上\n"
            "等价于分段情况；而两端点法只在**落差**上等价。\n"
            "数学上恒 ≤ 两端点法（√S 是凹函数），各段比降越均匀两者越接近。\n"
            "局部倒坡子段无法开方，会被跳过并在汇总区报出段数。")
        self.rb_end.setToolTip(
            "「平均比降」的朴素定义：河段两端高程差 ÷ 河段长度。\n"
            "只用首末两点，结果稳健、可手算复核。\n"
            "注：网上部分资料把它写成 ΣL_i·S_i / ΣL_i，那是同一个东西——\n"
            "ΣL_i·S_i = ΣΔh_i = 总落差，恒等。")
        self.rb_lsq.setToolTip(
            "对全部（起点距, 高程）做一元线性回归，取斜率的负值。\n"
            "用到每一个实测测点，单个测点的误差影响更小。\n"
            "汇总区会给 R²——越接近 1 说明河底越接近一条直线。")
        for rb in (self.rb_jc, self.rb_end, self.rb_lsq):
            rb.toggled.connect(self._on_mode_changed)

        grp_mode = QGroupBox("算法口径")
        v_mode = QVBoxLayout()
        v_mode.addWidget(self.rb_jc)
        v_mode.addWidget(self.rb_end)
        v_mode.addWidget(self.rb_lsq)
        grp_mode.setLayout(v_mode)

        # ---------------- 范围 ----------------
        self.chk_full = QCheckBox(
            "使用整条纵断面（含横断面覆盖范围之外的上下游延伸段）")
        self.chk_full.setToolTip(
            "不勾选（默认）：只用最上游到最下游横断面之间那段纵断面——\n"
            "比降是填给这些断面用的，延伸段的坡降未必代表本段河道。\n"
            "勾选：用整条纵断面的全部范围。")
        self.chk_full.toggled.connect(lambda _: self.refresh())

        # ---------------- 汇总 ----------------
        self.lbl_summary = QLabel("")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setTextFormat(Qt.RichText)
        self.lbl_summary.setStyleSheet(
            "background:#F5F7FA; border:1px solid #DCE3EC; padding:6px;")

        # ---------------- 表格 ----------------
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for c in range(2, len(self.COLS)):
            hdr.setSectionResizeMode(c, QHeaderView.Stretch)

        # ---------------- 底部按钮 ----------------
        self.btn_all = QPushButton("应用到全部断面")
        self.btn_all.setToolTip("把所有断面的比降都换成建议值（覆盖当前值）")
        self.btn_all.clicked.connect(lambda: self._apply(False))

        self.btn_missing = QPushButton("仅填补空白断面")
        self.btn_missing.setToolTip(
            "只给还没填比降的断面补上，已手填过的一律不动。")
        self.btn_missing.clicked.connect(lambda: self._apply(True))

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.hide)

        row = QHBoxLayout()
        row.addWidget(self.btn_all)
        row.addWidget(self.btn_missing)
        row.addStretch(1)
        row.addWidget(btn_close)

        self.lbl_hint = QLabel(
            "提示：本窗口为非模态，改动不会立即生效——核对建议值后点上面的按钮才会写入参数。")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#5F5E5A;")

        lay = QVBoxLayout(self)
        lay.addWidget(grp_mode)
        lay.addWidget(self.chk_full)
        lay.addWidget(self.lbl_summary)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.lbl_hint)
        lay.addLayout(row)

    # ---------------- 数据 ----------------
    def set_project(self, project) -> None:
        self.project = project
        self.refresh()

    def mode(self) -> str:
        if self.rb_jc.isChecked():
            return "jc"
        return "lsq" if self.rb_lsq.isChecked() else "endpoints"

    def _on_mode_changed(self, _=None):
        self.refresh()

    def refresh(self) -> None:
        if self.project is None:
            self.table.setRowCount(0)
            self.lbl_summary.setText("请先载入数据。")
            self.btn_all.setEnabled(False)
            self.btn_missing.setEnabled(False)
            return

        self.proposals, self.estimates = propose_slopes(
            self.project, mode=self.mode(),
            use_full_profile=self.chk_full.isChecked())
        self._fill_summary()
        self._fill_table()

    def _fill_summary(self) -> None:
        """按线列出河段信息；当前口径用粗体，另两个口径用浅色附在后面。

        并列显示是有意的：三种口径在同一段河道上可能差 5%~25%，
        用户需要一眼看到差异才能判断该信哪个（尤其某个断面结果异常时，
        有参照物才能区分「数据问题」还是「算法问题」）。
        """
        mode = self.mode()
        short = {"jc": "JC 法", "endpoints": "两端点", "lsq": "最小二乘"}
        rows = []
        for est in self.estimates:
            if not est.ok:
                rows.append(f"<b>{est.line}</b>　"
                            f"<span style='color:#A32D2D'>✗ {est.message}</span>")
                continue
            cur = est.slope(mode)
            txt = (f"<b>{est.line}</b>　河段 {est.start:.1f} ~ {est.end:.1f} m"
                   f"（长 {est.length:.1f} m，落差 {est.drop:.3f} m，"
                   f"{est.n_points} 个测点）　比降 <b>{cur:.5f}</b>")
            if mode == "jc":
                txt += f"（{est.jc_segments} 段参与"
                if est.jc_skipped:
                    txt += f"，跳过 {est.jc_skipped} 段倒坡"
                txt += "）"
            if mode == "lsq" and math.isfinite(est.r2):
                txt += f"　R²={est.r2:.3f}"

            others = "；".join(
                f"{short[m]} {est.slope(m):.5f}"
                for m in SLOPE_MODES if m != mode)
            txt += f"　<span style='color:#909090'>［{others}］</span>"

            if est.message:
                txt += f"　<span style='color:#A32D2D'>⚠ {est.message}</span>"
            rows.append(txt)
        self.lbl_summary.setText("<br>".join(rows))

    def _fill_table(self) -> None:
        bad_bg = QColor("#FBE3E3")
        bad_fg = QColor("#A32D2D")
        self.table.setRowCount(len(self.proposals))
        for i, p in enumerate(self.proposals):
            cur = "（空）" if not math.isfinite(p.current) else f"{p.current:.5f}"
            prop = "—" if p.bad else f"{p.proposed:.5f}"
            delta = "—" if not math.isfinite(p.delta) else f"{p.delta:+.5f}"
            vals = [p.line, p.section, f"{p.profile_dist:.2f}",
                    f"{p.chainage:.2f}", f"{p.z_profile:.3f}",
                    cur, prop, delta]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if j >= 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if p.bad:
                    item.setBackground(bad_bg)
                    item.setForeground(bad_fg)
                    item.setToolTip("建议值无效（倒坡或数据不足），不会被写入参数")
                self.table.setItem(i, j, item)

    # ---------------- 应用 ----------------
    def _apply(self, only_missing: bool) -> None:
        n = len(self.proposals)
        if not n:
            return
        bad = [p for p in self.proposals if p.bad]
        if bad and not only_missing:
            names = "、".join(p.section for p in bad[:6])
            more = "…" if len(bad) > 6 else ""
            ans = QMessageBox.question(
                self, "存在无效建议值",
                f"有 {len(bad)} 个断面的建议比降无效（倒坡或数据不足）："
                f"{names}{more}\n\n这些断面会被跳过，其余照常写入。是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans != QMessageBox.Yes:
                return
        self.applied.emit(only_missing)

    def popup(self):
        self.show()
        self.raise_()
        self.activateWindow()


# ======================================================================
# 手动调节（Q14）：分区 / 成灾水位
#
# 两个面板刻意**不提供数值输入**，一切靠"在主界面的断面形态图上点测点"。
# 这条是用户定的，而且从程序结构上说也必须如此：
# 分区边界与深泓点在核心层就是**测点索引**，落在两个测点之间的位置
# 没有任何数据结构能表达；成灾水位也约定取自某个测点的高程。
# ======================================================================

def _pt_text(sec: Section, idx) -> str:
    """把一个测点索引渲染成可读文本。"""
    if idx is None or not isinstance(idx, int) or not (0 <= idx < sec.n_points):
        return "—"
    return (f"起点距 {sec.s[idx]:.1f} m　高程 {sec.z[idx]:.2f} m　"
            f"第 {idx + 1} 个测点")


def _chainage_of(project, line_index: int, row: int):
    if project is None:
        return None
    try:
        ch = project.profile_lines[line_index].chainage
    except IndexError:
        return None
    if ch and row < len(ch) and ch[row] == ch[row]:
        return ch[row]
    return None


class _ManualBase(QDialog):
    """「分区调节」与「成灾水位」的共同骨架。

    共同约定：
    1. **只显示当前组**，跟随主界面切换，不自己维护"选中哪个断面"。
    2. **非模态**，改动立即重算并反映到三张图上，可以边看图边调。
    3. 顶部**永远显示当前断面是哪一个** —— 拾取动作发生在另一个窗口
       （主界面的断面形态图），不写清楚点下去会改到谁，很容易改错断面。
    4. 状态表**只读**：它的作用是"一眼看清这一组里哪些断面被人工改过"，
       不是输入控件。
    """

    pickRequested = Signal(str)        # 参数：拾取目标，见 section_view.PICK_LABELS
    clearRequested = Signal(str)       # 参数：要清除哪个目标的手动值
    resetGroupRequested = Signal()     # 本组全部恢复自动
    groupChanged = Signal(int)         # 面板里换组 -> 主界面跟着切
    sectionActivated = Signal(int)     # 双击状态表某行 -> 主界面切到该断面

    TITLE = ""
    COLS: list[str] = []
    HINT = ""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.TITLE)
        self.resize(820, 640)

        self.project = None
        self.infos: dict = {}
        self.results: dict = {}
        self.cfg = None
        self.line_index = 0
        self.sections: list[Section] = []
        self.current: Section | None = None
        self._picking: str | None = None
        self._group_busy = False
        self._pick_widgets: dict[str, tuple] = {}
        self._row = 0

        # ---------------- 顶部 ----------------
        self.lbl_cur = QLabel("—")
        self.lbl_cur.setStyleSheet("color:#185FA5; font-weight:600;")
        head = QHBoxLayout()
        tt = QLabel(self.TITLE)
        tt.setStyleSheet("font-size:15px; font-weight:600;")
        head.addWidget(tt)
        head.addStretch(1)
        head.addWidget(QLabel("当前断面"))
        head.addWidget(self.lbl_cur)

        self.cmb_group = QComboBox()
        self.cmb_group.setMinimumWidth(260)
        self.cmb_group.currentIndexChanged.connect(self._on_group_combo)
        self.lbl_count = QLabel("")
        grp_row = QHBoxLayout()
        grp_row.addWidget(QLabel("当前组"))
        grp_row.addWidget(self.cmb_group, 1)
        grp_row.addWidget(self.lbl_count)

        # ---------------- 字段区（子类填充）----------------
        self.body = QGridLayout()
        self.body.setHorizontalSpacing(10)
        self.body.setVerticalSpacing(8)
        self.body.setColumnStretch(1, 1)
        self._build_body()

        # ---------------- 提示条 ----------------
        self.lbl_warn = QLabel("")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setTextFormat(Qt.RichText)
        self.lbl_warn.setStyleSheet(
            "background:#FAEEDA; color:#854F0B; padding:6px 9px; border-radius:5px;")
        self.lbl_warn.setVisible(False)

        self.lbl_pick = QLabel("")
        self.lbl_pick.setWordWrap(True)
        self.lbl_pick.setStyleSheet(
            "background:#E6F1FB; color:#185FA5; padding:6px 9px; border-radius:5px;")
        self.lbl_pick.setVisible(False)

        # ---------------- 状态表 ----------------
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(
            lambda r, _c: self.sectionActivated.emit(r))
        hdr = self.table.horizontalHeader()
        for c in range(len(self.COLS)):
            hdr.setSectionResizeMode(c, QHeaderView.Stretch)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)

        # ---------------- 底部 ----------------
        self.btn_reset = QPushButton("本组全部恢复自动")
        self.btn_reset.setToolTip("把本组所有断面的手动设定全部清掉，回到程序自动推算")
        self.btn_reset.clicked.connect(self.resetGroupRequested.emit)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.hide)

        foot = QHBoxLayout()
        foot.addWidget(self.btn_reset)
        foot.addStretch(1)
        foot.addWidget(btn_close)

        self.lbl_hint = QLabel(self.HINT)
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#5F5E5A;")

        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addLayout(grp_row)
        lay.addLayout(self.body)
        lay.addWidget(self.lbl_hint)
        lay.addWidget(self.lbl_warn)
        lay.addWidget(self.lbl_pick)
        lay.addWidget(self.table, 1)
        lay.addLayout(foot)

    # ---------------- 子类实现 ----------------
    def _build_body(self):
        raise NotImplementedError

    def _fill_current(self):
        raise NotImplementedError

    def _fill_table(self):
        raise NotImplementedError

    def _update_warn(self):
        raise NotImplementedError

    def _is_manual(self, key: str) -> bool:
        raise NotImplementedError

    def _row_manual_note(self, sec: Section) -> str:
        raise NotImplementedError

    # ---------------- 构造小工具 ----------------
    def _add_section(self, text: str):
        lb = QLabel(text)
        lb.setStyleSheet("color:#5F5E5A; font-size:12px; "
                         "border-bottom:1px solid #E3E1D9; padding-bottom:2px;")
        self.body.addWidget(lb, self._row, 0, 1, 4)
        self._row += 1

    def _add_row(self, key: str, label: str, pickable: bool = True) -> QLabel:
        lb = QLabel(label)
        lb.setStyleSheet("color:#5F5E5A;")
        val = QLabel("—")
        val.setTextFormat(Qt.RichText)
        val.setWordWrap(True)
        self.body.addWidget(lb, self._row, 0)
        self.body.addWidget(val, self._row, 1)
        if pickable:
            bp = QPushButton("图上拾取")
            bp.setToolTip("到主界面的「断面形态」图上点一个测点")
            bp.clicked.connect(lambda _=False, k=key: self.pickRequested.emit(k))
            bc = QPushButton("清除")
            bc.clicked.connect(lambda _=False, k=key: self.clearRequested.emit(k))
            self.body.addWidget(bp, self._row, 2)
            self.body.addWidget(bc, self._row, 3)
            self._pick_widgets[key] = (val, bp, bc)
        self._row += 1
        return val

    def _add_note(self, text: str = "") -> QLabel:
        lb = QLabel(text)
        lb.setTextFormat(Qt.RichText)
        lb.setWordWrap(True)
        lb.setStyleSheet("color:#888780; font-size:12px;")
        self.body.addWidget(lb, self._row, 1, 1, 3)
        self._row += 1
        return lb

    # ---------------- 上下文 ----------------
    def set_context(self, project, infos: dict, results: dict, cfg,
                    line_index: int | None = None):
        """由主窗口推入全部上下文。面板不自己保存工程/结果的引用之外的状态。"""
        self.project = project
        self.infos, self.results, self.cfg = infos, results, cfg
        if line_index is not None:
            self.line_index = max(line_index, 0)
        self.refresh()

    def set_group(self, index: int):
        self.line_index = max(index, 0)
        self.refresh()

    def set_current(self, sec: Section | None):
        self.current = sec
        self._fill_current()
        self._update_warn()
        self._sync_buttons()

    def set_picking(self, target: str | None):
        self._picking = target
        if not target:
            self.lbl_pick.setVisible(False)
            return
        from .section_view import PICK_LABELS
        lb = PICK_LABELS.get(target, target)
        self.lbl_pick.setText(
            f"拾取模式：正在拾取【{lb}】… 请在主界面的「断面形态」图上点击一个测点。"
            f"按 Esc、右键，或再点一次「图上拾取」可取消。")
        self.lbl_pick.setVisible(True)

    # ---------------- 刷新 ----------------
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

    def _on_group_combo(self, index: int):
        if self._group_busy or index < 0:
            return
        self.line_index = index
        self.groupChanged.emit(index)

    def _current_sections(self) -> list[Section]:
        if self.project is None:
            return []
        try:
            return list(self.project.profile_lines[self.line_index].sections)
        except IndexError:
            return []

    def _info(self):
        if self.current is None:
            return None
        return self.infos.get(self.current.name)

    def _res(self):
        if self.current is None:
            return None
        return self.results.get(self.current.name)

    def refresh(self):
        self._reload_groups()
        self.sections = self._current_sections()
        self.lbl_count.setText(f"{len(self.sections)} 个断面" if self.sections else "")
        self.lbl_cur.setText(
            f"{self.current.name}　共 {self.current.n_points} 个测点"
            if self.current is not None else "—")
        self._fill_current()
        self._fill_table()
        self._update_warn()
        self._sync_buttons()

    def _sync_buttons(self):
        ok = self.current is not None and self._info() is not None
        self.btn_reset.setEnabled(bool(self.sections))
        for key, (val, bp, bc) in self._pick_widgets.items():
            bp.setEnabled(ok)
            bc.setEnabled(ok and self._is_manual(key))

    def popup(self):
        self.show()
        self.raise_()
        self.activateWindow()


class ZoneDialog(_ManualBase):
    """分区调节：手动深泓点 + 手动分区边界（Q15：后者与转折点已拆成两个概念）。

    四件事必须让用户看得见，否则很容易以为是程序算错：
      1. 深泓点一旦手动，**全程序**都用它（含 H~Q 曲线的起算水位），
         若它高于断面最低测点，曲线会少掉最低的若干行 —— 面板直接算出少几行。
      2. 单断面模式下分区**完全不参与计算**，此时怎么调都不会有变化。
      3. 手动值若不自洽（边界跑到深泓点同侧等），程序会暂时改用自动结果。
      4. **改分区边界不会改成灾水位**。转折点是扫描算法的产物、永远自动，
         成灾水位由它决定；分区边界默认等于转折点，可单独人工指定。
         不说清这一点，用户会奇怪"我把边界挪了，成灾水位怎么纹丝不动"。
    """

    TITLE = "分区调节"
    COLS = ["本组断面", "桩号 m", "深泓点 起点距 m", "左边界 起点距 m",
            "右边界 起点距 m", "分区", "来源"]
    HINT = ("深泓点、左边界、右边界都只能**在图上点实测测点**获得，没有数值输入。\n"
            "这里的「左/右边界」是**分区边界**（决定分区与各分区糙率取值），"
            "默认与断面图上青色的「转折点」重合。改分区边界**不会改动转折点，"
            "也不会改成灾水位**——转折点永远是程序自动扫出来的。\n"
            "清除某个边界 = 该侧不设边界；两侧都清除即为「不分区」（1 区）。"
            "要退回程序自动扫描（即用转折点当边界），用下面的「本组全部恢复自动」。\n"
            "边界必须分别落在深泓点的两侧，否则不写入（会明确提示，不会静默改掉）。")

    def _build_body(self):
        self._add_section("基准点")
        self._add_row("thalweg", "深泓点")
        self.lbl_th_ref = self._add_note("")

        self._add_section("分区边界（须分别在深泓点两侧）")
        self._add_row("left", "左边界")
        self._add_row("right", "右边界")

        self._add_section("分区结果")
        self.lbl_zone = self._add_note("")

    # ---------------- 字段显示 ----------------
    def _is_manual(self, key: str) -> bool:
        sec = self.current
        if sec is None:
            return False
        if key == "thalweg":
            return sec.thalweg_manual is not None
        if key == "left":
            return sec.zone_manual and sec.zone_left is not None
        if key == "right":
            return sec.zone_manual and sec.zone_right is not None
        return False

    def _set_pt(self, key: str, sec: Section, idx, manual: bool):
        val = self._pick_widgets[key][0]
        if idx is None or not (0 <= idx < sec.n_points):
            val.setText("<span style='color:#888780'>—（该侧无边界）</span>")
            return
        txt = _pt_text(sec, idx)
        if manual:
            val.setText(f"<span style='color:#185FA5'>{txt}　（手动）</span>")
        else:
            val.setText(f"{txt}　<span style='color:#888780'>（自动）</span>")

    def _fill_current(self):
        sec, info = self.current, self._info()
        if sec is None or info is None:
            for val, _b, _c in self._pick_widgets.values():
                val.setText("—")
            self.lbl_th_ref.setText("")
            self.lbl_zone.setText("")
            return

        self._set_pt("thalweg", sec, info.dmin_idx,
                     sec.thalweg_manual is not None)
        self._set_pt("left", sec, info.zone_left_idx, self._is_manual("left"))
        self._set_pt("right", sec, info.zone_right_idx, self._is_manual("right"))
        # ⚠ 用 zone_*_idx（分区边界），不是 left/right_turn_idx（转折点）：
        #   两者默认相同，但人工指定边界后就分道扬镳了（Q15）。

        ai = info.dmin_auto_idx
        if sec.thalweg_manual is not None and 0 <= ai < sec.n_points:
            self.lbl_th_ref.setText(
                f"断面最低测点：{_pt_text(sec, ai)}"
                + ("　（与手动值相同）" if ai == info.dmin_idx else ""))
        else:
            self.lbl_th_ref.setText("")

        nz = len(info.zones)
        if nz <= 1:
            self.lbl_zone.setText("1 区（不分区，整个断面一起算）")
        else:
            names = ("左滩 / 主槽 / 右滩" if nz == 3 else "左段 / 右段")
            ns = [sec.params.roughness_for_zone(k, nz) for k in range(nz)]
            self.lbl_zone.setText(
                f"{nz} 区　{names}　<span style='color:#888780'>"
                f"糙率依次 {' / '.join(f'{v:.3f}' for v in ns)}</span>")

    # ---------------- 警告 ----------------
    def _update_warn(self):
        msgs: list[str] = []
        sec, info = self.current, self._info()

        if self.cfg is not None and not self.cfg.compound_mode:
            msgs.append(
                "⚠ 当前是单断面模式：分区不参与计算，改边界不会有任何效果。"
                "请到「计算设置」把断面模式改为复式断面。")

        if sec is not None and info is not None:
            errs = sec.override_errors()
            if errs:
                msgs.append("⚠ 手动值不自洽，已暂时改用自动结果：" + "；".join(errs))

            if (sec.thalweg_manual is not None
                    and info.dmin == info.dmin and info.dmin_auto == info.dmin_auto
                    and info.dmin > info.dmin_auto + 1e-9):
                d = info.dmin - info.dmin_auto
                # 行数必须**实际数**，不能用 ceil(Δ/dH) 估：
                # 水位是等差数列，深泓点一动整条网格平移，与上端的对齐关系
                # 跟着变，实测 31 个断面里 21 个与估算差 1 行。
                rows_auto, rows_eff = (0, 0)
                if self.cfg is not None:
                    rows_auto, rows_eff = hvec_row_counts(info, self.cfg)
                if rows_eff < rows_auto:
                    tail = (f"水位行数 {rows_auto} → {rows_eff}，"
                            f"少 {rows_auto - rows_eff} 行")
                elif rows_auto:
                    tail = f"水位行数仍为 {rows_auto} 行（上端补回，未减少）"
                else:
                    tail = ""
                msgs.append(
                    f"⚠ 手动深泓点比断面最低测点高 {d:.2f} m：H~Q 曲线的起算水位"
                    f"将随之抬高 {d:.2f} m" + (f"，{tail}" if tail else "")
                    + "。若不希望如此，请把深泓点改选为最低测点。")

        self.lbl_warn.setText("<br>".join(msgs))
        self.lbl_warn.setVisible(bool(msgs))

    # ---------------- 状态表 ----------------
    def _row_manual_note(self, sec: Section) -> str:
        parts = []
        if sec.thalweg_manual is not None:
            parts.append("深泓点")
        if sec.zone_manual:
            parts.append("分区边界")
        return "、".join(parts)

    def _fill_table(self):
        self.table.setRowCount(0)
        cur_row = -1
        for i, sec in enumerate(self.sections):
            info = self.infos.get(sec.name)
            r = self.table.rowCount()
            self.table.insertRow(r)
            if sec is self.current:
                cur_row = r

            ch = _chainage_of(self.project, self.line_index, i)
            self.table.setItem(r, 0, QTableWidgetItem(sec.name))
            self.table.setItem(r, 1, QTableWidgetItem(
                f"{ch:.1f}" if ch is not None else "—"))
            if info is None:
                for c in range(2, len(self.COLS)):
                    self.table.setItem(r, c, QTableWidgetItem("—"))
                continue

            def ptext(idx, manual):
                if idx is None or not (0 <= idx < sec.n_points):
                    return "—", None
                return f"{sec.s[idx]:.1f}", ("#185FA5" if manual else None)

            for col, key, idx, manual in (
                    (2, "thalweg", info.dmin_idx, sec.thalweg_manual is not None),
                    (3, "left", info.zone_left_idx, self._is_manual("left")),
                    (4, "right", info.zone_right_idx, self._is_manual("right"))):
                t, color = ptext(idx, manual)
                it = QTableWidgetItem(t)
                if color:
                    it.setForeground(QColor(color))
                self.table.setItem(r, col, it)

            self.table.setItem(r, 5, QTableWidgetItem(f"{len(info.zones)} 区"))
            note = self._row_manual_note(sec)
            it = QTableWidgetItem("手动" if note else "自动")
            if note:
                it.setForeground(QColor("#185FA5"))
                it.setToolTip(f"人工设定：{note}")
            self.table.setItem(r, 6, it)

        self._highlight(cur_row)
        self.table.resizeRowsToContents()

    def _highlight(self, row: int):
        if row < 0:
            return
        for c in range(len(self.COLS)):
            it = self.table.item(row, c)
            if it is not None:
                it.setBackground(QColor("#E6F1FB"))


class DisasterDialog(_ManualBase):
    """成灾水位：手动指定。

    成灾水位由**转折点**决定（取两个转折点里较低的那个高程），而转折点是扫描算法的
    产物、永远自动，人工改不了。所以：

    - 改分区边界（「分区调节」面板）**不会**影响这里的自动值（Q15 明确要求）
    - 只有手动改深泓点会——因为扫描是从深泓点向两侧进行的，起点变了结果就变
    - 面板把"自动值"与"手动值"并排显示：不并排放，用户看到自动值变了个数
      却找不到原因（尤其改了深泓点之后）
    """

    TITLE = "成灾水位"
    COLS = ["本组断面", "桩号 m", "深泓 m", "自动成灾水位 m",
            "手动成灾水位 m", "成灾流量 m³/s", "来源"]
    HINT = ("成灾水位只能**在图上点实测测点**获得，取该点的高程。\n"
            "「自动」是按**转折点**推出来的（两个转折点里较低的那个高程）。"
            "转折点永远是程序自动扫出来的，所以**改分区边界不会影响它**——"
            "「分区调节」面板里改边界，这里的自动值不会动。\n"
            "成灾流量由水位在 H~Q 曲线上反查，超出曲线范围时是外推值，面板会标出。")

    def _build_body(self):
        self._add_section("成灾水位")
        self._add_row("disaster", "手动")
        self.lbl_auto = self._add_note("")
        self.lbl_flow = self._add_note("")

    def _is_manual(self, key: str) -> bool:
        return (key == "disaster" and self.current is not None
                and self.current.disaster_idx_manual is not None)

    def _fill_current(self):
        sec, info = self.current, self._info()
        if sec is None or info is None:
            for val, _b, _c in self._pick_widgets.values():
                val.setText("—")
            self.lbl_auto.setText("")
            self.lbl_flow.setText("")
            return

        self.lbl_auto.setText("自动：<b>%.2f m</b>　%s"
                              % (info.disaster_level_auto, _auto_source(sec, info)))

        dm = sec.disaster_idx_manual
        val = self._pick_widgets["disaster"][0]
        if dm is None:
            val.setText("<span style='color:#888780'>未设置（按自动值）</span>")
        else:
            depth = info.disaster_level - info.dmin
            val.setText(f"<span style='color:#185FA5'>{_pt_text(sec, dm)}"
                        f"　深泓以上 {depth:.2f} m</span>")

        res = self._res()
        if res is not None and res.disaster_flow == res.disaster_flow:
            self.lbl_flow.setText(
                f"成灾流量：<b>{res.disaster_flow:.2f} m³/s</b>"
                f"　<span style='color:#888780'>在 H~Q 曲线上按 "
                f"{info.disaster_level:.2f} m 反查</span>")
        else:
            self.lbl_flow.setText("")

    def _update_warn(self):
        msgs: list[str] = []
        sec, info = self.current, self._info()
        if sec is not None and info is not None:
            errs = sec.override_errors()
            if errs:
                msgs.append("⚠ 手动值不自洽，已暂时改用自动结果：" + "；".join(errs))
            if sec.disaster_idx_manual is not None:
                lv = info.disaster_level
                if lv == lv and info.zymin == info.zymin and lv > info.zymin + 1e-9:
                    msgs.append(f"⚠ 手动成灾水位 {lv:.2f} m 高于该断面的水位计算上限 "
                                f"{info.zymin:.2f} m，成灾流量将是外推值。")
                if lv == lv and info.dmin == info.dmin and lv < info.dmin - 1e-9:
                    msgs.append(f"⚠ 手动成灾水位 {lv:.2f} m 低于深泓点高程 "
                                f"{info.dmin:.2f} m，该水位下断面基本不过水，"
                                f"成灾流量接近 0。")
        self.lbl_warn.setText("<br>".join(msgs))
        self.lbl_warn.setVisible(bool(msgs))

    def _row_manual_note(self, sec: Section) -> str:
        return "成灾水位" if sec.disaster_idx_manual is not None else ""

    def _fill_table(self):
        self.table.setRowCount(0)
        cur_row = -1
        for i, sec in enumerate(self.sections):
            info = self.infos.get(sec.name)
            res = self.results.get(sec.name)
            r = self.table.rowCount()
            self.table.insertRow(r)
            if sec is self.current:
                cur_row = r

            ch = _chainage_of(self.project, self.line_index, i)
            self.table.setItem(r, 0, QTableWidgetItem(sec.name))
            self.table.setItem(r, 1, QTableWidgetItem(
                f"{ch:.1f}" if ch is not None else "—"))
            if info is None:
                for c in range(2, len(self.COLS)):
                    self.table.setItem(r, c, QTableWidgetItem("—"))
                continue

            self.table.setItem(r, 2, QTableWidgetItem(
                f"{info.dmin:.2f}" if info.dmin == info.dmin else "—"))
            it = QTableWidgetItem(
                f"{info.disaster_level_auto:.2f}"
                if info.disaster_level_auto == info.disaster_level_auto else "—")
            it.setForeground(QColor("#888780"))
            self.table.setItem(r, 3, it)

            dm = sec.disaster_idx_manual
            if dm is not None and 0 <= dm < sec.n_points:
                it = QTableWidgetItem(f"{sec.z[dm]:.2f}")
                it.setForeground(QColor("#185FA5"))
            else:
                it = QTableWidgetItem("—")
                it.setForeground(QColor("#888780"))
            self.table.setItem(r, 4, it)

            over = (info.disaster_level == info.disaster_level
                    and info.zymin == info.zymin
                    and info.disaster_level > info.zymin + 1e-9)
            if res is not None and res.disaster_flow == res.disaster_flow:
                it = QTableWidgetItem(f"{res.disaster_flow:.2f}")
                if over:
                    it.setForeground(QColor("#A32D2D"))
            else:
                it = QTableWidgetItem("—")
            self.table.setItem(r, 5, it)

            manual = dm is not None
            it = QTableWidgetItem("手动" if manual else "自动")
            if manual:
                it.setForeground(QColor("#185FA5"))
            self.table.setItem(r, 6, it)

        self._highlight(cur_row)
        self.table.resizeRowsToContents()

    def _highlight(self, row: int):
        if row < 0:
            return
        for c in range(len(self.COLS)):
            it = self.table.item(row, c)
            if it is not None:
                it.setBackground(QColor("#E6F1FB"))


def _auto_source(sec: Section, info) -> str:
    """给"自动成灾水位"标出来源，便于对照手动值。"""
    L, R = info.left_turn_idx, info.right_turn_idx
    if L is None and R is None:
        return "<span style='color:#888780'>（断面无转折点，取深泓点高程）</span>"
    if L is None:
        return (f"<span style='color:#888780'>（取自右转折点，"
                f"第 {R + 1} 个测点）</span>")
    if R is None:
        return (f"<span style='color:#888780'>（取自左转折点，"
                f"第 {L + 1} 个测点）</span>")
    k = L if sec.z[L] <= sec.z[R] else R
    side = "左" if k == L else "右"
    return (f"<span style='color:#888780'>（取自{side}转折点，"
            f"第 {k + 1} 个测点）</span>")


class AboutDialog(QDialog):
    """「关于」：版本号 + 构建信息。

    信息做成**可选中 + 一键复制**，是为了用户反馈问题时能直接把"跑的是哪一版"
    贴过来——这正是加版本号的目的。

    ⚠ 只显示版本号是不够的：同一个版本号在开发过程中会被反复打包，
    必须靠**构建哈希**才能定位到具体哪一次提交。打包后的 exe 里没有 git，
    这个哈希是打包时烘焙进去的（见 core/version.py 的说明）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关于")
        self.setModal(True)

        self._text = version_mod.full()
        lab = QLabel(self._text)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.btn_copy = QPushButton("复制信息")
        self.btn_copy.setToolTip("把版本与构建信息复制到剪贴板，便于反馈问题时附上")
        self.btn_copy.clicked.connect(self._copy)
        btn_close = QPushButton("关闭")
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.accept)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.btn_copy)
        row.addWidget(btn_close)

        lay = QVBoxLayout(self)
        lay.addWidget(lab)
        lay.addLayout(row)
        self.setMinimumWidth(360)

    def _copy(self):
        QApplication.clipboard().setText(self._text)
        self.btn_copy.setText("已复制")
