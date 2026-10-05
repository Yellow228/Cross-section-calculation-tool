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
from .terrain import analyze_terrain

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
    #: 流量模数（输水能力）。**构造时按分区算好**，不能在这里现算——
    #: 分区要 cfg + TerrainInfo，而 dataclass 字段拿不到它们。
    #: 分区口径必须与 `rating.py`（H~Q 曲线）一致，见 `zone_conductance`。
    K: float = 0.0

    @property
    def H(self) -> float:
        """总水头 (Z + V^2 / 2g)"""
        return self.Z + self.alpha * (self.V ** 2) / (2 * 9.81)

    @property
    def Sf(self) -> float:
        """摩阻比降"""
        if self.K <= 0:
            return 0.0
        return (self.Q / self.K) ** 2


def zone_conductance(sec: Section, Z: float, cfg, A_sub, P_sub
                     ) -> tuple[float, list[float]]:
    """按分区求流量模数，返回 (K_tot, [K_i, ...])。

    **这是唯一的口径**：`rating.py` 的 H~Q 曲线、动能修正系数 α、以及
    本模块的摩阻比降 Sf，都从这里取 K，三处必须一致。

    曾用 `A·R^(2/3)/n_uniform`（整断面一次算、统一糙率）算 Sf，
    而 H~Q 曲线用的是 `Σ A_i·R_i^(2/3)/n_i`（分区糙率）——
    同一个断面两套糙率。用户填了左右滩糙率时，两条链路的结果系统性偏离
    （实测 K 可差 50%，Sf 差 4 倍），而且不报错、图上看不出来。
    """
    k_tot = 0.0
    k_sub: list[float] = []
    n_zone = len(A_sub)
    for i in range(n_zone):
        A_i = A_sub[i]
        P_i = P_sub[i]
        if A_i > 1e-6 and P_i > 1e-6:
            R_i = A_i / P_i
            n_i = sec.params.roughness_for_zone(i, n_zone)
            if not n_i or math.isnan(n_i):
                n_i = 0.03
            k_i = A_i * (R_i ** (2/3)) / n_i
        else:
            k_i = 0.0
        k_sub.append(k_i)
        k_tot += k_i
    return k_tot, k_sub


def get_node_state(sec: Section, Z: float, Q: float, dist: float, cfg, info=None) -> HydroNode:
    """根据给定的水位 Z 计算断面水力要素"""
    # 计算几何
    if info is None:
        info = analyze_terrain(sec, cfg)

    if cfg.compound_mode:
        zones = info.zones
    else:
        zones = [(0, sec.n_points)]

    n_zone = len(zones)
    s_all, z_all = sec.s, sec.z

    A = 0.0
    P = 0.0
    B = 0.0

    A_sub = []
    P_sub = []

    for iz, (start, stop) in enumerate(zones):
        if stop - start < 2:
            A_sub.append(0.0)
            P_sub.append(0.0)
            continue
        xsub = s_all[start:stop]
        zsub = z_all[start:stop]

        a_i, p_i, b_i = section_geom(xsub, zsub, Z)

        # 单断面模式：顶宽改用「水面左右交点跨度」口径
        if not cfg.compound_mode:
            from .geom import surface_span
            xl, xr = surface_span(s_all, z_all, Z)
            b_i = (xr - xl) if (xl == xl and xr == xr) else 0.0

        A += max(a_i, 0.0)
        P += max(p_i, 0.0)
        B += max(b_i, 0.0)
        A_sub.append(max(a_i, 0.0))
        P_sub.append(max(p_i, 0.0))

    A = max(A, 1e-6)  # 防止除零
    P = max(P, 1e-6)
    B = max(B, 1e-6)
    R = A / P
    V = Q / A
    Fr = V / math.sqrt(9.81 * (A / B))

    # 流量模数：**无条件**按分区求（与 rating.py 的 H~Q 曲线同口径）。
    # 曾跟着 `kinetic_alpha_auto` 开关走，于是关掉 α 自动计算时
    # 两条链路又变回两套糙率——这是个独立的 bug，别再把 K 挂到那个开关上。
    k_tot, k_sub = zone_conductance(sec, Z, cfg, A_sub, P_sub)

    alpha = cfg.kinetic_alpha
    if cfg.kinetic_alpha_auto and cfg.compound_mode and len(A_sub) > 1:
        # alpha = sum(K_i^3 / A_i^2) / (K_tot^3 / A_tot^2)，复用上面同一份 K_i
        sum_k3_a2 = 0.0
        for i, K_i in enumerate(k_sub):
            A_i = A_sub[i]
            if A_i > 1e-6:
                sum_k3_a2 += (K_i ** 3) / (A_i ** 2)
        if k_tot > 1e-6 and A > 1e-6:
            alpha = sum_k3_a2 / ((k_tot ** 3) / (A ** 2))

    return HydroNode(sec=sec, dist=dist, Q=Q, Z=Z, A=A, B=B, R=R, V=V, Fr=Fr,
                     alpha=alpha, K=k_tot)



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


def _bisect_detail(low: float, high: float, f, tol: float,
                   rising: bool) -> tuple[float, dict]:
    """二分求根，返回 (解, 诊断)。

    ⚠ **为什么必须带诊断**：区间是固定死的 `[min(z)+0.01, max(z)+10]`。
    下游水位很高、回水上溯超过 `max(z)+10` 时，真实解落在区间之外，
    二分只会一路贴到边界并把**边界附近的假解**当答案返回——不报错、
    也没有任何提示，用户拿到的是一个平白低十几米的水位。

    判据（重要，别退回"看解离边界多远"的写法）：
    二分只要没提前收敛，区间宽度就会被压到 <= tol 才退出。此时解的
    位置距离原边界**恰好还有约 tol/2**（因为 mid 永远取区间中点），
    所以任何以 tol 为量级的绝对容差都测不出"贴边"——必须改成：
        **区间耗尽（宽度 <= tol）且残差未收敛** => 真解在区间外，结果不可信。
    纯残差发散（例如 1e14）也会落进这个分支，因为区间同样被耗尽。
    反之，真解恰好靠近边界但**确实收敛**时，`converged` 为真，不算贴边。

    rising=True 表示 f 随 x 递增（f>0 说明猜大了，往小收）。
    """
    orig_low, orig_high = low, high
    x = (low + high) / 2.0
    f_x = f(x)
    exhausted = False
    for _ in range(50):
        mid = (low + high) / 2.0
        f_mid = f(mid)
        x, f_x = mid, f_mid
        if abs(f_mid) < tol:
            break
        if (high - low) < tol:
            exhausted = True
            break
        if (f_mid > 0) == rising:
            high = mid
        else:
            low = mid

    converged = abs(f_x) < tol
    clipped = exhausted and not converged
    # 兜底：解因浮点原因正好落在原区间端点上，同样视为贴边。
    span = orig_high - orig_low
    margin = 1e-9 * max(1.0, abs(span))
    at_lower = abs(x - orig_low) <= max(margin, 0.5 * tol)
    at_upper = abs(x - orig_high) <= max(margin, 0.5 * tol)
    if at_lower or at_upper:
        clipped = True

    diag = {
        "converged": converged,
        "residual": f_x,
        "exhausted": exhausted,
        "at_lower": at_lower,
        "at_upper": at_upper,
        "clipped": clipped,
        "bracket": (low, high),
        "z_bounds": (orig_low, orig_high),
    }
    return x, diag


def standard_step_method_subcritical(
    sec_down: Section,
    Z_down: float,
    Q_down: float,
    dist_down: float,
    sec_up: Section,
    Q_up: float,
    dist_up: float,
    z_min: float,
    z_max: float,
    cfg,
    info_down=None,
    info_up=None,
    diag: Optional[dict] = None,
) -> float:
    """
    缓流：从下游推上游 (标准步长法)
    求上游断面水位 Z_up
    """
    node_d = get_node_state(sec_down, Z_down, Q_down, dist_down, cfg, info_down)
    L = abs(dist_up - dist_down)

    def energy_diff(Z_up_guess: float) -> float:
        node_u = get_node_state(sec_up, Z_up_guess, Q_up, dist_up, cfg, info_up)
        # 摩擦水头损失
        Sf_avg = (node_d.Sf + node_u.Sf) / 2
        hf = Sf_avg * L
        # 局部水头损失 (收缩/扩张)
        is_contraction = node_u.V > node_d.V
        default_loss = 0.1 if is_contraction else 0.3

        # 优先使用断面的手动局部水头损失系数
        sec_loss = sec_up.hydro_loss_contraction if is_contraction else sec_up.hydro_loss_expansion
        C_e = default_loss if sec_loss is None else sec_loss
        he = C_e * abs((node_u.V ** 2) / (2 * 9.81) - (node_d.V ** 2) / (2 * 9.81))

        # 能量方程: H_up = H_down + hf + he
        return node_u.H - (node_d.H + hf + he)

    # 使用割线法求根 (Bisection 在此处可能符号变化不明确，因单调性复杂)
    # 对于缓流，H_up 随 Z_up 严格单调增加
    # diff = H_up - (H_down + hf + he) 随 Z_up 递增 -> rising=True
    x, d = _bisect_detail(z_min, z_max, energy_diff, 1e-4, rising=True)
    if diag is not None:
        diag.update(d)
    return x


def standard_step_method_supercritical(
    sec_up: Section,
    Z_up: float,
    Q_up: float,
    dist_up: float,
    sec_down: Section,
    Q_down: float,
    dist_down: float,
    z_min: float,
    z_max: float,
    cfg,
    info_up=None,
    info_down=None,
    diag: Optional[dict] = None,
) -> float:
    """
    急流：从上游推下游 (标准步长法)
    求下游断面水位 Z_down
    """
    node_u = get_node_state(sec_up, Z_up, Q_up, dist_up, cfg, info_up)
    L = abs(dist_up - dist_down)

    def energy_diff(Z_down_guess: float) -> float:
        node_d = get_node_state(sec_down, Z_down_guess, Q_down, dist_down, cfg, info_down)
        Sf_avg = (node_d.Sf + node_u.Sf) / 2
        hf = Sf_avg * L
        is_contraction = node_d.V > node_u.V
        default_loss = 0.1 if is_contraction else 0.3

        # 优先使用断面的手动局部水头损失系数
        sec_loss = sec_down.hydro_loss_contraction if is_contraction else sec_down.hydro_loss_expansion
        C_e = default_loss if sec_loss is None else sec_loss
        he = C_e * abs((node_u.V ** 2) / (2 * 9.81) - (node_d.V ** 2) / (2 * 9.81))

        # 能量方程: H_up = H_down + hf + he
        return node_u.H - (node_d.H + hf + he)

    # 原逻辑：diff > 0 -> high = mid，与缓流同一个走向（rising=True）。
    # 这里**逐字保持原行为**，只把它换成带诊断的实现，不改数值。
    x, d = _bisect_detail(z_min, z_max, energy_diff, 1e-4, rising=True)
    if diag is not None:
        diag.update(d)
    return x


def downstream_index(sections: list[Section], infos: list) -> int:
    """找出最下游断面的索引。以河底最低点定下游，独立于排列顺序和重定基策略。"""
    if not sections:
        return 0
    # 使用 analyze_terrain 中的 info.dmin，如果 infos 没有对应，回退
    idx = 0
    min_z = infos[0].dmin if infos and infos[0] else min(sections[0].z)
    for i in range(1, len(sections)):
        z = infos[i].dmin if infos and i < len(infos) and infos[i] else min(sections[i].z)
        if z < min_z:
            min_z = z
            idx = i
    return idx


def compute_hydro1d_profile(line: ProfileLine, cfg, results: dict = None,
                            warnings: list[str] = None) -> list[float]:
    """计算整条纵断面线的一维水动力水位，返回推算的水位列表，与 sections 等长。

    warnings：出参。传入一个 list 时，会把"二分求根贴边/不收敛"这类
    **静默失败**写进去（core 层不认识界面，所以用出参照搬，
    与 `reader.load_folder` 的 `xy_report` 同一套路）。
    """
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
    Z_initials = []
    dists = []
    infos = []

    for i, sec in enumerate(sections):
        infos.append(analyze_terrain(sec, cfg))
        dist = line.chainage[i] if line.chainage else sec.s[0]
        dists.append(dist)
        # 获取现有的设计水位作为起推水位
        res_data = results.get(sec.name)
        if res_data and not math.isnan(res_data.design_level):
            z_init = res_data.design_level
        else:
            z_init = max(sec.z)
        Z_initials.append(z_init)

    ds_idx = downstream_index(sections, infos)
    us_idx = 0 if ds_idx == n - 1 else n - 1

    # 提取整个河段固定使用的流量 (取最上游断面)
    q_val = sections[us_idx].params.design_q
    if math.isnan(q_val):
        q_val = 50.0  # 与项目的默认占位符保持一致

    Qs = [q_val] * n

    regime = line.hydro1d_regime

    # 目前“混合流/自动”默认退化为缓流推算，因为纯混合流水面线推算需要复杂的水跃控制逻辑
    if regime in ("mixed", "auto"):
        regime = "subcritical"

    # 判断方向是否需要倒序遍历 (即索引 0 是下游还是 n-1 是下游)
    # 每次二分求根的诊断都攒在这里，最后统一汇总成一句可读的告警。
    _diags: list[tuple[str, dict]] = []

    def _note(i: int, d: dict) -> None:
        _diags.append((sections[i].name, d))

    if ds_idx == 0:
        # 索引 0 是下游
        if regime == "subcritical":
            # 缓流：从下游推上游 (0 -> n-1)
            res_levels[0] = Z_initials[0]
            for i in range(1, n):
                sec_up = sections[i]
                sec_down = sections[i-1]
                z_min_up = min(sec_up.z) + 0.01
                z_max_up = max(sec_up.z) + 10.0

                d: dict = {}
                Z_up = standard_step_method_subcritical(
                    sec_down, res_levels[i-1], Qs[i-1], dists[i-1],
                    sec_up, Qs[i], dists[i],
                    z_min_up, z_max_up, cfg,
                    info_down=infos[i-1], info_up=infos[i], diag=d
                )
                Z_crit = compute_critical_depth(sec_up, Qs[i], z_min_up, z_max_up)
                if Z_up < Z_crit:
                    d["crossed_critical"] = True
                _note(i, d)
                res_levels[i] = Z_up
        else:
            # 急流：从上游推下游 (n-1 -> 0)
            res_levels[-1] = Z_initials[-1]
            for i in range(n - 2, -1, -1):
                sec_up = sections[i+1]
                sec_down = sections[i]
                z_min_down = min(sec_down.z) + 0.01
                z_max_down = max(sec_down.z) + 10.0

                d = {}
                Z_down = standard_step_method_supercritical(
                    sec_up, res_levels[i+1], Qs[i+1], dists[i+1],
                    sec_down, Qs[i], dists[i],
                    z_min_down, z_max_down, cfg,
                    info_up=infos[i+1], info_down=infos[i], diag=d
                )
                Z_crit = compute_critical_depth(sec_down, Qs[i], z_min_down, z_max_down)
                if Z_down > Z_crit:
                    d["crossed_critical"] = True
                _note(i, d)
                res_levels[i] = Z_down
    else:
        # 索引 n-1 是下游
        if regime == "subcritical":
            # 缓流：从下游推上游 (n-1 -> 0)
            res_levels[-1] = Z_initials[-1]
            for i in range(n - 2, -1, -1):
                sec_up = sections[i]
                sec_down = sections[i+1]
                z_min_up = min(sec_up.z) + 0.01
                z_max_up = max(sec_up.z) + 10.0

                d = {}
                Z_up = standard_step_method_subcritical(
                    sec_down, res_levels[i+1], Qs[i+1], dists[i+1],
                    sec_up, Qs[i], dists[i],
                    z_min_up, z_max_up, cfg,
                    info_down=infos[i+1], info_up=infos[i], diag=d
                )
                Z_crit = compute_critical_depth(sec_up, Qs[i], z_min_up, z_max_up)
                if Z_up < Z_crit:
                    d["crossed_critical"] = True
                _note(i, d)
                res_levels[i] = Z_up
        else:
            # 急流：从上游推下游 (0 -> n-1)
            res_levels[0] = Z_initials[0]
            for i in range(1, n):
                sec_up = sections[i-1]
                sec_down = sections[i]
                z_min_down = min(sec_down.z) + 0.01
                z_max_down = max(sec_down.z) + 10.0

                d = {}
                Z_down = standard_step_method_supercritical(
                    sec_up, res_levels[i-1], Qs[i-1], dists[i-1],
                    sec_down, Qs[i], dists[i],
                    z_min_down, z_max_down, cfg,
                    info_up=infos[i-1], info_down=infos[i], diag=d
                )
                Z_crit = compute_critical_depth(sec_down, Qs[i], z_min_down, z_max_down)
                if Z_down > Z_crit:
                    d["crossed_critical"] = True
                _note(i, d)
                res_levels[i] = Z_down

    if warnings is not None:
        warnings.extend(_hydro1d_warnings(line, _diags))
    return res_levels


def _hydro1d_warnings(line: ProfileLine, diags: list[tuple[str, dict]]
                      ) -> list[str]:
    """把二分求根的诊断汇总成用户能照做的告警。

    只报**真出了事**的断面：要么解贴边（真解可能在区间外，水位不可信），
    要么残差没收敛（迭代用尽仍不满足能量方程），要么越过了临界水深。
    两种情况原实现都静默返回一个数字，用户完全没有线索。
    """
    clipped = [(nm, d) for nm, d in diags if d.get("clipped")]
    unconv = [(nm, d) for nm, d in diags if not d.get("converged")]
    crossed = [(nm, d) for nm, d in diags if d.get("crossed_critical")]

    out: list[str] = []

    if crossed:
        names = "、".join(nm for nm, _ in crossed[:5])
        more = f" 等 {len(crossed)} 个" if len(crossed) > 5 else ""
        out.append(
            f"一维推算：{names}{more} 断面的计算水位越过了临界水深，"
            f"可能出现了流态转变或需要进行混合流态分析。当前结果未作强制钳制，请核查。"
        )
    if clipped:
        names = "、".join(nm for nm, _ in clipped[:5])
        more = f" 等 {len(clipped)} 个" if len(clipped) > 5 else ""
        lo, hi = clipped[0][1].get("z_bounds", (float("nan"), float("nan")))
        where = "下界（最低测点附近）" if clipped[0][1].get("at_lower") \
            else "上界（最高测点 +10 m）"
        out.append(
            f"一维推算：{names}{more} 断面的水位解贴在搜索区间{where}，"
            f"结果不可信——通常表示该断面的回水高度超出了 "
            f"[{lo:.2f}, {hi:.2f}] 这个预设区间。"
            f"请检查下游边界水位/流量是否合理，必要时调整断面数据的测点范围。")
    if unconv:
        names = "、".join(nm for nm, _ in unconv[:5])
        more = f" 等 {len(unconv)} 个" if len(unconv) > 5 else ""
        worst = max(abs(d.get("residual") or 0.0) for _, d in unconv)
        out.append(
            f"一维推算：{names}{more} 断面未收敛（能量方程残差最大 {worst:.4g} m），"
            f"水位结果仅供参考。常见原因是断面间距过大或几何突变。")
    return out
