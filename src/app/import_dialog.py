from __future__ import annotations
import os
import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QMessageBox, QFileDialog, QInputDialog)

from core.model import Project, ProfileLine, Section
from core.reader import load_folder
from core.config import Config

from .dialogs import XYOrderDialog


class ImportDataDialog(QDialog):
    def __init__(self, current_project: Project | None, data_dir: str, cfg: Config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入excel数据")
        self.resize(600, 400)

        self.project = Project(
            profile_lines=copy.deepcopy(current_project.profile_lines)
            if current_project else []
        )
        self.data_dir = data_dir
        self.cfg = cfg
        #: 最近一次载入的「输入坐标列序」判定明细。
        #: `load_folder` 会把它写进这里，供调用方（主窗口）在导入完成后
        #: 走和 `_load()` 同一套"判不出来就问一句"的逻辑。
        #: ⚠ 不透出去的话，这条路径的列序判定结果会被**静默丢弃**：
        #:   判不出来也不弹窗、不记疑，数据按默认列序读进来，
        #:   用户看到的只是"自定义的坐标列序不弹出来"。
        self.xy_report: list = []
        #: 用户是否已在本对话框内确认过列序（确认过就不用再问第二遍）。
        self.xy_override: bool | None = None
        #: 判不出列序但用户没确认时留的记号，供主窗口提示。
        self.xy_pending: list = []

        self._build_ui()
        self._refresh_tree()

    def _build_ui(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["断面分组 / 横断面"])
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)

        self.btn_load_dir = QPushButton("载入数据目录")
        self.btn_delete = QPushButton("删除断面")
        self.btn_apply = QPushButton("载入并计算")

        self.btn_load_dir.clicked.connect(self._on_load_dir)
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_apply.clicked.connect(self.accept)

        bottom_layout = QHBoxLayout()
        bottom_layout.addWidget(self.btn_load_dir)
        bottom_layout.addWidget(self.btn_delete)
        bottom_layout.addWidget(self.btn_apply)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tree)
        layout.addLayout(bottom_layout)

    def _refresh_tree(self):
        self.tree.clear()
        for ln in self.project.profile_lines:
            group_item = QTreeWidgetItem(self.tree, [ln.name])
            group_item.setData(0, Qt.UserRole, ("group", ln))

            for sec in ln.sections:
                sec_item = QTreeWidgetItem(group_item, [sec.name])
                sec_item.setData(0, Qt.UserRole, ("section", ln, sec))

        self.tree.expandAll()

    def get_project(self) -> Project:
        return self.project

    def _on_load_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择存放 xlsx 的目录", self.data_dir)
        if not d:
            return
        self.data_dir = d

        try:
            new_project, warnings, note = self._read_dir()
        except Exception as e:
            QMessageBox.critical(self, "载入失败", str(e))
            return

        added, skipped = self._merge_project(new_project)
        self._refresh_tree()

        n_ln = len(self.project.profile_lines)
        n_sec = sum(len(ln.sections) for ln in self.project.profile_lines)
        msg = f"已载入 {os.path.basename(self.data_dir)}：本次新增 {added} 个分组。"
        msg += f"\n当前共 {n_ln} 个分组、{n_sec} 个横断面。"
        if skipped:
            # 跳过不是错，但必须说出来——否则"数据少了"会让人以为是程序丢的
            msg += f"\n⚠ 有 {skipped} 个同名分组按你的选择跳过，未导入。"
        if warnings:
            msg += "\n告警：" + "；".join(warnings[:3])
        if note:
            msg += "\n" + note
        msg += "\n\n确认无误后点「载入并计算」应用到主界面。"
        QMessageBox.information(self, "载入完成", msg)

    def _read_dir(self):
        """读数据目录，返回 (工程, 告警, 列序提示)。

        列序判不出来时**当场问**，问完立刻按用户的答案重读一遍——
        放在这里而不是丢给调用方，是为了让"导入"这条路径与 `_load()`
        行为一致：判不出就弹窗，绝不静默按默认列序读。
        （旧版这里不传 `xy_report`，判定结果被整个丢弃，于是从导入窗口
          进来的数据判不出列序时既不弹窗也不告知。）
        """
        report: list = []
        project, warnings = load_folder(self.data_dir, self.cfg,
                                        xy_override=self.xy_override,
                                        xy_report=report)
        self.xy_report = report

        ask = [(fn, d) for fn, d in report if not d.confident]
        if not ask or self.xy_override is not None:
            return project, warnings, ""

        choice = self._ask_xy_order(ask)
        if choice is None:
            self.xy_pending = ask
            return project, warnings, (
                f"⚠ 有 {len(ask)} 个文件的坐标列序未能自动判定，"
                "已暂按「输入第 1 列是北坐标」读取；"
                "该判定未确认，导出的平面坐标两列可能颠倒。"
                "可在「计算设置 → 坐标列序…」里复核。")

        self.xy_override = choice
        self.xy_pending = None
        report2: list = []
        project, warnings = load_folder(self.data_dir, self.cfg,
                                        xy_override=choice, xy_report=report2)
        self.xy_report = report2
        return project, warnings, (
            f"已按你确认的列序读取：输入第 1 列是"
            f"{'北' if choice else '东'}坐标。")

    def _ask_xy_order(self, ask) -> bool | None:
        """弹「坐标列序」确认框；返回 True/False，用户关窗则 None。

        单独抽出来是为了让冒烟测试能把它换成替身——offscreen 下真弹模态框会永久阻塞。
        """
        dlg = XYOrderDialog(ask, self)
        if dlg.exec() != QDialog.Accepted:
            return None
        choice = dlg.swap()
        if dlg.write_to_settings():
            self.cfg.first_col_is_north = choice
        return choice

    def _prompt_resolution(self, title: str, text: str, options: list[str]) -> str:
        """让用户在几个处理方式里选一个，返回选中的文本。

        ⚠ 用户**直接关掉**这个框时（Esc / 标题栏 ×）返回的不是某个选项，
        而是 `options[0]`——后者恰好是「跳过」。但"关窗"和"主动选跳过"
        在用户看来是两件事：前者常常是"我还不想决定、先看看"，若静默按跳过
        处理，数据没进来却毫无提示，排查起来毫无线索。
        所以这里把两种情形分开：关窗一律按**第一个选项之后的安全默认**处理，
        并返回一个空串让调用方有机会给出提示。

        返回值：选中的选项文本；用户关窗时返回 ""（调用方按"跳过"处理并提示）。
        """
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Question)

        buttons = {}
        for opt in options:
            btn = box.addButton(opt, QMessageBox.ActionRole)
            buttons[btn] = opt
        # 把首个选项设为默认焦点，回车即确认，行为与以往一致
        box.setDefaultButton(box.buttons()[0] if box.buttons() else None)

        box.exec()
        clicked_btn = box.clickedButton()
        if clicked_btn in buttons:
            return buttons[clicked_btn]
        return ""

    def _merge_project(self, new_project: Project) -> tuple[int, int]:
        """把新载入的工程合并进来。返回 (新增分组数, 被跳过的分组数)。

        「跳过」既包括用户主动选跳过，也包括用户直接关掉冲突对话框
        （见 `_prompt_resolution`）——两者都当作"这次不导入该项"，
        由调用方汇总后给出提示，不让数据无声无息地少掉。
        """
        added = skipped = 0
        for new_ln in new_project.profile_lines:
            existing_ln = next((ln for ln in self.project.profile_lines if ln.name == new_ln.name), None)

            if existing_ln:
                choice = self._prompt_resolution(
                    "分组名冲突",
                    f"已存在同名分组: {new_ln.name}。请选择操作：",
                    ["跳过", "覆盖", "合并", "重命名新增"]
                )
                if choice == "覆盖":
                    existing_idx = self.project.profile_lines.index(existing_ln)
                    self.project.profile_lines[existing_idx] = new_ln
                elif choice == "合并":
                    self._merge_line(existing_ln, new_ln)
                elif choice == "重命名新增":
                    new_name, ok = QInputDialog.getText(self, "重命名新增", "请输入新的分组名称:", text=new_ln.name)
                    if ok and new_name:
                        new_ln.name = new_name
                        self.project.profile_lines.append(new_ln)
                        added += 1
                    else:
                        skipped += 1        # 取消重命名 = 放弃这一项
                else:
                    skipped += 1            # 主动选「跳过」或直接关窗
            else:
                self.project.profile_lines.append(new_ln)
                added += 1
        return added, skipped

    def _merge_line(self, existing_ln: ProfileLine, new_ln: ProfileLine) -> None:
        for new_sec in new_ln.sections:
            existing_sec = next((s for s in existing_ln.sections if s.name == new_sec.name), None)

            if existing_sec:
                choice = self._prompt_resolution(
                    "断面名冲突",
                    f"分组 '{existing_ln.name}' 中已存在同名断面: {new_sec.name}。请选择操作：",
                    ["跳过", "覆盖", "重命名新增"]
                )
                if choice == "覆盖":
                    existing_idx = existing_ln.sections.index(existing_sec)
                    existing_ln.sections[existing_idx] = new_sec
                elif choice == "重命名新增":
                    new_name, ok = QInputDialog.getText(self, "重命名新增", "请输入新的断面名称:", text=new_sec.name)
                    if ok and new_name:
                        # 断面名有两处：Section.name 与 params.name，
                        # 不同步会让「批量填写」表格里显示的名字仍旧名。
                        new_sec.name = new_name
                        new_sec.params.name = new_name
                        existing_ln.sections.append(new_sec)
                # 其余（跳过 / 关窗 / 取消重命名）一律不导入该断面
            else:
                existing_ln.sections.append(new_sec)

        # 重新排序并处理 profile 等，如果不确定最好不改，但目前可以先保留

    def _on_delete(self):
        selected_items = self.tree.selectedItems()
        if not selected_items:
            return

        # 先算出实际会被删掉的组与断面：树支持多选，父项和子项可能同时被选中，
        # 提示里要说清"真正删了多少"，不然用户按选中项数核对会以为丢了东西。
        to_delete_groups, to_delete_sections = [], []
        for item in selected_items:
            data = item.data(0, Qt.UserRole)
            if not data:
                continue
            if data[0] == "section":
                to_delete_sections.append((data[1], data[2]))
            elif data[0] == "group":
                to_delete_groups.append(data[1])

        n_sec = len(to_delete_sections) + sum(
            len(ln.sections) for ln in to_delete_groups)
        n_grp = len(to_delete_groups)
        if n_grp == 0 and n_sec == 0:
            return

        parts = []
        if n_grp:
            parts.append(f"{n_grp} 个分组")
        if n_sec:
            parts.append(f"{n_sec} 个横断面")
        reply = QMessageBox.question(
            self, "确认删除",
            "确定要删除 " + "、".join(parts) + " 吗？\n"
            "（删除只作用于本次导入窗口，点「载入并计算」后才会写回主界面）",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.No:
            return

        for ln, sec in to_delete_sections:
            if ln in self.project.profile_lines and sec in ln.sections:
                ln.sections.remove(sec)

        for ln in to_delete_groups:
            if ln in self.project.profile_lines:
                self.project.profile_lines.remove(ln)

        self._refresh_tree()
