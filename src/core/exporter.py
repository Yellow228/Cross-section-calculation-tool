"""结果导出。CSV 部分仅用标准库；xlsx 部分惰性导入 openpyxl。

输出格式沿用原 MATLAB（文件名、列名、小数位数），保证下游流程无需改动。

⚠ **导出坐标的列序**（改动前务必读 `out_xy()` 的说明）：
  内部一律是数学惯例 `x=东、y=北`，导出的 CSV 一律是**测量惯例 `X=北、Y=东`**，
  与输入 xlsx 的列头含义一致。所有写坐标的地方都必须经过 `out_xy()`，
  否则导出坐标会和输入文件对不上（表现为关于 y=x 镜像）。

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
from .model import ProfileLine, Project, Section, SectionResult, TerrainInfo
from .interp import interpolate_xy, find_water_edge


def matlab_num2str(v: float) -> str:
    """近似 MATLAB num2str 的默认（short）格式：整数不带小数，其余 4 位小数。"""
    if v != v:
        return "NaN"
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:.4f}"


def out_xy(x: float, y: float) -> tuple[float, float]:
    """内部坐标 -> 导出坐标：**（x=东, y=北）->（X=北, Y=东）**。

    ⚠ 这里必须交换一次，别按"内部是什么就写什么"改回去。

    内部代码（`Section.x/y`、`interpolate_xy`、水面交点）一律用**数学惯例**
    `x = 东坐标、y = 北坐标`；而导出的 CSV 沿用**中国测量惯例**
    `X = 北坐标、Y = 东坐标` —— 与输入 xlsx 的列头含义一致
    （用户的数据就是「X坐标」列装北坐标、「Y坐标」列装东坐标）。

    两侧都要一致才不出错：输入按测量惯例标注 -> `reader` 读进来时换一次
    （见 `reader.detect_xy_order`）-> 内部用数学惯例算 -> 导出时再换回去。
    少任何一次，导出的坐标就和输入对不上（表现为关于 y=x 镜像）。
    """
    return y, x


def endpoint_xy_str(sec: Section) -> str:
    """断面起终点平面坐标串：'起点北,起点东;终点北,终点东'，三位小数。

    对应原 MATLAB `A.duanmianXY`，格式与列名沿用原程序。
    """
    x1, y1 = out_xy(sec.x[0], sec.y[0])
    x2, y2 = out_xy(sec.x[-1], sec.y[-1])
    return f"{x1:.3f},{y1:.3f};{x2:.3f},{y2:.3f}"


def _write_xy_rows(w, sec: Section, lp, ls, rp, rs) -> None:
    """写一个断面的左岸(Z)、右岸(Y)两条淹没交点记录。"""
    if lp is not None:
        ox, oy = out_xy(lp[0], lp[1])
        w.writerow([f"{sec.name}Z", f"{ox:.6f}", f"{oy:.6f}", ls])
    if rp is not None:
        ox, oy = out_xy(rp[0], rp[1])
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
    ox, oy = out_xy(sec.x[info.disaster_idx], sec.y[info.disaster_idx])
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
                           res.right_point, res.right_status)
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
                               res.right_point, res.right_status)
            else:
                _write_xy_rows(w, sec, res.left_point_hs, res.left_status_hs,
                               res.right_point_hs, res.right_status_hs)


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
                        encoding: str = "utf-8-sig") -> None:
    """断面起终点坐标及水位 CSV：名称,平面坐标[X+Y],百年一遇水位（m）"""
    with open(path, "w", encoding=encoding, newline="") as f:
        f.write("名称,平面坐标[X+Y],百年一遇水位（m）\n")
        for sec, res in zip(sections, results):
            f.write(f'{sec.name},"{endpoint_xy_str(sec)}",'
                    f'{matlab_num2str(res.design_level)}\n')


def export_hydro1d_csv(path: str, line: ProfileLine, results: list[SectionResult], encoding: str = "utf-8-sig") -> None:
    """一维推算水面线结果 CSV（仅对启用的线导出）。"""
    with open(path, "w", encoding=encoding, newline="") as f:
        w = csv.writer(f)
        w.writerow(["断面名称", "桩号(m)", "一维推算水位(m)", "设计流量(m3/s)", "推算流态"])

        hs = getattr(line, "hydro1d_levels", [])
        for i, sec in enumerate(line.sections):
            c = line.chainage[i] if line.chainage and i < len(line.chainage) else sec.s[0]
            h = hs[i] if i < len(hs) else float('nan')
            q = sec.params.design_q
            w.writerow([sec.name, f"{c:.3f}", f"{h:.6f}", f"{q:.3f}", line.hydro1d_regime])


def export_hydro1d_range_csv(path: str, line: ProfileLine, cfg: Config) -> None:
    """一维推算水面线淹没范围坐标 CSV（同淹没线坐标格式）。"""
    with open(path, "w", encoding=cfg.csv_encoding, newline="") as f:
        w = csv.writer(f)
        w.writerow(["名称", "平面坐标X", "平面坐标Y", "图标样式"])
        hs = getattr(line, "hydro1d_levels", [])
        for i, sec in enumerate(line.sections):
            h = hs[i] if i < len(hs) else float('nan')
            if h != h:
                continue

            z = sec.z
            dmin_idx = sec.thalweg_index()

            # 左岸
            edge_l = find_water_edge(z, h, dmin_idx, "left")
            if edge_l:
                i1, i2 = edge_l
                px, py = interpolate_xy(sec.x, sec.y, z, i1, i2, h)
                ox, oy = out_xy(px, py)
                w.writerow([f"{sec.name}Z", f"{ox:.6f}", f"{oy:.6f}", cfg.ICON_FOUND])

            # 右岸
            edge_r = find_water_edge(z, h, dmin_idx, "right")
            if edge_r:
                i1, i2 = edge_r
                px, py = interpolate_xy(sec.x, sec.y, z, i1, i2, h)
                ox, oy = out_xy(px, py)
                w.writerow([f"{sec.name}Y", f"{ox:.6f}", f"{oy:.6f}", cfg.ICON_FOUND])


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
      'hydro1d': 一维推算水面线.csv
      'hydro1d_range': 一维推算水面线淹没范围坐标.csv

    如果 options 为 None，默认全部导出（如果 cfg 及 line 允许的话）。
    """
    out_dir = out_dir or cfg.output_dir
    written: list[str] = []

    if options is None:
        options = {
            'inundation': True, 'rating': True, 'endpoint': True,
            'range_raised': True, 'range_design': True, 'disaster': True,
            'hydro1d': True, 'hydro1d_range': True
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
            export_endpoint_csv(p3, secs, res_list, cfg.csv_encoding)
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

        # 导出开启了一维水面线推算的组
        if getattr(line, "hydro1d_enabled", False):
            if options.get('hydro1d', True):
                p7 = os.path.join(line_dir, f"{line.name}一维推算水面线.csv")
                export_hydro1d_csv(p7, line, res_list, cfg.csv_encoding)
                written.append(p7)
            if options.get('hydro1d_range', True):
                p8 = os.path.join(line_dir, f"{line.name}一维推算水面线淹没范围坐标.csv")
                export_hydro1d_range_csv(p8, line, cfg)
                written.append(p8)

    return written
