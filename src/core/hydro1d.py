import math
from typing import Optional

from .model import ProfileLine, Section, HydroConfig, HydroResult
from .terrain import analyze_terrain, TerrainInfo
from .config import Config

def _downstream_index(sections: list[Section], infos: list[TerrainInfo]) -> int:
    """河底最低点（dmin 最小）为最下游，返回其索引。"""
    idx = 0
    mz = infos[0].dmin
    for i in range(1, len(sections)):
        if infos[i].dmin < mz:
            mz = infos[i].dmin
            idx = i
    return idx

from .chainage import thalweg_point

def _reach_chainage(line: ProfileLine, infos: list[TerrainInfo], ds_idx: int) -> list[float]:
    """给 RASlite 用的 chainage：沿流向递增（下游最大）。

    RASlite 约定 chainage 大 = 下游，因此最上游=0、最下游=最大。
    """
    sections = line.sections
    n = len(sections)
    dist_to_prev = [0.0] * n

    for i in range(1, n):
        if line.profile and len(line.profile_dist) == n:
            dist_to_prev[i] = abs(line.profile_dist[i] - line.profile_dist[i - 1])
        else:
            p1 = thalweg_point(sections[i-1], infos[i-1])
            p2 = thalweg_point(sections[i], infos[i])
            dist_to_prev[i] = math.hypot(p2[0] - p1[0], p2[1] - p1[1])

    cumulative = [0.0] * n
    for i in range(1, n):
        cumulative[i] = cumulative[i-1] + dist_to_prev[i]

    res = [abs(c - cumulative[ds_idx]) for c in cumulative]
    # 反转为"沿流向里程"：下游最大（RASlite 约定），最上游=0
    max_d = max(res)
    return [max_d - di for di in res]

def _to_cross_section(sec: Section, info: TerrainInfo, dist_ds: float):
    from raslite import CrossSection
    points = [(float(s), float(z)) for s, z in zip(sec.s, sec.z)]
    p = sec.params
    n = p.roughness if p.roughness and p.roughness == p.roughness else 0.03
    n_main = p.roughness_main if p.roughness_main and p.roughness_main == p.roughness_main else n

    L, R = info.zone_left_idx, info.zone_right_idx
    if L is None and R is None:
        bl, br = sec.s[0], sec.s[-1]
    elif R is None:
        bl, br = sec.s[L], sec.s[-1]
    elif L is None:
        bl, br = sec.s[0], sec.s[R]
    else:
        bl, br = sec.s[L], sec.s[R]

    return CrossSection(
        name=sec.name,
        points=points,
        bank_left=bl, bank_right=br,
        n_main=n_main,
        n_left=p.roughness_left if p.roughness_left and p.roughness_left == p.roughness_left else n_main,
        n_right=p.roughness_right if p.roughness_right and p.roughness_right == p.roughness_right else n_main,
        reach_lengths=(dist_ds, dist_ds, dist_ds),
        c_contract=0.1, c_expand=0.3,
    )

def _boundary(hydro: HydroConfig, sec_bound: Section, q: float) -> tuple:
    kind, val = hydro.boundary_kind, hydro.boundary_value
    if kind == "ws":
        return ("ws", val)
    if kind == "critical":
        return ("critical",)

    s0 = sec_bound.params.slope
    if s0 != s0 or s0 is None or s0 <= 0:
        s0 = 0.001

    if hydro.boundary_kind == "normal" and val == val and val > 0:
        s0 = val

    return ("normal", s0)

def compute_line_profile(line: ProfileLine, cfg: Config, hydro: HydroConfig) -> tuple[list[HydroResult], list[str]]:
    """对一条纵断面线做一维水面线推算。"""
    if not line.sections:
        return [], []

    infos = [analyze_terrain(s, cfg) for s in line.sections]
    ds_idx = _downstream_index(line.sections, infos)
    ras_chainage = _reach_chainage(line, infos, ds_idx)

    # sorted_items 升序 = 上游→下游（chainage 下游最大，见 _reach_chainage）
    sorted_items = sorted(list(range(len(line.sections))), key=lambda i: ras_chainage[i])

    from raslite import SteadyProfile

    cross_sections = []
    n = len(sorted_items)

    q_val = hydro.q_override
    if q_val is None or q_val != q_val:
        top_sec = line.sections[sorted_items[0]]     # 最上游
        dq = top_sec.params.design_q
        q_val = dq if (dq is not None and dq == dq) else 50.0

    for sort_idx, original_idx in enumerate(sorted_items):
        sec = line.sections[original_idx]
        info = infos[original_idx]

        # dist_ds = 本断面到其下游断面的距离；最下游（sort_idx == n-1）为 0
        if sort_idx == n - 1:
            dist_ds = 0.0
        else:
            ds_neighbor = sorted_items[sort_idx + 1]   # 下游相邻
            dist_ds = ras_chainage[ds_neighbor] - ras_chainage[original_idx]

        xs = _to_cross_section(sec, info, dist_ds)
        xs.chainage = ras_chainage[original_idx]
        cross_sections.append(xs)

    if hydro.regime == "mixed":
        ds_sec = line.sections[sorted_items[-1]]    # 最下游
        us_sec = line.sections[sorted_items[0]]     # 最上游
        boundary_tuple = (_boundary(hydro, ds_sec, q_val),
                          _boundary(hydro, us_sec, q_val))
    else:
        boundary_sec = (line.sections[sorted_items[-1]] if hydro.regime != "supercritical"
                        else line.sections[sorted_items[0]])
        boundary_tuple = _boundary(hydro, boundary_sec, q_val)

    profile = SteadyProfile(
        sections=cross_sections,
        q=q_val,
        regime=hydro.regime,
        boundary=boundary_tuple
    )

    ras_results = profile.run()

    hydro_results = [None] * len(line.sections)
    warnings_out = []

    for r in ras_results:
        idx = next((i for i, s in enumerate(line.sections) if s.name == r.section.name), -1)
        if idx != -1:
            warnings_out.extend(r.warnings)
            hydro_results[idx] = HydroResult(
                name=r.section.name,
                chainage=line.chainage[idx] if len(line.chainage) > idx else ras_chainage[idx],
                ws=r.ws,
                crit_ws=r.crit_ws,
                q=r.q,
                area=r.area,
                velocity=r.velocity,
                froude=r.froude,
                alpha=r.alpha,
                conveyance=r.conveyance,
                total_head=r.total_head,
                top_width=r.top_width,
                depth=r.depth,
                defaulted_critical=r.defaulted_critical,
                warnings=r.warnings
            )

    return [res for res in hydro_results if res is not None], warnings_out
