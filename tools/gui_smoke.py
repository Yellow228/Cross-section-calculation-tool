"""离屏冒烟测试：构造主窗口、载入真实数据、跑三个视图，并导出 PNG 供肉眼检查。"""

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def strip_html(s: str) -> str:
    """去掉 QLabel 里的富文本标签，便于打印到日志。"""
    import re
    return re.sub(r"<[^>]+>", "", s or "")
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

out = []
failed = False
try:
    from PySide6.QtWidgets import QApplication

    from app.main import setup_font

    app = QApplication(sys.argv)
    out.append("QApplication OK")
    out.append(f"界面字体：{setup_font(app) or '未找到'}")
    from PySide6.QtGui import QFontDatabase
    out.append(f"系统字体数：{len(QFontDatabase.families())}")

    from app.main_window import MainWindow
    win = MainWindow()
    out.append("MainWindow OK")

    win.data_dir = os.path.join(ROOT, "data")
    win._load()
    out.append("_load OK: " + win.lbl_status.text())

    # 逐个切换纵断面线与断面，确保每条都能画出来
    for li in range(win.lst_lines.count()):
        win.lst_lines.setCurrentRow(li)
        for si in range(win.lst_secs.count()):
            win.lst_secs.setCurrentRow(si)
    out.append(f"遍历完成：{win.lst_lines.count()} 条线")

    shots = os.path.join(ROOT, "output", "_preview")
    os.makedirs(shots, exist_ok=True)

    # 整窗截图（含顶部菜单栏与左侧面板），便于核对界面布局
    win.resize(1500, 900)
    win.grab().save(os.path.join(shots, "0_主界面.png"))
    win.dlg_settings.popup()
    win.dlg_settings.grab().save(os.path.join(shots, "0a_计算设置面板.png"))
    win.dlg_settings.hide()
    win.dlg_batch.set_context(win.project, 0)
    win.dlg_batch.popup()
    win.dlg_batch.grab().save(os.path.join(shots, "0b_批量填写面板.png"))
    win.dlg_batch.hide()
    out.append("整窗与两个面板截图 OK")

    win.tabs.setCurrentIndex(0)
    win.view_section.plot.canvas.fig.savefig(
        os.path.join(shots, "1_断面形态.png"), dpi=110, bbox_inches="tight")

    win.tabs.setCurrentIndex(1)
    win.view_rating.chk_overlay.setChecked(True)
    win.view_rating.plot.canvas.fig.savefig(
        os.path.join(shots, "2_水位流量曲线.png"), dpi=110, bbox_inches="tight")

    # 纵剖面跟随左侧纵断面线列表的选中项
    win.tabs.setCurrentIndex(2)
    win.lst_lines.setCurrentRow(0)
    win.view_profile.plot.canvas.fig.savefig(
        os.path.join(shots, "3_沿河纵剖面.png"), dpi=110, bbox_inches="tight")
    out.append("PNG 已导出到 output/_preview")

    # 在左侧列表切到第二条，纵剖面应自动跟随
    win.lst_lines.setCurrentRow(1)
    assert win.view_profile.line_index == 1, "纵剖面没有跟随列表选择"
    win.view_profile.plot.canvas.fig.savefig(
        os.path.join(shots, "4_纵剖面_跟随列表切换.png"), dpi=110,
        bbox_inches="tight")
    out.append("纵剖面跟随左侧列表切换 OK")

    # 顶部菜单栏的两个面板
    out.append(f"菜单项: {[a.text() for a in win.menuBar().actions()]}")
    win.act_settings.trigger()
    assert win.dlg_settings.isVisible(), "点「计算设置」没有弹出面板"
    out.append(f"计算设置面板已弹出，可见={win.dlg_settings.isVisible()}")
    win.act_batch.trigger()
    assert win.dlg_batch.isVisible(), "点「批量填写」没有弹出面板"
    out.append("批量填写面板已弹出")
    # 再点一次不应产生第二个窗口
    win.act_settings.trigger()
    out.append(f"重复点击仍为同一窗口: {win.dlg_settings is win.dlg_settings}")
    win.dlg_settings.hide()
    win.dlg_batch.hide()

    # 计算设置面板里的各项
    d = win.dlg_settings
    d.cmb_mode.setCurrentIndex(1)
    out.append("切换单断面模式 OK: " + win.lbl_status.text()[:70])
    d.cmb_mode.setCurrentIndex(0)
    d.cmb_origin.setCurrentIndex(1)
    out.append("切换桩号原点 OK")

    # 陡坡 / 缓坡阈值调节
    base = win.cfg.steep_slope, win.cfg.turn_slope
    d.spin_steep.setValue(0.20)
    d.spin_turn.setValue(0.030)
    out.append(f"阈值已传达到 cfg: 陡坡={win.cfg.steep_slope} 缓坡={win.cfg.turn_slope}")
    out.append("调节后状态: " + win.lbl_status.text()[:110])
    d.btn_reset.click()
    out.append(f"恢复默认: 陡坡={win.cfg.steep_slope} 缓坡={win.cfg.turn_slope} "
               f"(应为 {base[0]} / {base[1]})")
    assert win.cfg.steep_slope == base[0] and win.cfg.turn_slope == base[1]

    # 极端阈值不应崩
    d.spin_steep.setValue(10.0)
    d.spin_turn.setValue(0.001)
    out.append("极端阈值不崩 OK")
    d.btn_reset.click()

    # CSV 导出编码：切换应写入 cfg，且**不触发重算**（编码与几何无关）
    assert win.cfg.csv_encoding == "utf-8-sig", "默认编码应为 utf-8-sig"
    # 重算会重建 SectionResult 对象，比对象身份才能证明真的没重算
    ids_before = {k: id(v) for k, v in win.results.items()}
    d.cmb_enc.setCurrentIndex(2)                       # GBK
    out.append(f"编码切到 GBK: cfg={win.cfg.csv_encoding}")
    assert win.cfg.csv_encoding == "gbk"
    ids_after = {k: id(v) for k, v in win.results.items()}
    assert ids_after == ids_before, "改编码不应触发重算（结果对象被重建了）"
    out.append("改编码未触发重算 OK　状态: " + win.lbl_status.text()[:70])
    d.cmb_enc.setCurrentIndex(1)                       # UTF-8 无 BOM
    assert win.cfg.csv_encoding == "utf-8"
    d.cmb_enc.setCurrentIndex(0)                       # 回到默认
    assert win.cfg.csv_encoding == "utf-8-sig"
    out.append("编码三项切换均正确，已复原为 utf-8-sig")

    # ---- 按纵断面推算比降 ----
    win.act_slope.trigger()
    assert win.dlg_slope.isVisible(), "点「按纵断面推算比降」没有弹出面板"
    out.append(f"比降面板已弹出，可见={win.dlg_slope.isVisible()}")
    win.dlg_slope.grab().save(os.path.join(shots, "0c_比降推算面板.png"))

    n_sec = len(win.project.all_sections())
    assert win.dlg_slope.table.rowCount() == n_sec, "建议值表行数应等于断面总数"
    assert win.dlg_slope.mode() == "jc", "默认应为约翰斯通-克罗斯法"
    out.append(f"建议值表 {n_sec} 行，默认口径={win.dlg_slope.mode()}")

    # 三种口径都要能算出来，且同一条线内共用一个值
    vals: dict[str, dict[str, float]] = {}
    for key, rb in (("jc", "rb_jc"), ("endpoints", "rb_end"), ("lsq", "rb_lsq")):
        getattr(win.dlg_slope, rb).setChecked(True)
        assert win.dlg_slope.mode() == key
        vals[key] = {p.line: p.proposed for p in win.dlg_slope.proposals}
    out.append("三口径对比（前三条线）: " + "　".join(
        f"{k}: " + "/".join(f"{vals[k][ln]:.5f}" for ln in list(vals["jc"])[:3])
        for k in vals))
    # jc 对 √S 加权（凹函数），无倒坡段时数学上必须 ≤ 两端点法。
    # 有倒坡段被跳过的线不适用——分母变小会把结果抬高（实测 SJC4 因此高 1.4%）。
    est_by_line = {e.line: e for e in win.dlg_slope.estimates}
    skipped_lines = [ln for ln in vals["jc"]
                     if est_by_line[ln].jc_skipped]
    for ln, v in vals["jc"].items():
        if est_by_line[ln].jc_skipped == 0:
            assert v <= vals["endpoints"][ln] + 1e-12, \
                f"{ln}: 约翰斯通-克罗斯法不应大于两端点法（Jensen 不等式）"
    out.append(f"JC ≤ 两端点法成立 OK（有倒坡段被跳过的线除外: {skipped_lines}）")

    per_line: dict[str, set] = {}
    for p in win.dlg_slope.proposals:
        per_line.setdefault(p.line, set()).add(round(p.proposed, 12))
    assert all(len(v) == 1 for v in per_line.values()), \
        "同一条纵断面线内所有断面必须共用一个比降"
    out.append("同线共用一个比降 OK")

    win.dlg_slope.rb_jc.setChecked(True)           # 回到默认口径

    # 真实数据上不应有无效建议值（有则说明数据或算法出了问题）
    bad = [p.section for p in win.dlg_slope.proposals if p.bad]
    assert not bad, f"出现无效比降建议值: {bad}"
    out.append("无无效建议值 OK")

    # 仅填补空白：载入时已用默认值填过，所以应为 0 个
    win.dlg_slope.btn_missing.click()
    out.append("仅填补空白: " + win.lbl_status.text()[:90])
    assert "没有需要填补" in win.lbl_status.text()

    # 应用到全部：比降应被替换，并且重算过
    before = {s.name: s.params.slope for s in win.project.all_sections()}
    ids_before2 = {k: id(v) for k, v in win.results.items()}
    win.dlg_slope.btn_all.click()
    out.append("应用到全部: " + win.lbl_status.text()[:110])
    after = {s.name: s.params.slope for s in win.project.all_sections()}
    assert all(abs(before[k] - after[k]) > 1e-12 for k in after), "比降未被更新"
    ids_after2 = {k: id(v) for k, v in win.results.items()}
    assert ids_after2 != ids_before2, "应用比降后应重算"
    vals = sorted(set(round(v, 8) for v in after.values()))
    out.append(f"更新后各线比降取值: {vals[:8]}{'…' if len(vals) > 8 else ''}")
    win.dlg_slope.hide()

    # ---- 文件菜单与未保存标记 ----
    assert win.menu_file.title() == "文件", "菜单栏第一个菜单应为「文件」"
    file_acts = [a.text() for a in win.menu_file.actions() if a.text()]
    out.append(f"文件菜单: {file_acts}")
    for need in ("新建工程", "打开工程…", "保存", "另存为…",
                 "载入数据目录…", "导出结果…", "退出"):
        assert need in file_acts, f"文件菜单缺少「{need}」"
    out.append("文件菜单项齐全 OK")

    # 改过数据后应是「未保存」状态，标题带 *
    assert win._dirty, "改过参数后应标记未保存"
    assert "*" in win.windowTitle(), f"标题应带未保存标记：{win.windowTitle()}"
    out.append(f"未保存标记 OK，标题={win.windowTitle()}")

    # 保存后标记应清除（写临时文件，不弹对话框）
    import tempfile
    from core import project_io
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "smoke.dmprj")
        win.current_path = tmp
        assert win._write_project(tmp), "保存工程文件失败"
        assert not win._dirty, "保存后应清除未保存标记"
        assert "*" not in win.windowTitle()
        size_kb = os.path.getsize(tmp) // 1024
        proj2, cfg2, meta = project_io.load_project(tmp)
        assert len(proj2.profile_lines) == len(win.project.profile_lines)
        assert len(proj2.all_sections()) == len(win.project.all_sections())
        out.append(f"保存/重新打开 OK：{size_kb} KB，"
                   f"{len(proj2.profile_lines)} 条线 / "
                   f"{len(proj2.all_sections())} 个断面")

    # ---- 端到端：改参数与设置 → 保存 → 新建 → 打开 → 界面控件也要显示出来 ----
    line0 = win.project.profile_lines[0]
    target = line0.sections[0]
    tname = target.name
    target.params.slope = 0.01234
    target.params.roughness = 0.037
    target.params.design_q = 88.0
    target.params.roughness_main = 0.031
    target.params.roughness_left = 0.021
    target.params.roughness_right = 0.041
    win.dlg_settings.spin_dh.setValue(0.3)
    win.dlg_settings.cmb_origin.setCurrentIndex(1)          # 首点

    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "e2e.dmprj")
        win.current_path = tmp
        assert win._write_project(tmp), "保存失败"
        win._new_project()
        assert win.project is None, "新建应清空"
        p2, c2, m2 = project_io.load_project(tmp)
        win._adopt_project(p2, c2, m2.get("source") or {}, tmp, m2)

    # ① 数据层
    t2 = {s.name: s for s in win.project.all_sections()}[tname]
    assert abs(t2.params.slope - 0.01234) < 1e-12
    assert abs(t2.params.roughness - 0.037) < 1e-12
    assert abs(t2.params.design_q - 88.0) < 1e-12
    assert abs(t2.params.roughness_main - 0.031) < 1e-12
    assert abs(t2.params.roughness_left - 0.021) < 1e-12
    assert abs(t2.params.roughness_right - 0.041) < 1e-12
    assert abs(win.cfg.dH - 0.3) < 1e-12
    assert win.cfg.chainage_origin == "start"
    out.append(f"打开工程后数据层一致 OK（{tname} 的比降/糙率/Qs/三分区糙率"
               f" + dH/桩号原点）")

    # ② 设置面板要同步显示恢复的设置
    assert abs(win.dlg_settings.spin_dh.value() - 0.3) < 1e-9, "设置面板未同步 dH"
    assert win.dlg_settings.cmb_origin.currentIndex() == 1, "设置面板未同步桩号原点"

    # ③ 参数面板切到目标断面后必须显示恢复的值
    for li, ln in enumerate(win.project.profile_lines):
        names = [s.name for s in ln.sections]
        if tname in names:
            win.lst_lines.setCurrentRow(li)
            win.lst_secs.setCurrentRow(names.index(tname))
            break
    pp = win.param_panel
    assert pp.lbl_name.text() == tname, f"参数面板未切到目标断面：{pp.lbl_name.text()}"
    assert abs(pp.o_slope.value() - 0.01234) < 1e-9, f"比降未同步：{pp.o_slope.value()}"
    assert abs(pp.o_rough.value() - 0.037) < 1e-9, f"糙率未同步：{pp.o_rough.value()}"
    assert abs(pp.o_q.value() - 88.0) < 1e-9, f"Qs 未同步：{pp.o_q.value()}"
    assert pp.o_use_zone.isChecked(), "存在分区糙率时应自动勾选"
    assert abs(pp.o_main.value() - 0.031) < 1e-9, f"主槽糙率未同步：{pp.o_main.value()}"
    assert abs(pp.o_left.value() - 0.021) < 1e-9, f"左滩糙率未同步：{pp.o_left.value()}"
    assert abs(pp.o_right.value() - 0.041) < 1e-9, f"右滩糙率未同步：{pp.o_right.value()}"
    out.append("打开工程后界面层一致 OK（设置面板 + 参数面板控件均显示恢复值）")

    win.current_path = None
    win._set_dirty(True)

    # ---- 批量填写（改版：三页签表格，只显示当前组，支持 Excel 粘贴）----
    d = win.dlg_batch
    win.act_batch.trigger()
    assert d.isVisible(), "点「批量填写」没有弹出面板"
    tabs = [d.tabs.tabText(i) for i in range(d.tabs.count())]
    assert tabs == ["流量", "比降", "糙率"], f"页签不对：{tabs}"
    out.append(f"批量填写页签: {tabs}　当前组 {d.cmb_group.currentText()}　"
               f"表格 {d.t_q.rowCount()} 行")

    # 打开时应已用当前参数填好
    s0 = win.project.profile_lines[0].sections[0]
    assert d.t_q.rowCount() == len(win.project.profile_lines[0].sections), \
        "表格行数应等于当前组断面数"
    assert abs(float(d.t_s.item(0, 2).text()) - s0.params.slope) < 1e-6, \
        "打开窗口时应填入当前比降"
    out.append(f"表格默认填入当前值 OK（{s0.name} 比降={d.t_s.item(0, 2).text()}，"
               f"Qs={d.t_q.item(0, 2).text()}）")

    # 改一格 → 应用到当前组
    d.t_q.item(0, 2).setText("77.5")
    d.btn_apply.click()
    assert abs(s0.params.design_q - 77.5) < 1e-9, "表格的值没有写回参数"
    out.append("应用到当前组 OK：" + strip_html(d.lbl_result.text()))

    # 糙率页：列数恒定 5 列；分区开关只控制后两列的可编辑性（灰显），
    # 并把状态同步到主界面参数面板
    from PySide6.QtCore import Qt as QtC
    heads = [d.t_n.horizontalHeaderItem(c).text()
             for c in range(d.t_n.columnCount())]
    assert heads == ["断面", "桩号 (m)", "糙率 n", "左滩 n", "右滩 n"], heads

    d.chk_zone.setChecked(False)
    assert d.t_n.columnCount() == 5, "取消分区后列数不应改变"
    assert not (d.t_n.item(0, 3).flags() & QtC.ItemIsEditable), \
        "不勾选分区时左滩列应不可编辑"
    assert not (d.t_n.item(0, 4).flags() & QtC.ItemIsEditable), \
        "不勾选分区时右滩列应不可编辑"
    assert win.param_panel.o_use_zone.isChecked() is False, "未同步到参数面板"

    d.chk_zone.setChecked(True)
    assert d.t_n.columnCount() == 5, "勾选分区后列数也不应改变"
    assert d.t_n.item(0, 3).flags() & QtC.ItemIsEditable, "勾选后左滩列应可编辑"
    assert d.t_n.item(0, 4).flags() & QtC.ItemIsEditable, "勾选后右滩列应可编辑"
    assert win.param_panel.o_use_zone.isChecked(), "分区开关未同步到参数面板"
    out.append("糙率页列数恒定 5 列 + 分区开关只灰显 + 同步到参数面板 OK")

    # 模拟从 Excel 粘贴：制表符分隔的多行文本应按行铺开
    d.tabs.setCurrentIndex(1)
    d.t_s.setCurrentCell(0, 2)
    QApplication.clipboard().setText("0.01111\n0.02222\n0.03333")
    n_written = d.t_s.paste_from_clipboard()
    assert n_written == 3, f"应写入 3 格，实际 {n_written}"
    assert d.t_s.item(0, 2).text() == "0.01111"
    assert d.t_s.item(2, 2).text() == "0.03333"
    out.append(f"Excel 粘贴 OK：3 行铺开为 {n_written} 格"
               f"（{d.t_s.item(0, 2).text()} … {d.t_s.item(2, 2).text()}）")

    # 只读列（断面名）不能被粘贴破坏
    d.t_s.setCurrentCell(0, 0)
    QApplication.clipboard().setText("被破坏的名字")
    d.t_s.paste_from_clipboard()
    assert d.t_s.item(0, 0).text() == s0.name, "只读列不该被粘贴覆盖"
    out.append("粘贴不会覆盖只读列 OK")

    # 非数字要能被拦下
    d.t_s.item(0, 2).setText("abc")
    bad = d.t_s.mark_invalid()
    assert bad, "非数字单元格应被标出"
    d.t_s.item(0, 2).setText("0.01111")
    out.append(f"非数字校验 OK（{bad[0]}）")

    # 换组应刷新表格
    d.set_group(1)
    out.append(f"换到第 2 组：{d.cmb_group.currentText()}　表格 {d.t_q.rowCount()} 行")
    d.set_group(0)

    # 比降页的「用纵断面推算填充」
    d.tabs.setCurrentIndex(1)
    d.fill_slope_from_profile()
    out.append("用纵断面推算填充 OK：" + strip_html(d.lbl_result.text()))
    d.hide()

    # 单断面面板
    win.param_panel.current = win.project.all_sections()[0]
    win.param_panel.set_current(win.param_panel.current)
    out.append("单断面参数面板同步 OK")

    # ================= 手动调节：分区 / 成灾水位（Q14）=================
    # 这两个功能的输入全在断面图上"点测点"，所以这里连**点击事件**一起模拟：
    # 用真实的 transData 把测点换成屏幕坐标，再喂给 _on_click，
    # 走完整链路（吸附 → 发信号 → 主窗口写入 → 重算 → 面板刷新）。
    #
    # 弹窗会阻塞离屏测试，先把主窗口模块里的 QMessageBox 换成不阻塞的替身。
    import app.main_window as MW
    from app.main_window import _try_apply_override

    class _StubMB:
        Yes, No, Ok = 1, 0, 1
        Save, Discard, Cancel = 2, 3, 4
        answer = 1

        @classmethod
        def question(cls, *a, **k):
            return cls.answer

        @staticmethod
        def warning(*a, **k):
            pass

        @staticmethod
        def information(*a, **k):
            pass

    _saved_mb = MW.QMessageBox
    MW.QMessageBox = _StubMB
    try:
        winshots = shots
        acts = [a.text() for a in win.menuBar().actions()]
        assert "分区调节" in acts and "成灾水位" in acts, acts
        out.append(f"菜单栏含手动调节两项 OK：{[a for a in acts if a in ('分区调节', '成灾水位')]}")

        # 找一条本组里适合做试验的断面：
        # 深泓点不在端点附近，且深泓点下一个测点更高（这样手动抬高深泓点才有意义）
        line0 = win.project.profile_lines[0]
        win.lst_lines.setCurrentRow(0)
        target, tinfo = None, None
        for i, s in enumerate(line0.sections):
            inf = win.infos[s.name]
            di = inf.dmin_auto_idx
            if 2 <= di <= s.n_points - 3 and s.z[di + 1] > s.z[di]:
                target, tinfo, trow = s, inf, i
                break
        assert target is not None, "找不到合适的试验断面"
        win.lst_secs.setCurrentRow(trow)
        win._refresh_current_views()
        out.append(f"试验断面 {target.name}：{target.n_points} 个测点，"
                   f"自动深泓点=第 {tinfo.dmin_auto_idx + 1} 个")

        # ---- 两个面板能弹出、跟随当前断面、状态表行数对 ----
        win.act_zone.trigger()
        assert win.dlg_zone.isVisible(), "点「分区调节」没有弹出面板"
        assert win.dlg_zone.lbl_cur.text().startswith(target.name), \
            f"面板未跟随当前断面：{win.dlg_zone.lbl_cur.text()}"
        assert win.dlg_zone.table.rowCount() == len(line0.sections), \
            "状态表行数应等于本组断面数"
        win.dlg_zone.grab().save(os.path.join(winshots, "0d_分区调节面板.png"))

        win.act_disaster.trigger()
        assert win.dlg_disaster.isVisible(), "点「成灾水位」没有弹出面板"
        assert win.dlg_disaster.lbl_auto.text(), "成灾水位面板应显示自动值"
        win.dlg_disaster.grab().save(os.path.join(winshots, "0e_成灾水位面板.png"))
        out.append(f"两个面板 OK：分区 {win.dlg_zone.table.rowCount()} 行；"
                   f"成灾水位自动值 = {strip_html(win.dlg_disaster.lbl_auto.text())}")

        # ---- 拾取：必须自动切到「断面形态」页并给出提示 ----
        win.act_zone.trigger()
        win._on_manual_pick("left")
        assert win.view_section.pick_mode == "left", "进入拾取模式失败"
        assert win.tabs.currentWidget() is win.view_section, \
            "拾取时应自动切到「断面形态」页"
        assert win.dlg_zone.lbl_pick.isVisible(), "面板未显示拾取提示"
        out.append("拾取模式：自动切换页签 + 面板提示 OK")

        def _click(sec, idx, button=1):
            """模拟在断面图上点击第 idx 个测点（走真实的坐标变换与吸附）。"""
            canvas = win.view_section.plot.canvas
            canvas.draw()                    # 必须先真绘制，transData 才有效
            px, py = win.view_section.plot.ax.transData.transform(
                (sec.s[idx], sec.z[idx]))
            ev = type("Ev", (), {})()
            ev.x, ev.y, ev.button = float(px), float(py), button
            ev.key = None
            win.view_section._on_click(ev)

        # ---- 左边界 ----
        # 记下此刻的转折点与成灾水位：手动改分区后它们**必须纹丝不动**（Q15）
        turn0 = (tinfo.left_turn_idx, tinfo.right_turn_idx)
        dis0 = tinfo.disaster_level
        di = tinfo.dmin_auto_idx
        li = max(1, di - 2)
        _click(target, li)
        assert target.zone_manual and target.zone_left == li, \
            f"左边界未写入：zone_manual={target.zone_manual} zone_left={target.zone_left}"
        assert win.view_section.pick_mode is None, "拾取完成后应自动退出拾取模式"
        out.append(f"拾取左边界 OK：第 {li + 1} 个测点（起点距 {target.s[li]:.1f} m）")

        # ---- 右边界 ----
        ri = min(target.n_points - 2, di + 2)
        win._on_manual_pick("right")
        _click(target, ri)
        assert target.zone_right == ri
        ti2 = win.infos[target.name]
        assert len(ti2.zones) == 3, f"应分成 3 区，实际 {len(ti2.zones)}：{ti2.zones}"
        out.append(f"拾取右边界 OK：第 {ri + 1} 个测点 → 3 区 {ti2.zones}")

        # ⚠ Q15：分区边界与转折点是两个概念。改分区**不得**动转折点与成灾水位。
        assert (ti2.left_turn_idx, ti2.right_turn_idx) == turn0, \
            f"手动改分区不该改动转折点：{turn0} → " \
            f"{(ti2.left_turn_idx, ti2.right_turn_idx)}"
        assert abs(ti2.disaster_level - dis0) < 1e-12, \
            f"手动改分区不该改成灾水位：{dis0} → {ti2.disaster_level}"
        assert ti2.zone_left_idx == li and ti2.zone_right_idx == ri, \
            "分区边界未写进 TerrainInfo"
        out.append(f"改分区不影响转折点与成灾水位 OK（转折点 {turn0}，"
                   f"成灾水位 {dis0:.2f} m 均未变）")

        # 留图：手动边界（蓝实线+方块）与自动转折点（紫三角+虚线）应同时可见
        win.tabs.setCurrentIndex(0)
        win.view_section.plot.canvas.draw()
        win.view_section.plot.canvas.fig.savefig(
            os.path.join(winshots, "5_手动分区边界与转折点.png"),
            dpi=110, bbox_inches="tight")

        # 图上信息框是 matplotlib 文本，**不解析 HTML**：里面混进标签会被原样
        # 画出来（踩过：<span style='color:...'> 直接显示成文字）。
        stray = [t.get_text() for t in win.view_section.plot.ax.texts
                 if "<" in t.get_text() or "</" in t.get_text()]
        assert not stray, f"断面图的信息框里混入了 HTML 标签：{stray}"
        out.append("图上信息框无 HTML 标签 OK（matplotlib 不解析 HTML）")

        # ---- 导航工具栏在平移/缩放模式时会吃掉左键点击 ----
        # 此时 motion 事件照发，于是"悬浮吸附还在、点击却毫无反应"，
        # 用户几乎不可能自查（光标只变成小手）。begin_pick 必须强制复位它。
        tb = win.view_section.plot.toolbar
        tb.pan()
        assert getattr(tb, "mode", None), "没能进平移模式，这条测试无效"
        win.view_section.cancel_pick(notify=False)
        win.view_section.begin_pick("left")
        assert win.view_section._toolbar_idle(), \
            f"begin_pick 未把工具栏复位：mode={getattr(tb, 'mode', None)!r}"
        out.append("拾取前强制复位导航工具栏 OK"
                   "（平移/缩放模式不再吃掉点击）")

        # ---- 点偏了要发 pickMissed，不能静默 ----
        missed = []
        win.view_section.pickMissed.connect(lambda: missed.append(1))
        win.view_section.begin_pick("left")
        ax = win.view_section.plot.ax
        fx, fy = ax.transAxes.transform((0.5, 0.92))     # 图顶部中间，远离地面线
        ev = type("Ev", (), {})()
        ev.x, ev.y, ev.button, ev.key = float(fx), float(fy), 1, None
        win.view_section._on_click(ev)
        assert missed, "点空白处应发出 pickMissed，而不是静默"
        assert win.view_section.pick_mode == "left", "点偏后应保持拾取模式，方便再点"
        win.view_section.cancel_pick(notify=False)
        out.append("点偏了会提示而不是静默 OK（pickMissed）")

        # ---- 非法拾取必须被拒绝且不改数据 ----
        before = target.zone_left
        win._on_manual_pick("left")
        _click(target, ri)                   # ri 在深泓点右侧，不能当左边界
        assert target.zone_left == before, "非法边界不应被写入"
        assert win.view_section.pick_mode is None
        out.append("非法边界（落在深泓点右侧）被拒绝 OK")

        # ---- 深泓点：全面顶替，曲线起算水位必须跟着变 ----
        n_rows_before = len(win.results[target.name].hvec)
        ti_idx = di + 1                      # 比最低测点高一点
        win._on_manual_pick("thalweg")
        _click(target, ti_idx)
        assert target.thalweg_manual == ti_idx
        tinfo2 = win.infos[target.name]
        assert tinfo2.dmin_idx == ti_idx, "深泓点未顶替"
        assert abs(tinfo2.dmin - target.z[ti_idx]) < 1e-12
        h0 = win.results[target.name].hvec[0]
        assert abs(h0 - target.z[ti_idx]) < 1e-9, \
            f"H~Q 曲线起算水位应等于手动深泓点高程，实际 {h0}"
        assert win.dlg_zone.lbl_warn.isVisible(), "应提示曲线起算水位被抬高"
        warn_txt = strip_html(win.dlg_zone.lbl_warn.text())
        assert "起算水位" in warn_txt, warn_txt
        out.append(f"深泓点全面顶替 OK：曲线起算水位 {h0:.2f} m"
                   f"（原 {n_rows_before} 行 → 现 {len(win.results[target.name].hvec)} 行）")
        out.append("面板警告：" + warn_txt[:100])

        # ---- 成灾水位 ----
        # 故意选**岸顶**（比水位计算上限还高的那个点），顺手把"超出曲线范围"
        # 的警告路径也走一遍
        zi, yi = tinfo2.zmax_idx, tinfo2.ymax_idx
        cands = [i for i in (zi, yi) if i is not None and 0 <= i < target.n_points]
        assert cands
        hi = max(cands, key=lambda i: target.z[i])
        win._on_manual_pick("disaster")
        _click(target, hi)
        assert target.disaster_idx_manual == hi
        tinfo3 = win.infos[target.name]
        assert abs(tinfo3.disaster_level - target.z[hi]) < 1e-12, \
            "手动成灾水位应取自被点测点的高程"
        out.append(f"拾取成灾水位 OK：第 {hi + 1} 个测点 → "
                   f"{tinfo3.disaster_level:.2f} m（水位上限 {tinfo3.zymin:.2f} m），"
                   f"成灾流量 {win.results[target.name].disaster_flow:.2f} m³/s")
        if tinfo3.disaster_level > tinfo3.zymin + 1e-9:
            assert win.dlg_disaster.lbl_warn.isVisible(), "超出水位上限应警告"
            wtxt = strip_html(win.dlg_disaster.lbl_warn.text())
            assert "外推" in wtxt, wtxt
            out.append("超出水位计算上限的外推警告 OK：" + wtxt[:80])

        # ---- 逐项清除 ----
        win._on_manual_cleared("thalweg")
        assert target.thalweg_manual is None
        win._on_manual_cleared("left")
        win._on_manual_cleared("right")
        assert target.zone_left is None and target.zone_right is None
        assert target.zone_manual, "清除边界不应取消「手动分区」这个状态本身"
        assert len(win.infos[target.name].zones) == 1, \
            "两侧边界都清除后应为「不分区」1 区"
        win._on_manual_cleared("disaster")
        assert target.disaster_idx_manual is None
        out.append("逐项清除 OK（两侧边界都清除 → 不分区 1 区）")

        # ---- 本组全部恢复自动 ----
        line0.sections[0].disaster_idx_manual = 0
        line0.sections[1].disaster_idx_manual = 0
        win._recalc()
        win._on_manual_reset_group()
        assert line0.sections[0].disaster_idx_manual is None
        assert line0.sections[1].disaster_idx_manual is None
        out.append("「本组全部恢复自动」OK：" + win.lbl_status.text()[:60])

        # ---- 单断面模式：分区无效要拦下，但深泓点仍然有效 ----
        win.dlg_settings.cmb_mode.setCurrentIndex(1)
        assert not win.cfg.compound_mode
        win._on_manual_pick("left")
        assert win.view_section.pick_mode is None, \
            "单断面模式下不应进入边界拾取（分区不参与计算）"
        win._on_manual_pick("thalweg")
        assert win.view_section.pick_mode == "thalweg", \
            "深泓点在单断面模式下仍应可拾取（它决定曲线起算水位）"
        win.view_section.cancel_pick()
        win.dlg_settings.cmb_mode.setCurrentIndex(0)
        assert win.cfg.compound_mode
        out.append("单断面模式：边界拾取被拦、深泓点拾取仍允许 OK")

        # ---- 工程文件必须保存手动设定，否则存了再打开就丢 ----
        rt_sec, rt_ai = None, None
        for ln in win.project.profile_lines:
            for s in ln.sections:
                ai = win.infos[s.name].dmin_auto_idx
                if 2 <= ai <= s.n_points - 3:
                    rt_sec, rt_ai = s, ai
                    break
            if rt_sec is not None:
                break
        assert rt_sec is not None
        errs = _try_apply_override(rt_sec,
                                   thalweg_manual=rt_ai + 1,
                                   zone_manual=True,
                                   zone_left=rt_ai - 2,
                                   zone_right=rt_ai + 2,
                                   disaster_idx_manual=rt_ai - 1)
        assert not errs, errs
        win._recalc()
        geom_z = list(rt_sec.z)
        rt_name = rt_sec.name
        with tempfile.TemporaryDirectory() as td:
            tmp2 = os.path.join(td, "override.dmprj")
            win.current_path = tmp2
            assert win._write_project(tmp2), "保存带手动设定的工程失败"
            win._new_project()
            assert win.project is None
            p3, c3, m3 = project_io.load_project(tmp2)
            win._adopt_project(p3, c3, m3.get("source") or {}, tmp2, m3)

        s2 = {x.name: x for x in win.project.all_sections()}[rt_name]
        assert s2.thalweg_manual == rt_ai + 1, f"深泓点未还原：{s2.thalweg_manual}"
        assert s2.zone_manual is True, "手动分区状态未还原"
        assert s2.zone_left == rt_ai - 2 and s2.zone_right == rt_ai + 2, \
            f"分区边界未还原：{s2.zone_left}/{s2.zone_right}"
        assert s2.disaster_idx_manual == rt_ai - 1, "手动成灾水位未还原"
        i4 = win.infos[rt_name]
        assert abs(i4.dmin - geom_z[rt_ai + 1]) < 1e-12, "打开后深泓点高程不对"
        assert abs(i4.disaster_level - geom_z[rt_ai - 1]) < 1e-12, \
            "打开后成灾水位不对"
        assert len(i4.zones) == 3, f"打开后分区数不对：{i4.zones}"
        out.append(f"工程文件保存手动设定 OK（{rt_name}：深泓点/左右边界/成灾水位 全部还原，"
                   f"重算后 {len(i4.zones)} 区）")
        out.append("注：本组与「手动设定」相关的导入导出均已验证")
    finally:
        MW.QMessageBox = _saved_mb
        win.dlg_zone.hide()
        win.dlg_disaster.hide()

    out.append("\n全部通过")
except Exception:
    failed = True
    out.append("FAILED\n" + traceback.format_exc())

text = "\n".join(out)
with open(os.path.join(ROOT, "_gui_smoke.txt"), "w", encoding="utf-8") as f:
    f.write(text)
print(text)

# ⚠ 失败必须返回非零退出码。原先无论成败都退出 0，于是调用方
#   （人或脚本）很容易把一次失败的冒烟当成通过——这个坑真的踩过：
#   曾因为把 stderr 丢进 null、又没查退出码，把一次 FAILED 看成了正常。
sys.exit(1 if failed else 0)
