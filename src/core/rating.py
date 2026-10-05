"""水位–流量关系曲线（H~Q rating curve）计算。对应原 MATLAB 阶段 ④。

曼宁公式：Q = (1/n) * A * R^(2/3) * sqrt(S)，R = A / P
复式断面时各分区分别计算后求和（糙率可分区，见 model.SectionParams.roughness_for_zone）。
"""

from __future__ import annotations

import math

from .config import Config
from .geom import section_geom, surface_span
from .model import Section, TerrainInfo


def matlab_colon(a: float, d: float, b: float, tol: float = 1e-9) -> list[float]:
    """复现 MATLAB 的 a:d:b 冒号运算符（浮点步长）。

    元素个数 = floor((b-a)/d) + 1，元素值 = a + k*d。
    注意：MATLAB 内部对末位有容差处理，此处以 tol 近似；
    若回归比对发现水位个数差 1，优先检查这里。
    """
    if d == 0:
        return [a]
    if (b - a) * d < 0:
        return []
    n = int(math.floor((b - a) / d + tol))
    if n < 0:
        n = 0
    return [a + k * d for k in range(n + 1)]


def compute_rating_curve(sec: Section, info: TerrainInfo, cfg: Config
                         ) -> tuple[list[float], list[float], list[float],
                                    list[float], list[float]]:
    """计算整条 H~Q 曲线。

    返回 (Hvec, Qvec, Avec, Pvec, Bvec)。
    """
    hvec = matlab_colon(info.dmin, cfg.dH, info.zymin)
    if not hvec:
        return [], [], [], [], []

    if cfg.compound_mode:
        zones = info.zones
    else:
        zones = [(0, sec.n_points)]

    n_zone = len(zones)
    s_all, z_all = sec.s, sec.z
    slope = sec.params.slope

    qvec, avec, pvec, bvec = [], [], [], []

    for H in hvec:
        q_tot = 0.0
        a_tot = 0.0
        p_tot = 0.0
        b_tot = 0.0

        for iz, (start, stop) in enumerate(zones):
            if stop - start < 2:
                continue
            xsub = s_all[start:stop]
            zsub = z_all[start:stop]

            A, P, B = section_geom(xsub, zsub, H)
            if A <= 0:
                continue

            # 单断面模式：顶宽改用「水面左右交点跨度」口径（原 temp.m 的 x_right - x_left）
            if not cfg.compound_mode:
                xl, xr = surface_span(s_all, z_all, H)
                B = (xr - xl) if (xl == xl and xr == xr) else 0.0

            R = A / P

            n_manning = sec.params.roughness_for_zone(iz, n_zone)
            if not n_manning or math.isnan(n_manning):
                n_manning = 0.03

            safe_slope = slope
            if not math.isnan(safe_slope) and safe_slope < 0:
                safe_slope = 0.0

            Q = (1.0 / n_manning) * A * (R ** (2.0 / 3.0)) * math.sqrt(safe_slope)

            q_tot += Q
            a_tot += A
            p_tot += P
            b_tot += B

        qvec.append(q_tot)
        avec.append(a_tot)
        pvec.append(p_tot)
        bvec.append(b_tot)

    return hvec, qvec, avec, pvec, bvec


def check_monotonic(qvec: list[float]) -> list[int]:
    """检查 Q 是否随水位单调递增（对应缺陷 D5）。

    返回所有"下降段"的起始索引列表；空列表表示单调。
    """
    bad = []
    for i in range(1, len(qvec)):
        if qvec[i] < qvec[i - 1]:
            bad.append(i)
    return bad


def hvec_row_counts(info, cfg: Config) -> tuple[int, int]:
    """返回 (不手动改深泓点时的水位行数, 实际生效的水位行数)。

    供界面提示"手动深泓点让曲线少了几行"用。

    ⚠ **不能**用 `ceil(Δ/dH)` 估算，必须实际数。
      水位序列是 dmin + k·dH 的等差数列，深泓点一变，**整条网格整体平移**，
      与上端 zymin 的对齐关系随之改变，所以行数差可能落在
      floor(Δ/dH) 或 ceil(Δ/dH) 上。实测 31 个断面里有 21 个与 ceil 估算
      差 1 行（例如抬高 0.43 m 却只少 2 行，而 ceil(0.43/0.1)=5）——
      当时的警告数字因此系统性偏大，被 tools/check_overrides.py 抓出来。
    """
    dH = cfg.dH
    zy_auto = info.zymin_auto if info.zymin_auto == info.zymin_auto else info.zymin
    n_auto = len(matlab_colon(info.dmin_auto, dH, zy_auto))
    n_cur = len(matlab_colon(info.dmin, dH, info.zymin))
    return n_auto, n_cur
