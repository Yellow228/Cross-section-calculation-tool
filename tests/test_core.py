"""core 层单元测试。

仅用标准库，不需要安装任何依赖即可运行：
    python tests/test_core.py
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from core.config import Config
from core.geom import section_geom, surface_span, wetted_polygons
from core.interp import find_water_edge, interp1_linear_extrap
from core.model import Section, SectionParams, SectionResult, TerrainInfo
from core.rating import compute_rating_curve, hvec_row_counts, matlab_colon
from core.solver import solve_section
from core.terrain import analyze_terrain
from core.chainage import chainage_at_distance, compute_chainage, rebase_chainage
from core.model import ProfileData, ProfileLine, Project
from core.reader import (_dedupe_names, blocks_to_sections, classify,
                         split_blocks)
from core.spatial import assign_by_intersection, find_intersection, segment_intersection
from core import edit, exporter, params, project_io, slope, version

TOL = 1e-9


def make_compound_section() -> Section:
    """人工复式断面：有明显主槽 + 左右岸坡 + 平缓滩地。

    起点距 s:  0    20    25   30   40    45    65
    高程   z: 102  102.5  98   97   98  102.5  102.8
    深泓在 index 3 (s=30, z=97)
    """
    x = [500.0, 520.0, 525.0, 530.0, 540.0, 545.0, 565.0]
    y = [100.0, 120.0, 125.0, 130.0, 140.0, 145.0, 165.0]
    s = [0.0, 20.0, 25.0, 30.0, 40.0, 45.0, 65.0]
    z = [102.0, 102.5, 98.0, 97.0, 98.0, 102.5, 102.8]
    return Section(name="CS01", x=x, y=y, s=s, z=z,
                   params=SectionParams(name="CS01", slope=0.005, roughness=0.03, design_q=50.0))


def _solve_rows(sec: Section, cfg: Config) -> tuple[int, object]:
    """解一遍断面，返回 (H~Q 水位行数, TerrainInfo)。"""
    info = analyze_terrain(sec, cfg)
    hvec, *_ = compute_rating_curve(sec, info, cfg)
    return len(hvec), info


class TestSectionGeom(unittest.TestCase):
    """section_geom 的解析解校验：V 形断面"""

    def test_v_shape_analytic(self):
        x = [0.0, 10.0, 20.0]
        z = [100.0, 95.0, 100.0]
        A, P, B = section_geom(x, z, 97.5)
        # 湿润区：两个直角三角形，各底 5、高 2.5
        self.assertAlmostEqual(A, 12.5, places=9)                     # 2 * 0.5*5*2.5
        self.assertAlmostEqual(P, 2 * math.sqrt(5**2 + 2.5**2), places=9)
        self.assertAlmostEqual(B, 10.0, places=9)

    def test_dry_and_full(self):
        x = [0.0, 10.0, 20.0]
        z = [100.0, 95.0, 100.0]
        self.assertEqual(section_geom(x, z, 90.0), (0.0, 0.0, 0.0))   # 全干
        A, P, B = section_geom(x, z, 100.0)                           # 刚好齐平
        self.assertGreater(A, 0.0)

    def test_surface_span(self):
        x = [0.0, 10.0, 20.0]
        z = [100.0, 95.0, 100.0]
        xl, xr = surface_span(x, z, 97.5)
        self.assertAlmostEqual(xl, 5.0, places=9)
        self.assertAlmostEqual(xr, 15.0, places=9)


def _poly_area(poly: list[tuple[float, float]]) -> float:
    a = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


class TestWettedPolygons(unittest.TestCase):
    """水位填充多边形必须收在**插值交点**上，不能漫到外侧采样点。

    这是曾经踩过的坑：用 fill_between(x, minimum(z,H), H, where=z<=H) 时，
    minimum 把岸上地形截断成水位高度，填充下边界变成"截断后的地形"，
    水面就一路铺到最外侧采样点，看起来两岸都被淹了。
    """

    def test_v_shape_ends_at_interpolated_points(self):
        x = [0.0, 10.0, 20.0]
        z = [100.0, 95.0, 100.0]
        polys = wetted_polygons(x, z, 97.5)
        self.assertEqual(len(polys), 1)
        xs = [p[0] for p in polys[0]]
        self.assertAlmostEqual(min(xs), 5.0, places=9)     # 不是 0
        self.assertAlmostEqual(max(xs), 15.0, places=9)    # 不是 20

    def test_asymmetric_interpolation(self):
        x = [0.0, 30.0, 40.0, 60.0]
        z = [100.0, 96.0, 98.0, 100.0]
        polys = wetted_polygons(x, z, 98.5)
        self.assertEqual(len(polys), 1)
        xs = [p[0] for p in polys[0]]
        self.assertAlmostEqual(min(xs), 11.25, places=9)   # t=(98.5-100)/(96-100)=0.375
        self.assertAlmostEqual(max(xs), 45.0, places=9)    # t=(98.5-98)/(100-98)=0.25

    def test_area_matches_computed_wetted_area(self):
        """填充多边形面积必须等于 section_geom 算出的过水面积（关键交叉验证）"""
        x = [0.0, 30.0, 40.0, 60.0, 80.0]
        z = [102.0, 96.0, 94.0, 98.0, 103.0]
        for H in (94.5, 96.0, 97.0, 99.0, 101.0):
            A, _P, _B = section_geom(x, z, H)
            total = sum(_poly_area(p) for p in wetted_polygons(x, z, H))
            self.assertAlmostEqual(total, A, places=9,
                                   msg=f"H={H} 时填充面积 {total} != 过水面积 {A}")

    def test_island_yields_two_polygons(self):
        x = [0.0, 10.0, 20.0, 30.0, 40.0]
        z = [100.0, 95.0, 105.0, 95.0, 100.0]     # 中间露出的江心洲
        polys = wetted_polygons(x, z, 97.0)
        self.assertEqual(len(polys), 2)
        spans = sorted((min(p[0] for p in poly), max(p[0] for p in poly))
                       for poly in polys)
        self.assertAlmostEqual(spans[0][0], 6.0, places=9)      # 手算交点
        self.assertAlmostEqual(spans[0][1], 12.0, places=9)
        self.assertAlmostEqual(spans[1][0], 28.0, places=9)
        self.assertAlmostEqual(spans[1][1], 34.0, places=9)

    def test_dry_returns_empty(self):
        self.assertEqual(wetted_polygons([0.0, 10.0], [100.0, 95.0], 90.0), [])

    def test_fully_submerged_uses_sample_extent(self):
        x = [0.0, 10.0, 20.0]
        z = [100.0, 95.0, 100.0]
        polys = wetted_polygons(x, z, 105.0)     # 全淹，水面到不了岸顶
        self.assertEqual(len(polys), 1)
        xs = [p[0] for p in polys[0]]
        self.assertAlmostEqual(min(xs), 0.0, places=9)
        self.assertAlmostEqual(max(xs), 20.0, places=9)


class TestMatlabColon(unittest.TestCase):

    def test_basic(self):
        v = matlab_colon(0.0, 0.1, 1.0)
        self.assertEqual(len(v), 11)
        self.assertAlmostEqual(v[0], 0.0)
        self.assertAlmostEqual(v[-1], 1.0)

    def test_empty(self):
        self.assertEqual(matlab_colon(5.0, 0.1, 1.0), [])


class TestTerrain(unittest.TestCase):

    def setUp(self):
        self.sec = make_compound_section()
        self.cfg = Config()
        self.info = analyze_terrain(self.sec, self.cfg)

    def test_thalweg(self):
        self.assertEqual(self.info.dmin_idx, 3)
        self.assertAlmostEqual(self.info.dmin, 97.0)

    def test_peaks(self):
        self.assertEqual(self.info.zmax_idx, 1)
        self.assertAlmostEqual(self.info.zmax, 102.5)
        self.assertEqual(self.info.ymax_idx, 6)
        self.assertAlmostEqual(self.info.ymax, 102.8)
        self.assertAlmostEqual(self.info.zymin, 102.5)

    def test_turning_points(self):
        self.assertEqual(self.info.left_turn_idx, 1)
        self.assertEqual(self.info.right_turn_idx, 5)

    def test_disaster_level(self):
        self.assertAlmostEqual(self.info.disaster_level, 102.5)
        self.assertEqual(self.info.disaster_idx, 1)

    def test_zones_share_boundary_points(self):
        """分区共享转折点（{1:i1, i1:i2, i2:n}）。

        绝不能改成不重叠的 {1:i1, i1+1:i2, i2+1:n}：
        那样转折点两侧的线段不属于任何分区，会整段漏算面积。
        """
        self.assertEqual(self.info.zones, [(0, 2), (1, 6), (5, 7)])

    def test_zones_cover_every_segment(self):
        """核心不变量：所有床面线段必须被分区**不重不漏**地覆盖。

        曾经把分区改成不重叠，索引覆盖看着是完整的，但线段只覆盖 4/6，
        漏掉了转折点两侧的 (1,2) 与 (5,6)。面积因此少算最多 19%。
        所以这里断言的是**线段**覆盖，而不是索引覆盖。
        """
        n = self.sec.n_points
        segs = set()
        for a, b in self.info.zones:
            for i in range(a, b - 1):
                segs.add((i, i + 1))
        self.assertEqual(segs, {(i, i + 1) for i in range(n - 1)})

    def test_zones_cover_every_segment_single_turning_point(self):
        """只有一个转折点时同样要覆盖全部线段"""
        s = [0.0, 20.0, 25.0, 30.0, 40.0, 45.0, 65.0]
        z = [102.0, 102.5, 98.0, 97.0, 98.0, 102.5, 101.0]
        sec = Section(name="ONE", x=list(s), y=[0.0] * len(s), s=s, z=z,
                      params=SectionParams(name="ONE"))
        info = analyze_terrain(sec, Config())
        n = sec.n_points
        segs = set()
        for a, b in info.zones:
            for i in range(a, b - 1):
                segs.add((i, i + 1))
        self.assertEqual(segs, {(i, i + 1) for i in range(n - 1)})

    def test_zone_area_sum_equals_whole_section(self):
        """分区求和面积 == 整断面面积（跨水位全范围）。

        这是锁住"线段漏算"最有力的断言：只要有任何线段没被某个分区计入，
        求和就会小于整断面面积，立刻报错。
        """
        lo, hi = self.info.dmin, self.info.zymin
        for k in range(1, 20):
            H = lo + (hi - lo) * k / 20.0
            A_whole, P_whole, B_whole = section_geom(self.sec.s, self.sec.z, H)
            A_sum = P_sum = B_sum = 0.0
            for a, b in self.info.zones:
                if b - a < 2:
                    continue
                aa, pp, bb = section_geom(self.sec.s[a:b], self.sec.z[a:b], H)
                A_sum += aa; P_sum += pp; B_sum += bb
            self.assertAlmostEqual(A_sum, A_whole, places=9,
                                   msg=f"H={H:.3f} 时分区面积和 {A_sum} != 整断面 {A_whole}")
            self.assertAlmostEqual(P_sum, P_whole, places=9, msg=f"H={H:.3f} 湿周不一致")
            self.assertAlmostEqual(B_sum, B_whole, places=9, msg=f"H={H:.3f} 顶宽不一致")


class TestRating(unittest.TestCase):

    def setUp(self):
        self.sec = make_compound_section()
        self.cfg = Config()
        self.info = analyze_terrain(self.sec, self.cfg)

    def test_geometry_at_known_level(self):
        """H = 98.0 时，仅中间分区过水，手工核算 A / P / B"""
        hvec, qvec, avec, pvec, bvec = compute_rating_curve(self.sec, self.info, self.cfg)
        idx = min(range(len(hvec)), key=lambda i: abs(hvec[i] - 98.0))
        self.assertAlmostEqual(hvec[idx], 98.0, places=9)

        # 主槽区 (2,6)：s=[25,30,40,45], z=[98,97,98,102.5]
        #   25->30 全淹没: A+=2.5,  P+=sqrt(26)
        #   30->40 全淹没: A+=5.0,  P+=sqrt(101)
        #   40->45 全在水上（H <= min(98,102.5)）：跳过
        exp_a = 2.5 + 5.0
        exp_p = math.sqrt(5**2 + 1**2) + math.sqrt(10**2 + 1**2)
        exp_b = 5.0 + 10.0
        self.assertAlmostEqual(avec[idx], exp_a, places=9)
        self.assertAlmostEqual(pvec[idx], exp_p, places=9)
        self.assertAlmostEqual(bvec[idx], exp_b, places=9)

    def test_manning_formula_per_zone(self):
        """核对分区求和逻辑：Q = Σ Q_zone，每区独立套曼宁公式。

        注意：复式断面不能用「总 A / 总 P」套一次公式，那是错的——
        这正是原 MATLAB 把断面分区的原因（滩地流速远低于主槽）。
        """
        hvec, qvec, *_ = compute_rating_curve(self.sec, self.info, self.cfg)
        zones = self.info.zones
        for i, H in enumerate(hvec):
            q_ref = 0.0
            for a, b in zones:
                if b - a < 2:
                    continue
                A, P, _B = section_geom(self.sec.s[a:b], self.sec.z[a:b], H)
                if A <= 0:
                    continue
                R = A / P
                q_ref += (1.0 / 0.03) * A * (R ** (2.0 / 3.0)) * math.sqrt(0.005)
            self.assertAlmostEqual(qvec[i], q_ref,
                                   delta=abs(q_ref) * 1e-12 + 1e-12,
                                   msg=f"H={H:.4f} 处分区求和不一致")

    def test_single_zone_level_matches_total_formula(self):
        """只有一个分区过水时，分区求和应退化为整体一次曼宁公式"""
        hvec, qvec, avec, pvec, *_ = compute_rating_curve(self.sec, self.info, self.cfg)
        idx = min(range(len(hvec)), key=lambda i: abs(hvec[i] - 98.0))
        self.assertAlmostEqual(hvec[idx], 98.0, places=9)
        R = avec[idx] / pvec[idx]
        q_ref = (1.0 / 0.03) * avec[idx] * (R ** (2.0 / 3.0)) * math.sqrt(0.005)
        self.assertAlmostEqual(qvec[idx], q_ref, delta=abs(q_ref) * 1e-12 + 1e-12)

    def test_curve_monotonic(self):
        hvec, qvec, *_ = compute_rating_curve(self.sec, self.info, self.cfg)
        for i in range(1, len(qvec)):
            self.assertGreaterEqual(qvec[i], qvec[i - 1] - 1e-12,
                                    f"Q 在索引 {i} (H={hvec[i]:.3f}) 处下降")

    def test_single_mode_zone(self):
        cfg = Config(compound_mode=False)
        hvec, qvec, *_ = compute_rating_curve(self.sec, self.info, cfg)
        self.assertEqual(len(hvec), len(qvec))
        self.assertGreater(len(hvec), 1)

    def test_single_mode_top_width_is_span(self):
        """单断面模式的顶宽 = 水面左右交点跨度（x_right - x_left）。

        用一个"江心洲"断面制造不连续水面：两种口径必须不同，
        否则说明模式切换没生效。
        """
        s = [0.0, 10.0, 20.0, 30.0, 40.0]
        z = [100.0, 95.0, 105.0, 95.0, 100.0]      # 中间 105 露出水面
        sec = Section(name="ISLAND", x=list(s), y=[0.0] * 5, s=s, z=z,
                      params=SectionParams(name="ISLAND", slope=0.005,
                                           roughness=0.03, design_q=10.0))
        info = analyze_terrain(sec, Config())

        cfg = Config(compound_mode=False)
        hvec, _q, _a, _p, bvec = compute_rating_curve(sec, info, cfg)
        i = min(range(len(hvec)), key=lambda k: abs(hvec[k] - 97.0))

        # 水面交点：左 6，右 34 -> 跨度 28
        self.assertAlmostEqual(bvec[i], 28.0, places=6)

        # 对照：逐段 dx 累加口径为 12（不含中间干出的洲）
        _A, _P, B_acc = section_geom(sec.s, sec.z, hvec[i])
        self.assertAlmostEqual(B_acc, 12.0, places=6)
        self.assertNotAlmostEqual(bvec[i], B_acc, places=3)

    def test_compound_mode_top_width_is_dx_accumulation(self):
        """复式模式的顶宽 = 各分区逐段 dx 累加"""
        hvec, _q, _a, _p, bvec = compute_rating_curve(self.sec, self.info,
                                                      Config(compound_mode=True))
        i = min(range(len(hvec)), key=lambda k: abs(hvec[k] - 98.0))
        self.assertAlmostEqual(bvec[i], 15.0, places=6)   # 5 + 10


class TestInterp(unittest.TestCase):

    def test_inside(self):
        self.assertAlmostEqual(interp1_linear_extrap([0.0, 10.0], [0.0, 20.0], 5.0), 10.0)

    def test_extrap_below(self):
        self.assertAlmostEqual(interp1_linear_extrap([0.0, 10.0], [0.0, 20.0], -5.0), -10.0)

    def test_extrap_above(self):
        self.assertAlmostEqual(interp1_linear_extrap([0.0, 10.0], [0.0, 20.0], 15.0), 30.0)

    def test_water_edge(self):
        z = [102.0, 102.5, 98.0, 97.0, 98.0, 102.5, 102.8]
        # 向左：从 index 3 出发，z[2]=98 <= 100 且 z[1]=102.5 > 100
        self.assertEqual(find_water_edge(z, 100.0, 3, "left"), (2, 1))
        # 向右：z[5]=102.5 > 100 -> 继续; z[5] 不满足，先查 z[4]=98<=100, z[5]=102.5>100
        self.assertEqual(find_water_edge(z, 100.0, 3, "right"), (4, 5))


class TestSolver(unittest.TestCase):

    def setUp(self):
        self.sec = make_compound_section()
        self.cfg = Config()

    def test_full_solve(self):
        res, info = solve_section(self.sec, self.cfg)
        self.assertEqual(len(res.hvec), len(res.qvec))
        self.assertTrue(math.isfinite(res.design_level))
        self.assertAlmostEqual(res.design_level_plus, res.design_level + 1.0)
        self.assertIsNotNone(res.left_point)
        self.assertIsNotNone(res.right_point)
        self.assertIn(res.left_status, (self.cfg.ICON_FOUND, self.cfg.ICON_NOT_FOUND))
        self.assertIn(res.right_status, (self.cfg.ICON_FOUND, self.cfg.ICON_NOT_FOUND))

    def test_raise_level_configurable(self):
        """加高水位 = 设计水位 + 加高幅度，幅度可配置（原固定 +1.0）。"""
        cfg = Config()
        cfg.raise_level = 2.5
        res, _ = solve_section(self.sec, cfg)
        self.assertAlmostEqual(res.design_level_plus, res.design_level + 2.5)

    def test_raise_enabled_only_affects_drawing(self):
        """关闭加高水位线开关只隐藏线，不改变计算结果（Hs1 仍 = Hs + 幅度）。"""
        cfg_on = Config()
        cfg_on.raise_level = 1.5
        cfg_on.raise_enabled = True
        res_on, _ = solve_section(self.sec, cfg_on)

        cfg_off = Config()
        cfg_off.raise_level = 1.5
        cfg_off.raise_enabled = False
        res_off, _ = solve_section(self.sec, cfg_off)

        # 计算结果必须一致（开关仅影响绘图）
        self.assertAlmostEqual(res_on.design_level_plus, res_off.design_level_plus)
        self.assertAlmostEqual(res_off.design_level_plus,
                               res_off.design_level + 1.5)

    def test_validate(self):
        self.assertEqual(self.sec.validate(), [])

    def test_bad_section_reports(self):
        bad = Section(name="BAD", x=[0.0, 1.0], y=[0.0, 1.0], s=[0.0, 1.0], z=[10.0, 9.0],
                      params=SectionParams(name="BAD"))
        errs = bad.validate()
        self.assertTrue(any("糙率" in e for e in errs))
        self.assertTrue(any("比降" in e for e in errs))
        self.assertTrue(any("设计流量" in e for e in errs))


class TestChainage(unittest.TestCase):
    """Q5/Q9：每条纵断面线独立编桩号，原点 = 该线内深泓高程最低的断面"""

    def _mk(self, name, px, py, dmin_z):
        return (Section(name=name, x=[px], y=[py], s=[0.0], z=[dmin_z],
                        params=SectionParams(name=name)), px, py, dmin_z)

    def test_origin_at_lowest(self):
        from core.model import TerrainInfo
        secs, infos = [], []
        for name, px, py, dz in [("A", 0.0, 0.0, 100.0),
                                 ("B", 30.0, 40.0, 95.0),
                                 ("C", 30.0, 90.0, 98.0)]:
            s = Section(name=name, x=[px], y=[py], s=[0.0], z=[dz],
                        params=SectionParams(name=name))
            i = TerrainInfo(dmin=dz, dmin_idx=0)
            secs.append(s)
            infos.append(i)
        ch = compute_chainage(secs, infos, origin="lowest")
        self.assertAlmostEqual(ch[1], 0.0)          # B 最低 -> 原点
        self.assertAlmostEqual(ch[0], 50.0)         # A 到 B 距离 sqrt(30^2+40^2)
        self.assertAlmostEqual(ch[2], 50.0)         # C 到 B 距离
        self.assertTrue(all(c >= 0 for c in ch))

    def test_origin_first(self):
        from core.model import TerrainInfo
        secs, infos = [], []
        for name, px, py, dz in [("A", 0.0, 0.0, 100.0),
                                 ("B", 30.0, 40.0, 95.0)]:
            secs.append(Section(name=name, x=[px], y=[py], s=[0.0], z=[dz],
                                params=SectionParams(name=name)))
            infos.append(TerrainInfo(dmin=dz, dmin_idx=0))
        ch = compute_chainage(secs, infos, origin="first")
        self.assertAlmostEqual(ch[0], 0.0)
        self.assertAlmostEqual(ch[1], 50.0)


class TestExporter(unittest.TestCase):

    def test_csv_outputs(self):
        import tempfile
        sec = make_compound_section()
        cfg = Config()
        res, info = solve_section(sec, cfg)

        with tempfile.TemporaryDirectory() as td:
            p1 = os.path.join(td, "inund.csv")
            exporter.export_inundation_csv(p1, [sec], [res], [info], cfg)
            with open(p1, encoding="utf-8-sig") as f:
                lines = f.read().strip().splitlines()
            self.assertEqual(lines[0], "名称,平面坐标X,平面坐标Y,图标样式")
            self.assertTrue(lines[1].startswith("CS01Z"))
            self.assertTrue(lines[2].startswith("CS01Y"))

            p2 = os.path.join(td, "rating.csv")
            exporter.export_rating_csv(p2, [sec], [res])
            with open(p2, encoding="utf-8-sig") as f:
                txt = f.read()
            self.assertIn("断面=CS01", txt)
            self.assertIn("水位/m,流量/m3/s,面积/m2,湿周,顶宽/m", txt)

            p3 = os.path.join(td, "endpoint.csv")
            exporter.export_endpoint_csv(p3, [sec], [res])
            with open(p3, encoding="utf-8-sig") as f:
                lines = f.read().strip().splitlines()
            self.assertEqual(lines[0], "名称,平面坐标[X+Y],百年一遇水位（m）")
            self.assertIn('"500.000,100.000;565.000,165.000"', lines[1])

    def test_csv_has_utf8_bom(self):
        """回归：中文 Windows 的 Excel 按 ANSI(GBK) 解 CSV，无 BOM 就全乱码。

        必须以 EF BB BF 开头，Excel/WPS 才会按 UTF-8 解码。
        这条一旦被改回 "utf-8" 会立刻报错——这是用户实际踩过的坑。
        """
        import tempfile
        sec = make_compound_section()
        cfg = Config()
        res, info = solve_section(sec, cfg)
        self.assertEqual(cfg.csv_encoding, "utf-8-sig")   # 默认值本身也是断言点

        with tempfile.TemporaryDirectory() as td:
            for fn, call in (
                ("a.csv", lambda p: exporter.export_inundation_csv(
                    p, [sec], [res], [info], cfg)),
                ("b.csv", lambda p: exporter.export_rating_csv(
                    p, [sec], [res], cfg.csv_encoding)),
                ("c.csv", lambda p: exporter.export_endpoint_csv(
                    p, [sec], [res], cfg.csv_encoding)),
            ):
                p = os.path.join(td, fn)
                call(p)
                with open(p, "rb") as f:
                    head = f.read(3)
                self.assertEqual(head, b"\xef\xbb\xbf",
                                 f"{fn} 缺少 UTF-8 BOM，Excel 打开会乱码")

                # 模拟 Excel：按 GBK 解读无 BOM 文件得到的正是乱码形态，
                # 有了 BOM 则应能按 UTF-8 正确还原中文
                with open(p, encoding="utf-8-sig") as f:
                    txt = f.read()
                self.assertNotIn("\ufffd", txt)
                self.assertTrue(any(ch in txt for ch in "名称断面水位"),
                                f"{fn} 未还原出中文")

    def test_csv_gbk_option(self):
        """切换到 GBK 时不应写 BOM，且中文可按 GBK 正确解码。"""
        import tempfile
        sec = make_compound_section()
        cfg = Config(csv_encoding="gbk")
        res, info = solve_section(sec, cfg)

        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "gbk.csv")
            exporter.export_inundation_csv(p, [sec], [res], [info], cfg)
            with open(p, "rb") as f:
                raw = f.read()
            self.assertNotEqual(raw[:3], b"\xef\xbb\xbf")
            self.assertIn("名称", raw.decode("gbk"))

    def test_range_csv_design_vs_raised(self):
        """设计水位(Hs)与设计水位加高(Hs1)是两套淹没交点。

        关键不变量：加高幅度为 0 时 Hs1 == Hs，两套交点必须完全重合。
        这条能兜住"导出时拿错了水位口径"。
        """
        import tempfile
        sec = make_compound_section()
        cfg = Config()
        res, info = solve_section(sec, cfg)

        self.assertIsNotNone(res.left_point_hs)
        self.assertIsNotNone(res.right_point_hs)
        # 默认加高 1 m：水位更高 -> 交点更靠岸，必然与 Hs 的不同
        self.assertNotEqual(res.left_point, res.left_point_hs)
        self.assertNotEqual(res.right_point, res.right_point_hs)

        with tempfile.TemporaryDirectory() as td:
            p_raise = os.path.join(td, "raised.csv")
            exporter.export_range_csv(p_raise, [sec], [res], cfg, raised=True)
            p_design = os.path.join(td, "design.csv")
            exporter.export_range_csv(p_design, [sec], [res], cfg, raised=False)
            with open(p_raise, encoding="utf-8-sig") as f:
                r_rows = f.read().strip().splitlines()
            with open(p_design, encoding="utf-8-sig") as f:
                d_rows = f.read().strip().splitlines()

            head = "名称,平面坐标X,平面坐标Y,图标样式"
            self.assertEqual(r_rows[0], head)
            self.assertEqual(d_rows[0], head)
            # 只有 Z/Y 两行，不含成灾水位
            self.assertEqual(len(r_rows), 3)
            self.assertEqual(len(d_rows), 3)
            self.assertTrue(r_rows[1].startswith("CS01Z"))
            self.assertTrue(r_rows[2].startswith("CS01Y"))
            self.assertNotIn("成灾", "".join(r_rows))
            self.assertNotIn("成灾", "".join(d_rows))

        # 加高幅度 0 -> Hs1 == Hs，两套交点重合
        cfg0 = Config(raise_level=0.0)
        res0, _ = solve_section(sec, cfg0)
        self.assertAlmostEqual(res0.design_level_plus, res0.design_level)
        self.assertAlmostEqual(res0.left_point[0], res0.left_point_hs[0], places=9)
        self.assertAlmostEqual(res0.left_point[1], res0.left_point_hs[1], places=9)
        self.assertAlmostEqual(res0.right_point[0], res0.right_point_hs[0], places=9)
        self.assertAlmostEqual(res0.right_point[1], res0.right_point_hs[1], places=9)

    def test_disaster_csv_uses_own_section_index(self):
        """回归：成灾坐标必须取**本断面**的 disaster_idx。

        旧实现遍历全部 infos 取最后一个满足边界条件的，索引会串到别的断面，
        同一条线上各断面点数相近时几乎每个都错；单断面测试恰好掩盖了它。
        """
        import tempfile
        z = [105.0, 101.0, 100.0, 101.0, 105.0]
        s = [0.0, 1.0, 2.0, 3.0, 4.0]
        sec_a = Section(name="A", x=[0.0, 1.0, 2.0, 3.0, 4.0],
                        y=[0.0, 1.0, 2.0, 3.0, 4.0], s=s, z=z,
                        params=SectionParams(name="A"))
        sec_b = Section(name="B", x=[100.0, 101.0, 102.0, 103.0, 104.0],
                        y=[200.0, 201.0, 202.0, 203.0, 204.0], s=s, z=z,
                        params=SectionParams(name="B"))
        # 两个断面的成灾索引故意不同（1 与 3）
        info_a = TerrainInfo(disaster_idx=1, disaster_level=101.0)
        info_b = TerrainInfo(disaster_idx=3, disaster_level=101.0)

        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "disaster.csv")
            exporter.export_disaster_csv(p, [sec_a, sec_b],
                                         [SectionResult(), SectionResult()],
                                         [info_a, info_b], Config())
            with open(p, encoding="utf-8-sig") as f:
                rows = f.read().strip().splitlines()

        self.assertEqual(rows[0], "名称,平面坐标X,平面坐标Y,图标样式")
        self.assertEqual(len(rows), 3)
        # A 用 idx=1 -> (1,1)；B 用 idx=3 -> (103,203)
        self.assertTrue(rows[1].startswith("A成灾水位"))
        self.assertIn("1.000000,1.000000", rows[1])
        self.assertTrue(rows[2].startswith("B成灾水位"))
        self.assertIn("103.000000,203.000000", rows[2])

    def test_export_all_writes_six_files(self):
        """每条纵断面线现在输出 6 个文件（原 3 个 + 新增 3 个）。

        成灾水位开关关掉时少 1 个。
        """
        import tempfile
        sec = make_compound_section()
        cfg = Config()
        res, info = solve_section(sec, cfg)
        line = ProfileLine(name="L1", sections=[sec])
        proj = Project(profile_lines=[line])

        with tempfile.TemporaryDirectory() as td:
            written = exporter.export_all(proj, {sec.name: res}, {sec.name: info},
                                          cfg, td)
            names = sorted(os.path.basename(p) for p in written)
            self.assertEqual(len(names), 6)
            self.assertIn("L1设计水位加高淹没范围坐标.csv", names)
            self.assertIn("L1设计水位淹没范围坐标.csv", names)
            self.assertIn("L1成灾水位坐标.csv", names)
            self.assertIn("L1水位流量关系曲线.csv", names)

            cfg2 = Config(output_disaster_level=False)
            written2 = exporter.export_all(proj, {sec.name: res}, {sec.name: info},
                                           cfg2, os.path.join(td, "n2"))
            names2 = sorted(os.path.basename(p) for p in written2)
            self.assertEqual(len(names2), 5)
            self.assertNotIn("L1成灾水位坐标.csv", names2)


def make_raw_two_blocks() -> list[list]:
    """模拟真实表结构：块与块紧贴、无空行。

    行1 断面编号|yqc6-1   行2 列头   行3-4 数据
    行5 断面编号|yqc6-2   行6 列头   行7-8 数据
    """
    return [
        ["断面编号", "yqc6-1"],
        ["X坐标", "Y坐标", "起点距", "高程"],
        ["1", "2", "0", "10"],
        ["1", "2", "1", "9"],
        ["断面编号", "yqc6-2"],
        ["X坐标", "Y坐标", "起点距", "高程"],
        ["1", "2", "0", "8"],
        ["1", "2", "1", "7"],
    ]


class TestBlockSplit(unittest.TestCase):
    """切块 + 分类（Q11 非编号块排除，D9 末测点开关）"""

    def test_classify(self):
        cfg = Config()
        self.assertEqual(classify("yqc6-1", cfg), "cross")
        self.assertEqual(classify("SJC4-12", cfg), "cross")
        self.assertEqual(classify("纵断面", cfg), "profile")
        self.assertEqual(classify("桥", cfg), "skip")

    def test_classify_numbered_profile_blocks(self):
        """带编号的纵剖面块也要认出来（2三凌山有 纵断面1/2/3）。

        曾用精确相等匹配，导致这些块被当成"非编号块"排除，
        整个文件的横断面都找不到归属的纵断面。
        """
        cfg = Config()
        self.assertEqual(classify("纵断面1", cfg), "profile")
        self.assertEqual(classify("纵断面2", cfg), "profile")
        self.assertEqual(classify("纵断面12", cfg), "profile")
        self.assertEqual(classify("纵断面数据", cfg), "profile")

    def test_classify_bridge_with_number_still_skipped(self):
        """桥1/桥2 仍按 Q11 排除（不符合「前缀-序号」的横断面命名）"""
        cfg = Config()
        self.assertEqual(classify("桥1", cfg), "skip")
        self.assertEqual(classify("桥", cfg), "skip")

    def test_keep_all_points_by_default(self):
        cfg = Config()                       # compat_drop_last_point 默认 False
        blocks = split_blocks(make_raw_two_blocks(), cfg)
        self.assertEqual([b.name for b in blocks], ["yqc6-1", "yqc6-2"])
        self.assertEqual([len(b.rows) for b in blocks], [2, 2])

    def test_compat_drop_last_point(self):
        """D9：打开开关后应与 MATLAB 一致——非末尾块少一行，末尾块完整"""
        cfg = Config(compat_drop_last_point=True)
        blocks = split_blocks(make_raw_two_blocks(), cfg)
        self.assertEqual([len(b.rows) for b in blocks], [1, 2])

    def test_non_numbered_excluded(self):
        """Q11：「桥」这类非编号块排除；「纵断面」转作剖面数据"""
        raw = make_raw_two_blocks() + [
            ["断面编号", "桥"],
            ["X坐标", "Y坐标", "起点距", "高程"],
            ["1", "2", "0", "5"],
            ["1", "2", "1", "4"],
            ["断面编号", "纵断面"],
            ["X坐标", "Y坐标", "起点距", "高程"],
            ["1", "2", "0", "100"],
            ["1", "2", "50", "95"],
            ["1", "2", "90", "90"],
        ]
        cfg = Config()
        blocks = split_blocks(raw, cfg)
        parsed = blocks_to_sections(blocks, cfg)

        self.assertEqual([s.name for s in parsed.sections], ["yqc6-1", "yqc6-2"])
        self.assertEqual(parsed.skipped, ["桥"])
        self.assertEqual(len(parsed.profiles), 1)
        self.assertEqual(parsed.profiles[0].dist, [0.0, 50.0, 90.0])
        self.assertEqual(parsed.profiles[0].z, [100.0, 95.0, 90.0])

    def test_swap_xy(self):
        """swap_xy=True 时 X 取第 2 列、Y 取第 1 列"""
        cfg = Config(swap_xy=True)
        blocks = split_blocks(make_raw_two_blocks(), cfg)
        secs = blocks_to_sections(blocks, cfg).sections
        self.assertEqual(secs[0].x, [2.0, 2.0])
        self.assertEqual(secs[0].y, [1.0, 1.0])

    def test_multiple_profile_blocks_preserved(self):
        """一个文件含多个「纵断面」块时，全部保留（曾经只留最后一个）。

        真实情况：2三凌山.xlsx 有 3 个纵断面块、铜山溪沟7 有 2 个。
        """
        raw = [
            ["断面编号", "纵断面"],
            ["X坐标", "Y坐标", "起点距", "高程"],
            ["0", "0", "0", "100"],
            ["0", "0", "50", "95"],
            ["断面编号", "A-1"],
            ["X坐标", "Y坐标", "起点距", "高程"],
            ["0", "0", "0", "10"],
            ["0", "0", "1", "9"],
            ["断面编号", "纵断面"],
            ["X坐标", "Y坐标", "起点距", "高程"],
            ["0", "0", "0", "200"],
            ["0", "0", "30", "190"],
            ["断面编号", "A-2"],
            ["X坐标", "Y坐标", "起点距", "高程"],
            ["0", "0", "0", "20"],
            ["0", "0", "1", "19"],
        ]
        cfg = Config()
        parsed = blocks_to_sections(split_blocks(raw, cfg), cfg)
        self.assertEqual(len(parsed.profiles), 2)
        self.assertEqual(parsed.profiles[0].z, [100.0, 95.0])
        self.assertEqual(parsed.profiles[1].z, [200.0, 190.0])
        self.assertEqual([s.name for s in parsed.sections], ["A-1", "A-2"])
        # 顺序信息：纵断面0, 断面A-1, 纵断面1, 断面A-2
        self.assertEqual(parsed.seq,
                         [("profile", 0), ("cross", 0), ("profile", 1), ("cross", 1)])

    def test_build_lines_multi_profile_spatial(self):
        """一个文件含 2 条纵断面线时，按相交关系把横断面分给各自那条。

        模拟 2三凌山.xlsx：一个文件里 3 个「纵断面」块、11 个横断面。
        """
        from core.reader import _build_lines, ParsedBlocks
        from core.model import ProfileData

        # 纵断面 A 在 y=0，纵断面 B 在 y=100，都是沿 x 的水平线
        pA = ProfileData(x=[0.0, 100.0], y=[0.0, 0.0],
                         dist=[0.0, 100.0], z=[50.0, 45.0])
        pB = ProfileData(x=[0.0, 100.0], y=[100.0, 100.0],
                         dist=[0.0, 100.0], z=[60.0, 55.0])

        def mk(name, y0):
            return Section(name=name, x=[10.0, 10.0], y=[y0 - 5, y0 + 5],
                           s=[0.0, 10.0], z=[0.0, 0.0],
                           params=SectionParams(name=name))

        secs = [mk("sls2-1", 0.0), mk("sls2-2", 0.0), mk("sls2-3", 100.0)]
        parsed = ParsedBlocks(sections=secs, profiles=[pA, pB],
                              seq=[("profile", 0), ("cross", 0), ("cross", 1),
                                   ("profile", 1), ("cross", 2)])
        cfg = Config()
        warnings: list[str] = []
        lines = _build_lines([("2三凌山", parsed)], cfg, warnings)

        self.assertEqual(len(lines), 2)
        by_members = {tuple(s.name for s in ln.sections) for ln in lines}
        self.assertIn(("sls2-1", "sls2-2"), by_members)
        self.assertIn(("sls2-3",), by_members)
        # 两条线同名 -> 加 -段N
        self.assertEqual(sorted(ln.name for ln in lines), ["sls2-段1", "sls2-段2"])

    def test_duplicate_line_names_get_segment_suffix(self):
        """同名纵断面线加 -段N 后缀，避免下拉框里分不清"""
        out = _dedupe_names(["sls2", "yqc6", "sls2", "sls2"])
        self.assertEqual(out, ["sls2-段1", "yqc6", "sls2-段2", "sls2-段3"])


class TestChainageOrigin(unittest.TestCase):
    """Q10：起始桩号端点可选"""

    def setUp(self):
        self.dist = [0.0, 10.0, 20.0]
        self.z = [100.0, 95.0, 90.0]      # 最低点在末端

    def test_origin_start(self):
        self.assertEqual(rebase_chainage(self.dist, self.z, "start"), [0.0, 10.0, 20.0])

    def test_origin_end(self):
        self.assertEqual(rebase_chainage(self.dist, self.z, "end"), [20.0, 10.0, 0.0])

    def test_origin_lowest(self):
        # 最低点在末端 -> 与 "end" 同向
        self.assertEqual(rebase_chainage(self.dist, self.z, "lowest"), [20.0, 10.0, 0.0])

    def test_origin_lowest_middle(self):
        # 最低点在中间 -> 从该点向两侧递增，恒非负
        z = [100.0, 90.0, 95.0]
        self.assertEqual(rebase_chainage(self.dist, z, "lowest"), [10.0, 0.0, 10.0])

    def test_unknown_origin(self):
        with self.assertRaises(ValueError):
            rebase_chainage(self.dist, self.z, "middle")


def _sec(name, xs, ys):
    """用平面坐标快速造一个断面（s/z 给占位值，本组测试只关心几何相交）"""
    return Section(name=name, x=list(xs), y=list(ys),
                   s=[0.0, float(len(xs) - 1)], z=[0.0] * len(xs),
                   params=SectionParams(name=name))


class TestSpatial(unittest.TestCase):
    """横断面 × 纵断面 相交判定与空间分组"""

    def test_segment_intersection(self):
        self.assertIsNotNone(segment_intersection((0, 0), (10, 0), (5, -1), (5, 1)))
        self.assertIsNone(segment_intersection((0, 0), (10, 0), (0, 5), (10, 5)))  # 平行
        self.assertIsNone(segment_intersection((0, 0), (1, 0), (5, 0), (6, 0)))    # 不重叠

    def test_find_intersection_position(self):
        prof = [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)]
        sec = [(20.0, -10.0), (20.0, 10.0)]
        r = find_intersection(prof, sec)
        self.assertIsNotNone(r)
        d_along, gap, pt = r
        self.assertAlmostEqual(d_along, 20.0, places=6)
        self.assertAlmostEqual(gap, 0.0, places=9)
        self.assertAlmostEqual(pt[0], 20.0, places=6)

    def test_tolerance_for_near_miss(self):
        prof = [(0.0, 0.0), (100.0, 0.0)]
        sec = [(50.0, 2.0), (50.0, 10.0)]      # 差 2 m 没碰到
        self.assertIsNone(find_intersection(prof, sec, tol=1.0))
        r = find_intersection(prof, sec, tol=3.0)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r[0], 50.0, places=6)

    def test_assign_by_intersection(self):
        pa = ProfileData(x=[0.0, 50.0, 100.0], y=[0.0, 0.0, 0.0],
                         dist=[0.0, 50.0, 100.0], z=[100.0, 95.0, 90.0])
        pb = ProfileData(x=[0.0, 50.0, 100.0], y=[50.0, 50.0, 50.0],
                         dist=[0.0, 50.0, 100.0], z=[80.0, 75.0, 70.0])
        secs = [
            _sec("A-1", [20, 20], [-10, 10]),      # 交 pa
            _sec("A-2", [70, 70], [-10, 10]),      # 交 pa
            _sec("A-3", [200, 200], [-10, 10]),    # 谁都不交
            _sec("B-1", [30, 30], [40, 60]),       # 交 pb
        ]
        groups, details = assign_by_intersection(secs, [pa, pb], tol=1.0)

        self.assertEqual(groups[0], [0, 1])
        self.assertEqual(groups[1], [3])
        self.assertIsNone(details[2]["profile"])
        self.assertAlmostEqual(details[0]["dist"], 20.0, places=6)
        self.assertAlmostEqual(details[1]["dist"], 70.0, places=6)

    def test_sections_sorted_by_mileage(self):
        """交点里程应能排出沿河顺序"""
        pa = ProfileData(x=[0.0, 50.0, 100.0], y=[0.0, 0.0, 0.0],
                         dist=[0.0, 50.0, 100.0], z=[100.0, 95.0, 90.0],
                         chainage=[100.0, 50.0, 0.0], origin="lowest")
        # 故意乱序给出
        secs = [_sec("A-2", [70, 70], [-10, 10]),
                _sec("A-1", [20, 20], [-10, 10]),
                _sec("A-3", [45, 45], [-10, 10])]
        groups, details = assign_by_intersection(secs, [pa], tol=1.0)
        order = sorted(range(3), key=lambda i: details[i]["dist"])
        self.assertEqual([secs[i].name for i in order], ["A-1", "A-3", "A-2"])

    def test_chainage_at_distance(self):
        pa = ProfileData(x=[0.0, 50.0, 100.0], y=[0.0, 0.0, 0.0],
                         dist=[0.0, 50.0, 100.0], z=[100.0, 95.0, 90.0],
                         chainage=[100.0, 50.0, 0.0], origin="lowest")
        # 累计距离 20 落在 [0,50] 段，桩号从 100 线性降到 50 -> 80
        self.assertAlmostEqual(chainage_at_distance(pa, 20.0), 80.0, places=6)
        self.assertAlmostEqual(chainage_at_distance(pa, 75.0), 25.0, places=6)


class TestParams(unittest.TestCase):
    """Q12：参数手工填写 + 批量修改 + 后期导入合并"""

    def _secs(self):
        return [Section(name=f"CS{i}", x=[0.0, 1.0], y=[0.0, 1.0],
                        s=[0.0, 1.0], z=[10.0, 9.0],
                        params=SectionParams(name=f"CS{i}"))
                for i in range(3)]

    def test_set_one(self):
        secs = self._secs()
        params.set_one(secs[0], slope=0.005, roughness=0.03, design_q=50.0)
        self.assertAlmostEqual(secs[0].params.slope, 0.005)
        self.assertAlmostEqual(secs[0].params.roughness, 0.03)
        self.assertAlmostEqual(secs[0].params.design_q, 50.0)
        self.assertNotAlmostEqual(secs[1].params.slope, 0.005)

    def test_set_one_rejects_unknown(self):
        secs = self._secs()
        with self.assertRaises(KeyError):
            params.set_one(secs[0], bogus=1.0)

    def test_apply_batch(self):
        secs = self._secs()
        n = params.apply_batch(secs, roughness=0.035, slope=0.004)
        self.assertEqual(n, 3)
        for s in secs:
            self.assertAlmostEqual(s.params.roughness, 0.035)
            self.assertAlmostEqual(s.params.slope, 0.004)

    def test_apply_batch_only_missing(self):
        secs = self._secs()
        params.set_one(secs[0], roughness=0.05)          # 已填
        params.apply_batch(secs, roughness=0.03, only_missing=True)
        self.assertAlmostEqual(secs[0].params.roughness, 0.05)   # 不被覆盖
        self.assertAlmostEqual(secs[1].params.roughness, 0.03)   # 被填补

    def test_merge_keeps_override_and_fills_gaps(self):
        base = {"CS0": SectionParams("CS0", slope=0.004, roughness=0.03, design_q=40.0)}
        ov = {"CS0": SectionParams("CS0", slope=float("nan"), roughness=0.045,
                                   design_q=float("nan"))}
        merged = params.merge(base, ov)
        self.assertAlmostEqual(merged["CS0"].roughness, 0.045)   # 覆盖
        self.assertAlmostEqual(merged["CS0"].slope, 0.004)       # 留空则沿用
        self.assertAlmostEqual(merged["CS0"].design_q, 40.0)

    def test_to_rows_roundtrip(self):
        secs = self._secs()
        params.apply_batch(secs, slope=0.004, roughness=0.03, design_q=40.0)
        rows = params.to_rows(secs)
        d = params.from_rows(rows)
        self.assertEqual(sorted(d), ["CS0", "CS1", "CS2"])
        self.assertAlmostEqual(d["CS1"].roughness, 0.03)

    def test_missing_params_report(self):
        secs = self._secs()
        self.assertEqual(len(params.missing_params(secs)), 3)
        params.apply_batch(secs, slope=0.004, roughness=0.03, design_q=40.0)
        self.assertEqual(params.missing_params(secs), [])


class TestSlope(unittest.TestCase):
    """按纵断面推算平均比降（slope.py）。

    这是新增功能，口径由用户在界面上选（整线共用值 / 全部测点），
    所以测试重点是「两种口径各自算对」以及「二者必须不同」——
    如果哪天有人把它们合并成一个，下面的断言会立刻报错。
    """

    @staticmethod
    def _line(name="L1", dists=(0.0, 100.0, 200.0, 300.0, 400.0),
              zs=(100.0, 95.0, 90.0, 85.0, 80.0)):
        """构造一条纵断面线：直线河底，比降恰为 0.05。"""
        prof = ProfileData(x=[float(d) for d in dists],
                           y=[0.0] * len(dists),
                           dist=[float(d) for d in dists],
                           z=[float(v) for v in zs])
        s1 = Section(name="L1-1", x=[0.0, 1.0], y=[0.0, 0.0],
                     s=[0.0, 1.0], z=[100.0, 99.0],
                     params=SectionParams(name="L1-1"))
        s2 = Section(name="L1-2", x=[0.0, 1.0], y=[0.0, 0.0],
                     s=[0.0, 1.0], z=[90.0, 89.0],
                     params=SectionParams(name="L1-2", slope=0.999))
        ln = ProfileLine(name=name, sections=[s1, s2],
                         chainage=[0.0, 200.0], profile=prof,
                         profile_dist=[0.0, 200.0])
        return Project(profile_lines=[ln])

    # ---------------- 基础件 ----------------

    def test_clip_profile_inserts_endpoints(self):
        prof = self._line().profile_lines[0].profile
        ds, zs = slope.clip_profile(prof, 50.0, 250.0)
        self.assertEqual(ds[0], 50.0)
        self.assertEqual(ds[-1], 250.0)
        self.assertEqual(len(ds), len(zs))
        # 端点按线性插值：直线河底上，高程应为 97.5 / 87.5
        self.assertAlmostEqual(zs[0], 97.5, delta=TOL)
        self.assertAlmostEqual(zs[-1], 87.5, delta=TOL)

    def test_clip_profile_clamps_beyond_range(self):
        prof = self._line().profile_lines[0].profile
        ds, _ = slope.clip_profile(prof, -500.0, 9999.0)
        self.assertEqual((ds[0], ds[-1]), (0.0, 400.0))

    def test_clip_profile_empty_when_degenerate(self):
        prof = self._line().profile_lines[0].profile
        self.assertEqual(slope.clip_profile(prof, 100.0, 100.0), ([], []))

    def test_linear_fit_on_exact_line(self):
        k, r2 = slope.linear_fit([0.0, 1.0, 2.0, 3.0], [10.0, 8.0, 6.0, 4.0])
        self.assertAlmostEqual(k, 2.0, delta=TOL)     # 比降 = 2（取负号后）
        self.assertAlmostEqual(r2, 1.0, delta=TOL)

    def test_linear_fit_flat_returns_nan(self):
        k, r2 = slope.linear_fit([5.0, 5.0, 5.0], [1.0, 2.0, 3.0])
        self.assertTrue(math.isnan(k))
        self.assertTrue(math.isnan(r2))

    # ---------------- 两种口径 ----------------

    def test_straight_profile_both_modes_agree(self):
        """直线河底时两端点法与最小二乘必然一致（0.05）。"""
        prof = self._line().profile_lines[0].profile
        est = slope.estimate_line_slope(prof, line_name="L1")
        self.assertTrue(est.ok)
        self.assertAlmostEqual(est.length, 400.0, delta=TOL)
        self.assertAlmostEqual(est.drop, 20.0, delta=TOL)
        self.assertAlmostEqual(est.slope_endpoints, 0.05, delta=TOL)
        self.assertAlmostEqual(est.slope_lsq, 0.05, delta=TOL)
        self.assertAlmostEqual(est.r2, 1.0, delta=TOL)

    def test_curved_profile_modes_differ(self):
        """河底带坡折时，两种口径**必须**给出不同结果。

        两端点法只看首末两点 → 0.05；
        最小二乘用全部测点，受中间平台段影响 → 更小。
        这条断言锁住"口径不能合并"，也保证界面上的选择是有意义的。
        """
        prof = self._line(dists=(0.0, 50.0, 100.0, 300.0, 400.0),
                          zs=(100.0, 99.0, 98.0, 90.0, 80.0)).profile_lines[0].profile
        est = slope.estimate_line_slope(prof, line_name="L1")
        self.assertAlmostEqual(est.slope_endpoints, 0.05, delta=1e-12)
        self.assertAlmostEqual(est.slope_lsq, 0.04779661016949153, delta=1e-12)
        self.assertNotAlmostEqual(est.slope_endpoints, est.slope_lsq, places=4)
        self.assertTrue(0.0 < est.r2 < 1.0)

    # ---------------- 约翰斯通-克罗斯法（默认口径）----------------

    def test_jc_manual_formula(self):
        """手算核对：两段等长、比降 0.05 与 0.10。

        S = [ (100·√0.05 + 100·√0.10) / 200 ]²
          = [ (0.22360680 + 0.31622777) / 2 ]²
          = 0.26991729² = 0.07285534…
        """
        prof = ProfileData(x=[0.0, 100.0, 200.0], y=[0.0] * 3,
                           dist=[0.0, 100.0, 200.0], z=[100.0, 95.0, 85.0])
        est = slope.estimate_line_slope(prof, line_name="L1")
        self.assertAlmostEqual(est.slope_jc, 0.07285534, delta=1e-8)
        self.assertEqual(est.jc_segments, 2)
        self.assertEqual(est.jc_skipped, 0)
        # 两端点法是 0.075，JC 必须更小
        self.assertAlmostEqual(est.slope_endpoints, 0.075, delta=TOL)
        self.assertLess(est.slope_jc, est.slope_endpoints)

    def test_jc_equals_endpoints_when_uniform(self):
        """各子段比降相同时三口径必须相等（凹加权的等号条件）。"""
        prof = self._line().profile_lines[0].profile        # 直线河底，比降恒 0.05
        est = slope.estimate_line_slope(prof, line_name="L1")
        self.assertAlmostEqual(est.slope_jc, 0.05, delta=TOL)
        self.assertAlmostEqual(est.slope_jc, est.slope_endpoints, delta=TOL)
        self.assertAlmostEqual(est.slope_jc, est.slope_lsq, delta=TOL)

    def test_jc_le_endpoints_by_jensen(self):
        """√S 是凹函数 → (E√S)² ≤ E[S]，JC 恒 ≤ 两端点法（无倒坡段时）。

        这条锁住加权方式：若有人误改成线性加权 ΣL·S/ΣL，
        结果会恒等于两端点法，`places=6` 的断言立刻报错。
        """
        prof = self._line(dists=(0.0, 50.0, 100.0, 300.0, 400.0),
                          zs=(100.0, 99.0, 98.0, 90.0, 80.0)).profile_lines[0].profile
        est = slope.estimate_line_slope(prof, line_name="L1")
        self.assertLess(est.slope_jc, est.slope_endpoints)
        self.assertNotAlmostEqual(est.slope_jc, est.slope_endpoints, places=4)
        # 线性加权与两端点法恒等（这是解析结论，不是巧合）
        linear = sum((prof.dist[i + 1] - prof.dist[i])
                     * (prof.z[i] - prof.z[i + 1]) / (prof.dist[i + 1] - prof.dist[i])
                     for i in range(prof.n_points - 1)) / (prof.dist[-1] - prof.dist[0])
        self.assertAlmostEqual(linear, est.slope_endpoints, delta=TOL)

    def test_jc_skips_reverse_segments(self):
        """倒坡段（S ≤ 0）无法开方，必须跳过并计数，且在 message 里说明。"""
        #   段1 下降 10 (S=0.10) → 参与
        #   段2 上升  5 (S=-0.05) → 跳过
        #   段3 下降 10 (S=0.10) → 参与
        prof = ProfileData(x=[0.0, 100.0, 200.0, 300.0], y=[0.0] * 4,
                           dist=[0.0, 100.0, 200.0, 300.0],
                           z=[100.0, 90.0, 95.0, 85.0])
        est = slope.estimate_line_slope(prof, line_name="L1")
        self.assertEqual(est.jc_segments, 2)
        self.assertEqual(est.jc_skipped, 1)
        self.assertAlmostEqual(est.jc_skipped_len, 100.0, delta=TOL)
        # 剩下两段比降都是 0.10 → JC = 0.10
        self.assertAlmostEqual(est.slope_jc, 0.10, delta=TOL)
        self.assertIn("倒坡", est.message)
        self.assertIn("跳过", est.message)

    def test_jc_all_reverse_is_nan(self):
        """整段都是倒坡时 JC 无解，但其余口径仍可用，且 bad 标记要生效。"""
        prof = self._line(zs=(80.0, 85.0, 90.0, 95.0, 100.0)).profile_lines[0].profile
        est = slope.estimate_line_slope(prof, line_name="L1")
        self.assertTrue(math.isnan(est.slope_jc))
        self.assertEqual(est.jc_segments, 0)
        self.assertFalse(math.isnan(est.slope_endpoints))

    def test_jc_is_default_mode(self):
        """Q13 确认：默认口径是约翰斯通-克罗斯法，且能写进断面参数。"""
        proj = self._line()
        props, ests = slope.propose_slopes(proj)          # 不传 mode
        self.assertEqual(slope.SLOPE_MODES[0], "jc")
        self.assertAlmostEqual(ests[0].slope("jc"), 0.05, delta=TOL)
        self.assertAlmostEqual(props[0].proposed, 0.05, delta=TOL)
        self.assertEqual(slope.apply_slopes(proj, props), 2)
        for sec in proj.all_sections():
            self.assertAlmostEqual(sec.params.slope, 0.05, delta=TOL)

    def test_mode_selector(self):
        prof = self._line().profile_lines[0].profile
        est = slope.estimate_line_slope(prof)
        self.assertAlmostEqual(est.slope("endpoints"), est.slope_endpoints, delta=TOL)
        self.assertAlmostEqual(est.slope("lsq"), est.slope_lsq, delta=TOL)
        with self.assertRaises(ValueError):
            est.slope("nonsense")

    def test_insufficient_points(self):
        prof = ProfileData(x=[0.0], y=[0.0], dist=[0.0], z=[100.0])
        est = slope.estimate_line_slope(prof, line_name="L1")
        self.assertFalse(est.ok)
        self.assertTrue(est.message)
        self.assertTrue(math.isnan(est.slope_endpoints))

    def test_reverse_slope_flagged(self):
        """整段上坡（倒比降）要给出提示，而不是悄悄返回负值。"""
        prof = self._line(zs=(80.0, 85.0, 90.0, 95.0, 100.0)).profile_lines[0].profile
        est = slope.estimate_line_slope(prof, line_name="L1")
        self.assertLess(est.slope_endpoints, 0)
        self.assertIn("倒比降", est.message)

    # ---------------- 整线共用一个值 ----------------

    def test_one_value_shared_by_whole_line(self):
        proj = self._line()
        props, ests = slope.propose_slopes(proj, mode="endpoints")
        self.assertEqual(len(props), 2)
        self.assertEqual(len(ests), 1)
        vals = {p.proposed for p in props}
        self.assertEqual(len(vals), 1, "同一条线内所有断面必须共用一个比降")
        self.assertAlmostEqual(vals.pop(), 0.05, delta=TOL)

    def test_proposal_uses_section_span_by_default(self):
        """默认只用最上下游断面之间那段，不含断面范围之外的纵断面延伸段。

        纵剖面刻意做成非线性（首段平、末段陡），这样"限定范围"与"用全长"
        才会算出不同结果，否则测了等于没测。
        """
        prof = ProfileData(x=[0.0, 100.0, 200.0, 300.0, 400.0],
                           y=[0.0] * 5,
                           dist=[0.0, 100.0, 200.0, 300.0, 400.0],
                           z=[110.0, 100.0, 95.0, 90.0, 60.0])

        def _line_with(dists):
            secs = [Section(name=f"L1-{i}", x=[0.0, 1.0], y=[0.0, 0.0],
                            s=[0.0, 1.0], z=[100.0, 99.0],
                            params=SectionParams(name=f"L1-{i}"))
                    for i in range(len(dists))]
            return Project(profile_lines=[ProfileLine(
                name="L1", sections=secs, chainage=[0.0] * len(dists),
                profile=prof, profile_dist=list(dists))])

        # 断面在 100 与 300 → 只用 100~300 段：落差 10、长 200 → 0.05
        _, ests = slope.propose_slopes(_line_with([100.0, 300.0]),
                                       use_full_profile=False)
        self.assertAlmostEqual(ests[0].drop, 10.0, delta=TOL)
        self.assertAlmostEqual(ests[0].length, 200.0, delta=TOL)
        self.assertAlmostEqual(ests[0].slope_endpoints, 0.05, delta=TOL)

        # 用全长 → 110 到 60，落差 50、长 400 → 0.125，必须与上面不同
        _, ests_full = slope.propose_slopes(_line_with([100.0, 300.0]),
                                            use_full_profile=True)
        self.assertAlmostEqual(ests_full[0].length, 400.0, delta=TOL)
        self.assertAlmostEqual(ests_full[0].slope_endpoints, 0.125, delta=TOL)
        self.assertNotAlmostEqual(ests[0].slope_endpoints,
                                  ests_full[0].slope_endpoints, places=4)

    def test_single_section_falls_back_to_full_profile(self):
        """只有 1 个断面时无法限定河段，应回退到整条纵断面而不是报错。"""
        prof = ProfileData(x=[0.0, 400.0], y=[0.0, 0.0],
                           dist=[0.0, 400.0], z=[100.0, 60.0])
        s1 = Section(name="L1-1", x=[0.0, 1.0], y=[0.0, 0.0], s=[0.0, 1.0],
                     z=[100.0, 99.0], params=SectionParams(name="L1-1"))
        proj = Project(profile_lines=[ProfileLine(
            name="L1", sections=[s1], chainage=[0.0],
            profile=prof, profile_dist=[100.0])])
        props, ests = slope.propose_slopes(proj, use_full_profile=False)
        self.assertTrue(ests[0].ok)
        self.assertAlmostEqual(ests[0].length, 400.0, delta=TOL)
        self.assertAlmostEqual(ests[0].slope_endpoints, 0.1, delta=TOL)
        self.assertFalse(props[0].bad)

    def test_line_without_profile_is_flagged(self):
        s1 = Section(name="X-1", x=[0.0, 1.0], y=[0.0, 0.0], s=[0.0, 1.0],
                     z=[10.0, 9.0], params=SectionParams(name="X-1"))
        ln = ProfileLine(name="X", sections=[s1], chainage=[0.0], profile=None)
        proj = Project(profile_lines=[ln])
        props, ests = slope.propose_slopes(proj)
        self.assertFalse(ests[0].ok)
        self.assertTrue(props[0].bad)
        self.assertTrue(math.isnan(props[0].proposed))
        self.assertIn("纵断面", ests[0].message)

    def test_bad_mode_rejected(self):
        with self.assertRaises(ValueError):
            slope.propose_slopes(self._line(), mode="average")

    # ---------------- 应用 ----------------

    def test_apply_writes_to_all(self):
        proj = self._line()
        props, _ = slope.propose_slopes(proj)
        n = slope.apply_slopes(proj, props)
        self.assertEqual(n, 2)
        for sec in proj.all_sections():
            self.assertAlmostEqual(sec.params.slope, 0.05, delta=TOL)

    def test_apply_only_missing_keeps_hand_filled(self):
        proj = self._line()
        props, _ = slope.propose_slopes(proj)
        n = slope.apply_slopes(proj, props, only_missing=True)
        self.assertEqual(n, 1)                       # 只有 L1-1 是空的
        by = {s.name: s.params.slope for s in proj.all_sections()}
        self.assertAlmostEqual(by["L1-1"], 0.05, delta=TOL)
        self.assertEqual(by["L1-2"], 0.999)          # 手填值不动

    def test_apply_skips_bad_proposals(self):
        """倒比降的建议值不能写进参数——sqrt(S) 会算不出流量。"""
        prof = self._line(zs=(80.0, 85.0, 90.0, 95.0, 100.0)).profile_lines[0].profile
        s1 = Section(name="L1-1", x=[0.0, 1.0], y=[0.0, 0.0], s=[0.0, 1.0],
                     z=[100.0, 99.0], params=SectionParams(name="L1-1"))
        ln = ProfileLine(name="L1", sections=[s1], chainage=[0.0],
                         profile=prof, profile_dist=[0.0])
        proj = Project(profile_lines=[ln])
        props, _ = slope.propose_slopes(proj)
        self.assertTrue(props[0].bad)
        self.assertEqual(slope.apply_slopes(proj, props), 0)
        self.assertTrue(math.isnan(proj.profile_lines[0].sections[0].params.slope))


class TestProjectIO(unittest.TestCase):
    """工程文件（.dmprj，单个 JSON 文本）的保存与载入。"""

    @staticmethod
    def _project() -> Project:
        sec1 = Section(
            name="A-1", x=[1.0, 2.0, 3.0], y=[10.0, 11.0, 12.0],
            s=[0.0, 5.0, 10.0], z=[100.0, 95.0, 102.0],
            params=SectionParams(name="A-1", slope=0.012, roughness=0.035,
                                 design_q=48.0, roughness_main=0.030,
                                 roughness_left=None, roughness_right=0.040))
        sec2 = Section(                       # 参数全空 -> 应为 NaN
            name="A-2", x=[1.0, 2.0], y=[10.0, 11.0],
            s=[0.0, 5.0], z=[99.0, 97.0],
            params=SectionParams(name="A-2"))
        prof = ProfileData(x=[0.0, 100.0, 200.0], y=[0.0, 0.0, 0.0],
                           dist=[0.0, 100.0, 200.0], z=[100.0, 96.0, 90.0],
                           chainage=[200.0, 100.0, 0.0], origin="lowest")
        ln = ProfileLine(name="A", sections=[sec1, sec2],
                         chainage=[200.0, 100.0], order_source="spatial",
                         profile=prof, profile_dist=[0.0, 100.0])
        return Project(profile_lines=[ln])

    def test_roundtrip(self):
        import tempfile
        proj = self._project()
        cfg = Config(compound_mode=False, dH=0.25, chainage_origin="end",
                     steep_slope=0.33, turn_slope=0.02,
                     csv_encoding="gbk", intersection_tolerance=2.5)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.dmprj")
            project_io.save_project(p, proj, cfg,
                                    source={"dir": "D:/x", "files": ["a.xlsx"]})
            proj2, cfg2, meta = project_io.load_project(p)

        # ---- 配置 ----
        self.assertFalse(cfg2.compound_mode)
        self.assertAlmostEqual(cfg2.dH, 0.25, delta=TOL)
        self.assertEqual(cfg2.chainage_origin, "end")
        self.assertAlmostEqual(cfg2.steep_slope, 0.33, delta=TOL)
        self.assertAlmostEqual(cfg2.turn_slope, 0.02, delta=TOL)
        self.assertEqual(cfg2.csv_encoding, "gbk")
        self.assertAlmostEqual(cfg2.intersection_tolerance, 2.5, delta=TOL)

        # ---- 结构 ----
        self.assertEqual(len(proj2.profile_lines), 1)
        ln2 = proj2.profile_lines[0]
        self.assertEqual(ln2.name, "A")
        self.assertEqual(ln2.order_source, "spatial")
        self.assertEqual(ln2.chainage, [200.0, 100.0])
        self.assertEqual(ln2.profile_dist, [0.0, 100.0])
        self.assertEqual([s.name for s in ln2.sections], ["A-1", "A-2"])

        # ---- 纵剖面 ----
        self.assertIsNotNone(ln2.profile)
        self.assertEqual(ln2.profile.dist, [0.0, 100.0, 200.0])
        self.assertEqual(ln2.profile.z, [100.0, 96.0, 90.0])
        self.assertEqual(ln2.profile.origin, "lowest")

        # ---- 几何 ----
        s1 = ln2.sections[0]
        self.assertEqual(s1.x, [1.0, 2.0, 3.0])
        self.assertEqual(s1.z, [100.0, 95.0, 102.0])

        # ---- 参数 ----
        p1 = s1.params
        self.assertAlmostEqual(p1.slope, 0.012, delta=TOL)
        self.assertAlmostEqual(p1.roughness, 0.035, delta=TOL)
        self.assertAlmostEqual(p1.design_q, 48.0, delta=TOL)
        self.assertAlmostEqual(p1.roughness_main, 0.030, delta=TOL)
        self.assertIsNone(p1.roughness_left)
        self.assertAlmostEqual(p1.roughness_right, 0.040, delta=TOL)

        # ---- 元信息 ----
        self.assertEqual(meta["source"]["files"], ["a.xlsx"])
        self.assertTrue(meta["saved_at"])

    def test_nan_survives_and_none_stays_none(self):
        """NaN 必须能往返，且不能与「留空的 None」混为一谈——两者语义不同。

        比降/糙率/设计流量：null 表示「未填写」，读回 NaN
        分区糙率：        null 表示「留空、回退统一糙率」，读回 None
        """
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.dmprj")
            project_io.save_project(p, self._project(), Config())
            proj2, _, _ = project_io.load_project(p)

        p2 = proj2.profile_lines[0].sections[1].params
        self.assertTrue(math.isnan(p2.slope))
        self.assertTrue(math.isnan(p2.roughness))
        self.assertTrue(math.isnan(p2.design_q))
        self.assertIsNone(p2.roughness_main)

        p1 = proj2.profile_lines[0].sections[0].params
        self.assertFalse(math.isnan(p1.slope))
        self.assertIsNone(p1.roughness_left)          # 留空，不是 NaN
        self.assertAlmostEqual(p1.roughness_right, 0.040, delta=TOL)

    def test_file_is_utf8_bom_json(self):
        """文件要带 BOM 的合法 JSON：记事本不乱码，其他工具也能解析。"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.dmprj")
            project_io.save_project(p, self._project(), Config())
            with open(p, "rb") as f:
                raw = f.read()
            self.assertEqual(raw[:3], b"\xef\xbb\xbf")
            data = json.loads(raw[3:].decode("utf-8"))
        self.assertEqual(data["format"], project_io.FORMAT_ID)
        self.assertEqual(data["version"], project_io.FORMAT_VERSION)
        self.assertEqual(data["profile_lines"][0]["name"], "A")
        # 不得出现 JSON 规范不认的 NaN/Infinity 字面量，
        # 否则别的工具（乃至别的语言）解析会直接失败
        text = json.dumps(data, ensure_ascii=False)
        for bad in ("NaN", "Infinity", "-Infinity"):
            self.assertNotIn(bad, text)

    def test_reject_wrong_format(self):
        with self.assertRaises(ValueError) as cm:
            project_io.dict_to_project({"format": "something-else"})
        self.assertIn("工程文件", str(cm.exception))

    def test_reject_future_version(self):
        with self.assertRaises(ValueError) as cm:
            project_io.dict_to_project({
                "format": project_io.FORMAT_ID,
                "version": project_io.FORMAT_VERSION + 1})
        self.assertIn("版本", str(cm.exception))

    def test_tolerate_unknown_config_field(self):
        """打开「更新版本写出」的文件时，多出来的设置项不该导致失败。"""
        proj, cfg, _ = project_io.dict_to_project({
            "format": project_io.FORMAT_ID,
            "version": project_io.FORMAT_VERSION,
            "config": {"dH": 0.2, "某个以后才有的设置项": 123},
            "profile_lines": [],
        })
        self.assertAlmostEqual(cfg.dH, 0.2, delta=TOL)
        self.assertEqual(proj.profile_lines, [])
        # 严格模式仍应拒绝未知字段（防止配置文件写歪）
        with self.assertRaises(ValueError):
            Config.from_dict({"不存在": 1})

    def test_raise_config_roundtrip(self):
        """加高水位的两个设置项要能随工程文件保存 / 读回。"""
        cfg = Config()
        cfg.raise_enabled = False
        cfg.raise_level = 2.5
        d = cfg.to_dict()
        self.assertEqual(d["raise_enabled"], False)
        self.assertAlmostEqual(d["raise_level"], 2.5)
        # 通过工程文件字典往返
        cfg2 = Config.from_dict(d, strict=False)
        self.assertFalse(cfg2.raise_enabled)
        self.assertAlmostEqual(cfg2.raise_level, 2.5)
        # 旧文件（无这两个字段）读回应取默认值
        cfg3 = Config.from_dict({"dH": 0.2}, strict=False)
        self.assertTrue(cfg3.raise_enabled)
        self.assertAlmostEqual(cfg3.raise_level, 1.0)

    def test_reject_broken_json(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "bad.dmprj")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{ this is not json ")
            with self.assertRaises(ValueError) as cm:
                project_io.load_project(p)
            self.assertIn("JSON", str(cm.exception))

    def test_line_without_profile(self):
        """没有纵剖面数据的线也要能往返（回退编号分组时会出现）。"""
        import tempfile
        sec = Section(name="B-1", x=[0.0, 1.0], y=[0.0, 1.0],
                      s=[0.0, 1.0], z=[10.0, 9.0],
                      params=SectionParams(name="B-1"))
        proj = Project(profile_lines=[ProfileLine(name="B", sections=[sec])])
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.dmprj")
            project_io.save_project(p, proj, Config())
            proj2, _, _ = project_io.load_project(p)
        self.assertIsNone(proj2.profile_lines[0].profile)
        self.assertEqual(len(proj2.profile_lines[0].sections), 1)
        self.assertEqual(proj2.profile_lines[0].sections[0].z, [10.0, 9.0])

    def test_real_data_roundtrip(self):
        """用真实数据完整往返一遍：断面数、点数、坐标必须逐项一致。

        比逐字段比对更能抓出「只存了部分断面」这类结构性错误。
        """
        import tempfile
        from core.reader import load_folder

        folder = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data")
        if not os.path.isdir(folder):
            self.skipTest("没有 data 目录")

        proj, _ = load_folder(folder, Config())
        total = len(proj.all_sections())
        self.assertGreater(total, 0)

        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "real.dmprj")
            project_io.save_project(p, proj, Config(dH=0.2))
            proj2, cfg2, _ = project_io.load_project(p)

        self.assertEqual(len(proj2.profile_lines), len(proj.profile_lines))
        self.assertEqual(len(proj2.all_sections()), total)
        self.assertAlmostEqual(cfg2.dH, 0.2, delta=TOL)

        for a, b in zip(proj.all_sections(), proj2.all_sections()):
            self.assertEqual(a.name, b.name)
            self.assertEqual(a.n_points, b.n_points)
            self.assertEqual(a.x, b.x)
            self.assertEqual(a.y, b.y)
            self.assertEqual(a.s, b.s)
            self.assertEqual(a.z, b.z)

        for la, lb in zip(proj.profile_lines, proj2.profile_lines):
            self.assertEqual(la.name, lb.name)
            self.assertEqual(la.chainage, lb.chainage)
            self.assertEqual(la.profile_dist, lb.profile_dist)
            if la.profile is not None:
                self.assertEqual(la.profile.dist, lb.profile.dist)
                self.assertEqual(la.profile.z, lb.profile.z)
                self.assertEqual(la.profile.x, lb.profile.x)

    def test_suggest_path(self):
        proj = self._project()
        p = project_io.suggest_path(proj, "D:/tmp")
        self.assertTrue(p.endswith(project_io.SUFFIX))
        self.assertIn("A", os.path.basename(p))
        self.assertEqual(
            project_io.default_project_name(Project()),
            f"未命名工程{project_io.SUFFIX}")


class TestManualOverrides(unittest.TestCase):
    """手动覆盖（Q14）：深泓点 / 分区边界 / 成灾水位。

    这三个字段是**互相耦合**的（改深泓点可能让边界失效；改边界会连带改掉
    自动成灾水位），合法性只能整组判断，所以测试也按"组合"写，
    而不是逐个字段孤立地验。
    """

    def setUp(self):
        self.sec = make_compound_section()
        self.cfg = Config()
        # 参考断面：s = 0,20,25,30,40,45,65
        #             z = 102,102.5,98,97,98,102.5,102.8
        self.auto = analyze_terrain(make_compound_section(), self.cfg)

    # ---------------- 深泓点 ----------------
    def test_no_override_then_auto_equals_effective(self):
        info = analyze_terrain(self.sec, self.cfg)
        self.assertEqual(info.dmin_idx, info.dmin_auto_idx)
        self.assertAlmostEqual(info.dmin, info.dmin_auto, delta=TOL)
        self.assertAlmostEqual(info.disaster_level, info.disaster_level_auto,
                               delta=TOL)

    def test_manual_thalweg_replaces_curve_start(self):
        """全面顶替：H~Q 曲线的起算水位必须跟着手动深泓点走。"""
        self.sec.thalweg_manual = 4                  # z=98.0，比最低点 97.0 高 1 m
        info = analyze_terrain(self.sec, self.cfg)
        self.assertEqual(info.dmin_idx, 4)
        self.assertAlmostEqual(info.dmin, 98.0, delta=TOL)
        self.assertEqual(info.dmin_auto_idx, 3)      # 自动值仍保留供对照
        self.assertAlmostEqual(info.dmin_auto, 97.0, delta=TOL)
        hvec, *_ = compute_rating_curve(self.sec, info, self.cfg)
        self.assertAlmostEqual(hvec[0], 98.0, delta=TOL)

    def test_manual_thalweg_recomputes_banks(self):
        """深泓点一动，左右岸顶必须**重新划分**，不能沿用旧的。

        用左右不对称的断面：全局最高点 105 在自动深泓点(4)的**左侧**，
        深泓点左移到 2 之后它落到右侧，于是左岸顶换人、水位上限跟着变。
        """
        def mk():
            return Section(name="T", x=[0.0] * 6, y=[0.0] * 6,
                           s=[0.0, 10.0, 20.0, 30.0, 40.0, 50.0],
                           z=[101.0, 100.0, 99.0, 105.0, 98.0, 104.0],
                           params=SectionParams(name="T", slope=0.005,
                                                roughness=0.03, design_q=50.0))

        info = analyze_terrain(mk(), self.cfg)
        self.assertEqual(info.dmin_idx, 4)
        self.assertEqual(info.zmax_idx, 3)
        self.assertAlmostEqual(info.zymin, 104.0, delta=TOL)

        sec = mk()
        sec.thalweg_manual = 2                       # z=99.0
        info2 = analyze_terrain(sec, self.cfg)
        self.assertEqual(info2.dmin_idx, 2)
        self.assertEqual(info2.zmax_idx, 0)          # 105 现在在右侧了
        self.assertAlmostEqual(info2.zmax, 101.0, delta=TOL)
        self.assertEqual(info2.ymax_idx, 3)
        self.assertAlmostEqual(info2.ymax, 105.0, delta=TOL)
        self.assertAlmostEqual(info2.zymin, 101.0, delta=TOL)

    def test_manual_thalweg_moves_turning_scan(self):
        """转折点是从深泓点向两侧扫出来的，扫描起点也跟着手动深泓点变。"""
        self.sec.thalweg_manual = 0                  # 最左端 -> 左侧无点可扫
        info = analyze_terrain(self.sec, self.cfg)
        self.assertIsNone(info.left_turn_idx)
        self.assertEqual(info.right_turn_idx, 5)
        self.assertEqual(info.zones, [(0, 6), (5, 7)])

    def test_row_counts_must_be_counted_not_estimated(self):
        """手动深泓点造成的曲线行数变化，必须**实际数**，不能用 ceil(Δ/dH) 估。

        构造一个"等差数列整体平移后，与上端 zymin 的对齐关系改变"的断面：
        起算水位抬高 0.2049 m，实际只少 2 行，而 ceil(0.2049/0.1) = 3。
        真实数据上 31 个断面里 21 个都会差 1 行——当时面板那句话就是这么错的。
        """
        sec = Section(name="R", x=[0.0] * 4, y=[0.0] * 4,
                      s=[0.0, 10.0, 20.0, 30.0],
                      z=[100.02, 99.0, 99.2049, 100.5],
                      params=SectionParams(name="R", slope=0.005,
                                           roughness=0.03, design_q=50.0))
        cfg = Config()
        rows_auto, auto = _solve_rows(sec, cfg)
        self.assertAlmostEqual(auto.zymin_auto, auto.zymin, delta=TOL)

        sec.thalweg_manual = 2
        rows_man, info = _solve_rows(sec, cfg)
        d = info.dmin - info.dmin_auto
        self.assertAlmostEqual(d, 0.2049, delta=1e-9)
        self.assertEqual(rows_auto - rows_man, 2)
        self.assertEqual(int(math.ceil(d / cfg.dH - 1e-9)), 3)   # 估算会多报 1 行
        # 界面显示的两个数必须与真实求解结果一致
        self.assertEqual(hvec_row_counts(info, cfg), (rows_auto, rows_man))

    def test_zymin_auto_follows_its_own_thalweg(self):
        """zymin_auto 必须按**自动**深泓点算，不能顺手用生效值的岸顶。"""
        sec = Section(name="Z", x=[0.0] * 6, y=[0.0] * 6,
                      s=[0.0, 10.0, 20.0, 30.0, 40.0, 50.0],
                      z=[101.0, 100.0, 99.0, 105.0, 98.0, 104.0],
                      params=SectionParams(name="Z"))
        auto = analyze_terrain(sec, Config())
        self.assertAlmostEqual(auto.zymin_auto, 104.0, delta=TOL)
        sec.thalweg_manual = 2
        info = analyze_terrain(sec, Config())
        self.assertAlmostEqual(info.zymin, 101.0, delta=TOL)
        self.assertAlmostEqual(info.zymin_auto, 104.0, delta=TOL)

    # ---------------- 分区边界 ----------------
    def test_manual_zone_boundaries(self):
        """手动分区边界只改分区，**不改转折点**（Q15）。"""
        self.sec.zone_manual = True
        self.sec.zone_left = 2
        self.sec.zone_right = 4
        info = analyze_terrain(self.sec, self.cfg)
        self.assertEqual(info.zone_left_idx, 2)
        self.assertEqual(info.zone_right_idx, 4)
        self.assertEqual(info.zones, [(0, 3), (2, 5), (4, 7)])
        # 转折点仍是自动扫描的结果（左 1 右 5），没被拖走
        self.assertEqual(info.left_turn_idx, 1)
        self.assertEqual(info.right_turn_idx, 5)

    def test_manual_zones_do_not_move_turning_points_or_disaster(self):
        """⚠ Q15 的核心断言：改分区**不得**影响转折点与成灾水位。

        最初把"转折点"和"分区边界"合成一对索引（图省事），导致手动调分区时
        转折点与成灾水位被一并拖走。用户指出这是两个概念，已拆开。
        这条测试就是防止以后有人再合回去。
        """
        info0 = analyze_terrain(make_compound_section(), self.cfg)

        sec = make_compound_section()
        sec.zone_manual = True
        sec.zone_left = 2
        sec.zone_right = 4
        info = analyze_terrain(sec, self.cfg)

        self.assertEqual(info.left_turn_idx, info0.left_turn_idx)
        self.assertEqual(info.right_turn_idx, info0.right_turn_idx)
        self.assertAlmostEqual(info.disaster_level, info0.disaster_level, delta=TOL)
        self.assertEqual(info.disaster_idx, info0.disaster_idx)
        self.assertAlmostEqual(info.disaster_level_auto,
                               info0.disaster_level_auto, delta=TOL)
        # 而分区确实变了，说明覆盖生效了（不是整体被忽略）
        self.assertNotEqual(info.zones, info0.zones)

    def test_default_zone_boundaries_equal_turning_points(self):
        """默认（不手动）时分区边界 = 转折点 —— 拆分后默认行为必须一字不变。"""
        info = analyze_terrain(make_compound_section(), self.cfg)
        self.assertEqual(info.zone_left_idx, info.left_turn_idx)
        self.assertEqual(info.zone_right_idx, info.right_turn_idx)
        self.assertEqual(info.zones, [(0, 2), (1, 6), (5, 7)])

    def test_manual_zones_none_none_means_one_zone(self):
        """两侧都不设边界 = 「不分区」（1 区）。

        它与"全自动"在**结果上**可能一样，但语义不同、且必须能表达：
        自动扫描找出 2 个转折点、而用户认为该断面不该分区，是真实需求。
        """
        self.sec.zone_manual = True                  # left / right 都留空
        info = analyze_terrain(self.sec, self.cfg)
        self.assertIsNone(info.zone_left_idx)
        self.assertIsNone(info.zone_right_idx)
        self.assertEqual(info.zones, [(0, 7)])
        # 转折点照旧（自动），成灾水位也照旧 —— 正是 Q15 要的
        self.assertEqual(info.left_turn_idx, 1)
        self.assertEqual(info.right_turn_idx, 5)
        self.assertAlmostEqual(info.disaster_level, 102.5, delta=TOL)

    def test_manual_single_sided_boundary(self):
        self.sec.zone_manual = True
        self.sec.zone_left = 1
        self.assertEqual(analyze_terrain(self.sec, self.cfg).zones,
                         [(0, 2), (1, 7)])
        self.sec.zone_left = None
        self.sec.zone_right = 5
        self.assertEqual(analyze_terrain(self.sec, self.cfg).zones,
                         [(0, 6), (5, 7)])

    def test_invalid_zone_override_falls_back_to_auto(self):
        """边界跑到深泓点同侧 -> 整体退回自动，并给出可读的原因。"""
        self.sec.zone_manual = True
        self.sec.zone_left = 5                       # 深泓点在 3，左边界须在它左侧
        self.sec.zone_right = 6
        self.assertEqual(analyze_terrain(self.sec, self.cfg).zones,
                         self.auto.zones)
        errs = self.sec.override_errors()
        self.assertTrue(errs)
        self.assertIn("左侧", "".join(errs))

    def test_manual_boundary_changes_zone_count_for_roughness(self):
        """分区数变了，糙率取哪一格也跟着变（3 区用主槽，2 区不用）——必须测到。"""
        self.sec.params.roughness_main = 0.010
        self.sec.params.roughness_left = 0.020
        self.sec.params.roughness_right = 0.040
        self.assertEqual(self.sec.params.roughness_for_zone(1, 3), 0.010)
        self.assertEqual(self.sec.params.roughness_for_zone(1, 2), 0.040)
        self.sec.zone_manual = True
        self.sec.zone_left = 1                       # 只有左边界 -> 2 区
        info = analyze_terrain(self.sec, self.cfg)
        self.assertEqual(len(info.zones), 2)

    # ---------------- 成灾水位 ----------------
    def test_manual_disaster_level(self):
        self.sec.disaster_idx_manual = 2             # z=98.0
        info = analyze_terrain(self.sec, self.cfg)
        self.assertAlmostEqual(info.disaster_level, 98.0, delta=TOL)
        self.assertEqual(info.disaster_idx, 2)
        self.assertAlmostEqual(info.disaster_level_auto, 102.5, delta=TOL)

    def test_auto_disaster_ignores_manual_zones(self):
        """成灾水位只由**转折点**决定，手动分区边界不得影响它（Q15）。

        （这条断言在 Q15 之前是**反的**：当时分区边界就是转折点，
          所以改分区会连带改成灾水位。拆分后行为反过来了。）
        """
        sec = make_compound_section()
        sec.zone_manual = True
        sec.zone_left = 2
        sec.zone_right = 5
        info = analyze_terrain(sec, self.cfg)
        # 自动成灾水位 = min(z[左转折], z[右转折]) = min(z[1], z[5]) = 102.5
        self.assertAlmostEqual(info.disaster_level, 102.5, delta=TOL)
        self.assertAlmostEqual(info.disaster_level_auto, 102.5, delta=TOL)
        self.assertEqual(info.disaster_idx, 1)
        # 而分区确实按新边界分了
        self.assertEqual(info.zones, [(0, 3), (2, 6), (5, 7)])

    # ---------------- 校验 ----------------
    def test_override_errors_kinds(self):
        cases = [
            ({"thalweg_manual": 99}, "深泓点"),
            ({"disaster_idx_manual": -1}, "成灾水位"),
            ({"zone_manual": True, "zone_left": 99}, "左边界"),
            ({"zone_manual": True, "zone_right": -1}, "右边界"),
            ({"zone_manual": True, "zone_left": 5, "zone_right": 2}, "未小于右边界"),
            ({"zone_manual": True, "zone_left": 3}, "左侧"),   # 与深泓点重合
        ]
        for changes, key in cases:
            sec = make_compound_section()
            for k, v in changes.items():
                setattr(sec, k, v)
            errs = sec.override_errors()
            self.assertTrue(errs, f"{changes} 应当报错")
            self.assertIn(key, "".join(errs))

    def test_has_manual_flag(self):
        sec = make_compound_section()
        self.assertFalse(sec.has_manual)
        sec.zone_manual = True
        self.assertTrue(sec.has_manual)
        sec2 = make_compound_section()
        sec2.disaster_idx_manual = 0
        self.assertTrue(sec2.has_manual)

    def test_thalweg_index_matches_terrain(self):
        """`Section.thalweg_index()` 必须与 terrain 定的深泓点完全一致。

        这两个值分别用于"校验手动边界"和"实际计算"，一旦取法不同就会出现
        「校验说没问题、算出来却退回自动」——界面上完全看不出来。
        所以这里逐个真实断面锁死它们相等。
        """
        from core.reader import load_folder
        folder = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data")
        if not os.path.isdir(folder):
            self.skipTest("没有 data 目录")
        proj, _ = load_folder(folder, Config())
        checked = 0
        for sec in proj.all_sections():
            info = analyze_terrain(sec, Config())
            self.assertEqual(sec.thalweg_index(), info.dmin_idx,
                             f"{sec.name}: 生效深泓点两处不一致")
            checked += 1
        self.assertGreater(checked, 0)

    def test_bool_is_not_a_valid_index(self):
        """JSON 里的 true 不能被当成索引 1 —— 那种错不报错也看不出来。"""
        sec = make_compound_section()
        sec.thalweg_manual = True
        self.assertTrue(sec.override_errors())
        self.assertNotEqual(sec.thalweg_index(), 1)
        sec2 = make_compound_section()
        sec2.zone_manual = True
        sec2.zone_right = True
        self.assertTrue(sec2.override_errors())
        # 工程文件读入时也要挡掉
        self.assertIsNone(project_io._oint(True))
        self.assertIsNone(project_io._oint(False))

    def test_bad_override_surfaces_as_warning(self):
        """不合法的手动值必须让用户看见，不能被静默吞掉。"""
        self.sec.thalweg_manual = 99
        res, _info = solve_section(self.sec, self.cfg)
        self.assertTrue(any("手动值已被忽略" in w for w in res.warnings),
                        res.warnings)

    # ---------------- 工程文件 ----------------
    def _roundtrip(self, sec):
        import tempfile
        proj = Project(profile_lines=[ProfileLine(name="L", sections=[sec])])
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "o.dmprj")
            project_io.save_project(p, proj, Config())
            proj2, _cfg2, _meta = project_io.load_project(p)
        return proj2.profile_lines[0].sections[0]

    def test_serialized_override_fields(self):
        sec = make_compound_section()
        sec.thalweg_manual = 2
        sec.zone_manual = True
        sec.zone_left = 1
        sec.zone_right = 5
        sec.disaster_idx_manual = 4
        s2 = self._roundtrip(sec)
        self.assertEqual(s2.thalweg_manual, 2)
        self.assertTrue(s2.zone_manual)
        self.assertEqual(s2.zone_left, 1)
        self.assertEqual(s2.zone_right, 5)
        self.assertEqual(s2.disaster_idx_manual, 4)
        self.assertEqual(s2.override_errors(), [])
        # 索引必须仍是 int：terrain 用 isinstance(idx, int) 判合法性，
        # 一旦读成 3.0 就会被判非法、静默退回自动 —— "存进去了却没生效"
        for v in (s2.thalweg_manual, s2.zone_left, s2.zone_right,
                  s2.disaster_idx_manual):
            self.assertIsInstance(v, int)

    def test_unset_overrides_default_to_auto(self):
        s2 = self._roundtrip(make_compound_section())
        self.assertIsNone(s2.thalweg_manual)
        self.assertFalse(s2.zone_manual)
        self.assertIsNone(s2.zone_left)
        self.assertIsNone(s2.zone_right)
        self.assertIsNone(s2.disaster_idx_manual)
        self.assertFalse(s2.has_manual)

    def test_v1_file_reads_as_all_auto(self):
        """v1 工程文件没有这几个字段，必须仍能打开并按"全自动"处理。"""
        data = {
            "format": project_io.FORMAT_ID,
            "version": 1,
            "config": {"dH": 0.2},
            "profile_lines": [{
                "name": "L",
                "sections": [{"name": "L-1", "x": [0.0, 1.0], "y": [0.0, 1.0],
                              "s": [0.0, 1.0], "z": [10.0, 9.0],
                              "params": {"name": "L-1"}}],
            }],
        }
        proj, cfg, meta = project_io.dict_to_project(data)
        self.assertEqual(meta["version"], 1)
        self.assertAlmostEqual(cfg.dH, 0.2, delta=TOL)
        self.assertFalse(proj.profile_lines[0].sections[0].has_manual)

    def test_zone_manual_only_accepts_json_true(self):
        """zone_manual 只认 JSON 的 true。

        手工把文件改成字符串 "false" 时，若写成 bool(v) 会判成 True，
        语义就从"不分区"翻成"手动分区"，而且界面上看不出来。
        """
        data = {
            "format": project_io.FORMAT_ID,
            "version": 2,
            "profile_lines": [{
                "name": "L",
                "sections": [{"name": "L-1", "x": [0.0, 1.0], "y": [0.0, 1.0],
                              "s": [0.0, 1.0], "z": [10.0, 9.0],
                              "zone_manual": "false",
                              "thalweg_manual": "3"}],
            }],
        }
        proj, _cfg, _meta = project_io.dict_to_project(data)
        s = proj.profile_lines[0].sections[0]
        self.assertFalse(s.zone_manual)
        self.assertEqual(s.thalweg_manual, 3)        # 数字字符串仍应接受

    def test_real_data_roundtrip_with_overrides(self):
        """真实数据上带手动设定往返一遍。"""
        import tempfile
        from core.reader import load_folder

        folder = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data")
        if not os.path.isdir(folder):
            self.skipTest("没有 data 目录")

        proj, _ = load_folder(folder, Config())
        picked = []
        for ln in proj.profile_lines:
            for s in ln.sections:
                if 2 <= len(s.z) - 3:
                    info = analyze_terrain(s, Config())
                    ai = info.dmin_auto_idx
                    if 2 <= ai <= s.n_points - 3:
                        s.thalweg_manual = ai + 1
                        s.zone_manual = True
                        s.zone_left = ai - 2
                        s.zone_right = ai + 2
                        s.disaster_idx_manual = ai - 1
                        self.assertEqual(s.override_errors(), [])
                        picked.append((s.name, ai))
                        break
            if len(picked) >= 3:
                break
        self.assertTrue(picked, "没有找到可做试验的断面")

        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "real_ov.dmprj")
            project_io.save_project(p, proj, Config())
            proj2, _cfg, _meta = project_io.load_project(p)

        by_name = {s.name: s for s in proj2.all_sections()}
        for name, ai in picked:
            s2 = by_name[name]
            self.assertEqual(s2.thalweg_manual, ai + 1)
            self.assertTrue(s2.zone_manual)
            self.assertEqual(s2.zone_left, ai - 2)
            self.assertEqual(s2.zone_right, ai + 2)
            self.assertEqual(s2.disaster_idx_manual, ai - 1)
            info2 = analyze_terrain(s2, Config())
            self.assertEqual(info2.dmin_idx, ai + 1)
            self.assertEqual(len(info2.zones), 3)


class TestVersion(unittest.TestCase):
    """版本号与构建信息（core/version.py）。

    加版本号的目的是"拿到一个 exe 能判断它是哪次提交的产物"，
    所以**版本号与构建哈希两样都必须拿得到**，缺一样这个能力就废了。
    """

    def test_version_is_semver(self):
        """版本号必须是 主.次.修 三段纯数字——标题栏和「关于」直接显示它。"""
        parts = version.VERSION.split(".")
        self.assertEqual(len(parts), 3, version.VERSION)
        self.assertTrue(all(p.isdigit() for p in parts), version.VERSION)

    def test_title_suffix_shows_version(self):
        self.assertIn(version.VERSION, version.title_suffix())

    def test_full_includes_version_and_runtime(self):
        txt = version.full()
        self.assertIn(version.VERSION, txt)
        self.assertIn(version.runtime(), txt)
        self.assertIn(version.APP_NAME, txt)

    def test_git_hash_not_unknown(self):
        """构建哈希不能是"未知"。

        源码运行时靠 _build_info.py（打包烘焙）或实时查 git 二选一；
        两者都没有才会是"未知"——那意味着 exe 看不出是哪一版，等于功能失效。
        """
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not (os.path.isdir(os.path.join(repo, ".git")) or shutil.which("git")):
            self.skipTest("当前环境既不是 git 仓库也没有 git 命令")
        self.assertNotEqual(version.git_hash(), version.UNKNOWN)
        self.assertTrue(version.git_hash().strip())

    def test_build_time_is_string(self):
        """构建时间拿不到时返回"未知"而不是抛异常（不能因为查不到就崩界面）。"""
        self.assertIsInstance(version.build_time(), str)
        self.assertTrue(version.build_time().strip())


def _straight_sec(name="T", n=6, span=50.0, ux=1.0, uy=0.0):
    """造一条平面上的直线断面：起点距 0..span 均分，x/y 沿 (ux,uy) 铺开。

    真实数据就是这种形态（实测 31 个断面：s[0]=0、严格递增、
    弧长÷弦长≈1.0000、偏离首末连线 ≤0.34 m），所以测例按真实形态建。
    """
    s = [span * i / (n - 1) for i in range(n)]
    z = [20.0 - 2.0 * math.sin(math.pi * i / (n - 1)) for i in range(n)]
    x = [10.0 + si * ux for si in s]
    y = [100.0 + si * uy for si in s]
    return Section(name=name, x=x, y=y, s=s, z=z,
                   params=SectionParams(name=name))


class TestSectionEdit(unittest.TestCase):
    """横断面编辑（core/edit.py）。

    这个模块的价值全在"改了一个值之后，别的东西有没有跟着对"——
    所以每个用例都盯住**联动**，而不是盯住那个被改的数本身。
    """

    def test_recompute_xy_is_exact_on_straight_section(self):
        """直线断面上，由 s 反推 x/y 必须与原始值逐点吻合。

        这是"改起点距后同步平面坐标"这条规则成立的前提：
        真实数据若是弯的，反推就会把点挪到错误的位置。
        """
        sec = _straight_sec(ux=0.6, uy=0.8)
        ox, oy = list(sec.x), list(sec.y)
        self.assertTrue(edit.recompute_xy(sec))
        for i in range(sec.n_points):
            self.assertAlmostEqual(sec.x[i], ox[i], places=9)
            self.assertAlmostEqual(sec.y[i], oy[i], places=9)

    def test_set_s_syncs_xy(self):
        """改起点距必须连带把 x/y 挪到位。

        只改 s 不改 x/y 的后果：桩号、水位交点平面坐标、成灾水位坐标导出
        全部留在旧位置——图上是新断面，导出是旧坐标。
        """
        sec = _straight_sec(ux=0.6, uy=0.8, span=50.0)
        sec.s[3] = 30.0                      # 原为 30.0 -> 先造一个已知状态
        edit.recompute_xy(sec)
        edit.set_s(sec, 3, 35.0)
        self.assertAlmostEqual(sec.s[3], 35.0)
        self.assertAlmostEqual(sec.x[3], sec.x[0] + 35.0 * 0.6, places=9)
        self.assertAlmostEqual(sec.y[3], sec.y[0] + 35.0 * 0.8, places=9)

    def test_set_s_keeps_strictly_increasing(self):
        """起点距被夹在相邻两点之间，绝不出现相等或倒序。

        一旦出现，`interp` 与分区判定会遇到 0 长度 / 负长度区间。
        """
        sec = _straight_sec()
        applied = edit.set_s(sec, 2, 999.0)      # 想拖到最右端之外
        self.assertLess(applied, sec.s[3])
        self.assertGreater(applied, sec.s[1])
        applied = edit.set_s(sec, 2, -999.0)     # 想拖到最左端之外
        self.assertGreater(applied, sec.s[1])
        for i in range(1, sec.n_points):
            self.assertGreater(sec.s[i], sec.s[i - 1])

    def test_set_z_does_not_touch_s_or_xy(self):
        """只改高程时，起点距与平面坐标一律不动——这是最安全的编辑路径。"""
        sec = _straight_sec()
        s0, x0, y0 = list(sec.s), list(sec.x), list(sec.y)
        edit.set_z(sec, 2, 12.34)
        self.assertEqual(sec.s, s0)
        self.assertEqual(sec.x, x0)
        self.assertEqual(sec.y, y0)
        self.assertAlmostEqual(sec.z[2], 12.34)

    def test_insert_keeps_shape_and_shifts_manual(self):
        """插入点不应改变断面形态，且之后的手动索引要 +1。"""
        sec = _straight_sec()
        sec.thalweg_manual = 3
        sec.zone_manual = True
        sec.zone_left = 1
        sec.zone_right = 4
        idx = edit.insert_point(sec, 3)
        self.assertEqual(idx, 3)
        self.assertEqual(sec.n_points, 7)
        self.assertEqual(sec.thalweg_manual, 4)     # >=3 的都要 +1
        self.assertEqual(sec.zone_left, 1)          # <3 的不动
        self.assertEqual(sec.zone_right, 5)
        for i in range(1, sec.n_points):
            self.assertGreater(sec.s[i], sec.s[i - 1])   # 仍然严格递增
        self.assertEqual([len(a) for a in (sec.x, sec.y, sec.s, sec.z)],
                         [7, 7, 7, 7])

    def test_delete_shifts_manual_and_clears_hit(self):
        """删点：后面的索引 -1；删掉的正好是被指定的那个则清空并报出来。"""
        sec = _straight_sec()
        sec.thalweg_manual = 4
        sec.disaster_idx_manual = 2
        cleared = edit.delete_point(sec, 2)
        self.assertEqual(cleared, ["手动成灾水位测点"])
        self.assertIsNone(sec.disaster_idx_manual)
        self.assertEqual(sec.thalweg_manual, 3)     # 4 -> 3
        self.assertEqual(sec.n_points, 5)

    def test_delete_refuses_below_two_points(self):
        sec = _straight_sec(n=3)
        self.assertEqual(edit.delete_point(sec, 1), [])
        self.assertEqual(sec.n_points, 2)
        self.assertEqual(edit.delete_point(sec, 0), ["至少要保留 2 个测点，不能继续删除"])
        self.assertEqual(sec.n_points, 2, "拒绝删除时不能把点也删掉")

    def test_snapshot_restore_roundtrip(self):
        """撤销依赖快照能完整还原：几何四列 + 全部手动覆盖。"""
        sec = _straight_sec()
        sec.thalweg_manual = 2
        sec.zone_manual = True
        sec.zone_left, sec.zone_right = 1, 4
        sec.disaster_idx_manual = 3
        snap = edit.snapshot(sec)
        edit.delete_point(sec, 1)
        edit.set_z(sec, 0, -999.0)
        edit.restore(sec, snap)
        self.assertEqual(sec.s, snap["s"])
        self.assertEqual(sec.z, snap["z"])
        self.assertEqual(sec.x, snap["x"])
        self.assertEqual(sec.y, snap["y"])
        self.assertEqual(sec.thalweg_manual, 2)
        self.assertTrue(sec.zone_manual)
        self.assertEqual((sec.zone_left, sec.zone_right), (1, 4))
        self.assertEqual(sec.disaster_idx_manual, 3)
        self.assertEqual(sec.validate(check_params=False), [])

    def test_editing_keeps_section_valid(self):
        """改完、插完、删完，`Section.validate` 的几何校验都必须还是空的。

        四列不等长是最容易制造出来的隐蔽错误：它不崩，
        但会让后续所有按索引取值的计算错位。
        """
        sec = _straight_sec()
        edit.set_z(sec, 1, 5.0)
        edit.set_s(sec, 2, 22.0)
        edit.insert_point(sec, 4)
        edit.delete_point(sec, 0)
        self.assertEqual(sec.validate(check_params=False), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
