"""主窗口：顶部菜单栏 + 左侧纵断面线/断面树，右侧三个可视化 Tab。

「计算设置」与「批量填写」放在顶部菜单栏，点一下弹出**非模态**面板——
调阈值时要能一边改一边看右侧三张图跟着变，模态窗口会把交互挡死。
"""

from __future__ import annotations

import os
import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QLabel,
                              QListWidget, QListWidgetItem, QMainWindow,
                              QMessageBox, QPushButton, QSplitter, QTabWidget,
                              QVBoxLayout, QWidget)

from core import params as P
from core import project_io
from core import slope as slope_mod
from core.config import Config
from core.exporter import export_all
from core.model import Project, SectionResult, TerrainInfo
from core.reader import load_folder
from core.solver import solve_section
from core import version as version_mod

from .dialogs import (AboutDialog, BatchDialog, DisasterDialog,
                      SettingsDialog, SlopeDialog, ZoneDialog)
from .param_panel import ParamPanel
from .profile_view import ProfileView
from .rating_view import RatingView
from .section_view import PICK_LABELS, SectionView

# 载入数据后自动填补空参数用的默认值（原「参数集 xlsx」不提供，见 Q12）。
# 只是让三张图不至于空着的兜底值，用户需按实际取值修改。
DEFAULT_ROUGHNESS = 0.03
DEFAULT_SLOPE = 0.005
DEFAULT_DESIGN_Q = 50.0


def _try_apply_override(sec, **changes) -> list[str]:
    """把一处手动设定"试装"到断面上，校验通过才留下；不通过则原样还原。

    为什么不做"先检查再写入"的写法：手动设定是**互相耦合**的三个值
    （深泓点 / 两侧分区边界 / 成灾水位），合法性只能整组判断——
    例如把深泓点挪到左边界右侧，单独看每个值都合理，合起来却讲不通。
    所以干脆先写进去、跑一遍 `Section.override_errors()`、有错再回滚。

    返回错误列表；空列表表示已写入。
    """
    old = {k: getattr(sec, k) for k in changes}
    for k, v in changes.items():
        setattr(sec, k, v)
    errs = sec.override_errors()
    if errs:
        for k, v in old.items():
            setattr(sec, k, v)
    return errs


def _write_params(sec, rec: dict, zone: bool) -> None:
    """把批量填写表格的一行写进断面参数。

    **NaN 表示「该字段不动」**——表格里留空就是不改这一项，
    这样从 Excel 粘一列时不会顺手把别的列清掉。

    糙率分区：勾选了分区就写三个分区糙率；没勾选则把它们清回 `None`。
    注意 `None` 的语义是「留空、回退统一糙率」，与 NaN（未填写）不同，
    不能混为一谈。
    """
    p = sec.params
    for key in ("design_q", "slope", "roughness"):
        v = rec.get(key)
        if v is not None and v == v:            # 非 NaN
            setattr(p, key, float(v))
    if zone:
        for key in ("roughness_main", "roughness_left", "roughness_right"):
            v = rec.get(key)
            setattr(p, key, None if (v is None or v != v) else float(v))
    else:
        p.roughness_main = None
        p.roughness_left = None
        p.roughness_right = None


def app_root() -> str:
    """程序根目录。打包后是 exe 所在目录，源码运行时是工程根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def default_data_dir() -> str:
    """找一个合理的数据目录默认值（打包后 datas 可能在 _internal 下）。"""
    base = app_root()
    for cand in (os.path.join(base, "data"),
                 os.path.join(base, "_internal", "data"),
                 os.path.join(os.getcwd(), "data")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(base, "data")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(version_mod.APP_NAME + version_mod.title_suffix())
        self.resize(1500, 900)

        self.cfg = Config()
        self.cfg.output_dir = os.path.join(app_root(), "output")
        self.data_dir = default_data_dir()
        self.project: Project | None = None
        self.results: dict[str, SectionResult] = {}
        self.infos: dict[str, TerrainInfo] = {}
        self.warnings: list[str] = []

        # ---- 工程文件状态 ----
        self.current_path: str | None = None   # 当前 .dmprj 路径；None = 从未保存
        self.data_source: dict = {}            # {"dir": ..., "files": [...]}
        self._dirty = False                    # 有未保存改动

        self._build_ui()
        self._refresh_line_list()
        self._update_title()

    # ---------------- 界面 ----------------
    def _build_ui(self):
        # 顶部工具条
        self.btn_dir = QPushButton("选择数据目录…")
        self.btn_dir.clicked.connect(self._pick_dir)
        self.btn_load = QPushButton("载入并计算")
        self.btn_load.clicked.connect(self._load)
        self.btn_recalc = QPushButton("重新计算")
        self.btn_recalc.clicked.connect(self._recalc)
        self.btn_export = QPushButton("导出结果")
        self.btn_export.clicked.connect(self._export)
        self.lbl_dir = QLabel(self.data_dir)

        top = QHBoxLayout()
        top.addWidget(self.btn_dir)
        top.addWidget(self.lbl_dir, 1)
        top.addWidget(self.btn_load)
        top.addWidget(self.btn_recalc)
        top.addWidget(self.btn_export)

        # ---- 顶部菜单栏：设置面板都做成点开的非模态窗口 ----
        self.dlg_settings = SettingsDialog(self.cfg, self)
        self.dlg_batch = BatchDialog(self)
        self.dlg_slope = SlopeDialog(self)
        self.dlg_settings.changed.connect(self._on_settings_changed)
        self.dlg_settings.thresholdsChanged.connect(self._on_thresholds_changed)
        self.dlg_settings.exportChanged.connect(self._on_export_settings_changed)
        self.dlg_batch.applyRequested.connect(self._apply_batch_from_dialog)
        self.dlg_batch.groupChanged.connect(self._on_batch_group_changed)
        self.dlg_batch.zoneChanged.connect(self._on_batch_zone_changed)
        self.dlg_slope.applied.connect(self._on_slopes_applied)

        # ---- 手动调节（Q14）：两个面板都不提供数值输入，靠图上拾取 ----
        self.dlg_zone = ZoneDialog(self)
        self.dlg_disaster = DisasterDialog(self)
        self.dlg_about = AboutDialog(self)
        for d in (self.dlg_zone, self.dlg_disaster):
            d.pickRequested.connect(self._on_manual_pick)
            d.clearRequested.connect(self._on_manual_cleared)
            d.resetGroupRequested.connect(self._on_manual_reset_group)
            d.groupChanged.connect(self._on_manual_group_changed)
            d.sectionActivated.connect(self._on_manual_section_activated)

        mb = self.menuBar()
        self._build_file_menu(mb)

        act_set = mb.addAction("计算设置")
        act_set.setToolTip("断面模式 / 水位步长 / 桩号原点 / 陡坡·缓坡阈值 / CSV 编码")
        act_set.triggered.connect(self.dlg_settings.popup)
        act_batch = mb.addAction("批量填写")
        act_batch.setToolTip(
            "按页签（流量 / 比降 / 糙率）批量填写当前组的断面参数；支持从 Excel 粘贴")
        act_batch.triggered.connect(self._open_batch_dialog)
        act_slope = mb.addAction("按纵断面推算比降")
        act_slope.setToolTip("用纵断面实测数据推算各断面的河道平均比降（先出建议值再确认）")
        act_slope.triggered.connect(self._open_slope_dialog)
        act_zone = mb.addAction("分区调节")
        act_zone.setToolTip(
            "手动指定深泓点与分区边界。只能在断面形态图上拾取实测测点；"
            "跟随左侧当前选中的断面。")
        act_zone.triggered.connect(self._open_zone_dialog)
        act_disaster = mb.addAction("成灾水位")
        act_disaster.setToolTip(
            "手动指定成灾水位。只能在断面形态图上拾取实测测点；"
            "跟随左侧当前选中的断面。")
        act_disaster.triggered.connect(self._open_disaster_dialog)

        m_help = mb.addMenu("帮助")
        act_about = m_help.addAction("关于…")
        act_about.setToolTip("显示程序版本与构建信息（含 git 提交哈希，可复制）")
        act_about.triggered.connect(self._open_about_dialog)

        self.act_settings = act_set
        self.act_batch = act_batch
        self.act_slope = act_slope
        self.act_zone = act_zone
        self.act_disaster = act_disaster
        self.act_about = act_about

        self.lst_lines = QListWidget()
        self.lst_lines.currentRowChanged.connect(self._on_line_changed)
        grp_line = QGroupBox("纵断面线")
        vl = QVBoxLayout()
        vl.addWidget(self.lst_lines)
        grp_line.setLayout(vl)

        self.lst_secs = QListWidget()
        self.lst_secs.currentRowChanged.connect(self._on_section_changed)
        grp_sec = QGroupBox("横断面")
        vs = QVBoxLayout()
        vs.addWidget(self.lst_secs)
        grp_sec.setLayout(vs)

        self.param_panel = ParamPanel()
        self.param_panel.changed.connect(self._on_params_changed)

        left = QVBoxLayout()
        left.addWidget(grp_line, 1)
        left.addWidget(grp_sec, 2)
        left.addWidget(self.param_panel, 3)
        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setMaximumWidth(430)

        # 右侧：三个可视化
        self.view_section = SectionView()
        self.view_rating = RatingView()
        self.view_profile = ProfileView()
        # 把当前 Config 引用注入两个视图：它们的水位线绘制要读 raise_enabled /
        # raise_level（cfg 原地改写，引用稳定即可）。下面两处替换 cfg 时会重绑。
        self.view_section.cfg = self.cfg
        self.view_profile.cfg = self.cfg
        # 断面图把"点了哪个测点"回报给主窗口，由主窗口负责写入与重算。
        # 注意必须在这里连（view_section 刚创建），不能提到上面去。
        self.view_section.picked.connect(self._on_section_picked)
        self.view_section.pickCancelled.connect(lambda: self._set_pick_hint(None))
        self.view_section.pickMissed.connect(self._on_pick_missed)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.view_section, "断面形态")
        self.tabs.addTab(self.view_rating, "水位–流量曲线")
        self.tabs.addTab(self.view_profile, "沿河纵剖面")

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left_w)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 4, 8, 4)
        root.addLayout(top)
        root.addWidget(split, 1)
        self.lbl_status = QLabel("就绪。请点「载入并计算」。")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

    # ---------------- 载入与计算 ----------------
    # ---------------- 文件菜单 ----------------
    def _build_file_menu(self, mb):
        """「文件」菜单：工程读写 + 数据载入/导出 + 退出。

        工程文件（.dmprj）保存的是**全量快照**——断面几何、纵剖面实测数据、
        每个断面的参数、全部计算设置。打开时不依赖原始 xlsx，
        所以原文件被删或被改都仍能完整还原。
        """
        m = mb.addMenu("文件")

        self.act_new = m.addAction("新建工程")
        self.act_new.setToolTip("清空当前工程，回到未载入状态")
        self.act_new.triggered.connect(self._new_project)

        self.act_open = m.addAction("打开工程…")
        self.act_open.setShortcut("Ctrl+O")
        self.act_open.setToolTip(f"打开工程文件（{project_io.SUFFIX}）")
        self.act_open.triggered.connect(self._open_project)

        m.addSeparator()

        self.act_save = m.addAction("保存")
        self.act_save.setShortcut("Ctrl+S")
        self.act_save.setToolTip("保存到当前工程文件；从未保存过则等同「另存为」")
        self.act_save.triggered.connect(self._save_project)

        self.act_saveas = m.addAction("另存为…")
        self.act_saveas.setShortcut("Ctrl+Shift+S")
        self.act_saveas.triggered.connect(self._save_project_as)

        m.addSeparator()

        self.act_data = m.addAction("载入数据目录…")
        self.act_data.setToolTip("选择存放 xlsx 的目录，选完立即载入计算")
        self.act_data.triggered.connect(self._pick_and_load)

        self.act_export = m.addAction("导出结果…")
        self.act_export.setShortcut("Ctrl+E")
        self.act_export.triggered.connect(self._export)

        m.addSeparator()

        self.act_quit = m.addAction("退出")
        self.act_quit.setShortcut("Ctrl+Q")
        self.act_quit.triggered.connect(self.close)

        self.menu_file = m

    # ---------------- 工程文件读写 ----------------
    def _update_title(self):
        """标题里显示当前工程名与未保存标记。"""
        name = (os.path.basename(self.current_path)
                if self.current_path else "未命名工程")
        mark = " *" if self._dirty else ""
        self.setWindowTitle(f"{name}{mark} — "
                            f"{version_mod.APP_NAME}{version_mod.title_suffix()}")

    def _set_dirty(self, dirty: bool = True):
        if self._dirty != dirty:
            self._dirty = dirty
            self._update_title()

    def _maybe_save(self) -> bool:
        """有未保存改动时先问一句。返回 False 表示用户取消了当前操作。"""
        if self.project is None or not self._dirty:
            return True
        name = (os.path.basename(self.current_path)
                if self.current_path else "未命名工程")
        ans = QMessageBox.question(
            self, "尚未保存",
            f"工程「{name}」有改动尚未保存，是否先保存？",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save)
        if ans == QMessageBox.Cancel:
            return False
        if ans == QMessageBox.Save:
            return self._save_project()
        return True

    def _save_project(self) -> bool:
        if self.project is None:
            QMessageBox.information(self, "提示", "当前没有工程可保存。")
            return False
        if not self.current_path:
            return self._save_project_as()
        return self._write_project(self.current_path)

    def _save_project_as(self) -> bool:
        if self.project is None:
            QMessageBox.information(self, "提示", "当前没有工程可保存。")
            return False
        start = self.current_path or project_io.suggest_path(
            self.project, self.cfg.output_dir or app_root())
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为工程文件", start, project_io.FILE_FILTER)
        if not path:
            return False
        if not path.lower().endswith(project_io.SUFFIX):
            path += project_io.SUFFIX
        return self._write_project(path)

    def _write_project(self, path: str) -> bool:
        self._sync_cfg()            # 保存前把界面上的设置收进配置
        try:
            project_io.save_project(path, self.project, self.cfg,
                                    source=self.data_source)
        except Exception as e:
            QMessageBox.critical(self, "保存失败",
                                 f"{e}\n\n{traceback.format_exc()}")
            return False
        self.current_path = path
        self._set_dirty(False)
        self.lbl_status.setText(
            f"已保存到 {path}　（{len(self.project.profile_lines)} 条纵断面线 / "
            f"{len(self.project.all_sections())} 个横断面，含参数与计算设置）")
        return True

    def _open_project(self):
        if not self._maybe_save():
            return
        start = (os.path.dirname(self.current_path)
                 if self.current_path else self.data_dir)
        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程文件", start, project_io.FILE_FILTER)
        if not path:
            return
        try:
            project, cfg, meta = project_io.load_project(path)
        except Exception as e:
            QMessageBox.critical(self, "打开失败", f"{e}")
            return
        self._adopt_project(project, cfg, meta.get("source") or {}, path, meta)

    def _adopt_project(self, project: Project, cfg: Config, source: dict,
                       path: str | None, meta: dict | None = None):
        """把载入的工程装进界面。"""
        self.project = project
        self.cfg = cfg
        self.view_section.cfg = cfg
        self.view_profile.cfg = cfg
        self.data_source = source or {}
        self.current_path = path

        # 设置面板要刷成新配置，否则界面显示与实际不符。
        # sync_from 内部屏蔽了信号，不会触发重算。
        self.dlg_settings.sync_from(cfg)

        self._solve_all()
        self._refresh_line_list()
        self._refresh_current_views()
        self.param_panel.set_sections(project.all_sections())
        self._set_dirty(False)
        self._update_title()

        msg = (f"已打开 {os.path.basename(path) if path else '工程'}："
               f"{len(project.profile_lines)} 条纵断面线 / "
               f"{len(project.all_sections())} 个横断面。")
        desc = project_io.describe_source(meta or {})
        if desc:
            msg += "　" + desc
        # 工程文件忠实还原保存时的状态。若当时参数就是空的（例如用脚本
        # 直接由 xlsx 生成、跳过了界面的自动填补），这里必须说清，
        # 否则用户会以为「工程文件没保存参数」。
        miss = P.missing_params(project.all_sections())
        if miss:
            msg += (f"　⚠ 其中 {len(miss)} 个断面的参数为空，"
                    f"请用「批量填写」补齐后重新保存。")
        self.lbl_status.setText(msg)

    def _new_project(self):
        if not self._maybe_save():
            return
        self.project = None
        self.results.clear()
        self.infos.clear()
        self.warnings = []
        self.current_path = None
        self.data_source = {}
        self.cfg = Config()
        self.cfg.output_dir = os.path.join(app_root(), "output")
        self.view_section.cfg = self.cfg
        self.view_profile.cfg = self.cfg
        self.dlg_settings.sync_from(self.cfg)
        self._refresh_line_list()
        self._refresh_current_views()
        self._set_dirty(False)
        self._update_title()
        self.lbl_status.setText(
            "已新建空工程。请用「文件 → 载入数据目录…」或工具栏载入数据。")

    def _pick_and_load(self):
        """菜单里的「载入数据目录…」：选完目录立即载入（一步完成）。"""
        if not self._maybe_save():
            return
        d = QFileDialog.getExistingDirectory(
            self, "选择存放 xlsx 的目录", self.data_dir)
        if not d:
            return
        self.data_dir = d
        self.lbl_dir.setText(d)
        self._load()

    def closeEvent(self, event):
        """关窗口前给未保存的改动一次机会。"""
        if self._maybe_save():
            event.accept()
        else:
            event.ignore()

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择存放 xlsx 的目录", self.data_dir)
        if d:
            self.data_dir = d
            self.lbl_dir.setText(d)

    def _sync_cfg(self):
        self.dlg_settings.sync_to(self.cfg)

    def _on_settings_changed(self):
        """计算设置里任何一项改动都走这里。

        阈值与桩号原点会改变转折点 -> 分区 -> 整条曲线 -> 成灾水位，
        所以统一整体重算，并把「影响了什么」报出来——
        否则用户改完阈值若图上看不出变化，会以为控件没生效。
        """
        if self.project is None:
            self._sync_cfg()
            return
        self._recalc()
        msg = (f"已按新设置重算：断面模式＝"
               f"{'复式' if self.cfg.compound_mode else '单断面'}，"
               f"dH={self.cfg.dH:g}，陡坡阈值={self.cfg.steep_slope:g}，"
               f"缓坡阈值={self.cfg.turn_slope:g}。")
        self.lbl_status.setText(msg)
        self._set_dirty()          # 计算设置是工程数据的一部分

    def _on_export_settings_changed(self):
        """导出项（CSV 编码）变化：只同步配置，**不重算**。

        编码只影响写文件的字节，与几何/水力计算无关，
        重算 30 个断面纯属浪费，还会让状态栏冒出莫名其妙的"已重算"。
        """
        self._sync_cfg()
        show = {"utf-8-sig": "UTF-8 带 BOM", "utf-8": "UTF-8 无 BOM", "gbk": "GBK"}
        self.lbl_status.setText(
            f"导出编码已改为 {show.get(self.cfg.csv_encoding, self.cfg.csv_encoding)}，"
            f"下次导出即生效（无需重算）。")
        self._set_dirty()

    def _on_thresholds_changed(self):
        """仅阈值变化时的提示：突出受影响的断面数。"""
        if self.project is None:
            self._sync_cfg()
            return
        before = {n: i.disaster_level for n, i in self.infos.items()}
        self._recalc()
        changed = [n for n, i in self.infos.items()
                   if n in before and abs(i.disaster_level - before[n]) > 1e-9]
        msg = (f"已按新阈值重算：斜率 >{self.cfg.steep_slope:g} 判为陡坡，"
               f"<{self.cfg.turn_slope:g} 判为转折。")
        if changed:
            shown = "、".join(changed[:6]) + ("…" if len(changed) > 6 else "")
            msg += f"　{len(changed)} 个断面的成灾水位发生变化：{shown}"
        else:
            msg += "　所有断面的成灾水位未变（当前阈值下结果与之前相同）。"
        self.lbl_status.setText(msg)
        self._set_dirty()

    # ---------------- 批量填写 ----------------
    def _open_batch_dialog(self):
        """打开批量填写面板：先把工程与当前组喂进去，表格才有内容。"""
        if self.project is None:
            QMessageBox.information(self, "提示", "请先载入数据。")
            return
        self.dlg_batch.set_context(self.project, max(self.lst_lines.currentRow(), 0))
        self.dlg_batch.popup()

    def _on_batch_group_changed(self, index: int):
        """在批量填写窗口里换组时，主界面左侧列表跟着切——避免两处显示不一致。"""
        if self.project is None or index < 0:
            return
        if index < self.lst_lines.count() and self.lst_lines.currentRow() != index:
            self.lst_lines.setCurrentRow(index)

    def _on_batch_zone_changed(self, on: bool):
        """批量窗口的「分区填写」开关同步到主界面参数面板（用户要求的单向同步）。

        只改**显示**，不写数据——真正生效仍要用户点「应用到当前组」。
        粒度差异要留意：批量窗口的开关是**整组**的，参数面板是**当前断面**的，
        所以这里只是让两处看起来一致。
        """
        self.param_panel.set_zone_ui(on)

    def _apply_batch_from_dialog(self, to_all: bool = False):
        """把批量填写表格里的值写回断面参数。

        to_all=False  只写当前组（表格显示哪组就写哪组）
        to_all=True   把当前组的值套用到**所有**纵断面线

        「应用到全部组」需要一个统一值：各组断面数不同（2~7 个），无法逐行对位。
        所以本组该列有多个不同值时直接拒绝并说明原因，而不是替用户猜一个。
        """
        if self.project is None:
            self.dlg_batch.set_result("请先载入数据。", ok=False)
            return

        data = self.dlg_batch.collect()
        if not data:
            return

        if to_all:
            for field, label in (("design_q", "设计流量"), ("slope", "比降"),
                                 ("roughness", "糙率")):
                vals = {v for v in (rec.get(field) for rec in data.values())
                        if v is not None and v == v}
                if len(vals) > 1:
                    QMessageBox.information(
                        self, "需要一个统一值",
                        f"「{label}」在当前组里有 {len(vals)} 个不同的值，"
                        f"无法确定该用哪一个套用到全部组。\n\n"
                        f"「应用到全部组」是把一个统一值套到所有纵断面线的全部断面"
                        f"（各组断面数不同，无法逐行对位）；\n"
                        f"若要逐个断面填不同的值，请用「应用到当前组」。")
                    return

        zone = self.dlg_batch.zone_enabled()
        by_name = {s.name: s for s in self.project.all_sections()}

        if to_all:
            template: dict = {}
            for field in ("design_q", "slope", "roughness",
                          "roughness_main", "roughness_left", "roughness_right"):
                vs = {rec.get(field) for rec in data.values()}
                vs = {v for v in vs if v is not None and v == v}
                template[field] = next(iter(vs)) if len(vs) == 1 else float("nan")
            targets = self.project.all_sections()
            for sec in targets:
                _write_params(sec, template, zone)
            changed = len(targets)
        else:
            changed = 0
            for name, rec in data.items():
                sec = by_name.get(name)
                if sec is None:
                    continue
                _write_params(sec, rec, zone)
                changed += 1

        self._solve_all()
        self._refresh_current_views()
        self.param_panel.refresh_status()
        if self.dlg_batch.isVisible():
            self.dlg_batch.refresh_from_params()

        left = len(P.missing_params(self.project.all_sections()))
        scope = "全部纵断面线" if to_all else "当前组"
        txt = f"已更新 {changed} 个断面（{scope}）。"
        txt += f"仍有 {left} 个断面参数未填。" if left else "参数已全部填齐。"
        self.dlg_batch.set_result(txt, ok=(left == 0))
        self.lbl_status.setText(txt)
        if changed:
            self._set_dirty()

    def _scan_source_files(self) -> dict:
        """记录数据来源（目录 + xlsx 文件名），存进工程文件便于追溯。"""
        files: list[str] = []
        try:
            files = sorted(
                f for f in os.listdir(self.data_dir)
                if f.lower().endswith(".xlsx") and not f.startswith("~$"))
        except OSError:
            pass
        return {"dir": self.data_dir, "files": files}

    def _load(self):
        self._sync_cfg()
        try:
            project, warnings = load_folder(self.data_dir, self.cfg)
        except Exception as e:
            QMessageBox.critical(self, "载入失败", f"{e}\n\n{traceback.format_exc()}")
            return
        self.project = project
        self.warnings = warnings
        # 从数据目录载入的工程还没有对应的 .dmprj 文件
        self.current_path = None
        self.data_source = self._scan_source_files()

        # 参数集通常没有，载入后若参数为空，先用默认值填补，
        # 否则三张图都是空的，用户会以为程序坏了。
        # 这里用 only_missing=True，不会覆盖任何已填的值，并在状态栏明确告知。
        all_secs = project.all_sections()
        filled = 0
        if P.missing_params(all_secs):
            filled = P.apply_batch(all_secs, only_missing=True,
                                   roughness=DEFAULT_ROUGHNESS,
                                   slope=DEFAULT_SLOPE,
                                   design_q=DEFAULT_DESIGN_Q)
            self.dlg_batch.set_result(
                f"载入时自动填补了 {filled} 个断面的参数。请核实后按实际取值修改。",
                ok=False)
        self.param_panel.set_sections(all_secs)

        self._solve_all()
        self._refresh_line_list()

        msg = (f"载入完成：{len(project.profile_lines)} 条纵断面线，"
               f"{len(all_secs)} 个横断面。")
        if filled:
            msg += (f"　⚠ 参数集缺失，已用默认值（糙率 {DEFAULT_ROUGHNESS:g}、"
                    f"比降 {DEFAULT_SLOPE:g}、Qs {DEFAULT_DESIGN_Q:g}）填补 "
                    f"{filled} 个断面，**请核实后按实际取值修改**。")
        if warnings:
            msg += "　告警：" + "；".join(warnings[:3])
        self.lbl_status.setText(msg)
        self._set_dirty(True)      # 刚载入的数据还没保存成工程文件
        self._update_title()

    def _solve_all(self):
        if self.project is None:
            return
        self.results.clear()
        self.infos.clear()
        for sec in self.project.all_sections():
            res, info = solve_section(sec, self.cfg)
            self.results[sec.name] = res
            self.infos[sec.name] = info

    def _recalc(self):
        if self.project is None:
            return
        self._sync_cfg()
        self._solve_all()
        self._refresh_current_views()
        self.lbl_status.setText("已按当前设置重新计算。")

    def _on_params_changed(self):
        self._recalc()
        self._set_dirty()

    # ---------------- 比降推算 ----------------
    def _open_slope_dialog(self):
        """打开比降推算面板。先把当前工程喂进去再弹，否则表是空的。"""
        if self.project is None:
            QMessageBox.information(self, "提示", "请先载入数据。")
            return
        self.dlg_slope.set_project(self.project)
        self.dlg_slope.popup()

    def _on_slopes_applied(self, only_missing: bool):
        """把建议比降写入断面参数并重算。

        比降是曼宁公式里的 sqrt(S)，直接决定流量；所以这里明确报出
        「改了几个 / 跳过几个」，不静默处理。
        """
        if self.project is None:
            return
        props = self.dlg_slope.proposals
        changed = slope_mod.apply_slopes(self.project, props,
                                         only_missing=only_missing)
        skipped = sum(1 for p in props if p.bad)

        self._recalc()

        # 左侧参数面板显示的是单个断面的比降，改了参数必须同步刷新，
        # 否则面板上还是旧值，看起来像没生效
        ln = self._current_line()
        row = self.lst_secs.currentRow()
        if ln is not None and 0 <= row < len(ln.sections):
            self.param_panel.set_current(ln.sections[row])
        # 表里的「当前比降 / 差异」也要跟着更新
        self.dlg_slope.refresh()

        if changed:
            msg = f"已用纵断面推算的比降更新 {changed} 个断面。"
        elif only_missing:
            msg = "没有需要填补的比降——所有断面都已填过。"
        else:
            msg = "没有断面被更新。"
        if skipped:
            msg += f"　跳过 {skipped} 个无效建议值（倒坡或数据不足）。"
        self.lbl_status.setText(msg)
        if changed:
            self._set_dirty()

    # ---------------- 手动调节（Q14）----------------
    def _open_zone_dialog(self):
        self._open_manual_dialog(self.dlg_zone)

    def _open_disaster_dialog(self):
        self._open_manual_dialog(self.dlg_disaster)

    def _open_about_dialog(self):
        """「关于」：不依赖工程是否已载入，随时可看自己跑的是哪一版。"""
        self.dlg_about.show()
        self.dlg_about.raise_()
        self.dlg_about.activateWindow()

    def _open_manual_dialog(self, dlg):
        """弹出手动调节面板。先喂数据再弹，否则表格是空的。"""
        if self.project is None:
            QMessageBox.information(self, "提示", "请先载入数据。")
            return
        self._refresh_manual_panels()
        dlg.popup()

    def _refresh_manual_panels(self):
        """把当前工程 / 结果 / 设置 / 当前组 / 当前断面推给两个面板。

        面板不自己维护选中状态，全部由这里推——只有一处真相，
        不会出现"面板显示 yqc6-2、实际改到 sls2-1"这种错位。
        """
        idx = max(self.lst_lines.currentRow(), 0)
        sec = self._current_section()
        for d in (self.dlg_zone, self.dlg_disaster):
            d.set_context(self.project, self.infos, self.results, self.cfg, idx)
            d.set_current(sec)

    def _current_section(self):
        ln = self._current_line()
        if ln is None:
            return None
        row = self.lst_secs.currentRow()
        if 0 <= row < len(ln.sections):
            return ln.sections[row]
        return None

    def _set_pick_hint(self, target: str | None):
        """把"正在拾取哪一个"同步给对应面板，另一个清掉。"""
        self.dlg_zone.set_picking(target if target in ("thalweg", "left", "right")
                                  else None)
        self.dlg_disaster.set_picking(target if target == "disaster" else None)

    def _on_manual_pick(self, target: str):
        if self.project is None:
            return
        if self._current_section() is None:
            QMessageBox.information(self, "提示", "请先在左侧选中一个横断面。")
            return
        # 单断面模式下 info.zones 被整个忽略（rating.py 里直接 zones=[(0,n)]），
        # 拾取边界不会有任何效果 —— 当场拦下，不让用户白点。
        # 注意深泓点**不**受此限制：它还决定 H~Q 曲线的起算水位，两种模式都用。
        if target in ("left", "right") and not self.cfg.compound_mode:
            QMessageBox.information(
                self, "当前是单断面模式",
                "单断面模式下分区不参与计算，拾取边界不会有任何效果。\n\n"
                "请先到「计算设置」把断面模式改为复式断面。")
            return
        # 同一个目标再点一次 = 取消
        if self.view_section.pick_mode == target:
            self.view_section.cancel_pick()
            return
        self.view_section.cancel_pick(notify=False)
        # 拾取必须看着断面图，否则用户不知道往哪点
        self.tabs.setCurrentWidget(self.view_section)
        if not self.view_section.begin_pick(target):
            return
        self._set_pick_hint(target)
        self.lbl_status.setText(
            f"拾取模式：请在「断面形态」图上点击一个测点，"
            f"把它设为【{PICK_LABELS[target]}】。")

    def _on_pick_missed(self):
        """点到了图上，但没落在任何实测测点上。

        这里**不能静默**：静默的结果就是用户反复点、以为功能坏了，
        却不知道自己只是没点准。明确说一句，并保留拾取模式让他再点一次。
        """
        self.lbl_status.setText(
            "没落在实测测点上——请把光标移到图上的小圆圈（测点）上再点。"
            "分区边界与深泓点只能选实测测点，不能选两点之间的位置。")

    def _on_section_picked(self, target: str, idx: int):
        """断面图回报：用户点了第 idx 个测点。"""
        self._set_pick_hint(None)
        sec = self._current_section()
        if sec is None:
            return
        if target == "thalweg":
            # 手动深泓点一并改掉曲线起算水位，所以必须先确认它不会与已有边界打架
            changes = {"thalweg_manual": idx}
        elif target == "left":
            changes = {"zone_manual": True, "zone_left": idx}
        elif target == "right":
            changes = {"zone_manual": True, "zone_right": idx}
        elif target == "disaster":
            changes = {"disaster_idx_manual": idx}
        else:
            return

        errs = _try_apply_override(sec, **changes)
        if errs:
            QMessageBox.warning(
                self, "无法写入",
                "这个测点与已有的手动设定冲突，未写入：\n\n"
                + "\n".join(errs)
                + "\n\n可先清除相关的手动值（面板上的「清除」），或另选一个测点。")
            self._refresh_manual_panels()
            return

        self._recalc()
        self._set_dirty()
        self.lbl_status.setText(
            f"{sec.name}：已把【{PICK_LABELS[target]}】设为第 {idx + 1} 个测点"
            f"（起点距 {sec.s[idx]:.1f} m，高程 {sec.z[idx]:.2f} m）。")

    def _on_manual_cleared(self, target: str):
        """清除某一项手动值。

        这里**不做校验**：清除只会减少约束，不该因为"清完之后剩下的手动值
        不自洽"而拒绝执行——那会让按钮看起来坏了。清除之后若真的不自洽，
        面板的警告条与断面图的告警框会把它显示出来。
        （可能的情形：手动深泓点被清掉后，自动深泓点恰好落在手动边界外侧。）
        """
        sec = self._current_section()
        if sec is None:
            return
        if target == "thalweg":
            sec.thalweg_manual = None
        elif target == "left":
            sec.zone_left = None
        elif target == "right":
            sec.zone_right = None
        elif target == "disaster":
            sec.disaster_idx_manual = None
        else:
            return
        self._recalc()
        self._set_dirty()
        self.lbl_status.setText(
            f"{sec.name}：已清除【{PICK_LABELS[target]}】的手动设定，改用自动推算值。")

    def _on_manual_reset_group(self):
        ln = self._current_line()
        if ln is None:
            return
        n = sum(1 for s in ln.sections if s.has_manual)
        if n == 0:
            self.lbl_status.setText(f"{ln.name}：本组没有任何手动设定。")
            return
        ans = QMessageBox.question(
            self, "恢复自动",
            f"「{ln.name}」有 {n} 个断面存在手动设定（深泓点 / 分区边界 / 成灾水位）。\n\n"
            f"确认全部清除、改回程序自动推算？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        for s in ln.sections:
            s.thalweg_manual = None
            s.zone_manual = False
            s.zone_left = None
            s.zone_right = None
            s.disaster_idx_manual = None
        self._recalc()
        self._set_dirty()
        self.lbl_status.setText(f"{ln.name}：已清除 {n} 个断面的手动设定，全部恢复自动推算。")

    def _on_manual_group_changed(self, index: int):
        """在手动调节面板里换组时，主界面左侧列表跟着切。"""
        if self.project is None or index < 0:
            return
        if index < self.lst_lines.count() and self.lst_lines.currentRow() != index:
            self.lst_lines.setCurrentRow(index)

    def _on_manual_section_activated(self, row: int):
        """双击状态表某行 -> 主界面切到该断面（面板顶部会跟着更新）。"""
        if 0 <= row < self.lst_secs.count():
            self.lst_secs.setCurrentRow(row)

    # ---------------- 列表 ----------------
    def _refresh_line_list(self):
        self.lst_lines.blockSignals(True)
        self.lst_lines.clear()
        if self.project:
            for ln in self.project.profile_lines:
                self.lst_lines.addItem(
                    QListWidgetItem(f"{ln.name}　({len(ln.sections)} 个断面)"))
        self.lst_lines.blockSignals(False)
        if self.lst_lines.count():
            self.lst_lines.setCurrentRow(0)
        else:
            self.lst_secs.clear()
            self.param_panel.set_sections([])

    def _current_line(self):
        if self.project is None or self.lst_lines.currentRow() < 0:
            return None
        return self.project.profile_lines[self.lst_lines.currentRow()]

    def _on_line_changed(self, row: int):
        # 纵剖面图直接跟随这里的选中项，不再单独设下拉框
        self.view_profile.set_line(max(row, 0))
        ln = self._current_line()
        self.lst_secs.blockSignals(True)
        self.lst_secs.clear()
        if ln is not None:
            for sec, ch in zip(ln.sections, ln.chainage or [None] * len(ln.sections)):
                label = sec.name
                if ch is not None and ch == ch:
                    label += f"　桩号 {ch:.1f} m"
                self.lst_secs.addItem(QListWidgetItem(label))
            self.param_panel.set_sections(ln.sections)
        self.lst_secs.blockSignals(False)
        if self.lst_secs.count():
            self.lst_secs.setCurrentRow(0)
        else:
            self.view_section.set_data(None, None, None)
            self.param_panel.set_current(None)

        # 批量填写窗口若开着，表格要跟着换到当前组（它只显示一组）
        if self.dlg_batch.isVisible():
            self.dlg_batch.set_group(max(row, 0))
        # 手动调节面板同理，且**必须无条件刷新**：
        # 上面 setCurrentRow(0) 在"行号本来就已经是 0"时不发信号，
        # 那条路径不会走到 _refresh_current_views，面板就会留在上一组。
        self._refresh_manual_panels()

    def _on_section_changed(self, row: int):
        ln = self._current_line()
        if ln is None or row < 0 or row >= len(ln.sections):
            self.param_panel.set_current(None)
            return
        sec = ln.sections[row]
        self.param_panel.set_current(sec)
        self._refresh_current_views()

    def _refresh_current_views(self):
        ln = self._current_line()
        items = list(self.results.items())
        if self.project:
            ordered = [(s, self.results[s.name], self.infos[s.name])
                       for line in self.project.profile_lines for s in line.sections
                       if s.name in self.results]
        else:
            ordered = []

        row = self.lst_secs.currentRow()
        if ln is not None and 0 <= row < len(ln.sections):
            sec = ln.sections[row]
            ch = (ln.chainage[row] if ln.chainage and row < len(ln.chainage) else None)
            self.view_section.set_data(sec, self.results[sec.name],
                                       self.infos[sec.name], ch)
            self.view_rating.set_data(ordered, sec.name)
        else:
            self.view_rating.set_data(ordered, None)
        self.view_profile.set_data(self.project, self.results, self.infos,
                                   line_index=max(self.lst_lines.currentRow(), 0))
        # 两个手动调节面板也要跟着走，否则会出现"面板显示的是别的断面"
        self._refresh_manual_panels()

    # ---------------- 导出 ----------------
    def _export(self):
        if self.project is None:
            QMessageBox.information(self, "提示", "请先载入数据。")
            return
        out = QFileDialog.getExistingDirectory(self, "选择导出目录", self.cfg.output_dir)
        if not out:
            return
        try:
            written = export_all(self.project, self.results, self.infos, self.cfg, out)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出完成", f"已生成 {len(written)} 个文件：\n" +
                                "\n".join(written))
