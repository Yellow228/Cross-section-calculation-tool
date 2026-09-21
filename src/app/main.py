"""界面程序入口。

    .venv\\Scripts\\python.exe src\\app\\main.py

自检模式（无需显示器，用于验证打包后的 exe 是否完好）：
    dist\\断面计算工具\\断面计算工具.exe --selftest [数据目录]
返回码 0 表示全部通过，非 0 表示失败，原因写入 _selftest.txt。
"""

from __future__ import annotations

import os
import sys
import traceback

# 允许以脚本方式直接运行（把 src 加入搜索路径）
_HERE = os.path.dirname(os.path.abspath(__file__))
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(_HERE))


def asset_path(name: str) -> str:
    """定位打包进程序的静态资源。打包后 datas 落在 sys._MEIPASS 下。"""
    if getattr(sys, "frozen", False):
        base = os.path.join(getattr(sys, "_MEIPASS",
                                    os.path.dirname(sys.executable)), "assets")
    else:
        base = os.path.join(_HERE, "assets")
    return os.path.join(base, name)


def app_icon():
    """程序图标；找不到就返回 None，不影响启动。"""
    from PySide6.QtGui import QIcon
    p = asset_path("icon.ico")
    return QIcon(p) if os.path.exists(p) else None


# 界面上全是中文，必须确保选到带 CJK 字形的字体，
# 否则 Qt 会回退到不含中文的字体，界面上的字会变成方块
_CJK_FONTS = ["Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑",
              "SimHei", "黑体", "SimSun", "宋体", "Noto Sans CJK SC"]


def setup_font(app) -> str | None:
    """给整个应用指定一个含中文字形的字体，返回实际选中的字体名。"""
    from PySide6.QtGui import QFont, QFontDatabase
    try:
        families = set(QFontDatabase.families())
    except Exception:
        return None
    for name in _CJK_FONTS:
        if name in families:
            app.setFont(QFont(name, 9))
            return name
    return None


def _selftest(argv: list[str]) -> int:
    """离屏构造界面、载入数据、渲染三张图，验证整包可用。"""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)      # 打包后：exe 所在目录
    else:
        base = os.path.dirname(os.path.dirname(_HERE))

    data_dir = argv[0] if argv else os.path.join(base, "data")
    log_path = os.path.join(os.getcwd(), "_selftest.txt")
    lines: list[str] = []

    try:
        from PySide6.QtWidgets import QApplication
        from matplotlib.figure import Figure

        from app.main_window import MainWindow

        app = QApplication([])
        lines.append("QApplication OK")
        # 版本与构建信息：自检日志里留一份，便于事后确认"跑的是哪一版"。
        # 打包后的 exe 里没有 git，这个哈希是打包时烘焙进去的。
        from core import version as V
        lines.append(f"版本：{V.VERSION}　构建：{V.git_hash()}　{V.runtime()}")
        lines.append(f"frozen={getattr(sys, 'frozen', False)}  base={base}")
        plat = app.platformName()
        lines.append(f"平台插件：{plat}")
        picked = setup_font(app)
        if plat == "offscreen":
            # offscreen 插件不加载字体库（families() 为 0），这里查不到任何字体是正常的，
            # 不能据此判断界面会显示方块——要用 tools/font_check.py 走真实平台验。
            lines.append("界面字体：offscreen 平台不加载字体库，本项无法判定（属正常）")
        else:
            lines.append(f"界面字体：{picked or '未找到带中文的字体（界面中文可能显示为方块）'}")

        ico_p = asset_path("icon.ico")
        ok = os.path.exists(ico_p)
        lines.append(f"图标资源{'已找到' if ok else '未找到'}: {ico_p}")
        if ok:
            ic = app_icon()
            lines.append(f"QIcon 加载{'成功' if (ic is not None and not ic.isNull()) else '失败'}")
            if ic is not None:
                app.setWindowIcon(ic)

        win = MainWindow()
        lines.append("MainWindow 构造 OK")

        if os.path.isdir(data_dir):
            win.data_dir = data_dir
            win._load()
            lines.append("载入数据 OK：" + win.lbl_status.text())
        else:
            lines.append(f"数据目录不存在（跳过载入）：{data_dir}")

        # 逐个切换，确保每个视图都能画出来
        tabs = ["断面形态", "水位–流量曲线", "沿河纵剖面"]
        for i in range(win.lst_lines.count()):
            win.lst_lines.setCurrentRow(i)
            for j in range(win.lst_secs.count()):
                win.lst_secs.setCurrentRow(j)
        lines.append(f"遍历 {win.lst_lines.count()} 条纵断面线 OK")

        for i, name in enumerate(tabs):
            win.tabs.setCurrentIndex(i)
            fig = (win.view_section if i == 0 else
                   win.view_rating if i == 1 else win.view_profile).plot.canvas.fig
            assert isinstance(fig, Figure)
            lines.append(f"视图『{name}』渲染 OK")

        # 导出一次，验证写文件链路
        if win.project is not None:
            from core.exporter import export_all
            out_dir = os.path.join(base, "output", "_selftest")
            written = export_all(win.project, win.results, win.infos, win.cfg, out_dir)
            lines.append(f"导出 OK：{len(written)} 个文件 -> {out_dir}")

            # 编码核验：CSV 必须带 UTF-8 BOM，否则中文 Windows 的 Excel
            # 会按 GBK 误码，中文表头与断面名全变乱码
            with open(written[0], "rb") as f:
                head = f.read(3)
            has_bom = head == b"\xef\xbb\xbf"
            lines.append(f"CSV 编码核验：{win.cfg.csv_encoding}，"
                         f"BOM={'有' if has_bom else '无'}"
                         f"{'（OK）' if has_bom else '（✗ Excel 打开会乱码）'}")
            assert has_bom, "导出的 CSV 缺少 UTF-8 BOM，Excel 打开会乱码"

        # 顶部菜单栏的两个面板
        lines.append(f"菜单项：{[a.text() for a in win.menuBar().actions()]}")
        win.act_settings.trigger()
        lines.append(f"计算设置面板弹出 OK（visible={win.dlg_settings.isVisible()}）")
        win.act_batch.trigger()
        lines.append(f"批量填写面板弹出 OK（visible={win.dlg_batch.isVisible()}）")

        # 「关于」：版本号 + 构建哈希。打包后的 exe 靠烘焙信息才能显示哈希，
        # 这里断言它真的拿到了（而不是"未知"）。
        win.act_about.trigger()
        assert win.dlg_about.isVisible(), "点「关于」没有弹出面板"
        about = win.dlg_about._text
        assert V.VERSION in about, "关于信息里没有版本号"
        # 版本历史会一直变长，整段拼进日志就没有可读性了——只打前 5 行 + 总行数。
        head = about.splitlines()
        lines.append("关于面板 OK：" + "　".join(head[:5])
                     + f"　…（共 {len(head)} 行，含版本历史）")
        assert "版本历史" in about, "关于里看不到版本历史"
        win.dlg_about.hide()

        # 批量填写改版：三页签表格 + 只显示当前组 + 从表格写回参数
        d = win.dlg_batch
        tabs = [d.tabs.tabText(i) for i in range(d.tabs.count())]
        lines.append(f"批量填写页签：{tabs}")
        assert tabs == ["流量", "比降", "糙率"], "页签应为 流量/比降/糙率"
        # 注意：前面遍历过所有分组，当前组不一定是第 0 组，要按实际选中行取
        cur_i = max(win.lst_lines.currentRow(), 0)
        cur_line = win.project.profile_lines[cur_i]
        n_sec0 = len(cur_line.sections)
        assert d.t_q.rowCount() == n_sec0, "表格行数应等于当前组断面数"
        lines.append(f"当前组 {d.cmb_group.currentText()}，表格 {d.t_q.rowCount()} 行；"
                     f"比降页首行 = {d.t_s.item(0, 2).text()}")
        # 改一格并应用，参数与重算结果都要跟着变
        d.t_q.item(0, 2).setText("123.45")
        d.btn_apply.click()
        lines.append("批量填写应用 OK：" + win.lbl_status.text()[:70])
        assert abs(cur_line.sections[0].params.design_q - 123.45) < 1e-9, \
            "表格的值未写回参数"
        # 糙率页列数恒定 5 列；分区开关只控制后两列的可编辑性（灰显），
        # 并把状态同步到主界面参数面板
        from PySide6.QtCore import Qt as QtC
        assert d.t_n.columnCount() == 5, "糙率页应恒为 5 列"
        d.chk_zone.setChecked(False)
        assert d.t_n.columnCount() == 5, "取消分区后列数不应改变"
        assert not (d.t_n.item(0, 3).flags() & QtC.ItemIsEditable), \
            "不勾选分区时左滩列应不可编辑"
        d.chk_zone.setChecked(True)
        assert d.t_n.item(0, 3).flags() & QtC.ItemIsEditable, "勾选后左滩列应可编辑"
        assert win.param_panel.o_use_zone.isChecked(), "分区开关未同步到参数面板"
        lines.append("糙率页列数恒定 5 列 + 分区开关灰显 + 同步主界面 OK")

        # 按纵断面推算比降（新增功能）：算 → 展示 → 应用，整条链路都要过
        win.act_slope.trigger()
        lines.append(f"比降推算面板弹出 OK（visible={win.dlg_slope.isVisible()}）")
        props = win.dlg_slope.proposals
        lines.append(f"比降建议：{len(props)} 个断面，"
                     f"口径={win.dlg_slope.mode()}")
        assert win.dlg_slope.mode() == "jc", "默认口径应为约翰斯通-克罗斯法"
        per_line: dict = {}
        for p in props:
            per_line.setdefault(p.line, set()).add(round(p.proposed, 12))
        assert all(len(v) == 1 for v in per_line.values()), \
            "同一条纵断面线内所有断面必须共用一个比降"
        lines.append("各线建议比降：" + "　".join(
            f"{k}={next(iter(v)):.5f}" for k, v in per_line.items()))
        bad = [p.section for p in props if p.bad]
        if bad:
            lines.append(f"⚠ 有 {len(bad)} 个无效建议值（倒坡/数据不足），跳过应用测试")
        else:
            win.dlg_slope.btn_all.click()
            lines.append("比降应用 OK：" + win.lbl_status.text()[:70])

        # ---- 手动调节（Q14）：深泓点 / 分区边界 / 成灾水位 ----
        # 弹窗会阻塞无人值守的自检，先把主窗口模块里的 QMessageBox 换成替身。
        # 这里直接调 _on_section_picked（等价于"图上点了一下第 idx 个测点"），
        # 真实的点击/吸附链路由 tools/gui_smoke.py 覆盖。
        import app.main_window as MW

        class _StubMB:
            Yes, No, Ok = 1, 0, 1
            Save, Discard, Cancel = 2, 3, 4

            @staticmethod
            def warning(*a, **k):
                pass

            @staticmethod
            def information(*a, **k):
                pass

            @staticmethod
            def question(*a, **k):
                return 1

        _saved_mb = MW.QMessageBox
        MW.QMessageBox = _StubMB
        try:
            # 注意：不要写成"最后两项 == [分区调节, 成灾水位]"——
            # 末尾加了「帮助」菜单后这条就失效了，按名字查更稳。
            acts = [a.text() for a in win.menuBar().actions()]
            assert "分区调节" in acts and "成灾水位" in acts, "菜单栏缺少手动调节两项"
            assert "帮助" in acts, "菜单栏缺少帮助项"
            assert "编辑" in acts, "菜单栏缺少编辑项"
            win.act_zone.trigger()
            assert win.dlg_zone.isVisible(), "点「分区调节」没有弹出面板"
            win.act_disaster.trigger()
            assert win.dlg_disaster.isVisible(), "点「成灾水位」没有弹出面板"

            target, trow = None, 0
            for ln in win.project.profile_lines:
                for i, s in enumerate(ln.sections):
                    ai = win.infos[s.name].dmin_auto_idx
                    if 2 <= ai <= s.n_points - 3:
                        target, trow = s, i
                        break
                if target is not None:
                    break
            assert target is not None, "找不到适合做手动覆盖自检的断面"
            # 切到该断面所在组与位置，让面板跟随
            for gi, ln in enumerate(win.project.profile_lines):
                if target.name in [x.name for x in ln.sections]:
                    win.lst_lines.setCurrentRow(gi)
                    win.lst_secs.setCurrentRow(
                        [x.name for x in ln.sections].index(target.name))
                    break
            ai = win.infos[target.name].dmin_auto_idx

            assert win.view_section.begin_pick("thalweg"), "断面图未接受拾取模式"
            assert win.view_section.pick_mode == "thalweg"
            win.view_section.cancel_pick()
            assert win.view_section.pick_mode is None, "取消拾取失败"

            # 记下自动的转折点与成灾水位：手动改分区后必须纹丝不动（Q15）
            turn0 = (win.infos[target.name].left_turn_idx,
                     win.infos[target.name].right_turn_idx)
            dis0 = win.infos[target.name].disaster_level

            win._on_section_picked("left", ai - 2)
            win._on_section_picked("right", ai + 2)
            assert win.infos[target.name].zone_left_idx == ai - 2, \
                "分区边界未写进 TerrainInfo"
            assert (win.infos[target.name].left_turn_idx,
                    win.infos[target.name].right_turn_idx) == turn0, \
                "手动改分区不该改动转折点"
            assert abs(win.infos[target.name].disaster_level - dis0) < 1e-12, \
                "手动改分区不该改成灾水位"

            win._on_section_picked("thalweg", ai + 1)
            win._on_section_picked("disaster", ai - 1)
            assert target.zone_manual and target.zone_left == ai - 2
            assert target.zone_right == ai + 2
            assert target.thalweg_manual == ai + 1
            assert target.disaster_idx_manual == ai - 1
            assert not target.override_errors(), target.override_errors()

            info_t = win.infos[target.name]
            assert info_t.dmin_idx == ai + 1, "手动深泓点未生效"
            assert abs(info_t.dmin - target.z[ai + 1]) < 1e-12
            assert abs(win.results[target.name].hvec[0]
                       - target.z[ai + 1]) < 1e-9, "H~Q 曲线起算水位未跟随手动深泓点"
            assert len(info_t.zones) == 3, f"分区数不对：{info_t.zones}"
            assert abs(info_t.disaster_level - target.z[ai - 1]) < 1e-12, \
                "手动成灾水位未生效"
            assert win.dlg_zone.table.rowCount() == win.lst_secs.count(), \
                "手动调节面板的状态表未跟随当前组"
            lines.append(
                f"手动覆盖 OK：{target.name} 深泓点=第 {ai + 2} 个测点，"
                f"边界={ai - 1}/{ai + 3}，成灾水位={info_t.disaster_level:.2f} m，"
                f"{len(info_t.zones)} 区")
            lines.append("改分区未动转折点与成灾水位 OK（Q15：两者是两个概念）")

            # 非法覆盖必须被拒绝且不改数据
            keep = target.zone_left
            win._on_section_picked("left", ai + 2)          # 落在深泓点右侧
            assert target.zone_left == keep, "非法边界不应被写入"
            lines.append("非法手动值被拒绝 OK（边界落在深泓点同侧）")

            # 工程文件往返：保存 → 新建 → 打开，数据必须逐项一致。
            # 这是「下次打开工程文件还是一样的数据」唯一能自证的方式。
            if win.project is not None:
                from core import project_io

                tmp = os.path.join(base, "output", "_selftest", "_roundtrip.dmprj")
                os.makedirs(os.path.dirname(tmp), exist_ok=True)

                def _key(s):
                    return (s.name, len(s.z), s.z[0], s.z[-1], s.params.slope,
                            s.params.roughness, s.params.design_q,
                            s.thalweg_manual, s.zone_manual, s.zone_left,
                            s.zone_right, s.disaster_idx_manual)

                before = [_key(s) for s in win.project.all_sections()]
                n_line = len(win.project.profile_lines)
                n_manual = sum(1 for s in win.project.all_sections() if s.has_manual)

                win.current_path = tmp
                assert win._write_project(tmp), "保存工程文件失败"
                lines.append(f"工程保存 OK：{os.path.basename(tmp)}"
                             f"（{os.path.getsize(tmp) // 1024} KB，"
                             f"含 {n_manual} 个带手动设定的断面）")

                win._new_project()
                assert win.project is None, "新建工程应清空数据"
                lines.append("新建工程 OK（已清空）")

                proj2, cfg2, meta = project_io.load_project(tmp)
                win._adopt_project(proj2, cfg2, meta.get("source") or {}, tmp, meta)
                after = [_key(s) for s in win.project.all_sections()]
                assert after == before, "工程文件往返后数据不一致"
                assert len(win.project.profile_lines) == n_line, "纵断面线数量不一致"
                lines.append(f"工程打开 OK：{len(proj2.profile_lines)} 条线 / "
                             f"{len(proj2.all_sections())} 个断面，数据逐项一致"
                             f"（含手动设定）")

            # ---- 横断面编辑：改测点几何 ----
            # 只验证联动是否成立（表格<->数据<->平面坐标），
            # 真实的鼠标拖动吸附链路由 tools/gui_smoke.py 覆盖。
            from PySide6.QtCore import Qt as QtE
            from PySide6.QtWidgets import QApplication as _App, QTableWidget as _TW
            from app.section_editor import COL_S, COL_Z

            win.act_edit_sec.trigger()
            dlg = win.dlg_edit
            assert dlg.isVisible(), "点「编辑当前横断面…」没有弹出窗口"
            sec = win._current_section()
            assert sec is not None, "编辑窗口打开时没有当前断面"
            n0, z0 = sec.n_points, list(sec.z)
            assert dlg.table.rowCount() == n0, "表格行数应等于测点数"

            # 0) 默认必须真只读。只判 item 的 ItemIsEditable 不够：
            #    默认 editTriggers 里含 AnyKeyPressed，选中后敲任意键都会改写数据。
            assert not dlg.chk_type.isChecked(), "「允许键入 / 粘贴」应默认不勾"
            assert dlg.table.editTriggers() == _TW.NoEditTriggers, \
                "默认应完全不允许进入编辑态"
            for col in (COL_S, COL_Z):
                assert not (dlg.table.item(1, col).flags() & QtE.ItemIsEditable), \
                    f"默认第 {col} 列应不可编辑"
            lines.append(f"编辑窗口 OK：{sec.name}，{n0} 个测点；表格默认只读")

            # 1) 解锁后双击/Enter/F2 可改——这里用程序化 setText 走同一条路径
            dlg.chk_type.setChecked(True)
            assert dlg.table.editTriggers() & _TW.DoubleClicked, \
                "解锁后应允许双击进入编辑"
            assert not (dlg.table.editTriggers() & _TW.AnyKeyPressed), \
                "解锁后也不能带 AnyKeyPressed——那是误改数据的老毛病"
            assert dlg.table.item(1, COL_S).flags() & QtE.ItemIsEditable, \
                "解锁后起点距列应可编辑（不必再取消「只改高程」）"
            dlg.table.item(1, COL_Z).setText("12.34")
            assert abs(sec.z[1] - 12.34) < 1e-9, "表格改动未写回断面"
            lines.append("解锁后键入 OK：两列均可改，未误开 AnyKeyPressed")

            # 2) 改起点距必须连带重算 x/y
            mid = round((sec.s[0] + sec.s[2]) * 0.5, 2)
            mid = min(max(mid, sec.s[0] + 0.05), sec.s[2] - 0.05)
            x1_before = sec.x[1]
            dlg.table.item(1, COL_S).setText(f"{mid:.2f}")
            assert abs(sec.s[1] - mid) < 1e-9, "起点距未写回断面"
            assert abs(sec.x[1] - x1_before) > 1e-6, \
                "改了起点距却没有同步平面坐标 x/y（导出坐标会与断面形态对不上）"
            lines.append(f"起点距 -> 平面坐标同步 OK（第 2 点 s={mid:.2f} m）")

            # 3) Excel 粘贴：整批写入
            #    数值一律先过一遍 `%.2f`——表格里显示两位小数，粘贴进去的也是两位，
            #    拿完整浮点精度去比对会假失败。
            z_before = list(sec.z)
            want_z = [round(z_before[i] + 5.0, 2) for i in range(3)]
            _App.clipboard().setText("\n".join(
                f"{sec.s[i]:.2f}\t{want_z[i]:.2f}" for i in range(3)))
            dlg.table.setCurrentCell(0, COL_S)
            n_written = dlg._paste_from_clipboard()
            assert n_written == 6, f"粘贴了 {n_written} 个值，应为 6"
            for i in range(3):
                assert abs(sec.z[i] - want_z[i]) < 1e-9, \
                    f"第 {i + 1} 点高程未按粘贴值更新：{sec.z[i]} != {want_z[i]}"
                assert abs(sec.s[i] - round(sec.s[i], 2)) < 1e-9, \
                    f"第 {i + 1} 点起点距未按粘贴值更新"
            assert dlg.table.item(2, COL_Z).text() == f"{sec.z[2]:.2f}", \
                "粘贴后表格未回填"
            lines.append("Excel 粘贴 OK：3 行 × 2 列一次性写入并重算")

            # 4) 粘贴整批校验：夹一个非数字进去，必须整批否决、数据零改动
            z_snap = list(sec.z)
            _App.clipboard().setText("\n".join(
                f"{sec.s[i]:.2f}\t{'高程' if i == 1 else f'{99.0 + i:.2f}'}"
                for i in range(3)))
            dlg.table.setCurrentCell(0, COL_S)
            assert dlg._paste_from_clipboard() == 0, "含无效值时应整批取消"
            assert sec.z == z_snap, "粘贴被取消后断面不该有任何改动"
            lines.append("粘贴整批校验 OK：含非法值时一个点都没动")

            # 5) 拖动的锁定开关仍在（保留原有行为）
            dlg.chk_type.setChecked(False)
            dlg.chk_lock.setChecked(True)
            dlg.btn_reset.click()
            dlg.chk_lock.setChecked(True)

            # 3) 插入 / 删除 / 撤销 / 还原
            dlg.btn_insert.click()
            assert sec.n_points == n0 + 1, "插入测点失败"
            dlg.btn_delete.click()
            assert sec.n_points == n0, "删除测点失败"
            dlg.btn_undo.click()
            assert sec.n_points == n0 + 1, "撤销未恢复被删除的测点"
            dlg.btn_reset.click()
            assert sec.n_points == n0, "还原未回到打开时的点数"
            assert sec.z == z0, "还原未恢复打开时的高程"
            lines.append("插入 / 删除 / 撤销 / 还原 OK")
        finally:
            MW.QMessageBox = _saved_mb
            win.dlg_zone.hide()
            win.dlg_disaster.hide()

        win.dlg_settings.hide()
        win.dlg_batch.hide()
        win.dlg_slope.hide()
        win.dlg_edit.hide()

        # 设置切换
        win.dlg_settings.cmb_mode.setCurrentIndex(1)
        lines.append("切换单断面模式 OK")
        win.dlg_settings.cmb_mode.setCurrentIndex(0)
        win.dlg_settings.cmb_origin.setCurrentIndex(1)
        lines.append("切换桩号原点 OK")
        win.dlg_settings.spin_steep.setValue(0.25)
        lines.append(f"阈值调节 OK（陡坡={win.cfg.steep_slope}）")
        win.dlg_settings.btn_reset.click()
        lines.append(f"恢复默认 OK（陡坡={win.cfg.steep_slope} 缓坡={win.cfg.turn_slope}）")

        lines.append("")
        lines.append("SELFTEST PASSED")
        code = 0
    except Exception:
        lines.append("SELFTEST FAILED")
        lines.append(traceback.format_exc())
        code = 1

    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass
    return code


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] in ("--selftest", "-t"):
        return _selftest(argv[1:])

    from PySide6.QtWidgets import QApplication

    from app.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("断面水位-流量关系计算工具")
    app.setStyle("Fusion")
    setup_font(app)
    ico = app_icon()
    if ico is not None:
        app.setWindowIcon(ico)
    win = MainWindow()
    if ico is not None:
        win.setWindowIcon(ico)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
