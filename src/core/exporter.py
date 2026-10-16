"""结果导出。CSV 部分仅用标准库；xlsx 部分惰性导入 openpyxl。

输出格式沿用原 MATLAB（文件名、列名、小数位数），保证下游流程无需改动。

⚠ **导出坐标的列序**（改动前务必读 `out_xy()` 的说明）：
  内部恒为**测量坐标系** `x=北坐标（纵向）、y=东坐标（横向）`；
  导出按 `Config.export_coord_system` 决定是"原样"还是"交换"。
  所有写坐标的地方都必须经过 `out_xy()`，否则导出坐标会和输入文件对不上
  （表现为关于 y=x 镜像）。**该选项只影响导出的两列内容，不影响任何计算结果。**

⚠ 待回归验证点：
  断面起终点坐标及水位.csv 的"百年一遇水位"一列，MATLAB 用 fprintf('%s', 数值)，
  实际走 num2str 的 short 格式（4 位小数）。此处用 matlab_num2str 近似，
  需用真实数据比对确认。

⚠ 编码：CSV 一律按 `Config.csv_encoding` 写出，默认 `utf-8-sig`（UTF-8 带 BOM）。
  中文 Windows 的 Excel / WPS 打开 CSV 时按系统 ANSI(GBK) 解码，无 BOM 的 UTF-8
  会被误判成 GBK，中文全部变乱码（"断面" → "鏂潰"）。带 BOM 即可正常识别。
  此处刻意**不沿用** MATLAB 的 GBK 行为——旧版 Excel 只认 ANSI，
  现代 Excel/WPS/其他工具都能正确读带 BOM 的 UTF-8，兼容面更宽。
"""

from __future__ import annotations

import csv
import os
from typing import Optional

from .config import Config
from .model import ProfileLine, Project, Section, SectionResult, TerrainInfo, HydroResult
from .interp import interpolate_xy, find_water_edge


def matlab_num2str(v: float) -> str:
    """近似 MATLAB num2str 的默认（short）格式：整数不带小数，其余 4 位小数。"""
    if v != v:
        return ""
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:.4f}"


#: 导出坐标系的取值 -> 界面文案。改这里就够了，界面直接读它。
COORD_SYSTEMS: dict[str, str] = {
    "survey": "测量坐标系（X=北坐标·纵向，Y=东坐标·横向）",
    "math": "数学坐标系（X=横轴·东向，Y=纵轴·北向）",
}


def out_xy(x: float, y: float, coord_system: str = "survey") -> tuple[float, float]:
    """内部坐标（x=北、y=东）-> 导出坐标。**所有写坐标的地方都必须经过它。**

    内部恒为**测量坐标系**（`x = 北坐标·纵向`、`y = 东坐标·横向`），
    导出按 `cfg.export_coord_system` 决定，见 `config.Config` 里该字段的说明：

      * `"survey"`（默认）—— 测量坐标系：X=北、Y=东 → **与内部一致，不换**
      * `"math"`          —— 数学坐标系：X=横轴(东)、Y=纵轴(北) → **交换**

    ⚠ 换与不换只影响写出去的两列内容，**不影响任何计算结果**。
      "内部什么就写什么"这个直觉在 `math` 档下是错的，别把分支删掉。
    """
    if coord_system == "math":
        return y, x
    return x, y


def endpoint_xy_str(sec: Section, coord_system: str = "survey") -> str:
    """断面起终点平面坐标串：'起点X,起点Y;终点X,终点Y'，三位小数。

    对应原 MATLAB `A.duanmianXY`，格式与列名沿用原程序；
    X/Y 的含义随导出坐标系（见 `out_xy`）。
    """
    x1, y1 = out_xy(sec.x[0], sec.y[0], coord_system)
    x2, y2 = out_xy(sec.x[-1], sec.y[-1], coord_system)
    return f"{x1:.3f},{y1:.3f};{x2:.3f},{y2:.3f}"


def _write_xy_rows(w, sec: Section, lp, ls, rp, rs,
                   coord_system: str = "survey") -> None:
    """写一个断面的左岸(Z)、右岸(Y)两条淹没交点记录。

    注意行首的 Z / Y 是原 MATLAB 的**岸别**记号（Z=左岸、Y=右岸），与坐标轴无关。
    """
    if lp is not None:
        ox, oy = out_xy(lp[0], lp[1], coord_system)
        w.writerow([f"{sec.name}Z", f"{ox:.6f}", f"{oy:.6f}", ls])
    if rp is not None:
        ox, oy = out_xy(rp[0], rp[1], coord_system)
        w.writerow([f"{sec.name}Y", f"{ox:.6f}", f"{oy:.6f}", rs])


def _disaster_row(sec: Section, res: SectionResult, info: TerrainInfo, cfg: Config):
    """成灾水位那一行；本断面没有合法成灾水位索引时返回 None。

    ⚠ 必须取**本断面**的 info。旧实现写成遍历全部 infos 取最后一个满足边界条件的，
      索引会串到别的断面，导致坐标算错——同一条纵断面线上各断面点数相近时，
      几乎每个断面都会错。单断面的测试恰好掩盖了这个问题。
    """
    if not (0 <= info.disaster_idx < sec.n_points):
        return None
    label = (f"{sec.name}成灾水位:{matlab_num2str(info.disaster_level)}"
             f"成灾流量:{matlab_num2str(res.disaster_flow)}")
    ox, oy = out_xy(sec.x[info.disaster_idx], sec.y[info.disaster_idx],
                    cfg.export_coord_system)
    return [label, f"{ox:.6f}", f"{oy:.6f}", cfg.ICON_DISASTER]


def export_inundation_csv(path: str, sections: list[Section],
                          results: list[SectionResult], infos: list[TerrainInfo],
                          cfg: Config) -> None:
    """淹没线坐标 CSV（原 MATLAB 口径）：名称,平面坐标X,平面坐标Y,图标样式

    内容为 Hs1（设计水位+加高）的左右岸交点，附可选的成灾水位行。
    """
    with open(path, "w", encoding=cfg.csv_encoding, newline="") as f:
        w = csv.writer(f)
        w.writerow(["名称", "平面坐标X", "平面坐标Y", "图标样式"])
        for sec, res, info in zip(sections, results, infos):
            _write_xy_rows(w, sec, res.left_point, res.left_status,
                           res.right_point, res.right_status,
                           cfg.export_coord_system)
            if cfg.output_disaster_level:
                row = _disaster_row(sec, res, info, cfg)
                if row is not None:
                    w.writerow(row)


def export_range_csv(path: str, sections: list[Section],
                     results: list[SectionResult], cfg: Config,
                     raised: bool) -> None:
    """淹没范围坐标 CSV（只含左右岸交点，不含成灾水位）。

    raised=True  -> 设计水位加高（Hs1 = Hs + 加高幅度）的淹没范围
    raised=False -> 设计水位（Hs）的淹没范围
    """
    with open(path, "w", encoding=cfg.csv_encoding, newline="") as f:
        w = csv.writer(f)
        w.writerow(["名称", "平面坐标X", "平面坐标Y", "图标样式"])
        for sec, res in zip(sections, results):
            if raised:
                _write_xy_rows(w, sec, res.left_point, res.left_status,
                               res.right_point, res.right_status,
                               cfg.export_coord_system)
            else:
                _write_xy_rows(w, sec, res.left_point_hs, res.left_status_hs,
                               res.right_point_hs, res.right_status_hs,
                               cfg.export_coord_system)


def export_disaster_csv(path: str, sections: list[Section],
                        results: list[SectionResult], infos: list[TerrainInfo],
                        cfg: Config) -> None:
    """成灾水位坐标 CSV：只含成灾水位那一行（格式同淹没线坐标）。"""
    with open(path, "w", encoding=cfg.csv_encoding, newline="") as f:
        w = csv.writer(f)
        w.writerow(["名称", "平面坐标X", "平面坐标Y", "图标样式"])
        for sec, res, info in zip(sections, results, infos):
            row = _disaster_row(sec, res, info, cfg)
            if row is not None:
                w.writerow(row)


def export_rating_csv(path: str, sections: list[Section],
                      results: list[SectionResult],
                      encoding: str = "utf-8-sig") -> None:
    """水位流量关系曲线 CSV：每断面一段，空行分隔。"""
    with open(path, "w", encoding=encoding, newline="") as f:
        for sec, res in zip(sections, results):
            f.write(f"断面={sec.name}\n")
            f.write("水位/m,流量/m3/s,面积/m2,湿周,顶宽/m\n")
            for h, q, a, p, b in zip(res.hvec, res.qvec, res.avec, res.pvec, res.bvec):
                f.write(f"{h:.6f},{q:.6f},{a:.6f},{p:.6f},{b:.6f}\n")
            f.write("\n")


def export_endpoint_csv(path: str, sections: list[Section],
                        results: list[SectionResult],
                        encoding: str = "utf-8-sig",
                        coord_system: str = "survey") -> None:
    """断面起终点坐标及水位 CSV：名称,平面坐标[X+Y],百年一遇水位（m）"""
    with open(path, "w", encoding=encoding, newline="") as f:
        f.write("名称,平面坐标[X+Y],百年一遇水位（m）\n")
        for sec, res in zip(sections, results):
            f.write(f'{sec.name},"{endpoint_xy_str(sec, coord_system)}",'
                    f'{matlab_num2str(res.design_level)}\n')


def export_hydro_csv(path: str, results: list[HydroResult]):
    """导出一维水面线推算结果表。

    列名沿用老版一维水面线要素表：断面名/里程/计算水位/临界水位/流量/过水面积/平均流速/动能修正系数/弗鲁德数/摩阻坡降/水头损失...
    NaN 写入为空字符串。
    """
    headers = [
        "断面名", "里程 (m)", "计算水位 (m)", "临界水位 (m)",
        "流量 (m³/s)", "过水面积 (m²)", "平均流速 (m/s)", "动能修正系数",
        "弗鲁德数", "输水能力", "总水头 (m)", "水面宽 (m)", "水深 (m)", "告警"
    ]

    def fmt(v: float) -> str:
        if v != v or v is None:
            return ""
        return f"{v:.4f}"

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for r in results:
            warnings_str = "; ".join(r.warnings) if r.warnings else ""
            if r.defaulted_critical:
                if warnings_str:
                    warnings_str = "Defaulted Critical; " + warnings_str
                else:
                    warnings_str = "Defaulted Critical"

            row = [
                r.name,
                fmt(r.chainage),
                fmt(r.ws),
                fmt(r.crit_ws),
                fmt(r.q),
                fmt(r.area),
                fmt(r.velocity),
                fmt(r.alpha),
                fmt(r.froude),
                fmt(r.conveyance),
                fmt(r.total_head),
                fmt(r.top_width),
                fmt(r.depth),
                warnings_str
            ]
            writer.writerow(row)


def export_section_names_xlsx(path: str, names: list[str]) -> None:
    """断面编号参考 xlsx（对应原 xlswrite(..., 'A2')）。需要 openpyxl。"""
    try:
        from openpyxl import Workbook
    except ImportError as e:      # pragma: no cover - 依赖未装时
        raise RuntimeError("导出 xlsx 需要 openpyxl，请先安装依赖") from e

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for i, name in enumerate(names, start=2):
        ws.cell(row=i, column=1, value=name)
    wb.save(path)


def export_all(project: Project,
               results: dict[str, SectionResult],
               infos: dict[str, TerrainInfo],
               cfg: Config,
               out_dir: Optional[str] = None,
               options: Optional[dict[str, bool]] = None) -> list[str]:
    """按纵断面线分目录导出全部结果，返回生成的文件路径列表。

    options 接受一个字典来控制哪些类型的文件被导出：
      'inundation': 淹没线坐标输出结果.csv
      'rating': 水位流量关系曲线.csv
      'endpoint': 断面起终点坐标及水位.csv
      'range_raised': 设计水位加高淹没范围坐标.csv
      'range_design': 设计水位淹没范围坐标.csv
      'disaster': 成灾水位坐标.csv

    如果 options 为 None，默认全部导出（如果 cfg 及 line 允许的话）。
    """
    out_dir = out_dir or cfg.output_dir
    written: list[str] = []

    if options is None:
        options = {
            'inundation': True, 'rating': True, 'endpoint': True,
            'range_raised': True, 'range_design': True, 'disaster': True
        }

    for line in project.profile_lines:
        line_dir = out_dir if len(project.profile_lines) == 1 else os.path.join(out_dir, line.name)
        os.makedirs(line_dir, exist_ok=True)

        secs = line.sections
        res_list = [results[s.name] for s in secs]
        info_list = [infos[s.name] for s in secs]

        if options.get('inundation', True):
            p1 = os.path.join(line_dir, f"{line.name}淹没线坐标输出结果.csv")
            export_inundation_csv(p1, secs, res_list, info_list, cfg)
            written.append(p1)

        if options.get('rating', True):
            p2 = os.path.join(line_dir, f"{line.name}水位流量关系曲线.csv")
            export_rating_csv(p2, secs, res_list, cfg.csv_encoding)
            written.append(p2)

        if options.get('endpoint', True):
            p3 = os.path.join(line_dir, "断面起终点坐标及水位.csv")
            export_endpoint_csv(p3, secs, res_list, cfg.csv_encoding,
                                cfg.export_coord_system)
            written.append(p3)

        if options.get('range_raised', True):
            p4 = os.path.join(line_dir, f"{line.name}设计水位加高淹没范围坐标.csv")
            export_range_csv(p4, secs, res_list, cfg, raised=True)
            written.append(p4)

        if options.get('range_design', True):
            p5 = os.path.join(line_dir, f"{line.name}设计水位淹没范围坐标.csv")
            export_range_csv(p5, secs, res_list, cfg, raised=False)
            written.append(p5)

        if cfg.output_disaster_level and options.get('disaster', True):
            p6 = os.path.join(line_dir, f"{line.name}成灾水位坐标.csv")
            export_disaster_csv(p6, secs, res_list, info_list, cfg)
            written.append(p6)

        if options.get('hydro1d', True):
            # Try to get hydro_results from the current line object, fallback gracefully
            # During normal execution flow from main_window, results might be cached there,
            # but since export_all only takes project and results, we must re-compute
            # or pass it in. Re-computing on the fly for export is safest.
            from .hydro1d import compute_line_profile
            from .model import HydroConfig
            try:
                hydro_cfg = line.hydro_config if hasattr(line, "hydro_config") and line.hydro_config else HydroConfig()
                hydro_res, _ = compute_line_profile(line, cfg, hydro_cfg)
                if hydro_res:
                    p7 = os.path.join(line_dir, f"{line.name}一维水面线要素表.csv")
                    export_hydro_csv(p7, hydro_res)
                    written.append(p7)
            except Exception:
                pass # If it fails to compute (e.g. no area), skip exporting for this line

    return written
