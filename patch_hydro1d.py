import math
from dataclasses import dataclass
from typing import Optional

from .model import ProfileLine, Section
from .geom import section_geom
from .terrain import analyze_terrain
from .rating import _compute_section_geom

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
    alpha: float = 1.0  # 动能修正系数

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


def get_node_state(sec: Section, Z: float, Q: float, dist: float, cfg) -> HydroNode:
    """根据给定的水位 Z 计算断面水力要素"""
    # 计算几何
    info = analyze_terrain(sec, cfg.steep_slope, cfg.turn_slope)
    geom_res = _compute_section_geom(sec, info, Z, cfg.compound_mode)

    A, P, B = geom_res.A, geom_res.P, geom_res.B
    A = max(A, 1e-6)  # 防止除零
    P = max(P, 1e-6)
    B = max(B, 1e-6)
    R = A / P
    V = Q / A
    Fr = V / math.sqrt(9.81 * (A / B))

    alpha = cfg.kinetic_alpha
    if cfg.kinetic_alpha_auto and cfg.compound_mode and len(geom_res.A_sub) > 1:
        # 动态计算 alpha = sum(K_i^3 / A_i^2) / (K_tot^3 / A_tot^2)
        k_tot = 0.0
        sum_k3_a2 = 0.0
        for i in range(len(geom_res.A_sub)):
            A_i = geom_res.A_sub[i]
            P_i = geom_res.P_sub[i]
            if A_i > 1e-6 and P_i > 1e-6:
                R_i = A_i / P_i
                n_i = sec.params.roughness_for_zone(i, len(geom_res.A_sub))
                if not n_i or math.isnan(n_i):
                    n_i = 0.03
                K_i = A_i * (R_i ** (2/3)) / n_i
                k_tot += K_i
                sum_k3_a2 += (K_i ** 3) / (A_i ** 2)
        if k_tot > 1e-6 and A > 1e-6:
            alpha = sum_k3_a2 / ((k_tot ** 3) / (A ** 2))

    return HydroNode(sec=sec, dist=dist, Q=Q, Z=Z, A=A, B=B, R=R, V=V, Fr=Fr, alpha=alpha)
