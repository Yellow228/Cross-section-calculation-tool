"""地形特征提取：深泓点、左右岸峰点、岸坡转折点、成灾水位、分区。

对应原 MATLAB transfer.m 的阶段 ② 与 ③。
所有索引均为 0-based（MATLAB 原码为 1-based，此处已换算）。
"""

from __future__ import annotations

from typing import Optional

from .config import Config
from .model import Section, TerrainInfo, argmin_index, is_valid_index


def _argmax(values: list[float]) -> tuple[float, int]:
    """返回 (最大值, 首个最大值索引)，空列表返回 (nan, -1)。对应 MATLAB max()"""
    if not values:
        return float("nan"), -1
    best = 0
    for i in range(1, len(values)):
        if values[i] > values[best]:
            best = i
    return values[best], best


def _peaks_around(z: list[float], dmin_idx: int) -> tuple[float, int, float, int]:
    """按**给定**的深泓点索引，把断面切成左右两段各取最高点。

    返回 (zmax, zmax_idx, ymax, ymax_idx)。
    对应 MATLAB：max(vals(1:IdxDmin-1)) 与 max(vals(IdxDmin+1:end))

    单独抽出来是因为**手动深泓点会改变这个划分**：深泓点一动，
    "左岸"包含的测点集合就变了，必须重新取岸顶，
    否则会把右岸的点当成左岸顶（原代码只在初次求深泓点时顺带算了岸顶，
    一旦深泓点可人工指定，那个顺带就不成立了）。
    """
    n = len(z)
    zmax = float("nan")
    zmax_idx = -1
    if dmin_idx > 0:
        zmax, zmax_idx = _argmax(z[0:dmin_idx])

    ymax = float("nan")
    ymax_idx = -1
    if 0 <= dmin_idx < n - 1:
        ymax, rel = _argmax(z[dmin_idx + 1:])
        ymax_idx = rel + dmin_idx + 1 if rel >= 0 else -1

    return zmax, zmax_idx, ymax, ymax_idx


def find_thalweg_and_peaks(z: list[float]) -> tuple[float, int, float, int, float, int]:
    """深泓点与左右岸最高点。

    返回 (dmin, dmin_idx, zmax, zmax_idx, ymax, ymax_idx)，索引 0-based。
    对应 MATLAB：
        [Dmin, IdxDmin] = min(vals)
        [Zmax, IdxZmax] = max(vals(1:IdxDmin-1))
        [Ymax, IdxYmax] = max(vals(IdxDmin+1:end))
    """
    n = len(z)
    if n == 0:
        return float("nan"), -1, float("nan"), -1, float("nan"), -1

    dmin_idx = argmin_index(z)                  # 与 Section.thalweg_index 同一函数，
    dmin = z[dmin_idx]                          # 两处取法必须一致

    zmax, zmax_idx, ymax, ymax_idx = _peaks_around(z, dmin_idx)

    return dmin, dmin_idx, zmax, zmax_idx, ymax, ymax_idx


def _scan_turning_point(s: list[float], z: list[float], dmin_idx: int,
                        direction: str, cfg: Config) -> Optional[int]:
    """从深泓点向一侧扫描岸坡转折点。

    direction: "left" | "right"
    两阶段：先遇到陡坡 slope > steep_slope 置 passedSteep，
            之后遇到 dz < 0 或 slope < turn_slope 即为转折点。

    返回 0-based 转折点索引，未找到返回 None。
    """
    n = len(z)
    passed_steep = False

    if direction == "left":
        # MATLAB: for k = IdxDmin-1 : -1 : 1   (1-based)
        # 0-based k1 从 dmin_idx-1 递减到 0
        for k1 in range(dmin_idx - 1, -1, -1):
            dx = s[k1 + 1] - s[k1]
            if dx <= 0:
                continue
            dz = z[k1] - z[k1 + 1]
            slope = dz / dx
            if not passed_steep:
                if slope > cfg.steep_slope:
                    passed_steep = True
                continue
            if dz < 0 or slope < cfg.turn_slope:
                return k1 + 1          # MATLAB IdxLeftTurn = k+1
    else:
        # MATLAB: for k = IdxDmin+1 : npt   (1-based)
        # 0-based k1 从 dmin_idx+1 递增到 n-1
        for k1 in range(dmin_idx + 1, n):
            dx = s[k1] - s[k1 - 1]
            if dx <= 0:
                continue
            dz = z[k1] - z[k1 - 1]
            slope = dz / dx
            if not passed_steep:
                if slope > cfg.steep_slope:
                    passed_steep = True
                continue
            if dz < 0 or slope < cfg.turn_slope:
                return k1 - 1          # MATLAB IdxRightTurn = k-1

    return None


def _build_zones(n: int, left_idx: Optional[int],
                 right_idx: Optional[int]) -> list[tuple[int, int]]:
    """生成分区范围列表 [(start, stop_exclusive)]，0-based。

    参数是**分区边界**索引（默认等于两个转折点，也可人工指定，见 Q15）。

    相邻分区**共享边界点**：{1:i1, i1:i2, i2:n}（与原 MATLAB 一致）

    边界数量：
        0 个 -> 单分区
        1 个 -> 两分区
        2 个 -> 左滩、主槽、右滩

    ⚠⚠ 这里必须是"共享端点"，**绝不能**改成不重叠的
        {1:i1, i1+1:i2, i2+1:n} —— 曾经这么改过，是错的。

    原因：每个分区是用**自己的点序列**独立调用 section_geom 算 A/P/B 的，
    分区 i 算的是它自己那条折线下的面积。若改成不重叠，转折点两侧的线段
    (i1-1, i1) 与 (i1, i1+1) 都不属于任何分区，**整条线段被漏掉**，
    实测面积最多少算 19%（secC-1: 92.20 → 74.67），secC-2 少 24%。

    而共享一个"点"不会造成面积重复——点的宽度为零，贡献的面积也是零。
    所以"重叠"只是索引写法上的重合，数值上恰好保证每条线段被计入且仅计一次。
    """
    if n < 2:
        return [(0, n)]

    if left_idx is None and right_idx is None:
        return [(0, n)]

    if right_idx is None:                       # 仅左侧转折点
        return [(0, left_idx + 1), (left_idx, n)]
    if left_idx is None:                        # 仅右侧转折点
        return [(0, right_idx + 1), (right_idx, n)]
    return [(0, left_idx + 1), (left_idx, right_idx + 1), (right_idx, n)]


def _zone_override(sec: Section, n: int, di: int
                   ) -> Optional[tuple[Optional[int], Optional[int]]]:
    """取人工**分区边界**，返回 (左, 右)。

    返回 None 表示"不采用人工边界、退回自动扫描"（即用转折点）。
    返回 (None, None) 是**合法**结果，意思是「这个断面不分区」（1 区）。

    合法条件（以生效深泓点为参照）：
        0 <= 左 < 深泓点 < 右 <= n-1
    任一项不满足就整体退回自动——不做"半手动半自动"，
    那种中间状态既算错又难以向用户解释。

    ⚠ 约束的是**深泓点**，不是转折点：主槽必须包含深泓点，
      否则"左滩/主槽/右滩"这组名字失去意义。转折点与分区边界相互独立，
      所以这里不涉及转折点。
    """
    if not sec.zone_manual:
        return None
    L, R = sec.zone_left, sec.zone_right
    if L is not None and not (is_valid_index(L, n) and L < di):
        return None
    if R is not None and not (is_valid_index(R, n) and di < R):
        return None
    return L, R


def _auto_disaster(z: list[float], L: Optional[int], R: Optional[int],
                   dmin: float, dmin_idx: int) -> tuple[float, int]:
    """原 MATLAB 规则：成灾水位取两个转折点中**较低**的那个高程；
    没有转折点时退化为深泓点高程。返回 (水位, 测点索引)。"""
    if L is None and R is None:
        return dmin, dmin_idx
    if L is None:
        return z[R], R
    if R is None:
        return z[L], L
    if z[L] <= z[R]:
        return z[L], L
    return z[R], R


def analyze_terrain(sec: Section, cfg: Config) -> TerrainInfo:
    """完整执行阶段 ② + ③，并**在最后套用人工覆盖**（Q14）。

    结构：先无条件算一遍自动值（既作兜底、又供界面显示"自动值是多少"），
    再用 `Section` 上的手动字段替换。输出 `TerrainInfo` 里的有效值永远是
    "套用之后"的，下游（曲线、成灾流量、导出、三张图）无需知道有没有手动干预。

    手动覆盖一律**先校验后采用**，不合法就退回自动；不合法原因由
    `Section.override_errors()` 报给界面，不会静默吞掉。
    """
    z = sec.z
    s = sec.s
    n = len(z)

    info = TerrainInfo()
    if n == 0:
        return info

    # ---------------- ① 深泓点 ----------------
    d_auto, di_auto, *_ = find_thalweg_and_peaks(z)
    info.dmin_auto, info.dmin_auto_idx = d_auto, di_auto
    # 水位上限也按自动深泓点算一份：界面上要用它算出"改深泓点会让曲线少几行"。
    # 不能只记 dmin_auto——zymin 由左右岸顶决定，而岸顶是按深泓点分左右取的，
    # 深泓点一动手动值的岸顶划分就变了。
    _za, _zia, _ya, _yia = _peaks_around(z, di_auto)
    _cand_a = [v for v in (_za, _ya) if v == v]
    info.zymin_auto = min(_cand_a) if _cand_a else d_auto

    th = sec.thalweg_manual
    if is_valid_index(th, n):
        d, di = z[th], th
    else:
        d, di = d_auto, di_auto
    info.dmin, info.dmin_idx = d, di

    # 岸顶必须按**最终**深泓点重新划分；深泓点一动，"左岸"包含的测点集合就变了。
    info.zmax, info.zmax_idx, info.ymax, info.ymax_idx = _peaks_around(z, di)

    cand = [v for v in (info.zmax, info.ymax) if v == v]
    info.zymin = min(cand) if cand else d

    # ---------------- ② 转折点（永远自动，人工改不了）----------------
    # 转折点是扫描算法的产物，决定成灾水位。它与"分区边界"是两件事：
    # 分区边界默认取这对索引，但**可以单独人工指定**，改了也不回头影响这里。
    # （原 MATLAB 把两者合成一对索引，本项目一开始也沿用，Q15 拆开。）
    turn_L = _scan_turning_point(s, z, di, "left", cfg)
    turn_R = _scan_turning_point(s, z, di, "right", cfg)
    info.left_turn_idx, info.right_turn_idx = turn_L, turn_R

    # ---------------- ③ 分区边界（默认 = 转折点，可人工覆盖）----------------
    zone_L, zone_R = turn_L, turn_R
    ov = _zone_override(sec, n, di)
    if ov is not None:
        zone_L, zone_R = ov
    info.zone_left_idx, info.zone_right_idx = zone_L, zone_R
    info.zones = _build_zones(n, zone_L, zone_R)

    # ---------------- ④ 成灾水位 ----------------
    # ⚠ 只由**转折点**决定，与分区边界无关。所以"手动改分区"不会再改成灾水位——
    #   这是 Q15 明确要求的行为。只有手动改深泓点才会（因为扫描起点变了）。
    a_level, a_idx = _auto_disaster(z, turn_L, turn_R, d, di)
    info.disaster_level_auto = a_level

    dm = sec.disaster_idx_manual
    if is_valid_index(dm, n):
        info.disaster_level, info.disaster_idx = z[dm], dm
    else:
        info.disaster_level, info.disaster_idx = a_level, a_idx

    return info
