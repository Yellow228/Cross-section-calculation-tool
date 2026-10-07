"""单个断面的完整求解编排。

流程：地形特征提取 -> H~Q 曲线 -> 插值反查 -> 水面交点。
对应原 MATLAB transfer.m 主循环体的一个断面。
"""

from __future__ import annotations

from typing import Optional

from .config import Config
from .interp import find_water_edge, interp1_linear_extrap, interpolate_xy
from .model import ProfileLine, Section, SectionResult, TerrainInfo
from .rating import check_monotonic, compute_rating_curve
from .terrain import analyze_terrain
from .hydro1d import compute_hydro1d_profile


def solve_section(sec: Section, cfg: Config) -> tuple[SectionResult, TerrainInfo]:
    """求解单个断面，返回 (结果, 地形信息)。"""
    result = SectionResult()

    errs = sec.validate()
    if errs:
        result.warnings.extend(errs)

    # 人工覆盖若不合自洽（如深泓点跑到边界外侧），analyze_terrain 会退回自动，
    # 但必须让用户看见"我设的值没生效"，否则会以为是程序算错了。
    ov_errs = sec.override_errors()
    if ov_errs:
        result.warnings.extend(ov_errs + ["（上述手动值已被忽略，改用自动推算结果）"])

    info = analyze_terrain(sec, cfg)

    hvec, qvec, avec, pvec, bvec = compute_rating_curve(sec, info, cfg)
    result.hvec, result.qvec = hvec, qvec
    result.avec, result.pvec, result.bvec = avec, pvec, bvec

    if not hvec:
        result.warnings.append(
            f"{sec.name}: 水位区间为空（Dmin={info.dmin}, ZYmin={info.zymin}），无法计算曲线")
        return result, info

    # 单调性检查（缺陷 D5）
    bad = check_monotonic(qvec)
    if bad:
        result.warnings.append(
            f"{sec.name}: 流量-水位曲线非单调，共 {len(bad)} 处下降"
            f"（首处索引 {bad[0]}），插值结果可能不可靠")

    # 设计流量 -> 设计水位
    result.design_level = interp1_linear_extrap(qvec, hvec, sec.params.design_q)
    # 加高水位（原硬编码 Hs+1）：始终按 Hs + 加高幅度 计算，影响左右岸水面交点。
    # 是否绘制该水位线由 cfg.raise_enabled 控制（见 *_view）。
    result.design_level_plus = result.design_level + cfg.raise_level

    # 成灾水位 -> 成灾流量
    result.disaster_flow = interp1_linear_extrap(hvec, qvec, info.disaster_level)

    z = sec.z

    # 左右岸水面交点（基于 Hs1 = Hs + 加高幅度）——原 MATLAB 的输出口径
    result.left_point, result.left_status = _edge_point(
        sec, cfg, z, result.design_level_plus, info.dmin_idx, "left", info.zmax_idx)
    result.right_point, result.right_status = _edge_point(
        sec, cfg, z, result.design_level_plus, info.dmin_idx, "right", info.ymax_idx)

    # 设计水位 Hs 处的交点（新增）：用于「设计水位淹没范围坐标」，与 Hs1 无关。
    result.left_point_hs, result.left_status_hs = _edge_point(
        sec, cfg, z, result.design_level, info.dmin_idx, "left", info.zmax_idx)
    result.right_point_hs, result.right_status_hs = _edge_point(
        sec, cfg, z, result.design_level, info.dmin_idx, "right", info.ymax_idx)

    return result, info


def solve_profile_line(line: ProfileLine, cfg: Config, results: dict,
                       warnings: Optional[list[str]] = None) -> None:
    """求解整个纵断面线的一维水动力推算。

    warnings：出参。一维推算的二分求根贴边/不收敛会写进这里——
    那些情况原本是**静默**返回一个不可信的水位，界面上完全看不出来。
    """
    if line.hydro1d_enabled:
        line.hydro1d_levels, line.hydro1d_nodes = compute_hydro1d_profile(line, cfg, results,
                                                                          warnings=warnings)
    else:
        line.hydro1d_levels = [float('nan')] * len(line.sections)
        line.hydro1d_nodes = [None] * len(line.sections)


def _edge_point(sec: Section, cfg: Config, z: list[float], H: float,
                dmin_idx: int, direction: str,
                fallback_idx: int) -> tuple[Optional[tuple[float, float]], int]:
    """求水位 H 在一侧的水面交点，返回 (点, 图标样式)。

    找得到交点就线性插出平面坐标；找不到（该侧岸线全在水位以下）则退回该侧的
    最高点，图标样式标记为"未找到"，与原 MATLAB 行为一致。

    抽成函数是为了让 Hs 与 Hs1 两套交点共用同一判定——两处各写一遍必然分叉。
    """
    edge = find_water_edge(z, H, dmin_idx, direction)
    if edge is not None:
        i1, i2 = edge
        return interpolate_xy(sec.x, sec.y, z, i1, i2, H), cfg.ICON_FOUND
    if fallback_idx >= 0:
        return (sec.x[fallback_idx], sec.y[fallback_idx]), cfg.ICON_NOT_FOUND
    return None, cfg.ICON_NOT_FOUND
