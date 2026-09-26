"""一维水动力推算模块（恒定流水面线推算）

遵循零第三方依赖约束（无 Numpy/Scipy），使用纯 Python 实现标准步长法（Standard Step Method）。
支持流态：
  - 缓流（subcritical）：从下游往上游推算
  - 急流（supercritical）：从上游往下游推算
  - 混合流 / 自动（mixed / auto）：暂时回退到缓流处理
"""

import math
from dataclasses import dataclass
from typing import Optional

from .model import ProfileLine, Section
from .geom import section_geom


@dataclass
class HydroNode:
    """一维推算中的断面状态"""
    sec: Section
    dist: float    # 起点距 / 里程
    Q: float       # 流量
    Z: float       # 水位
    A: float       # 过水面积
    B: float       # 顶宽
    R: float       # 水力半径
    V: float       # 流速
    Fr: float      # 弗劳德数
    alpha: float = 1.0  # 动能修正系数 (暂取 1.0)

    @property
    def H(self) -> float:
        """总水头 (Z + V^2 / 2g)"""
        return self.Z + self.alpha * (self.V ** 2) / (2 * 9.81)

    @property
    def K(self) -> float:
        """流量模数 (输水能力)"""
        n = self.sec.params.roughness
        if not n or math.isnan(n):
            n = 0.03
        return self.A * (self.R ** (2/3)) / n

    @property
    def Sf(self) -> float:
        """摩阻比降"""
        k = self.K
        if k <= 0:
            return 0.0
        return (self.Q / k) ** 2


def get_node_state(sec: Section, Z: float, Q: float, dist: float) -> HydroNode:
    """根据给定的水位 Z 计算断面水力要素"""
    A, P, B = section_geom(sec.s, sec.z, Z)
    A = max(A, 1e-6)  # 防止除零
    P = max(P, 1e-6)
    B = max(B, 1e-6)
    R = A / P
    V = Q / A
    Fr = V / math.sqrt(9.81 * (A / B))
    return HydroNode(sec=sec, dist=dist, Q=Q, Z=Z, A=A, B=B, R=R, V=V, Fr=Fr)


def compute_critical_depth(sec: Section, Q: float, z_min: float, z_max: float) -> float:
    """计算临界水深（Fr=1 的水位）"""
    def target_func(z: float) -> float:
        A, P, B = section_geom(sec.s, sec.z, z)
        A = max(A, 1e-6)
        B = max(B, 1e-6)
        # 临界流方程: Q^2 * B / (g * A^3) = 1  => Q^2 * B - g * A^3 = 0
        return (Q ** 2) * B - 9.81 * (A ** 3)

    # 二分法求根
    low, high = z_min, z_max
    for _ in range(50):
        mid = (low + high) / 2
        f_mid = target_func(mid)
        if abs(f_mid) < 1e-3 or (high - low) < 1e-3:
            return mid
        # 随着 z 增加，A 增加得比 B 快，通常 f(z) 随 z 增加而减小（单调递减）
        if f_mid > 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def standard_step_method_subcritical(
    sec_down: Section,
    Z_down: float,
    Q_down: float,
    dist_down: float,
    sec_up: Section,
    Q_up: float,
    dist_up: float,
    z_min: float,
    z_max: float
) -> float:
    """
    缓流：从下游推上游 (标准步长法)
    求上游断面水位 Z_up
    """
    node_d = get_node_state(sec_down, Z_down, Q_down, dist_down)
    L = abs(dist_up - dist_down)

    def energy_diff(Z_up_guess: float) -> float:
        node_u = get_node_state(sec_up, Z_up_guess, Q_up, dist_up)
        # 摩擦水头损失
        Sf_avg = (node_d.Sf + node_u.Sf) / 2
        hf = Sf_avg * L
        # 局部水头损失 (收缩/扩张)
        C_e = 0.1 if node_u.V > node_d.V else 0.3  # 简单经验系数 (收缩0.1, 扩张0.3)
        he = C_e * abs((node_u.V ** 2) / (2 * 9.81) - (node_d.V ** 2) / (2 * 9.81))

        # 能量方程: H_up = H_down + hf + he
        return node_u.H - (node_d.H + hf + he)

    # 使用割线法求根 (Bisection 在此处可能符号变化不明确，因单调性复杂)
    # 对于缓流，H_up 随 Z_up 严格单调增加
    low, high = z_min, z_max
    for _ in range(50):
        mid = (low + high) / 2
        diff = energy_diff(mid)
        if abs(diff) < 1e-4 or (high - low) < 1e-4:
            return mid
        if diff > 0:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def standard_step_method_supercritical(
    sec_up: Section,
    Z_up: float,
    Q_up: float,
    dist_up: float,
    sec_down: Section,
    Q_down: float,
    dist_down: float,
    z_min: float,
    z_max: float
) -> float:
    """
    急流：从上游推下游 (标准步长法)
    求下游断面水位 Z_down
    """
    node_u = get_node_state(sec_up, Z_up, Q_up, dist_up)
    L = abs(dist_up - dist_down)

    def energy_diff(Z_down_guess: float) -> float:
        node_d = get_node_state(sec_down, Z_down_guess, Q_down, dist_down)
        Sf_avg = (node_d.Sf + node_u.Sf) / 2
        hf = Sf_avg * L
        C_e = 0.1 if node_d.V > node_u.V else 0.3
        he = C_e * abs((node_u.V ** 2) / (2 * 9.81) - (node_d.V ** 2) / (2 * 9.81))

        # 能量方程: H_up = H_down + hf + he
        return node_u.H - (node_d.H + hf + he)

    low, high = z_min, z_max
    for _ in range(50):
        mid = (low + high) / 2
        diff = energy_diff(mid)
        if abs(diff) < 1e-4 or (high - low) < 1e-4:
            return mid
        # 急流中，H 随 Z 的变化单调性在临界水深附近反转
        # 一般在急流区 (Z < Zc)，Z增加，H减小
        # target_func(Z) = H_up - (H_down + hf + he)
        # 当 Z_down_guess 增加时，H_down 减小，hf 减小，因此 diff 增加
        # 所以 diff 是随 Z 递增的。
        if diff > 0:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def compute_hydro1d_profile(line: ProfileLine, results: dict = None) -> list[float]:
    """计算整条纵断面线的一维水动力水位，返回推算的水位列表，与 sections 等长"""
    sections = line.sections
    n = len(sections)
    results = results or {}

    if n < 2:
        # 断层面数量不足，无法推算，回退到原设计水位
        res = [float('nan')] * n
        for i, sec in enumerate(sections):
            res_data = results.get(sec.name)
            if res_data and not math.isnan(res_data.design_level):
                res[i] = res_data.design_level
        return res

    # 初始化返回数组
    res_levels = [float('nan')] * n

    # 提取各断面参数
    Qs = []
    Z_initials = []
    dists = []

    for i, sec in enumerate(sections):
        Qs.append(sec.params.design_q if not math.isnan(sec.params.design_q) else 100.0)
        dist = line.chainage[i] if line.chainage else sec.s[0]
        dists.append(dist)
        # 获取现有的设计水位作为起推水位
        res_data = results.get(sec.name)
        if res_data and not math.isnan(res_data.design_level):
            z_init = res_data.design_level
        else:
            z_init = max(sec.z)
        Z_initials.append(z_init)

    regime = line.hydro1d_regime

    # 目前“混合流/自动”默认退化为缓流推算，因为纯混合流水面线推算需要复杂的水跃控制逻辑
    if regime in ("mixed", "auto"):
        regime = "subcritical"

    if regime == "subcritical":
        # 缓流：从下游推上游 (索引 n-1 -> 0)
        res_levels[-1] = Z_initials[-1]  # 下游起推水位
        for i in range(n - 2, -1, -1):
            sec_up = sections[i]
            sec_down = sections[i+1]

            z_min_up = min(sec_up.z) + 0.01
            z_max_up = max(sec_up.z) + 10.0

            Z_up = standard_step_method_subcritical(
                sec_down, res_levels[i+1], Qs[i+1], dists[i+1],
                sec_up, Qs[i], dists[i],
                z_min_up, z_max_up
            )
            # 防止跌破临界水深
            Z_crit = compute_critical_depth(sec_up, Qs[i], z_min_up, z_max_up)
            res_levels[i] = max(Z_up, Z_crit)

    elif regime == "supercritical":
        # 急流：从上游推下游 (索引 0 -> n-1)
        res_levels[0] = Z_initials[0]  # 上游起推水位
        for i in range(1, n):
            sec_up = sections[i-1]
            sec_down = sections[i]

            z_min_down = min(sec_down.z) + 0.01
            z_max_down = max(sec_down.z) + 10.0

            Z_down = standard_step_method_supercritical(
                sec_up, res_levels[i-1], Qs[i-1], dists[i-1],
                sec_down, Qs[i], dists[i],
                z_min_down, z_max_down
            )
            Z_crit = compute_critical_depth(sec_down, Qs[i], z_min_down, z_max_down)
            res_levels[i] = min(Z_down, Z_crit)

    return res_levels
