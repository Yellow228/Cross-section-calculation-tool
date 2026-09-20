"""纵断面线内的断面排序与里程（桩号）计算。

Q5/Q9 确认的规则：
    **每条纵断面线独立编桩号，原点 = 该线内深泓高程最低的那个断面（其桩号 = 0）**
    其余断面里程 = 到该原点的沿程累计距离（取相邻断面深泓点的平面直线距离），恒非负。

同时提供：
    - 按平面位置自动排序（最近邻链式），用于数据文件顺序不可靠时
    - 界面可手工覆盖任一断面的里程
"""

from __future__ import annotations

import math

from .model import Section, TerrainInfo


def thalweg_point(sec: Section, info: TerrainInfo) -> tuple[float, float]:
    """断面的深泓点平面坐标。"""
    if 0 <= info.dmin_idx < sec.n_points:
        return sec.x[info.dmin_idx], sec.y[info.dmin_idx]
    return sec.x[0], sec.y[0]


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def rebase_chainage(dist: list[float], z: list[float], origin: str) -> list[float]:
    """按选定的起始端点重定基里程（Q10：起始桩号可选）。

    dist : 原始起点距（沿河累计，递增）
    z    : 对应高程
    origin:
        "start"  -> 首点为桩号 0，里程 = dist[i] - dist[0]
        "end"    -> 末点为桩号 0，里程 = dist[-1] - dist[i]（反向）
        "lowest" -> 高程最低点为桩号 0，里程 = |dist[i] - dist[lowest]|
    返回值恒非负，且从原点沿河向两侧递增。
    """
    if not dist:
        return []

    if origin == "start":
        base = dist[0]
    elif origin == "end":
        base = dist[-1]
    elif origin == "lowest":
        oi = 0
        if z:
            for i in range(1, len(z)):
                if z[i] < z[oi]:
                    oi = i
        base = dist[oi]
    else:
        raise ValueError(f"未知 chainage_origin: {origin!r}（可选 start / end / lowest）")

    return [abs(d - base) for d in dist]


def chainage_at_distance(prof, dist_along: float) -> float:
    """已知某点沿纵断面的累计距离，插值出它的桩号。

    比"用相邻断面深泓点距离累加"精确得多——横断面与纵断面的交点
    直接给出了它在河上的确切位置。
    """
    from .interp import interp1_linear_extrap
    from .spatial import cumulative_distances, profile_to_polyline

    cum = cumulative_distances(profile_to_polyline(prof))
    if len(cum) < 2 or not prof.chainage:
        return float("nan")
    return interp1_linear_extrap(cum, prof.chainage, dist_along)


def compute_chainage(sections: list[Section], infos: list[TerrainInfo],
                     origin: str = "lowest") -> list[float]:
    """计算一条纵断面线内各断面的里程。

    origin:
        "lowest" -> 深泓高程最低的断面为原点（默认，Q5 确认）
        "first"  -> 列表中第一个断面为原点
    """
    n = len(sections)
    if n == 0:
        return []
    if n == 1:
        return [0.0]

    pts = [thalweg_point(s, i) for s, i in zip(sections, infos)]

    # 沿列表顺序的累计距离
    cum = [0.0]
    for i in range(1, n):
        cum.append(cum[i - 1] + _dist(pts[i - 1], pts[i]))

    if origin == "first":
        return list(cum)

    # 原点：深泓高程最低的断面
    oi = 0
    for i in range(1, n):
        if infos[i].dmin < infos[oi].dmin:
            oi = i
    base = cum[oi]
    return [abs(c - base) for c in cum]


def auto_order(sections: list[Section],
               infos: list[TerrainInfo]) -> list[int]:
    """按平面位置自动排序：从当前首断面出发的最近邻链式遍历。

    返回重排后的原始索引列表。适用于数据文件顺序不可靠的情况。
    注意：河道若存在分叉或断面过密，最近邻可能不是真实沿河顺序，
    界面上应允许手工调整（order_source = "manual"）。
    """
    n = len(sections)
    if n <= 2:
        return list(range(n))

    pts = [thalweg_point(s, i) for s, i in zip(sections, infos)]
    remaining = set(range(1, n))
    order = [0]
    cur = 0

    while remaining:
        nxt = min(remaining, key=lambda j: _dist(pts[cur], pts[j]))
        order.append(nxt)
        remaining.discard(nxt)
        cur = nxt

    return order
