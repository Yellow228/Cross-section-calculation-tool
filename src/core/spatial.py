"""平面几何：判断横断面与纵断面是否相交，并按相交关系分组。

核心思路
--------
横断面是一条横跨河流的折线，纵断面（「纵断面」块）是一条沿河的折线。
二者在平面上应当**真实相交**。据此：
    - 相交 → 该横断面归属这条纵断面线
    - 都不相交 → 未分组，界面上告警
    - 与多条相交 → 取距离最近的一条，并报告冲突

额外收益：交点在纵断面上的位置**就是该横断面的里程**，
比"用相邻断面深泓点距离累加"的推算法精确得多，还能自动排出沿河顺序。

纯标准库实现，不引入 shapely。
"""

from __future__ import annotations

import math
from typing import Optional

# 判断共线/平行时用的相对阈值
_EPS = 1e-12


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _cross(u, v):
    return u[0] * v[1] - u[1] * v[0]


def _dot(u, v):
    return u[0] * v[0] + u[1] * v[1]


def segment_intersection(a1, a2, b1, b2):
    """两线段求交。返回 (交点, 在 a 上的参数 t)，不相交返回 None。

    t ∈ [0,1]，可用于换算交点在 a 折线上的累计距离。
    """
    d1 = _sub(a2, a1)
    d2 = _sub(b2, b1)
    den = _cross(d1, d2)
    if abs(den) < _EPS:
        return None                      # 平行或共线
    w = _sub(b1, a1)
    t = _cross(w, d2) / den
    u = _cross(w, d1) / den
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        t = min(max(t, 0.0), 1.0)
        return (a1[0] + t * d1[0], a1[1] + t * d1[1]), t
    return None


def point_segment_distance(p, a, b):
    """点到线段的距离。"""
    d = _sub(b, a)
    L2 = _dot(d, d)
    if L2 < _EPS:
        return math.hypot(p[0] - a[0], p[1] - a[1]), 0.0
    t = _dot(_sub(p, a), d) / L2
    t = min(max(t, 0.0), 1.0)
    q = (a[0] + t * d[0], a[1] + t * d[1])
    return math.hypot(p[0] - q[0], p[1] - q[1]), t


def segment_segment_distance(a1, a2, b1, b2):
    """两线段的最短距离，返回 (距离, 最近点在 a 上的参数 t)。

    相交时距离为 0。
    """
    hit = segment_intersection(a1, a2, b1, b2)
    if hit is not None:
        return 0.0, hit[1], hit[0]
    best = (float("inf"), 0.0, None)
    for (p, seg_t) in ((b1, 0.0), (b2, 1.0)):
        d, t = point_segment_distance(p, a1, a2)
        if d < best[0]:
            best = (d, t, None)
    for (p, _) in ((a1, 0.0), (a2, 1.0)):
        d, _t = point_segment_distance(p, b1, b2)
        if d < best[0]:
            # 最近点落在 b 上，反投影回 a 用端点近似即可，这里保守取 a 的中点参数
            best = (d, 0.5, None)
    return best[0], best[1], best[2]


def cumulative_distances(poly: list[tuple[float, float]]) -> list[float]:
    """折线上每个点的累计距离（首点为 0）。"""
    out = [0.0]
    for i in range(1, len(poly)):
        out.append(out[-1] + math.hypot(poly[i][0] - poly[i - 1][0],
                                        poly[i][1] - poly[i - 1][1]))
    return out


def find_intersection(poly_a: list[tuple[float, float]],
                      poly_b: list[tuple[float, float]],
                      tol: float = 1.0):
    """求两条折线的交点，返回 (沿 a 的累计距离, 两线最短距离, 交点) 或 None。

    先看是否真实相交；不相交但最短距离 <= tol 也视为相交（测量误差导致的擦肩而过）。
    有多处相交时取**沿 a 累计距离最小**的那处。
    """
    if len(poly_a) < 2 or len(poly_b) < 2:
        return None

    cum = cumulative_distances(poly_a)
    best_hit: Optional[tuple[float, float, tuple[float, float]]] = None
    best_near: Optional[tuple[float, float, tuple[float, float]]] = None

    for i in range(len(poly_a) - 1):
        a1, a2 = poly_a[i], poly_a[i + 1]
        seg_len = cum[i + 1] - cum[i]
        for j in range(len(poly_b) - 1):
            b1, b2 = poly_b[j], poly_b[j + 1]
            hit = segment_intersection(a1, a2, b1, b2)
            if hit is not None:
                pt, t = hit
                d_along = cum[i] + t * seg_len
                if best_hit is None or d_along < best_hit[0]:
                    best_hit = (d_along, 0.0, pt)
                continue
            d, t, _ = segment_segment_distance(a1, a2, b1, b2)
            if d <= tol:
                d_along = cum[i] + t * seg_len
                if best_near is None or d < best_near[1]:
                    best_near = (d_along, d, (a1[0] + t * (a2[0] - a1[0]),
                                              a1[1] + t * (a2[1] - a1[1])))

    return best_hit or best_near


def section_to_polyline(sec) -> list[tuple[float, float]]:
    """断面对象 -> 平面折线。"""
    return list(zip(sec.x, sec.y))


def profile_to_polyline(prof) -> list[tuple[float, float]]:
    """纵剖面对象 -> 平面折线。"""
    return list(zip(prof.x, prof.y))


def assign_by_intersection(sections, profiles, tol: float = 1.0):
    """按"横断面是否与纵断面相交"分组。

    返回 (groups, details)：
        groups  : 每个纵断面对应的横断面索引列表
        details : 每个横断面的归属信息
                  {profile: 归属的纵断面索引或 None,
                   dist:    交点在纵断面上的累计距离,
                   gap:     两折线最短距离（真实相交时为 0）,
                   conflicts: 同时相交的其他纵断面索引}
    """
    groups: list[list[int]] = [[] for _ in profiles]
    details: list[dict] = []

    for si, sec in enumerate(sections):
        sp = section_to_polyline(sec)
        cands = []
        for pi, prof in enumerate(profiles):
            r = find_intersection(profile_to_polyline(prof), sp, tol)
            if r is not None:
                cands.append((pi, r[0], r[1]))

        if not cands:
            details.append({"profile": None, "dist": None,
                            "gap": None, "conflicts": []})
            continue

        cands.sort(key=lambda c: (c[2], c[1]))     # 先取真实相交，再取靠前的
        pi, dist_along, gap = cands[0]
        groups[pi].append(si)
        details.append({"profile": pi, "dist": dist_along, "gap": gap,
                        "conflicts": [c[0] for c in cands[1:]]})

    return groups, details
