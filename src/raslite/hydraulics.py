"""临界水深、正常水深与 Froude 数。

临界水深: 解 Fr^2(ws) = alpha*Q^2*T/(g*A^3) = 1。
  该条件由总水头 H = WS + alpha*V^2/2g 对 WS 取极小导出, 对任意
  断面形状严格成立 (矩形棱柱渠道退化为 yc=(q^2/g)^(1/3))。
  Fr^2 随水位总体单调递减, 用二分法求根, 稳定可靠。

  开放断面 (断面两端低于水面可无限抬升, z_max == 最低河底) 时,
  由河底附近逐级扩张上界直至 Fr^2 < 1。

  局限 (v1): 复式断面 T 在滩肩突变可能使 Fr^2(ws) 非单调 (多重
  临界水深现象), 此处取最浅的第一个解; 与 HEC-RAS 比能最小法
  可能有差异, L2 验证时核对。

正常水深: 解 Q = K(ws)*sqrt(S0) (曼宁公式), K 随 ws 单调增, 二分求根。

HEC-RAS 行为对齐说明:
  - 断面内找不到临界水深时, 本模块抛 CriticalDepthError;
    推算过程由 profile 层决定降级行为 (报警/取边界值)。
"""

from __future__ import annotations

from . import G
from .geometry import CrossSection


class CriticalDepthError(ValueError):
    pass


def specific_force(p, q: float) -> float:
    """比力 SF = beta*Q^2/(gA) + A*Ybar (beta 取 alpha, 同 alpha 公式)。

    用于混合流判别与多重临界水深选择 (HEC-RAS 规则: 多临界取 SF 最大者)。
    p 为 SectionProps 或含 area/alpha/y_centroid 属性的对象。
    """
    return p.alpha * q * q / (G * p.area) + p.area * p.y_centroid


def _lo_bound(cs: CrossSection) -> float:
    z = cs.z_min
    # 相对高程量纲取下限, 避免极浅水深时面积数值下溢
    return z + max(0.001, abs(z) * 1e-9)


def critical_ws(cs: CrossSection, q: float, tol: float = 1e-9):
    """返回 (临界水面高程, 临界比能 E = H - z_min)。

    临界判据 (HEC-RAS Reference Manual 式 2-19): 总水头
    H = WS + alpha*V^2/2g 的最小值。注意: 复式断面上 alpha 随水位
    显著变化时, 极小值处 Fr^2 != 1 (Fr^2=1 只在 alpha 冻结时成立),
    因此必须直接对 H 曲线求极小, 不能用 Fr^2=1 求根 (真实断面
    验证两种方法差可达 1m)。

    求解规则 = HEC-RAS "secant" 方法 (技术参考 Critical Depth
    Determination): 将断面切成 30 片 (复式 25+5), 在 H-WS 表中找
    局部极小 (最多 3 个), 逐个细化后**取比能最小者**。这是
    HEC-RAS 文档声明的确定性规则 (parabolic 默认法只找第一个
    极小且依赖迭代初值, 不可复现; 文档承认 secant 结果更准)。
    """
    if q <= 0:
        raise ValueError("流量必须为正")

    def H(ws: float) -> float:
        return cs.props(ws).total_head_of(q)

    lo = _lo_bound(cs)
    top = cs.z_max

    # ---- 开放断面 (平底, z_max==z_min): 深度扩张找上界 ----
    # (H 先降后升, 上界取到 H 首次回升处, 保证极小落在范围内)
    if top - lo <= 1e-9:
        d0 = max(1e-3, 0.001 * max(1.0, abs(cs.z_min)))
        top = cs.z_min + d0
        h_prev = H(lo)
        it = 0
        while True:
            h_cur = H(top)
            if h_cur > h_prev:
                break
            h_prev = h_cur
            d0 *= 1.5
            top = cs.z_min + d0
            it += 1
            if it > 200:
                raise CriticalDepthError(
                    f"{cs.name}: 开放断面内未找到比能极小 (扩张超过上限)")

    # ---- 切片 (HEC-RAS secant 法规则) ----
    # 复式判据: 断面总高 < 1.5 x 主槽滩肩最高点至河底的高差 -> 全断面 30 片
    bank_elev = None
    for x, z in cs.points:
        if abs(x - cs.bank_left) < 1e-9 or abs(x - cs.bank_right) < 1e-9:
            bank_elev = z if bank_elev is None else max(bank_elev, z)
    if bank_elev is None:
        bank_elev = cs.z_max
    if (top - lo) < 1.5 * (bank_elev - cs.z_min) \
            and (bank_elev - cs.z_min) > 1e-9:
        n_slices, split = 30, 25   # 25 片到滩肩高程 + 5 片到断面顶
    else:
        n_slices, split = 30, None  # 单一水深范围: 全断面 30 片

    def _grid():
        if split is None:
            return [lo + (top - lo) * i / n_slices for i in range(n_slices + 1)]
        g1 = [lo + (bank_elev - lo) * i / split for i in range(split + 1)]
        g2 = [bank_elev + (top - bank_elev) * i / 5 for i in range(1, 6)]
        return g1 + g2

    grid = _grid()
    hs = [H(w) for w in grid]

    # ---- 找表内局部极小 (最多 3 个, 与 HEC-RAS 一致) ----
    brackets = []
    for i in range(1, len(grid) - 1):
        if hs[i] <= hs[i - 1] and hs[i] <= hs[i + 1] \
                and (hs[i] < hs[i - 1] or hs[i] < hs[i + 1]):
            brackets.append((grid[i - 1], grid[i + 1]))
        if len(brackets) == 3:
            break
    if not brackets:
        # 表内无极小: 极小可能贴着下边界 (E 在 lo 处因 A->0 而发散,
        # 网格首点通常不是极小) 或介于两网格点之间被单调段掩盖,
        # 回退: 找 H 最小的网格点及其邻域作为极小括弧
        i_min = min(range(len(grid)), key=lambda i: hs[i])
        if i_min == 0:
            raise CriticalDepthError(
                f"{cs.name}: 断面内未找到比能极小 (河底处 H 最小?), "
                f"流量过小")
        brackets.append((grid[max(i_min - 1, 0)], grid[min(i_min + 1, len(grid) - 1)]))

    # ---- 每个括弧黄金分割细化 -> 候选 ----
    invphi = (5.0 ** 0.5 - 1.0) / 2.0
    best = None          # (H, ws)
    for (a, b) in brackets:
        c = b - invphi * (b - a)
        d2 = a + invphi * (b - a)
        fc, fd = H(c), H(d2)
        for _ in range(200):
            if (b - a) <= tol:
                break
            if fc < fd:
                b, d2, fd = d2, c, fc
                c = b - invphi * (b - a)
                fc = H(c)
            else:
                a, c, fc = c, d2, fd
                d2 = a + invphi * (b - a)
                fd = H(d2)
        ws = 0.5 * (a + b)
        hmin = H(ws)
        if best is None or hmin < best[0]:
            best = (hmin, ws)

    ws = best[1]
    p = cs.props(ws)
    return ws, p.total_head_of(q) - cs.z_min
def normal_ws(cs: CrossSection, q: float, s0: float, tol: float = 1e-9,
              max_iter: int = 200):
    """正常水深对应的水面高程 (二分法解 Q = K(ws)*sqrt(S0))。"""
    if q <= 0 or s0 <= 0:
        raise ValueError("流量与坡度必须为正")

    lo = _lo_bound(cs)
    hi = cs.z_max
    if hi <= lo:
        # 开放断面: 上界扩张至 K 足够
        d0 = max(1e-3, 0.001 * max(1.0, abs(cs.z_min)))
        hi = cs.z_min + d0
        it = 0
        while cs.props(hi).conveyance * s0 ** 0.5 < q:
            d0 *= 2.0
            hi = cs.z_min + d0
            it += 1
            if it > 200:
                raise CriticalDepthError(
                    f"{cs.name}: 断面内无正常水深 (扩张超过上限)")

    def f(ws: float) -> float:
        return cs.props(ws).conveyance * s0 ** 0.5 - q

    f_lo, f_hi = f(lo), f(hi)
    if f_lo > 0 or f_hi < 0:
        raise CriticalDepthError(
            f"{cs.name}: 断面内无正常水深 (f(lo)={f_lo:.3f}, f(hi)={f_hi:.3f})")
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if (hi - lo) < tol:
            break
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def froude_squared(props, q: float) -> float:
    """Fr^2 = alpha*Q^2*T/(g*A^3)"""
    return props.froude_sq_of(q)
