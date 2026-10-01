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
        self.resize(800, 500)

        # 左栏：直接引用主界面的工程对象
        self.current_project = current_project if current_project else Project()
        # 右栏：从目录新读出的待导入数据
        self.new_project = Project()

        #: 打开本窗口那一刻的断面快照，供「撤销删除」回滚。
        #:
        #: ⚠ 为什么非要有这份快照：左栏的删除是**立即生效**的
        #:   （直接改主界面那个工程对象，不走 `accept()` 闸门），
        #:   而窗口又是可以取消的——用户删错了随手关掉窗口，
        #:   删除**不会**跟着还原，且没有任何提示。
        #:   既然要做"立即生效"，就必须自己留一条回退的路。
        self._baseline_lines = copy.deepcopy(self.current_project.profile_lines)

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
        self._refresh_left_tree()
        self._refresh_right_tree()

    def _build_ui(self):
        # 左栏 UI
        self.left_tree = QTreeWidget()
        self.left_tree.setHeaderLabels(["当前工程已导入断面"])
        self.left_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.btn_delete_left = QPushButton("删除已有断面")
        self.btn_delete_left.clicked.connect(self._on_delete_left)

        #: 左栏删除立即生效、关窗也不还原，所以必须给一条回退的路。
        #: 初始禁用：还没删过东西时没什么可撤销的。
        self.btn_undo_delete = QPushButton("撤销删除")
        self.btn_undo_delete.setToolTip(
            "把已有工程恢复到打开本窗口时的状态。\n"
            "左栏的删除是立即生效的，关闭本窗口也不会撤销——删错了用这里找回。")
        self.btn_undo_delete.setEnabled(False)
        self.btn_undo_delete.clicked.connect(self._on_undo_delete)

        left_btn_row = QHBoxLayout()
        left_btn_row.addWidget(self.btn_delete_left)
        left_btn_row.addWidget(self.btn_undo_delete)

        left_layout = QVBoxLayout()
        left_layout.addWidget(self.left_tree)
        left_layout.addLayout(left_btn_row)

        # 右栏 UI
        self.right_tree = QTreeWidget()
        self.right_tree.setHeaderLabels(["数据目录下的断面 (待导入)"])
        self.right_tree.setSelectionMode(QTreeWidget.ExtendedSelection)

        self.btn_load_dir = QPushButton("载入数据目录")
        self.btn_delete_right = QPushButton("删除断面")
        self.btn_apply = QPushButton("载入并计算")

        self.btn_load_dir.clicked.connect(self._on_load_dir)
        self.btn_delete_right.clicked.connect(self._on_delete_right)
        self.btn_apply.clicked.connect(self._on_apply)

        right_bottom_layout = QHBoxLayout()
        right_bottom_layout.addWidget(self.btn_load_dir)
        right_bottom_layout.addWidget(self.btn_delete_right)
        right_bottom_layout.addWidget(self.btn_apply)

        right_layout = QVBoxLayout()
        right_layout.addWidget(self.right_tree)
        right_layout.addLayout(right_bottom_layout)

        main_layout = QHBoxLayout(self)
        main_layout.addLayout(left_layout, 1)
        main_layout.addLayout(right_layout, 1)

    def _refresh_left_tree(self):
        self.left_tree.clear()
        for ln in self.current_project.profile_lines:
            group_item = QTreeWidgetItem(self.left_tree, [ln.name])
            group_item.setData(0, Qt.UserRole, ("group", ln))

            for sec in ln.sections:
                sec_item = QTreeWidgetItem(group_item, [sec.name])
                sec_item.setData(0, Qt.UserRole, ("section", ln, sec))
        self.left_tree.expandAll()

    def _refresh_right_tree(self):
        self.right_tree.clear()
        for ln in self.new_project.profile_lines:
            group_item = QTreeWidgetItem(self.right_tree, [ln.name])
            group_item.setData(0, Qt.UserRole, ("group", ln))

            for sec in ln.sections:
                sec_item = QTreeWidgetItem(group_item, [sec.name])
                sec_item.setData(0, Qt.UserRole, ("section", ln, sec))
        self.right_tree.expandAll()

    def get_project(self) -> Project:
        return self.current_project

    def _on_delete_left(self):
        selected_items = self.left_tree.selectedItems()
        if not selected_items:
            return

        to_delete_groups, to_delete_sections = [], []
        for item in selected_items:
            data = item.data(0, Qt.UserRole)
            if not data:
                continue
            if data[0] == "section":
                to_delete_sections.append((data[1], data[2]))
            elif data[0] == "group":
                to_delete_groups.append(data[1])

        n_sec = len(to_delete_sections) + sum(len(ln.sections) for ln in to_delete_groups)
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
            "确定要删除已有工程的 " + "、".join(parts) + " 吗？\n\n"
            "⚠ 删除会立即生效并从主界面移除，关闭本窗口也不会还原。\n"
            "删错了可以点「撤销删除」回到打开本窗口时的状态。",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.No:
            return

        for ln, sec in to_delete_sections:
            if ln in self.current_project.profile_lines and sec in ln.sections:
                ln.sections.remove(sec)

        for ln in to_delete_groups:
            if ln in self.current_project.profile_lines:
                self.current_project.profile_lines.remove(ln)

        self._refresh_left_tree()
        self.btn_undo_delete.setEnabled(True)
        self._sync_to_parent()

    def _on_undo_delete(self):
        """把已有工程回滚到打开本窗口时的状态。

        ⚠ 为什么是"整份快照回滚"而不是"记住删了哪些、再插回去"：
          删除只会让断面/分组变少，整份还原一定正确；而"插回去"要还原
          它在列表里的**位置**和**对象身份**，中间只要还发生过别的改动
          （比如又删了另一个分组）就会错位。整份回滚没有这个风险。
        """
        self.current_project.profile_lines = copy.deepcopy(self._baseline_lines)
        self._refresh_left_tree()
        self.btn_undo_delete.setEnabled(False)   # 已回到基线，没什么可再撤销
        self._sync_to_parent()

    def _sync_to_parent(self) -> None:
        """把左栏对工程的改动同步到主界面（重算 + 刷新三处视图）。

        ⚠ 左栏的删除/撤销是**立即生效**的，不走 `accept()` 那道闸门，
          所以必须在这里主动通知主界面；漏掉的话数据变了、界面还是旧的。
          （`_merge_project` 不需要这一步：它只在 `_on_apply` 里被调用，
            紧接着就 `accept()`，由 `_pick_and_load` 统一收尾。）

        方法逐个用 `getattr` 探一遍：本对话框在测试里可能被挂到非
        MainWindow 的 parent 上，不该因为缺一个私有方法就整条崩掉。
        生产路径上 parent 一定是 MainWindow，这些方法都在。
        """
        parent = self.parent()
        if parent is None:
            return
        dirty = getattr(parent, "_set_dirty", None)
        if callable(dirty):
            dirty(True)
        for name in ("_solve_all", "_refresh_line_list", "_refresh_current_views"):
            fn = getattr(parent, name, None)
            if callable(fn):
                fn()
        panel = getattr(parent, "param_panel", None)
        if panel is not None and hasattr(panel, "set_sections"):
            panel.set_sections(self.current_project.all_sections())

    def _on_delete_right(self):
        selected_items = self.right_tree.selectedItems()
        if not selected_items:
            return

        to_delete_groups, to_delete_sections = [], []
        for item in selected_items:
            data = item.data(0, Qt.UserRole)
            if not data:
                continue
            if data[0] == "section":
                to_delete_sections.append((data[1], data[2]))
            elif data[0] == "group":
                to_delete_groups.append(data[1])

        n_sec = len(to_delete_sections) + sum(len(ln.sections) for ln in to_delete_groups)
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
            "确定要删除本次待导入的 " + "、".join(parts) + " 吗？\n"
            "（删除只作用于本次导入窗口，不导入这些数据）",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.No:
            return

        for ln, sec in to_delete_sections:
            if ln in self.new_project.profile_lines and sec in ln.sections:
                ln.sections.remove(sec)

        for ln in to_delete_groups:
            if ln in self.new_project.profile_lines:
                self.new_project.profile_lines.remove(ln)

        self._refresh_right_tree()

    def _on_apply(self):
        added, skipped = self._merge_project(self.new_project)
        self.accept()

    def _on_load_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择存放 xlsx 的目录", self.data_dir)
        if not d:
            return
        self.data_dir = d

        try:
            loaded_project, warnings, note = self._read_dir()
        except Exception as e:
            QMessageBox.critical(self, "载入失败", str(e))
            return

        self.new_project = loaded_project
        self._refresh_right_tree()

        n_ln = len(self.new_project.profile_lines)
        n_sec = sum(len(ln.sections) for ln in self.new_project.profile_lines)
        msg = f"已读取 {os.path.basename(self.data_dir)}：共读取到 {n_ln} 个分组、{n_sec} 个横断面。"
        if warnings:
            msg += "\n告警：" + "；".join(warnings[:3])
        if note:
            msg += "\n" + note
        msg += "\n\n你可以在右侧列表删除本次不想导入的断面，然后点击「载入并计算」将它们合并到工程中。"
        QMessageBox.information(self, "读取完成", msg)

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
            existing_ln = next((ln for ln in self.current_project.profile_lines if ln.name == new_ln.name), None)

            if existing_ln:
                choice = self._prompt_resolution(
                    "分组名冲突",
                    f"已存在同名分组: {new_ln.name}。请选择操作：",
                    ["跳过", "覆盖", "合并", "重命名新增"]
                )
                if choice == "覆盖":
                    existing_idx = self.current_project.profile_lines.index(existing_ln)
                    self.current_project.profile_lines[existing_idx] = new_ln
                elif choice == "合并":
                    self._merge_line(existing_ln, new_ln)
                elif choice == "重命名新增":
                    new_name, ok = QInputDialog.getText(self, "重命名新增", "请输入新的分组名称:", text=new_ln.name)
                    if ok and new_name:
                        new_ln.name = new_name
                        self.current_project.profile_lines.append(new_ln)
                        added += 1
                    else:
                        skipped += 1        # 取消重命名 = 放弃这一项
                else:
                    skipped += 1            # 主动选「跳过」或直接关窗
            else:
                self.current_project.profile_lines.append(new_ln)
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

