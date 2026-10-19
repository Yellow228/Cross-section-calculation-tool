"""插值与水面交点求解。对应原 MATLAB 阶段 ⑤。

interp1_linear_extrap 复现 MATLAB 的 interp1(..., 'linear', 'extrap')：
    区间内线性插值；区间外用首尾段的斜率线性外推。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:                      # 仅用于类型标注，运行期不导入（避免耦合）
    from .config import Config
    from .model import Section


def interp1_linear_extrap(xs: list[float], ys: list[float], xi: float) -> float:
    """在单调（假设递增）样本 (xs, ys) 上求 xi 处的值。"""
    n = len(xs)
    if n == 0:
        return float("nan")
    if n == 1:
        return ys[0]

    if xi <= xs[0]:                     # 左侧外推：用首段斜率
        x0, x1 = xs[0], xs[1]
        y0, y1 = ys[0], ys[1]
    elif xi >= xs[-1]:                  # 右侧外推：用末段斜率
        x0, x1 = xs[-2], xs[-1]
        y0, y1 = ys[-2], ys[-1]
    else:
        i = 0
        while i < n - 1 and xi > xs[i + 1]:
            i += 1
        x0, x1 = xs[i], xs[i + 1]
        y0, y1 = ys[i], ys[i + 1]

    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (xi - x0) / (x1 - x0)


def find_water_edge(z: list[float], H: float, dmin_idx: int,
                    direction: str) -> Optional[tuple[int, int]]:
    """从深泓点向一侧找首个跨越水位 H 的线段。

    direction: "left" | "right"
    返回 (i_wet, i_dry) 两个 0-based 索引；未找到返回 None。

    原 MATLAB：
        左：for i = idx0:-1:2,  if z(i) <= Hs && z(i-1) > Hs
        右：for i = idx0:Nz-1,  if z(i) <= Hs && z(i+1) > Hs
    """
    n = len(z)
    if direction == "left":
        for i0 in range(dmin_idx, 0, -1):
            if z[i0] <= H and z[i0 - 1] > H:
                return i0, i0 - 1
    else:
        for i0 in range(dmin_idx, n - 1):
            if z[i0] <= H and z[i0 + 1] > H:
                return i0, i0 + 1
    return None


def interpolate_xy(x: list[float], y: list[float], z: list[float],
                   i1: int, i2: int, H: float) -> tuple[float, float]:
    """在测点 i1(水下) 与 i2(水上) 之间线性插值出水位 H 处的平面坐标。"""
    z1, z2 = z[i1], z[i2]
    if z2 == z1:
        return x[i1], y[i1]
    t = (H - z1) / (z2 - z1)
    return x[i1] + t * (x[i2] - x[i1]), y[i1] + t * (y[i2] - y[i1])


def edge_point(sec: "Section", cfg: "Config", z: list[float], H: float,
               dmin_idx: int, direction: str,
               fallback_idx: int) -> tuple[Optional[tuple[float, float]], int]:
    """求水位 H 在一侧的水面交点，返回 (点, 图标样式)。

    找得到交点就线性插出平面坐标；找不到（该侧岸线全在水位以下）则退回该侧的
    最高点，图标样式标记为"未找到"，与原 MATLAB 行为一致。

    放在 interp 而不是各调用方里：设计水位、加高水位、一维推算水位都要用它，
    两处各写一遍必然分叉（历史上就是这么错的）。
    """
    edge = find_water_edge(z, H, dmin_idx, direction)
    if edge is not None:
        i1, i2 = edge
        return interpolate_xy(sec.x, sec.y, z, i1, i2, H), cfg.ICON_FOUND
    if fallback_idx >= 0:
        return (sec.x[fallback_idx], sec.y[fallback_idx]), cfg.ICON_NOT_FOUND
    return None, cfg.ICON_NOT_FOUND
