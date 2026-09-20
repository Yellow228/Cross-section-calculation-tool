"""断面几何量计算。对应原 sectionGeom.m。

Q6 决策：顶宽 B 统一以复式断面（sectionGeom）的逐段 dx 累加口径为准。
"""

from __future__ import annotations

import math


def section_geom(x: list[float], z: list[float], H: float) -> tuple[float, float, float]:
    """计算给定水位 H 下，一条折线断面的过水面积 A、湿周 P、顶宽 B。

    参数
    ----
    x : 起点距（横向坐标）
    z : 高程
    H : 水位

    返回
    ----
    (A, P, B)

    算法逐段线性插值，与原 MATLAB sectionGeom.m 完全一致：
      - 线段全在水上 (H <= min(z1,z2))：无贡献
      - 线段全在水下 (H >= max(z1,z2))：完整梯形
      - 线段跨越水位：插值出交点 xi，取湿润的那一半三角形
    """
    A = 0.0
    P = 0.0
    B = 0.0

    for i in range(len(x) - 1):
        x1, x2 = x[i], x[i + 1]
        z1, z2 = z[i], z[i + 1]

        # 全在水上
        if H <= min(z1, z2):
            continue

        # 全在水下
        if H >= max(z1, z2):
            h1 = H - z1
            h2 = H - z2
            dx = abs(x2 - x1)
            A += (h1 + h2) / 2.0 * dx
            P += math.sqrt(dx * dx + (z2 - z1) ** 2)
            B += dx
            continue

        # 部分浸没：插值交点
        xi = x1 + (x2 - x1) * (H - z1) / (z2 - z1)

        if z1 < H:              # 点 1 在水下，湿润段为 x1 -> xi
            dx = abs(xi - x1)
            h1 = H - z1
            A += h1 / 2.0 * dx
            P += math.sqrt(dx * dx + h1 * h1)
            B += dx
        else:                   # 点 2 在水下，湿润段为 xi -> x2
            dx = abs(x2 - xi)
            h2 = H - z2
            A += h2 / 2.0 * dx
            P += math.sqrt(dx * dx + h2 * h2)
            B += dx

    return A, P, B


def _densify_at_level(x: list[float], z: list[float], H: float
                      ) -> list[tuple[float, float]]:
    """在跨越水位线的线段上插入精确交点，使折线只在采样点处与水位线相交。"""
    pts: list[tuple[float, float]] = [(x[0], z[0])]
    for i in range(len(x) - 1):
        x1, z1 = x[i], z[i]
        x2, z2 = x[i + 1], z[i + 1]
        if (z1 - H) * (z2 - H) < 0:                 # 严格跨越
            t = (H - z1) / (z2 - z1)
            pts.append((x1 + t * (x2 - x1), H))
        pts.append((x2, z2))
    return pts


def wetted_polygons(x: list[float], z: list[float], H: float
                    ) -> list[list[tuple[float, float]]]:
    """水位 H 下被水覆盖的若干多边形（水面不连续时会返回多个）。

    每个多边形形如 [(xL, H), 床面点…, (xR, H)]，
    左右端点都是**线段与水位线的插值交点**，不是采样点。

    ⚠ 反面教材：不要用 fill_between(x, np.minimum(z, H), H, where=z <= H)。
       minimum 会把高于水位的地形截断成 H，填充下边界随之变成"截断后的地形"，
      水面就会越过真实交点一路漫到最外侧的采样点，看起来两岸都被淹了。
    """
    if len(x) < 2:
        return []

    pts = _densify_at_level(x, z, H)

    polys: list[list[tuple[float, float]]] = []
    run: list[tuple[float, float]] = []
    for p in pts:
        if p[1] <= H:
            run.append(p)
        else:
            if len(run) >= 2:
                polys.append(_close_run(run, H))
            run = []
    if len(run) >= 2:
        polys.append(_close_run(run, H))
    return polys


def _close_run(run: list[tuple[float, float]], H: float
               ) -> list[tuple[float, float]]:
    """把一段连续水下点闭合成多边形：两端补上水位线上的端点。"""
    out: list[tuple[float, float]] = [(run[0][0], H)]
    for p in run:
        if not out or (abs(p[0] - out[-1][0]) > 1e-12 or abs(p[1] - out[-1][1]) > 1e-12):
            out.append(p)
    last = (run[-1][0], H)
    if abs(last[0] - out[-1][0]) > 1e-12 or abs(last[1] - out[-1][1]) > 1e-12:
        out.append(last)
    return out


def surface_span(x: list[float], z: list[float], H: float) -> tuple[float, float]:
    """水面左右交点横坐标 (x_left, x_right)。

    仅用于 compat_single_top_width=True 时复现 MATLAB 单断面分支的
    B = x_right - x_left 口径。找不到湿润区域时返回 (nan, nan)。
    """
    x_left = math.inf
    x_right = -math.inf
    wetted = False

    for i in range(len(x) - 1):
        x1, x2 = x[i], x[i + 1]
        z1, z2 = z[i], z[i + 1]

        if z1 <= H and z2 <= H:
            x_left = min(x_left, x1)
            x_right = max(x_right, x2)
            wetted = True
        elif (z1 <= H < z2) or (z2 <= H < z1):
            t = (H - z1) / (z2 - z1)
            xi = x1 + t * (x2 - x1)
            x_left = min(x_left, xi)
            x_right = max(x_right, xi)
            wetted = True

    if not wetted or math.isinf(x_left) or math.isinf(x_right):
        return float("nan"), float("nan")
    return x_left, x_right
