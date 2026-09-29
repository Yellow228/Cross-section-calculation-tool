from __future__ import annotations
import os
import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QMessageBox, QFileDialog, QInputDialog)

from core.model import Project, ProfileLine, Section
from core.reader import load_folder
from core.config import Config


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
            new_project, warnings = load_folder(self.data_dir, self.cfg)
        except Exception as e:
            QMessageBox.critical(self, "载入失败", str(e))
            return

        self._merge_project(new_project)
        self._refresh_tree()

    def _prompt_resolution(self, title: str, text: str, options: list[str]) -> str:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)

        buttons = {}
        for opt in options:
            btn = box.addButton(opt, QMessageBox.ActionRole)
            buttons[btn] = opt

        box.exec()
        clicked_btn = box.clickedButton()
        if clicked_btn in buttons:
            return buttons[clicked_btn]
        return "跳过"

    def _merge_project(self, new_project: Project):
        for new_ln in new_project.profile_lines:
            existing_ln = next((ln for ln in self.project.profile_lines if ln.name == new_ln.name), None)

            if existing_ln:
                choice = self._prompt_resolution(
                    "分组名冲突",
                    f"已存在同名分组: {new_ln.name}。请选择操作：",
                    ["跳过", "覆盖", "合并", "重命名新增"]
                )

                if choice == "跳过":
                    continue
                elif choice == "覆盖":
                    existing_idx = self.project.profile_lines.index(existing_ln)
                    self.project.profile_lines[existing_idx] = new_ln
                elif choice == "合并":
                    self._merge_line(existing_ln, new_ln)
                elif choice == "重命名新增":
                    new_name, ok = QInputDialog.getText(self, "重命名新增", "请输入新的分组名称:", text=new_ln.name)
                    if ok and new_name:
                        new_ln.name = new_name
                        self.project.profile_lines.append(new_ln)
            else:
                self.project.profile_lines.append(new_ln)

    def _merge_line(self, existing_ln: ProfileLine, new_ln: ProfileLine):
        for new_sec in new_ln.sections:
            existing_sec = next((s for s in existing_ln.sections if s.name == new_sec.name), None)

            if existing_sec:
                choice = self._prompt_resolution(
                    "断面名冲突",
                    f"分组 '{existing_ln.name}' 中已存在同名断面: {new_sec.name}。请选择操作：",
                    ["跳过", "覆盖", "重命名新增"]
                )

                if choice == "跳过":
                    continue
                elif choice == "覆盖":
                    existing_idx = existing_ln.sections.index(existing_sec)
                    existing_ln.sections[existing_idx] = new_sec
                elif choice == "重命名新增":
                    new_name, ok = QInputDialog.getText(self, "重命名新增", "请输入新的断面名称:", text=new_sec.name)
                    if ok and new_name:
                        new_sec.name = new_name
                        new_sec.params.name = new_name
                        existing_ln.sections.append(new_sec)
            else:
                existing_ln.sections.append(new_sec)

        # 重新排序并处理 profile 等，如果不确定最好不改，但目前可以先保留

    def _on_delete(self):
        selected_items = self.tree.selectedItems()
        if not selected_items:
            return

        reply = QMessageBox.question(self, "确认删除", f"确定要删除选中的 {len(selected_items)} 项吗？",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.No:
            return

        for item in selected_items:
            # 过滤掉已经被删除子项的父项，或者子项已经被删除的项
            # 最好的办法是收集需要删除的 section 和 group，然后统一操作
            pass

        to_delete_sections = []
        to_delete_groups = []

        for item in selected_items:
            data = item.data(0, Qt.UserRole)
            if data[0] == "section":
                to_delete_sections.append((data[1], data[2]))
            elif data[0] == "group":
                to_delete_groups.append(data[1])

        for ln, sec in to_delete_sections:
            if ln in self.project.profile_lines and sec in ln.sections:
                ln.sections.remove(sec)

        for ln in to_delete_groups:
            if ln in self.project.profile_lines:
                self.project.profile_lines.remove(ln)

        self._refresh_tree()
