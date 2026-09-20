"""按纵断面实测数据推算河道平均比降。

用户需求（2026-09-19）：每个横断面都要填比降 S，而文件里自带纵断面实测数据，
应当用它来推算，方法为「平均比降法」。确认的口径：

    - **整条纵断面线共用一个平均比降** → 同线内所有横断面填同一个值
    - **用纵断面全部实测测点**
    - 结果先出建议值表，用户核对后再应用（不自动覆盖）

两种算法并列提供，**不设默认**，必须在界面上选：

    "jc"         约翰斯通-克罗斯法（Johnstone & Cross, 1949）—— **默认口径**
                 S = [ Σ L_i·√S_i / Σ L_i ]²
                 把河段按纵断面相邻测点分成子河段，各子段比降 S_i、长度 L_i，
                 对 √S 做长度加权平均后平方。

    "endpoints"  两端点法 —— 「平均比降」的朴素定义
                 平均比降 = 河段落差 ÷ 河段长度

    "lsq"        最小二乘线性拟合
                 对全部 (起点距, 高程) 做一元线性回归，斜率取负即比降

**为什么默认用 jc**：曼宁公式里 Q ∝ √S，所以对 √S 做加权得到的等效比降，
在**输水能力**上等价于分段情况；而两端点法只在**落差**上等价。

数学性质（可作断言）：√S 是凹函数，由 Jensen 不等式 (E√S)² ≤ E[S]，
故 jc 结果**恒 ≤ 两端点法**，各子段比降相等时取等号。

⚠ 注意：网上部分资料（CSDN / ArcGIS 教程）给出的「约翰斯通-克罗斯公式」
是**线性加权**版 S = ΣL_i·S_i / ΣL_i，但它在数学上恒等于两端点法：
    Σ L_i·S_i = Σ L_i·(Δh_i/L_i) = Σ Δh_i = 总落差
所以那个版本并非另一种方法，不要照抄。本模块实现的是教材上的开方版。

坐标系说明：本模块一律使用**原始起点距**（`profile.dist`），
不是重定基后的桩号（`line.chainage`）。两者原点不同，混用会算错。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .interp import interp1_linear_extrap
from .model import ProfileData, Project, Section

SLOPE_MODES = ("jc", "endpoints", "lsq")

MODE_LABELS = {
    "jc": "约翰斯通-克罗斯法（按 √S 长度加权）",
    "endpoints": "两端点法（总落差 ÷ 河段长）",
    "lsq": "最小二乘拟合（用全部测点回归）",
}


@dataclass
class SlopeEstimate:
    """一条纵断面线的平均比降估计。"""

    line: str = ""
    start: float = float("nan")      # 计算河段起点（原始起点距, m）
    end: float = float("nan")        # 计算河段终点
    length: float = float("nan")     # 河段长度 (m)
    drop: float = float("nan")       # 总落差 (m) = 上游端高程 - 下游端高程
    n_points: int = 0                # 参与计算的纵断面测点数
    slope_jc: float = float("nan")
    slope_endpoints: float = float("nan")
    slope_lsq: float = float("nan")
    r2: float = float("nan")         # 最小二乘的判定系数
    jc_segments: int = 0             # 参与 jc 计算的子河段数
    jc_skipped: int = 0              # 因非正比降被跳过的子河段数
    jc_skipped_len: float = 0.0      # 被跳过的子河段总长度 (m)
    message: str = ""

    @property
    def ok(self) -> bool:
        return (self.n_points >= 2 and math.isfinite(self.length)
                and self.length > 0)

    def slope(self, mode: str) -> float:
        """按指定口径取比降值。"""
        if mode == "jc":
            return self.slope_jc
        if mode == "lsq":
            return self.slope_lsq
        if mode == "endpoints":
            return self.slope_endpoints
        raise ValueError(f"未知比降口径 {mode!r}（可选 {SLOPE_MODES}）")


@dataclass
class SlopeProposal:
    """给单个横断面的比降建议。"""

    line: str                 # 所属纵断面线
    section: str              # 断面名
    profile_dist: float       # 该断面在纵断面上的原始起点距 (m)
    chainage: float           # 桩号（按 chainage_origin 重定基, m）
    z_profile: float          # 该处纵断面的插值高程 (m)
    current: float            # 当前已填比降
    proposed: float           # 建议比降

    @property
    def delta(self) -> float:
        if not (math.isfinite(self.current) and math.isfinite(self.proposed)):
            return float("nan")
        return self.proposed - self.current

    @property
    def bad(self) -> bool:
        """建议值不可用：非有限、或 ≤ 0（倒坡 / 平坡）。

        曼宁公式里是 sqrt(S)，S ≤ 0 会直接算不出流量，
        所以必须在应用前标出来，不能悄悄写进去。
        """
        return not math.isfinite(self.proposed) or self.proposed <= 0.0


# ---------------------------------------------------------------- 基础计算

def clip_profile(prof: ProfileData, lo: float, hi: float
                 ) -> tuple[list[float], list[float]]:
    """截取纵断面在起点距区间 [lo, hi] 内的测点，两端各补一个插值点。

    返回 (起点距列表, 高程列表)，区间无效时返回 ([], [])。
    两端补点是为了让河段端点落在断面位置上，而不是落在最近的实测测点上。
    """
    if prof is None or prof.n_points < 2:
        return [], []

    # dist 理论上递增，但不假定；按起点距排序后一并带上高程
    pairs = sorted(zip(prof.dist, prof.z))
    ds = [p[0] for p in pairs]
    zs = [p[1] for p in pairs]

    lo = max(lo, ds[0])
    hi = min(hi, ds[-1])
    if not (hi - lo > 1e-9):
        return [], []

    out_d = [lo]
    out_z = [interp1_linear_extrap(ds, zs, lo)]
    for a, b in zip(ds, zs):
        if lo < a < hi:
            out_d.append(a)
            out_z.append(b)
    out_d.append(hi)
    out_z.append(interp1_linear_extrap(ds, zs, hi))
    return out_d, out_z


def linear_fit(x: list[float], y: list[float]) -> tuple[float, float]:
    """一元线性回归 y = k·x + b，返回 (-k, R²)。

    取负号是因为比降定义为沿程**下降**率（起点距增大、高程减小 → 比降为正）。
    x 的方差为 0 时返回 (nan, nan)。
    """
    n = len(x)
    if n < 2:
        return float("nan"), float("nan")

    mx = sum(x) / n
    my = sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx <= 0.0:
        return float("nan"), float("nan")

    sxy = sum((a - mx) * (c - my) for a, c in zip(x, y))
    k = sxy / sxx
    b = my - k * mx

    syy = sum((c - my) ** 2 for c in y)
    ss_res = sum((c - (k * a + b)) ** 2 for a, c in zip(x, y))
    r2 = (1.0 - ss_res / syy) if syy > 0 else float("nan")
    return -k, r2


def johnstone_cross(dist: list[float], z: list[float]
                    ) -> tuple[float, int, int, float]:
    """约翰斯通-克罗斯法（Johnstone & Cross, 1949）平均纵比降。

        S = [ Σ L_i·√S_i / Σ L_i ]²

    把河段按纵断面**相邻测点**分成子河段（实测点之间可视为均匀坡），
    第 i 段的长度 L_i = dist[i+1] - dist[i]，比降 S_i = (z[i] - z[i+1]) / L_i
    （起点距递增方向为顺流，故上游减下游为正）。

    为什么对 √S 加权而不是直接对 S 加权：曼宁公式 Q ∝ √S，
    这样得到的等效比降在**输水能力**上等价于分段情况；
    而对 S 直接加权只保证**落差**等价（那恰好退化为两端点法）。

    倒坡子段（S_i ≤ 0）使 √S_i 无定义，**跳过**（分子分母都不计入）并计数，
    由调用方决定是否告警——实测数据里 3 条线存在这种段（占长 3.7%~5.4%）。

    返回 (比降, 参与段数, 跳过段数, 跳过总长)。
    """
    num = den = 0.0
    used = skipped = 0
    skip_len = 0.0
    for i in range(len(dist) - 1):
        li = dist[i + 1] - dist[i]
        if li <= 0:
            continue
        si = (z[i] - z[i + 1]) / li
        if si <= 0:
            skipped += 1
            skip_len += li
            continue
        num += li * math.sqrt(si)
        den += li
        used += 1
    if den <= 0:
        return float("nan"), 0, skipped, skip_len
    return (num / den) ** 2, used, skipped, skip_len


def estimate_line_slope(prof: Optional[ProfileData],
                        lo: Optional[float] = None,
                        hi: Optional[float] = None,
                        line_name: str = "") -> SlopeEstimate:
    """估算一条纵断面线的平均比降。

    lo / hi 为**原始起点距**范围；留空则用整条纵断面的范围。
    """
    est = SlopeEstimate(line=line_name)
    if prof is None or prof.n_points < 2:
        est.message = "缺少纵断面数据（或测点不足 2 个），无法推算比降"
        return est

    d_all = prof.dist
    if lo is None or hi is None:
        lo = min(d_all)
        hi = max(d_all)

    ds, zs = clip_profile(prof, lo, hi)
    if len(ds) < 2:
        est.message = "指定河段内没有有效纵断面测点"
        return est

    est.start, est.end = ds[0], ds[-1]
    est.length = ds[-1] - ds[0]
    est.n_points = len(ds)
    est.drop = zs[0] - zs[-1]          # 起点距小的一端（上游）减另一端
    est.slope_endpoints = est.drop / est.length
    est.slope_lsq, est.r2 = linear_fit(ds, zs)
    (est.slope_jc, est.jc_segments,
     est.jc_skipped, est.jc_skipped_len) = johnstone_cross(ds, zs)

    notes = []
    if est.slope_endpoints <= 0:
        notes.append("该河段纵剖面整体呈上坡（倒比降），请核对数据或里程方向")
    if est.jc_skipped:
        pct = 100.0 * est.jc_skipped_len / est.length if est.length > 0 else 0.0
        notes.append(f"约翰斯通-克罗斯法跳过了 {est.jc_skipped} 个倒坡子河段"
                     f"（占长度 {pct:.1f}%）；其余口径不受影响")
    est.message = "；".join(notes)
    return est


# ---------------------------------------------------------------- 对外接口

def propose_slopes(project: Project, mode: str = "jc",
                   use_full_profile: bool = False
                   ) -> tuple[list[SlopeProposal], list[SlopeEstimate]]:
    """对整个工程逐断面给出比降建议。

    mode             "jc" | "endpoints" | "lsq"，见模块开头说明（默认 jc）
    use_full_profile True  用整条纵断面（含横断面覆盖范围之外的河段）
                     False 只用**最上下游横断面之间**那段纵断面（默认）
                           理由：比降是要填给这些断面用的，
                           纵断面两端超出断面范围的部分未必代表该段河道。

    返回 (每个断面的建议列表, 每条线的估算详情)。
    """
    if mode not in SLOPE_MODES:
        raise ValueError(f"未知比降口径 {mode!r}（可选 {SLOPE_MODES}）")

    proposals: list[SlopeProposal] = []
    estimates: list[SlopeEstimate] = []

    for line in project.profile_lines:
        prof = line.profile
        lo = hi = None
        if not use_full_profile:
            ds = [d for d in line.profile_dist if math.isfinite(d)]
            # 至少要两个**不同位置**的断面才能限定出河段；
            # 单断面线、或所有断面位置重合时无法限定，回退到整条纵断面。
            if len(ds) >= 2 and (max(ds) - min(ds)) > 1e-9:
                lo, hi = min(ds), max(ds)

        est = estimate_line_slope(prof, lo, hi, line.name)
        estimates.append(est)
        value = est.slope(mode) if est.ok else float("nan")

        for k, sec in enumerate(line.sections):
            pd = (line.profile_dist[k]
                  if k < len(line.profile_dist) else float("nan"))
            ch = (line.chainage[k]
                  if k < len(line.chainage) else float("nan"))
            z_prof = float("nan")
            if prof is not None and math.isfinite(pd):
                pairs = sorted(zip(prof.dist, prof.z))
                z_prof = interp1_linear_extrap([p[0] for p in pairs],
                                               [p[1] for p in pairs], pd)
            proposals.append(SlopeProposal(
                line=line.name,
                section=sec.name,
                profile_dist=pd,
                chainage=ch,
                z_profile=z_prof,
                current=sec.params.slope,
                proposed=value,
            ))

    return proposals, estimates


def apply_slopes(project: Project,
                 proposals: list[SlopeProposal],
                 only_missing: bool = False) -> int:
    """把建议比降写入各断面参数，返回实际改动的断面数。

    only_missing=True  只填当前为空的（NaN），已手填的一律不动
    跳过 bad 的建议值（非有限或 ≤ 0），避免把 sqrt(S) 算不出的值写进去。
    """
    by_name = {s.name: s for ln in project.profile_lines for s in ln.sections}
    changed = 0
    for p in proposals:
        sec = by_name.get(p.section)
        if sec is None or p.bad:
            continue
        if only_missing and math.isfinite(sec.params.slope):
            continue
        sec.params.slope = p.proposed
        changed += 1
    return changed
