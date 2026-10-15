"""横断面几何与水力属性计算。

核心职责: 给定水面高程 WSE, 计算过水面积 A、湿周 P、水面宽 T、
分区输水能力 K、动能修正系数 alpha (HEC-RAS Reference Manual 式 2-4/2-5/2-11)。

划分规则 (HEC-RAS 默认输水能力划分法):
  - 主槽 (左右滩肩之间) 作为一个整体分区;
  - 滩地按糙率 n 值突变点 (break points) 划分为若干分区;
  - 每个分区 K_i = A_i * R_i^(2/3) / n_i, 总 K = sum(K_i)。

湿周约定:
  - 水面边壁 (断面两端竖直墙、滩中高地两侧竖直切边) 计入湿周;
  - 分区之间的竖直分界线 (滩地 n 值突变点、滩肩) 不计入湿周
    (虚拟分界, 分区间无剪切假定)。

比能压强水头: 采用面积形心压强修正 (E = y_bar + alpha*V^2/2g,
y_bar 为水面至面积形心的高差)。对矩形/棱柱形断面退化为通常的
E = y + alpha*V^2/2g。这是任意断面下物理正确的比能定义。

局限 (v1): 单一连通主河槽假定; 不处理无效流动区、封闭管流、
纵多水池 (multi-pool) 的独立处理 (岛形高地的面积/湿周可正确累加)。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from . import G

_EPS = 1e-12


def _norm_n_zones(spec, x_lo: float, x_hi: float, default_n: float):
    """把 n 值规格归一化为 [(x0, x1, n), ...] 区间列表。

    spec 可为:
      - float          -> 整段单一 n
      - [(sta, n),...] -> HEC-RAS 语义: 每个突变点 (sta, n) 表示从 sta 起
                          到下一突变点使用 n (首个分区从 x_lo 起即用第一个 n)
    """
    if spec is None:
        spec = default_n
    if isinstance(spec, (int, float)):
        return [(x_lo, x_hi, float(spec))]
    pts = [(float(s), float(n)) for s, n in spec]
    zones = []
    for i in range(len(pts)):
        x0 = x_lo if i == 0 else pts[i][0]
        x1 = pts[i + 1][0] if i + 1 < len(pts) else x_hi
        if x1 > x0:
            zones.append((x0, x1, pts[i][1]))
    return zones


@dataclass
class Subdivision:
    """一个输水分区 (同 n 值、流速均匀分布假定)。"""
    n: float
    area: float = 0.0
    perimeter: float = 0.0  # 仅地面湿周

    @property
    def conveyance(self) -> float:
        if self.area <= _EPS or self.perimeter <= _EPS or self.n <= 0:
            return 0.0
        return self.area * (self.area / self.perimeter) ** (2.0 / 3.0) / self.n


@dataclass
class SectionProps:
    """断面在某一水位下的水力属性。"""
    ws: float
    area: float
    perimeter: float          # 总湿周 (含竖直边壁)
    top_width: float
    conveyance: float         # 总 K
    alpha: float
    y_centroid: float         # 水面至面积形心的高差 (压强水头)
    z_min: float              # 断面最低点高程
    # 分区结果: [(label, Subdivision)], label in {lob, ch, rob}
    subdivisions: list = field(default_factory=list)

    @property
    def depth_min(self) -> float:
        return self.ws - self.z_min

    def velocity_of(self, q: float) -> float:
        return q / self.area

    def velocity_head_of(self, q: float) -> float:
        """alpha * V^2 / 2g"""
        v = q / self.area
        return self.alpha * v * v / (2.0 * G)

    def total_head_of(self, q: float) -> float:
        """H = WS + alpha*V^2/2g"""
        return self.ws + self.velocity_head_of(q)

    def specific_energy_of(self, q: float) -> float:
        """比能 E = y_centroid + alpha*V^2/2g (任意断面物理正确形式)"""
        v = q / self.area
        return self.y_centroid + self.alpha * v * v / (2.0 * G)

    def k_by_part(self) -> dict:
        d = {"lob": 0.0, "ch": 0.0, "rob": 0.0}
        for label, sub in self.subdivisions:
            d[label] += sub.conveyance
        return d

    def froude_sq_of(self, q: float) -> float:
        """Fr^2 = alpha * Q^2 * T / (g * A^3)"""
        return self.alpha * q * q * self.top_width / (G * self.area ** 3)


class CrossSection:
    """横断面。

    Parameters
    ----------
    name : str  断面名 (如河名/里程)
    points : [(station, elev), ...]  按站距升序排列的地面线
    bank_left, bank_right : float  左右滩肩站距 (主槽范围)
    n_main : float  主槽曼宁糙率
    n_left, n_right : float 或 [(station, n), ...]
        滩地糙率; 传列表时为突变点定义 (首区从断面左/右端起)
    reach_lengths : (L_lob, L_ch, L_rob)  本断面到下一断面的距离
        (HEC-RAS 约定: 存在上游断面, 指向下游方向)
    c_contract, c_expand : float  收缩/扩散损失系数
        (HEC-RAS 约定: 存本断面, 用于本断面与其下游断面之间)
    """

    def __init__(self, name, points, bank_left, bank_right, n_main,
                 n_left=None, n_right=None,
                 reach_lengths=(0.0, 0.0, 0.0),
                 c_contract=0.1, c_expand=0.3, levees=None):
        self.name = name
        pts = sorted((float(s), float(z)) for s, z in points)
        if len(pts) < 2:
            raise ValueError(f"{name}: 至少需要 2 个地面点")
        # 站距去重
        dedup = [pts[0]]
        for p in pts[1:]:
            if p[0] > dedup[-1][0] + _EPS:
                dedup.append(p)
        self.points = dedup
        self.x_min, self.x_max = self.points[0][0], self.points[-1][0]
        if not (self.x_min <= bank_left < bank_right <= self.x_max):
            raise ValueError(f"{name}: 滩肩站距超出断面范围")
        self.bank_left = float(bank_left)
        self.bank_right = float(bank_right)
        self.levees = [(-1 if side < 0 else 1, float(sta), float(elev))
                       for side, sta, elev in (levees or [])]
        self.n_main = float(n_main)
        self.z_min = min(z for _, z in self.points)
        self.z_max = max(z for _, z in self.points)
        self.reach_lengths = tuple(float(v) for v in reach_lengths)
        self.c_contract = float(c_contract)
        self.c_expand = float(c_expand)

        # 糙率分区 (滩地): [(x0, x1, n)]
        self.zones_lob = _norm_n_zones(n_left, self.x_min, self.bank_left, self.n_main)
        self.zones_rob = _norm_n_zones(n_right, self.bank_right, self.x_max, self.n_main)

        # 预处理: 在滩肩与 n 突变点处插入节点, 使每段地面线属于唯一分区
        self._refine()

    # ---------- 构建辅助 ----------

    def _insert_point(self, x: float):
        pts = self.points
        i = bisect.bisect_left(pts, (x, float("-inf")))
        if i < len(pts) and abs(pts[i][0] - x) < _EPS:
            return
        # 线性内插高程
        z = self._z_at(x)
        pts.insert(i, (x, z))

    def _z_at(self, x: float) -> float:
        import bisect
        pts = self.points
        i = bisect.bisect_left(pts, (x, float("-inf")))
        if i < len(pts) and abs(pts[i][0] - x) < _EPS:
            return pts[i][1]
        x1, z1 = pts[i - 1]
        x2, z2 = pts[i]
        t = (x - x1) / (x2 - x1)
        return z1 + t * (z2 - z1)

    def _n_of_zone(self, zones, x: float) -> float:
        for x0, x1, n in zones:
            if x0 - _EPS <= x <= x1 + _EPS:
                return n
        # 数值容差兜底
        return zones[-1][2] if x > zones[-1][1] else zones[0][2]

    def _refine(self):
        cuts = [self.bank_left, self.bank_right]
        for _, x1, _ in self.zones_lob[:-1]:
            cuts.append(x1)
        for x0, _, _ in self.zones_rob[1:]:
            cuts.append(x0)
        for x in cuts:
            self._insert_point(x)

    def _subdivision_of(self, x_mid: float):
        """返回 (label, Subdivision) 所属分区键"""
        if x_mid < self.bank_left - _EPS:
            n = self._n_of_zone(self.zones_lob, x_mid)
            return ("lob", n)
        if x_mid > self.bank_right + _EPS:
            n = self._n_of_zone(self.zones_rob, x_mid)
            return ("rob", n)
        return ("ch", self.n_main)

    # ---------- 水力属性 ----------

    def _effective_points(self, ws: float):
        """按堤防规则截断断面点。

        HEC-RAS 规则: 水位低于堤顶时, 堤后 (背水侧) 地面点全部剔除,
        断面以堤防站距 + 堤顶高程作为端点 (水被挡在堤前);
        水位达到堤顶后恢复全断面。side: -1=左堤 (堤后在左), +1=右堤。
        """
        if not self.levees:
            return self.points
        pts = list(self.points)
        for side, sta, elev in self.levees:
            if ws >= elev:
                continue
            if side < 0:
                front = [(x, z) for (x, z) in pts if x > sta + _EPS]
                if front:
                    pts = [(sta, elev)] + front
            else:
                front = [(x, z) for (x, z) in pts if x < sta - _EPS]
                if front:
                    pts = front + [(sta, elev)]
        return pts

    def props(self, ws: float) -> SectionProps:
        """计算给定水面高程下的断面水力属性。"""
        if ws <= self.z_min:
            raise ValueError(f"{self.name}: WSE={ws:.3f} 低于河底 {self.z_min:.3f}")

        subs: dict = {}       # (label, n) -> Subdivision
        area_t = 0.0
        perim_t = 0.0
        top_t = 0.0
        moment = 0.0          # ∫ (ws - z)^2 /2 * ... -> ∫ h^2 dx (用于形心)

        points = self._effective_points(ws)
        n_pts = len(points)
        for i in range(n_pts - 1):
            x1, z1 = points[i]
            x2, z2 = points[i + 1]
            h1, h2 = ws - z1, ws - z2
            dx = x2 - x1
            if h1 <= 0.0 and h2 <= 0.0:
                continue
            label, n = self._subdivision_of(0.5 * (x1 + x2))
            sub = subs.setdefault((label, n), Subdivision(n))

            if h1 >= 0.0 and h2 >= 0.0:
                # 全湿段
                sub.area += 0.5 * (h1 + h2) * dx
                sub.perimeter += (dx * dx + (z2 - z1) ** 2) ** 0.5
                top_t += dx
                area_t += 0.5 * (h1 + h2) * dx
                perim_t += (dx * dx + (z2 - z1) ** 2) ** 0.5
                moment += dx * (h1 * h1 + h1 * h2 + h2 * h2) / 3.0
            else:
                # 半湿段: 水面交点处水深为 0, 无竖直切边湿周
                t = h1 / (h1 - h2)  # h=0 位置
                xc = x1 + t * dx
                if h1 > 0.0:
                    hw, xw0, xw1 = h1, x1, xc
                else:
                    hw, xw0, xw1 = h2, xc, x2
                dw = abs(xw1 - xw0)
                sub.area += 0.5 * hw * dw
                area_t += 0.5 * hw * dw
                top_t += dw
                ground_len = (dw * dw + ((z2 - z1) / dx * dw) ** 2) ** 0.5 if dx > 0 else 0.0
                sub.perimeter += ground_len
                perim_t += ground_len
                moment += dw * hw * hw / 3.0

        # 断面两端竖直端墙 (计入总湿周与所属分区湿周)
        z_l, z_r = points[0][1], points[-1][1]
        if ws > z_l:
            h = ws - z_l
            label, n = self._subdivision_of(points[0][0])
            subs[(label, n)].perimeter += h
            perim_t += h
        if ws > z_r:
            h = ws - z_r
            label, n = self._subdivision_of(points[-1][0])
            subs[(label, n)].perimeter += h
            perim_t += h

        if area_t <= _EPS:
            raise ValueError(f"{self.name}: WSE={ws:.3f} 无过水面积")

        sub_list = [(label, s) for (label, n), s in subs.items()]
        k_t = sum(s.conveyance for _, s in sub_list)
        if k_t <= 0.0:
            raise ValueError(f"{self.name}: 输水能力为 0")

        # alpha (式 2-11): alpha = At^2 * sum(K_i^3/A_i^2) / Kt^3
        alpha = (area_t ** 2) * sum(
            s.conveyance ** 3 / s.area ** 2 for _, s in sub_list
        ) / k_t ** 3

        # 压强水头 (面积平均): (1/A)∫h dA = ∫h^2 dx / (2A)
        y_centroid = moment / (2.0 * area_t)

        return SectionProps(
            ws=ws, area=area_t, perimeter=perim_t, top_width=top_t,
            conveyance=k_t, alpha=alpha, y_centroid=y_centroid,
            z_min=self.z_min, subdivisions=sub_list,
        )
