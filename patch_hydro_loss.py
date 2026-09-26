class HydroLossDialog(_ManualBase):
    """局部水头损失系数调节（收缩 / 扩张系数）。

    默认值为：收缩系数 0.1，扩张系数 0.3。
    仅用于一维恒定流推算。手动改写此处的值会覆盖该断面的默认系数。
    """

    TITLE = "局部水头损失系数"
    COLS = ["本组断面", "桩号 m", "收缩系数 Cc", "扩张系数 Ce", "来源"]
    HINT = ("手动修改特定断面的局部水头损失系数。\n"
            "默认取值：收缩系数 (Cc) = 0.1，扩张系数 (Ce) = 0.3。\n"
            "遇到桥梁、涵洞或急剧缩扩段时，可以调大该值（如 0.3 / 0.5）。\n"
            "留空则表示使用程序默认值。")

    def _build_body(self):
        self._add_section("局部水头损失系数")

        self.spin_contraction = QDoubleSpinBox()
        self.spin_contraction.setRange(0.0, 1.0)
        self.spin_contraction.setSingleStep(0.05)
        self.spin_contraction.setDecimals(2)
        self.spin_contraction.setKeyboardTracking(False)
        self.spin_contraction.valueChanged.connect(lambda v: self._apply_val("contraction", v))

        self.spin_expansion = QDoubleSpinBox()
        self.spin_expansion.setRange(0.0, 1.0)
        self.spin_expansion.setSingleStep(0.05)
        self.spin_expansion.setDecimals(2)
        self.spin_expansion.setKeyboardTracking(False)
        self.spin_expansion.valueChanged.connect(lambda v: self._apply_val("expansion", v))

        self.btn_clear_contraction = QPushButton("清除")
        self.btn_clear_contraction.clicked.connect(lambda: self._apply_val("contraction", None))

        self.btn_clear_expansion = QPushButton("清除")
        self.btn_clear_expansion.clicked.connect(lambda: self._apply_val("expansion", None))

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("收缩系数 Cc:"))
        row1.addWidget(self.spin_contraction)
        row1.addWidget(self.btn_clear_contraction)
        row1.addStretch()
        self.body.addLayout(row1, self._row, 0, 1, 4)
        self._row += 1

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("扩张系数 Ce:"))
        row2.addWidget(self.spin_expansion)
        row2.addWidget(self.btn_clear_expansion)
        row2.addStretch()
        self.body.addLayout(row2, self._row, 0, 1, 4)
        self._row += 1

        # 将输入控件存起来方便禁用
        self._inputs = {
            "contraction": (self.spin_contraction, self.btn_clear_contraction),
            "expansion": (self.spin_expansion, self.btn_clear_expansion)
        }

    def _apply_val(self, key, val):
        if self.current is None:
            return
        sec = self.current
        if key == "contraction":
            sec.hydro_loss_contraction = val
        else:
            sec.hydro_loss_expansion = val

        # 刷新自身显示
        self._fill_current()
        self._fill_table()
        # 通知重算，这个面板和分区一样也会影响后续计算，需要发出 clearRequested/pickRequested（复用_ManualBase信号机制会太绕，这里我们可以借用 resetGroupRequested 或增加新信号）
        # 这里最简单的是借用 pickRequested 来触发主界面的 update
        self.pickRequested.emit("")

    def _is_manual(self, key: str) -> bool:
        if self.current is None:
            return False
        if key == "contraction":
            return self.current.hydro_loss_contraction is not None
        if key == "expansion":
            return self.current.hydro_loss_expansion is not None
        return False

    def _row_manual_note(self, sec: Section) -> str:
        res = []
        if sec.hydro_loss_contraction is not None:
            res.append(f"Cc={sec.hydro_loss_contraction:.2f}")
        if sec.hydro_loss_expansion is not None:
            res.append(f"Ce={sec.hydro_loss_expansion:.2f}")
        return " ".join(res)

    def _fill_current(self):
        sec = self.current
        if sec is None:
            for sp, btn in self._inputs.values():
                sp.blockSignals(True)
                sp.setEnabled(False)
                sp.setValue(0)
                sp.blockSignals(False)
                btn.setEnabled(False)
            return

        for sp, btn in self._inputs.values():
            sp.setEnabled(True)

        self._inputs["contraction"][0].blockSignals(True)
        if sec.hydro_loss_contraction is not None:
            self._inputs["contraction"][0].setValue(sec.hydro_loss_contraction)
            self._inputs["contraction"][1].setEnabled(True)
        else:
            self._inputs["contraction"][0].setValue(0.1)  # 默认显示0.1
            self._inputs["contraction"][1].setEnabled(False)
        self._inputs["contraction"][0].blockSignals(False)

        self._inputs["expansion"][0].blockSignals(True)
        if sec.hydro_loss_expansion is not None:
            self._inputs["expansion"][0].setValue(sec.hydro_loss_expansion)
            self._inputs["expansion"][1].setEnabled(True)
        else:
            self._inputs["expansion"][0].setValue(0.3)  # 默认显示0.3
            self._inputs["expansion"][1].setEnabled(False)
        self._inputs["expansion"][0].blockSignals(False)

    def _fill_table(self):
        self.table.setRowCount(0)
        cur_row = -1
        for r, sec in enumerate(self.sections):
            if sec is self.current:
                cur_row = r
            self.table.insertRow(r)

            self.table.setItem(r, 0, QTableWidgetItem(sec.name))
            ch = _chainage_of(self.project, self.line_index, r)
            self.table.setItem(r, 1, QTableWidgetItem(f"{ch:.1f}" if ch is not None else "—"))

            cc = sec.hydro_loss_contraction
            ce = sec.hydro_loss_expansion

            it_cc = QTableWidgetItem(f"{cc:.2f}" if cc is not None else "— (0.1)")
            it_ce = QTableWidgetItem(f"{ce:.2f}" if ce is not None else "— (0.3)")
            self.table.setItem(r, 2, it_cc)
            self.table.setItem(r, 3, it_ce)

            note = self._row_manual_note(sec)
            it = QTableWidgetItem("手动" if note else "自动")
            if note:
                it.setForeground(QColor("#185FA5"))
                it.setToolTip(f"人工设定：{note}")
            self.table.setItem(r, 4, it)

        self._highlight(cur_row)
        self.table.resizeRowsToContents()

    def _update_warn(self):
        pass # 此面板不需要额外警告
