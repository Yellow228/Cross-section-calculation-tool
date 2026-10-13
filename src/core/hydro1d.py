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
from .slope import estimate_line_slope

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
    Z_crit: float = float('nan') # 临界水深/水位
    hl: Optional[float] = None   # 上游至该断面的水头损失 (Head Loss)

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
            n_i = max(n_i, 1e-6)
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
    Fr = V / max(math.sqrt(9.81 * (A / B)), 1e-6)

    # 流量模数：**无条件**按分区求（与 rating.py 的 H~Q 曲线同口径）。
    # 曾跟着 `kinetic_alpha_auto` 开关走，于是关掉 α 自动计算时
    # 两条链路又变回两套糙率——这是个独立的 bug，别再把 K 挂到那个开关上。
    k_tot, k_sub = zone_conductance(sec, Z, cfg, A_sub, P_sub)

    alpha = cfg.kinetic_alpha
    if cfg.kinetic_alpha_auto and cfg.compound_mode and len(A_sub) > 1:
        # alpha = sum(K_i^3 / A_i^2) / (K_tot^3 / A_tot^2)，复用上面同一份 K_i
        # 严谨对标 HEC-RAS：A_tot 需是有效算入分区的面积总和，防止因微小舍入或区间裁剪导致与总 A 产生漂移
        sum_k3_a2 = 0.0
        valid_A_tot = 0.0
        for i, K_i in enumerate(k_sub):
            A_i = A_sub[i]
            if A_i > 1e-6:
                sum_k3_a2 += (K_i ** 3) / (A_i ** 2)
                valid_A_tot += A_i
        if k_tot > 1e-6 and valid_A_tot > 1e-6:
            alpha = sum_k3_a2 / ((k_tot ** 3) / (valid_A_tot ** 2))

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


def _secant_detail(low: float, high: float, f, tol: float,
                   z_crit: float = float('nan')) -> tuple[float, dict]:
    """割线法求根，最大迭代20次。返回 (解, 诊断)。

    不收敛处理：设置最大迭代次数（20 次）。当超出迭代次数不收敛时，
    回退为临界水深（Critical Depth）尝试继续推进；若全局发散则明确标记计算失败。
    """
    # 因为能量方程 f(z) = H_up - (H_down + hf + he) 可能非常非线性，在低水位容易跑偏。
    # 用几步二分法作为种子，找一个好的区间进行割线法，或者混合使用。
    # 结合了 Brent's method 思想的简化版。
    a = low
    b = high
    fa = f(a)
    fb = f(b)

    # 二分预处理，尝试收缩边界，使得符号相反
    for _ in range(10):
        if math.isnan(fa) or math.isnan(fb):
            break
        if fa * fb < 0:
            break
        mid = (a + b) / 2
        fmid = f(mid)
        if abs(fmid) < tol:
            return mid, {"converged": True, "residual": fmid, "exhausted": False, "clipped": False, "fallback_to_critical": False, "bracket": (low, high), "z_bounds": (low, high)}

        # 尝试猜测方向（一般情况随着z增大，H变大，f(z)也变大）
        if fmid > 0:
            b = mid
            fb = fmid
        else:
            a = mid
            fa = fmid

    x0 = a
    x1 = b
    f0 = fa
    f1 = fb

    exhausted = False
    converged = False
    max_iter = 20
    x_new = x1

    for _ in range(max_iter):
        if math.isnan(f1) or math.isinf(f1):
            break

        if abs(f1) < tol:
            converged = True
            break

        diff = f1 - f0
        if abs(diff) < 1e-12:
            exhausted = True
            break

        x_new = x1 - f1 * (x1 - x0) / diff

        # 如果新解跑出去了 [a, b] 这个有根区间，退化成二分
        if x_new < min(x0, x1) - 1.0 or x_new > max(x0, x1) + 1.0:
             x_new = (x0 + x1) / 2.0

        x0, f0 = x1, f1
        x1 = x_new
        try:
            f1 = f(x1)
        except:
            f1 = float('inf')

    converged = abs(f1) < tol
    clipped = False
    fallback_used = False

    if not converged:
        exhausted = True
        # 不收敛时直接返回 NaN，不回退临界水深，避免伪造平滑解
        x_new = float('nan')

    if math.isnan(x_new) or math.isinf(x_new):
        exhausted = True
        x_new = float('nan')

    diag = {
        "converged": converged,
        "residual": f1 if not math.isnan(x_new) else float('inf'),
        "exhausted": exhausted,
        "at_lower": False, # Retained for compatibility
        "at_upper": False, # Retained for compatibility
        "clipped": clipped,
        "fallback_to_critical": fallback_used,
        "bracket": (low, high),
        "z_bounds": (low, high),
    }
    return x_new, diag


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
    Z_crit: float = float('nan'),
) -> float:
    """
    缓流：从下游推上游 (标准步长法)
    求上游断面水位 Z_up
    """
    node_d = get_node_state(sec_down, Z_down, Q_down, dist_down, cfg, info_down)
    L = abs(dist_up - dist_down)

    def energy_diff(Z_up_guess: float) -> float:
        node_u = get_node_state(sec_up, Z_up_guess, Q_up, dist_up, cfg, info_up)
        # 摩擦水头损失: 对标 HEC-RAS 默认使用 平均输水能力法 (Average Conveyance Equation)
        K_avg = (node_d.K + node_u.K) / 2.0
        Q_avg = (Q_down + Q_up) / 2.0
        Sf_avg = (Q_avg / K_avg) ** 2 if K_avg > 1e-6 else 0.0
        hf = Sf_avg * L
        # 局部水头损失 (收缩/扩张)
        is_contraction = node_d.V > node_u.V
        default_loss = 0.1 if is_contraction else 0.3

        # 优先使用断面的手动局部水头损失系数
        sec_loss = sec_up.hydro_loss_contraction if is_contraction else sec_up.hydro_loss_expansion
        C_e = default_loss if sec_loss is None else sec_loss
        he = C_e * abs(node_u.alpha * (node_u.V ** 2) / (2 * 9.81) - node_d.alpha * (node_d.V ** 2) / (2 * 9.81))

        # 能量方程: H_up = H_down + hf + he
        return node_u.H - (node_d.H + hf + he)

    # 使用割线法求根 (Bisection 在此处可能符号变化不明确，因单调性复杂)
    # 对于缓流，H_up 随 Z_up 严格单调增加
    # diff = H_up - (H_down + hf + he) 随 Z_up 递增 -> rising=True
    x, d = _secant_detail(z_min, z_max, energy_diff, 1e-4, z_crit=Z_crit)
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
    Z_crit: float = float('nan'),
) -> float:
    """
    急流：从上游推下游 (标准步长法)
    求下游断面水位 Z_down
    """
    node_u = get_node_state(sec_up, Z_up, Q_up, dist_up, cfg, info_up)
    L = abs(dist_up - dist_down)

    def energy_diff(Z_down_guess: float) -> float:
        node_d = get_node_state(sec_down, Z_down_guess, Q_down, dist_down, cfg, info_down)
        # 摩擦水头损失: 对标 HEC-RAS 默认使用 平均输水能力法 (Average Conveyance Equation)
        K_avg = (node_d.K + node_u.K) / 2.0
        Q_avg = (Q_down + Q_up) / 2.0
        Sf_avg = (Q_avg / K_avg) ** 2 if K_avg > 1e-6 else 0.0
        hf = Sf_avg * L
        is_contraction = node_d.V > node_u.V
        default_loss = 0.1 if is_contraction else 0.3

        # 优先使用断面的手动局部水头损失系数
        sec_loss = sec_down.hydro_loss_contraction if is_contraction else sec_down.hydro_loss_expansion
        C_e = default_loss if sec_loss is None else sec_loss
        he = C_e * abs(node_u.alpha * (node_u.V ** 2) / (2 * 9.81) - node_d.alpha * (node_d.V ** 2) / (2 * 9.81))

        # 能量方程: H_up = H_down + hf + he
        return node_u.H - (node_d.H + hf + he)

    # 原逻辑：diff > 0 -> high = mid，与缓流同一个走向（rising=True）。
    # 这里**逐字保持原行为**，只把它换成带诊断的实现，不改数值。
    x, d = _secant_detail(z_min, z_max, energy_diff, 1e-4, z_crit=Z_crit)
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



def compute_normal_depth(sec: Section, Q: float, S0: float, z_min: float, z_max: float, cfg, info=None) -> float:
    """使用曼宁公式 Q = K * sqrt(S0) 反算正常水深。"""
    if S0 <= 0:
        return float('nan')

    target_K = Q / math.sqrt(S0)

    def f(z: float) -> float:
        node = get_node_state(sec, z, Q, 0.0, cfg, info)
        return node.K - target_K

    # K 随 z 递增，所以可以使用二分法或者割线法。这里直接用简单的二分。
    low, high = z_min, z_max
    for _ in range(50):
        mid = (low + high) / 2
        f_mid = f(mid)
        if abs(f_mid) < 1e-3 or (high - low) < 1e-3:
            return mid
        if f_mid > 0:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def compute_hydro1d_profile(line: ProfileLine, cfg, results: dict = None,
                            warnings: list[str] = None) -> tuple[list[float], list['HydroNode']]:
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
        return res, [None] * n

    # 初始化返回数组
    res_levels = [float('nan')] * n
    res_nodes: list[Optional[HydroNode]] = [None] * n

    # 提取各断面参数
    Z_initials = []
    dists = []
    infos = []

    for i, sec in enumerate(sections):
        infos.append(analyze_terrain(sec, cfg))
        dist = line.chainage[i] if line.chainage else sec.s[0]
        dists.append(dist)

    ds_idx = downstream_index(sections, infos)
    us_idx = 0 if ds_idx == n - 1 else n - 1

    # 计算全线平均比降，用于正常水深起推
    slope_est = estimate_line_slope(line.profile, line_name=line.name)
    S0 = slope_est.slope("jc") if slope_est.ok else float('nan')
    if not math.isfinite(S0) or S0 <= 0:
        S0 = 0.001 # Fallback dummy slope if totally invalid

    # 提取整个河段固定使用的流量 (取最上游断面)
    q_val = sections[us_idx].params.design_q
    if math.isnan(q_val):
        q_val = 50.0  # 与项目的默认占位符保持一致

    Qs = [q_val] * n

    for i, sec in enumerate(sections):
        res_data = results.get(sec.name)
        if res_data and not math.isnan(res_data.design_level):
            z_init = res_data.design_level
        else:
            # 边界断面如果是起推断面，使用正常水深 (Normal Depth)
            # 我们不知道当前断面是不是起推断面，所以只要没填就尝试使用正常水深兜底，而不是 max(sec.z)
            z_init = compute_normal_depth(sec, Qs[i], S0, min(sec.z) + 0.01, max(sec.z) + 10.0, cfg, infos[i])
            if math.isnan(z_init):
                z_init = max(sec.z)
        Z_initials.append(z_init)

    regime = line.hydro1d_regime

    _diags: list[tuple[str, dict]] = []

    def _note(i: int, d: dict) -> None:
        _diags.append((sections[i].name, d))

    def calc_hl_and_warnings():
        spacing_issues = []
        if ds_idx == 0:
            for i in range(n - 1):
                dn = res_nodes[i]
                up = res_nodes[i+1]
                if dn and up:
                    dn.hl = up.H - dn.H
                    if dn.hl < -1e-4:
                        # 能量反号，通常发生在混合流流态转换处处理不当。标为 NaN
                        dn.hl = float('nan')
                        res_levels[i] = float('nan') # downstream node invalid
                        # res_nodes[i] is already 'dn', but modifying res_levels will signal invalidity
                        spacing_issues.append((dn.sec.name, up.sec.name + " (能量反号)"))
                    dh_kin = abs(up.alpha * (up.V**2) / (2 * 9.81) - dn.alpha * (dn.V**2) / (2 * 9.81))
                    dist = abs(up.dist - dn.dist)
                    D = (up.A / up.B + dn.A / dn.B) / 2.0 if up.B > 0 and dn.B > 0 else 1.0
                    local_S0 = abs(min(up.sec.z) - min(dn.sec.z)) / dist if dist > 0 else 0.0
                    max_spacing = 0.15 * D / local_S0 if local_S0 > 1e-6 else float('inf')
                    if dh_kin > 0.15 or (dist > 0 and dist > max_spacing):
                        spacing_issues.append((dn.sec.name, up.sec.name))
        else:
            for i in range(1, n):
                dn = res_nodes[i]
                up = res_nodes[i-1]
                if dn and up:
                    dn.hl = up.H - dn.H
                    if dn.hl < -1e-4:
                        # 能量反号，通常发生在混合流流态转换处处理不当。标为 NaN
                        dn.hl = float('nan')
                        res_levels[i] = float('nan') # downstream node invalid
                        # res_nodes[i] is already 'dn', but modifying res_levels will signal invalidity
                        spacing_issues.append((dn.sec.name, up.sec.name + " (能量反号)"))
                    dh_kin = abs(up.alpha * (up.V**2) / (2 * 9.81) - dn.alpha * (dn.V**2) / (2 * 9.81))
                    dist = abs(up.dist - dn.dist)
                    D = (up.A / up.B + dn.A / dn.B) / 2.0 if up.B > 0 and dn.B > 0 else 1.0
                    local_S0 = abs(min(up.sec.z) - min(dn.sec.z)) / dist if dist > 0 else 0.0
                    max_spacing = 0.15 * D / local_S0 if local_S0 > 1e-6 else float('inf')
                    if dh_kin > 0.15 or (dist > 0 and dist > max_spacing):
                        spacing_issues.append((dn.sec.name, up.sec.name))
        return spacing_issues


    def calc_hl():
        # 根据流向计算水头损失 hl = H_upstream - H_downstream
        if ds_idx == 0:
            for i in range(n - 1):
                if res_nodes[i] and res_nodes[i+1]:
                    res_nodes[i].hl = res_nodes[i+1].H - res_nodes[i].H
        else:
            for i in range(1, n):
                if res_nodes[i] and res_nodes[i-1]:
                    res_nodes[i].hl = res_nodes[i-1].H - res_nodes[i].H

    if regime in ("mixed", "auto"):
        # 混合流推算 (Two-Pass Algorithm)
        res_levels_sub = [float('nan')] * n
        res_nodes_sub = [None] * n

        # 第一遍：缓流 (从下游推上游)
        if ds_idx == 0:
            res_levels_sub[0] = Z_initials[0]
            for i in range(1, n):
                sec_up, sec_down = sections[i], sections[i-1]
                z_min_up, z_max_up = min(sec_up.z) + 0.01, max(sec_up.z) + 10.0
                d: dict = {}
                Z_crit = compute_critical_depth(sec_up, Qs[i], z_min_up, z_max_up)
                Z_up = standard_step_method_subcritical(
                    sec_down, res_levels_sub[i-1], Qs[i-1], dists[i-1],
                    sec_up, Qs[i], dists[i], z_min_up, z_max_up, cfg,
                    info_down=infos[i-1], info_up=infos[i], diag=d, Z_crit=Z_crit
                )
                if math.isnan(Z_up):
                    d["crossed_critical"] = True
                    Z_up = Z_crit
                elif Z_up < Z_crit or d.get("fallback_to_critical"):
                    d["crossed_critical"] = True
                    Z_up = Z_crit
                _note(i, d)
                res_levels_sub[i] = Z_up
        else:
            res_levels_sub[-1] = Z_initials[-1]
            for i in range(n - 2, -1, -1):
                sec_up, sec_down = sections[i], sections[i+1]
                z_min_up, z_max_up = min(sec_up.z) + 0.01, max(sec_up.z) + 10.0
                d = {}
                Z_crit = compute_critical_depth(sec_up, Qs[i], z_min_up, z_max_up)
                Z_up = standard_step_method_subcritical(
                    sec_down, res_levels_sub[i+1], Qs[i+1], dists[i+1],
                    sec_up, Qs[i], dists[i], z_min_up, z_max_up, cfg,
                    info_down=infos[i+1], info_up=infos[i], diag=d, Z_crit=Z_crit
                )
                if math.isnan(Z_up):
                    d["crossed_critical"] = True
                    Z_up = Z_crit
                elif Z_up < Z_crit or d.get("fallback_to_critical"):
                    d["crossed_critical"] = True
                    Z_up = Z_crit
                _note(i, d)
                res_levels_sub[i] = Z_up

        for i in range(n):
            if not math.isnan(res_levels_sub[i]):
                node = get_node_state(sections[i], res_levels_sub[i], Qs[i], dists[i], cfg, infos[i])
                node.Z_crit = compute_critical_depth(sections[i], Qs[i], min(sections[i].z)+0.01, max(sections[i].z)+10.0)
                res_nodes_sub[i] = node

        # 第二遍：急流 (从上游推下游)
        res_levels_sup = [float('nan')] * n
        res_nodes_sup = [None] * n
        if ds_idx == 0:
            res_levels_sup[-1] = Z_initials[-1]
            for i in range(n - 2, -1, -1):
                sec_up, sec_down = sections[i+1], sections[i]
                z_min_down, z_max_down = min(sec_down.z) + 0.01, max(sec_down.z) + 10.0
                d = {}
                Z_crit = compute_critical_depth(sec_down, Qs[i], z_min_down, z_max_down)
                Z_down = standard_step_method_supercritical(
                    sec_up, res_levels_sup[i+1], Qs[i+1], dists[i+1],
                    sec_down, Qs[i], dists[i], z_min_down, z_max_down, cfg,
                    info_up=infos[i+1], info_down=infos[i], diag=d, Z_crit=Z_crit
                )
                if math.isnan(Z_down):
                    d["crossed_critical"] = True
                    Z_down = Z_crit
                elif Z_down > Z_crit or d.get("fallback_to_critical"):
                    d["crossed_critical"] = True
                    Z_down = Z_crit
                _note(i, d)
                res_levels_sup[i] = Z_down
        else:
            res_levels_sup[0] = Z_initials[0]
            for i in range(1, n):
                sec_up, sec_down = sections[i-1], sections[i]
                z_min_down, z_max_down = min(sec_down.z) + 0.01, max(sec_down.z) + 10.0
                d = {}
                Z_crit = compute_critical_depth(sec_down, Qs[i], z_min_down, z_max_down)
                Z_down = standard_step_method_supercritical(
                    sec_up, res_levels_sup[i-1], Qs[i-1], dists[i-1],
                    sec_down, Qs[i], dists[i], z_min_down, z_max_down, cfg,
                    info_up=infos[i-1], info_down=infos[i], diag=d, Z_crit=Z_crit
                )
                if math.isnan(Z_down):
                    d["crossed_critical"] = True
                    Z_down = Z_crit
                elif Z_down > Z_crit or d.get("fallback_to_critical"):
                    d["crossed_critical"] = True
                    Z_down = Z_crit
                _note(i, d)
                res_levels_sup[i] = Z_down

        for i in range(n):
            if not math.isnan(res_levels_sup[i]):
                node = get_node_state(sections[i], res_levels_sup[i], Qs[i], dists[i], cfg, infos[i])
                node.Z_crit = res_nodes_sub[i].Z_crit if res_nodes_sub[i] else compute_critical_depth(sections[i], Qs[i], min(sections[i].z)+0.01, max(sections[i].z)+10.0)
                res_nodes_sup[i] = node

        # 根据比力 (Specific Force) 合并水面线
        for i in range(n):
            z_sub = res_levels_sub[i]
            z_sup = res_levels_sup[i]
            if math.isnan(z_sub) and not math.isnan(z_sup):
                res_levels[i] = z_sup
                res_nodes[i] = res_nodes_sup[i]
            elif not math.isnan(z_sub) and math.isnan(z_sup):
                res_levels[i] = z_sub
                res_nodes[i] = res_nodes_sub[i]
            elif not math.isnan(z_sub) and not math.isnan(z_sup):
                M_sub = calculate_specific_force(sections[i], z_sub, Qs[i], res_nodes_sub[i].alpha, cfg)
                M_sup = calculate_specific_force(sections[i], z_sup, Qs[i], res_nodes_sup[i].alpha, cfg)
                # 水跃条件 M_sub >= M_sup
                if M_sub >= M_sup:
                    res_levels[i] = z_sub
                    res_nodes[i] = res_nodes_sub[i]
                else:
                    res_levels[i] = z_sup
                    res_nodes[i] = res_nodes_sup[i]

        spacing_issues = calc_hl_and_warnings()
    else:
        # 单一流态推算 (缓流或急流)
        if ds_idx == 0:
            if regime == "subcritical":
                res_levels[0] = Z_initials[0]
                for i in range(1, n):
                    sec_up, sec_down = sections[i], sections[i-1]
                    z_min_up, z_max_up = min(sec_up.z) + 0.01, max(sec_up.z) + 10.0
                    d: dict = {}
                    Z_crit = compute_critical_depth(sec_up, Qs[i], z_min_up, z_max_up)
                    Z_up = standard_step_method_subcritical(
                        sec_down, res_levels[i-1], Qs[i-1], dists[i-1],
                        sec_up, Qs[i], dists[i], z_min_up, z_max_up, cfg,
                        info_down=infos[i-1], info_up=infos[i], diag=d, Z_crit=Z_crit
                    )
                    if Z_up < Z_crit or d.get("fallback_to_critical"):
                        d["crossed_critical"] = True
                        Z_up = Z_crit
                    _note(i, d)
                    res_levels[i] = Z_up
            else:
                res_levels[-1] = Z_initials[-1]
                for i in range(n - 2, -1, -1):
                    sec_up, sec_down = sections[i+1], sections[i]
                    z_min_down, z_max_down = min(sec_down.z) + 0.01, max(sec_down.z) + 10.0
                    d = {}
                    Z_crit = compute_critical_depth(sec_down, Qs[i], z_min_down, z_max_down)
                    Z_down = standard_step_method_supercritical(
                        sec_up, res_levels[i+1], Qs[i+1], dists[i+1],
                        sec_down, Qs[i], dists[i], z_min_down, z_max_down, cfg,
                        info_up=infos[i+1], info_down=infos[i], diag=d, Z_crit=Z_crit
                    )
                    if math.isnan(Z_down):
                        d["crossed_critical"] = True
                        Z_down = Z_crit
                    elif Z_down > Z_crit or d.get("fallback_to_critical"):
                        d["crossed_critical"] = True
                        Z_down = Z_crit
                    _note(i, d)
                    res_levels[i] = Z_down
        else:
            if regime == "subcritical":
                res_levels[-1] = Z_initials[-1]
                for i in range(n - 2, -1, -1):
                    sec_up, sec_down = sections[i], sections[i+1]
                    z_min_up, z_max_up = min(sec_up.z) + 0.01, max(sec_up.z) + 10.0
                    d = {}
                    Z_crit = compute_critical_depth(sec_up, Qs[i], z_min_up, z_max_up)
                    Z_up = standard_step_method_subcritical(
                        sec_down, res_levels[i+1], Qs[i+1], dists[i+1],
                        sec_up, Qs[i], dists[i], z_min_up, z_max_up, cfg,
                        info_down=infos[i+1], info_up=infos[i], diag=d, Z_crit=Z_crit
                    )
                    if Z_up < Z_crit or d.get("fallback_to_critical"):
                        d["crossed_critical"] = True
                        Z_up = Z_crit
                    _note(i, d)
                    res_levels[i] = Z_up
            else:
                res_levels[0] = Z_initials[0]
                for i in range(1, n):
                    sec_up, sec_down = sections[i-1], sections[i]
                    z_min_down, z_max_down = min(sec_down.z) + 0.01, max(sec_down.z) + 10.0
                    d = {}
                    Z_crit = compute_critical_depth(sec_down, Qs[i], z_min_down, z_max_down)
                    Z_down = standard_step_method_supercritical(
                        sec_up, res_levels[i-1], Qs[i-1], dists[i-1],
                        sec_down, Qs[i], dists[i], z_min_down, z_max_down, cfg,
                        info_up=infos[i-1], info_down=infos[i], diag=d, Z_crit=Z_crit
                    )
                    if math.isnan(Z_down):
                        d["crossed_critical"] = True
                        Z_down = Z_crit
                    elif Z_down > Z_crit or d.get("fallback_to_critical"):
                        d["crossed_critical"] = True
                        Z_down = Z_crit
                    _note(i, d)
                    res_levels[i] = Z_down

        for i in range(n):
            if not math.isnan(res_levels[i]):
                node = get_node_state(sections[i], res_levels[i], Qs[i], dists[i], cfg, infos[i])
                node.Z_crit = compute_critical_depth(sections[i], Qs[i], min(sections[i].z)+0.01, max(sections[i].z)+10.0)
                res_nodes[i] = node
        spacing_issues = calc_hl_and_warnings()


    if 'spacing_issues' not in locals(): spacing_issues = []
    if warnings is not None:
        warnings.extend(_hydro1d_warnings(line, _diags, spacing_issues))
    return res_levels, res_nodes



def calculate_specific_force(sec: Section, Z: float, Q: float, beta: float, cfg) -> float:
    """计算断面的比力 M = A*y_bar + beta * Q^2 / (g*A)。"""
    A, P, B = section_geom(sec.s, sec.z, Z)
    if A <= 1e-6:
        return float('inf')

    # 计算 A*y_bar (First moment of area about water surface)
    # y_bar = 面积中心到水面的深度
    # 对于多边形积分：Sum ( (H - z_centroid_i) * A_i )
    # 为简单和准确起见，迭代各个浸没梯形：
    Ay_bar = 0.0
    for i in range(len(sec.s) - 1):
        x1, x2 = sec.s[i], sec.s[i + 1]
        z1, z2 = sec.z[i], sec.z[i + 1]

        if Z <= min(z1, z2):
            continue

        # 完全在水下
        if Z >= max(z1, z2):
            h1 = Z - z1
            h2 = Z - z2
            dx = abs(x2 - x1)
            area_i = (h1 + h2) / 2.0 * dx
            # 梯形的形心深度： y_c = (h1^2 + h1*h2 + h2^2) / (3 * (h1 + h2))
            if h1 + h2 > 1e-6:
                yc = (h1**2 + h1*h2 + h2**2) / (3.0 * (h1 + h2))
            else:
                yc = 0.0
            Ay_bar += area_i * yc
            continue

        # 部分浸没
        xi = x1 + (x2 - x1) * (Z - z1) / (z2 - z1)
        if z1 < Z:
            dx = abs(xi - x1)
            h1 = Z - z1
            area_i = h1 / 2.0 * dx
            yc = h1 / 3.0
            Ay_bar += area_i * yc
        else:
            dx = abs(x2 - xi)
            h2 = Z - z2
            area_i = h2 / 2.0 * dx
            yc = h2 / 3.0
            Ay_bar += area_i * yc

    M = Ay_bar + beta * (Q**2) / (9.81 * A)
    return M


def _hydro1d_warnings(line: ProfileLine, diags: list[tuple[str, dict]], spacing_issues: list = None
                      ) -> list[str]:
    """把二分求根的诊断汇总成用户能照做的告警。

    只报**真出了事**的断面：要么解贴边（真解可能在区间外，水位不可信），
    要么残差没收敛（迭代用尽仍不满足能量方程），要么越过了临界水深。
    两种情况原实现都静默返回一个数字，用户完全没有线索。
    """
    def _unique_names(items):
        seen = set()
        res = []
        for nm, d in items:
            if nm not in seen:
                seen.add(nm)
                res.append((nm, d))
        return res

    clipped = _unique_names([(nm, d) for nm, d in diags if d.get("clipped")])
    unconv = _unique_names([(nm, d) for nm, d in diags if not d.get("converged")])
    crossed = _unique_names([(nm, d) for nm, d in diags if d.get("crossed_critical")])

    out: list[str] = []

    if crossed:
        names = "、".join(nm for nm, _ in crossed[:5])
        more = f" 等 {len(crossed)} 个" if len(crossed) > 5 else ""
        out.append(
            f"一维推算：{names}{more} 断面发生流态转换（计算水位穿越临界水深），"
            f"可能存在水跃或跌水。当前模型未显式处理混合流，结果仅供参考。")
    if clipped:
        names = "、".join(nm for nm, _ in clipped[:5])
        more = f" 等 {len(clipped)} 个" if len(clipped) > 5 else ""
        lo, hi = clipped[0][1].get("z_bounds", (float("nan"), float("nan")))
        where = "下界（最低测点附近）" if clipped[0][1].get("at_lower")             else "上界（最高测点 +10 m）"
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

    if spacing_issues:
        pairs = "、".join(f"{d}-{u}" for d, u in spacing_issues[:3])
        more = f" 等 {len(spacing_issues)} 个" if len(spacing_issues) > 3 else ""
        # 分离普通的间距告警和能量反号告警
        normal_issues = [f"{d}-{u}" for d, u in spacing_issues if "能量反号" not in u]
        energy_issues = [f"{d}-{u.replace(' (能量反号)', '')}" for d, u in spacing_issues if "能量反号" in u]

        if normal_issues:
            pairs = "、".join(normal_issues[:3])
            more = f" 等 {len(normal_issues)} 个" if len(normal_issues) > 3 else ""
            out.append(
                f"一维推算：{pairs}{more} 断面间距可能过大（动能头变化 > 0.15m 或超出 Samuels 经验公式上限），"
                f"建议在这两断面之间加密/插值以提高推算精度。")

        if energy_issues:
            pairs = "、".join(energy_issues[:3])
            more = f" 等 {len(energy_issues)} 个" if len(energy_issues) > 3 else ""
            out.append(
                f"一维推算：{pairs}{more} 相邻断面计算后出现能量反号（水头损失 < 0）。"
                f"这通常发生在混合流流态转换处理不连续处。相关断面结果已被标记为无效。")
    return out
