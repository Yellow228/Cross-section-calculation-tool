"""河网 (Reach + Junction) 亚临界恒定流解算。

结构:
  Reach    河段: 有名断面序列 (US->DS) + 流量 (常数或逐断面)
  Junction 汇合口: 若干入流河段 + 1 出流河段, 各入流河段有汇合口长度
  SteadyNetwork 亚临界河网解算器

汇合口算法 (对齐 HEC-RAS 默认能量法 "reach by reach energy balance"):
  - 自出流河段下游边界向上游推算;
  - 到汇合口: 出流河段首断面与每个入流河段末断面之间做一次
    标准步进配对能量步, 河段长度取该入流河段的汇合口长度
    (HEC-RAS Junction Information 的 L&A 值);
  - 再以该末断面水位为边界, 继续向各入流河段上游推算。

v1 限制: 仅支持亚临界过汇合口 (超临界过汇合口涉及动量法, 二期);
流量在汇合口处自动合并 (出流河段流量应 >= 各入流之和, 沿程入流
用逐断面 q 表达)。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import CrossSection
from .profile import SteadyProfile


@dataclass
class Reach:
    name: str
    sections: list            # [CrossSection] US->DS, 需带 chainage (仅用于排序)
    q: float | list = 0.0     # 常数或逐断面流量 (US->DS)

    @property
    def first(self) -> CrossSection:
        return self.sections[0]

    @property
    def last(self) -> CrossSection:
        return self.sections[-1]


@dataclass
class Junction:
    name: str
    inflows: dict             # {reach_name: 汇合口长度 m}
    outflow: str              # 出流河段名


class SteadyNetwork:
    """亚临界恒定流河网解算器。

    Parameters
    ----------
    reaches : {name: Reach}
    junctions : [Junction]
    outlet_reach : str  最下游河段名 (整体唯一的出流河段)
    boundary : tuple    出流河段下游边界 (同 SteadyProfile 格式)
    """

    def __init__(self, reaches, junctions, outlet_reach, boundary,
                 sbar_method="avg_conveyance", tol=0.003, max_iter=40):
        self.reaches = dict(reaches)
        self.junctions = list(junctions)
        self.outlet_reach = outlet_reach
        self.boundary = boundary
        self.sbar_method = sbar_method
        self.tol = tol
        self.max_iter = max_iter
        self._check()

        self._junc_of_outflow = {j.outflow: j for j in self.junctions}

    def _check(self):
        for j in self.junctions:
            for r in j.inflows:
                if r not in self.reaches:
                    raise ValueError(f"Junction {j.name}: 入流河段 {r} 不存在")
            if j.outflow not in self.reaches:
                raise ValueError(f"Junction {j.name}: 出流河段 {j.outflow} 不存在")
        if self.outlet_reach not in self.reaches:
            raise ValueError(f"出流河段 {self.outlet_reach} 不存在")
        # 每个非出流河段必须恰好是一个 junction 的入流
        for name in self.reaches:
            if name == self.outlet_reach:
                continue
            js = [j for j in self.junctions if name in j.inflows]
            if len(js) != 1:
                raise ValueError(f"河段 {name} 必须恰好汇入一个 junction")

    # ---------------- 主流程 ----------------

    def run_subcritical(self):
        """返回 {reach_name: [SectionResult US->DS]}。

        自出流河段向上游递归: 河段内用标准步进 (边界=下游端已知水位),
        河段顶端若是汇合口, 对每个入流河段做汇合口能量步后继续。
        """
        self.results = {}
        self._run_reach(self.outlet_reach, self.boundary)
        return self.results

    def _run_reach(self, reach_name: str, boundary):
        if reach_name in self.results:
            return
        reach = self.reaches[reach_name]
        if not reach.sections:
            raise ValueError(f"河段 {reach_name} 无断面")

        q = reach.q
        if not isinstance(q, (list, tuple)):
            q = [q] * len(reach.sections)
        if len(q) != len(reach.sections):
            raise ValueError(f"河段 {reach_name}: q 长度与断面数不一致")

        engine = SteadyProfile(reach.sections, list(q), regime="subcritical",
                               boundary=boundary, sbar_method=self.sbar_method,
                               tol=self.tol, max_iter=self.max_iter)
        res = engine.run()          # 推算顺序 DS->US, 转成 US->DS
        res_usds = list(reversed(res))
        self.results[reach_name] = res_usds

        # 河段顶端上游的汇合口
        junc = self._junc_of_outflow.get(reach_name)
        if junc is None:
            return

        # 汇合口能量步: 出流河段首断面 (已知) -> 各入流河段末断面
        ds_sec = reach.first
        ds_res = res_usds[0]
        p_known = ds_sec.props(ds_res.ws)
        q_known = q[0]   # q 为 US->DS 排列, 首断面即汇合口下游侧
        for inflow_name, Lj in junc.inflows.items():
            in_reach = self.reaches[inflow_name]
            us_sec = in_reach.last
            q_in = in_reach.q
            q_us = q_in[-1] if isinstance(q_in, (list, tuple)) else q_in
            # 汇合口长度赋给入流末断面 (临时), 复用标准步进;
            # HEC-RAS junction 能量法: 汇合口处只计摩擦, 不计收缩/扩散
            saved = us_sec.reach_lengths
            saved_c = (us_sec.c_contract, us_sec.c_expand)
            saved_cus, saved_cds = us_sec.chainage, ds_sec.chainage
            us_sec.reach_lengths = (Lj, Lj, Lj)
            us_sec.c_contract = us_sec.c_expand = 0.0
            # 临时 chainage 保证排序方向 (DS 大)
            us_sec.chainage, ds_sec.chainage = 0.0, 1.0
            try:
                junc_engine = SteadyProfile(
                    [us_sec, ds_sec], [q_us, q_known],
                    regime="subcritical", boundary=("ws", ds_res.ws),
                    sbar_method=self.sbar_method, tol=self.tol,
                    max_iter=self.max_iter)
                jres = junc_engine.run()   # 返回 [us, ds]? 顺序: DS->US
                us_ws = jres[-1].ws        # us 断面结果
                us_warnings = list(jres[-1].warnings)
            finally:
                us_sec.reach_lengths = saved
                us_sec.c_contract, us_sec.c_expand = saved_c
                us_sec.chainage, ds_sec.chainage = saved_cus, saved_cds
            self._run_reach(inflow_name, ("ws", us_ws))
