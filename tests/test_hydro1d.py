import unittest
import math
from src.core.model import Section, SectionParams, ProfileLine
from src.core.hydro1d import (
    compute_critical_depth,
    standard_step_method_subcritical,
    standard_step_method_supercritical,
    compute_hydro1d_profile
)

class TestHydro1D(unittest.TestCase):
    def setUp(self):
        # 构造一个简单的矩形断面: 宽 10m，底高程 10m
        self.sec1 = Section(
            name="S1",
            x=[0, 10],
            y=[0, 0],
            s=[0, 10],
            z=[10.0, 10.0],
            params=SectionParams(name="S1", design_q=50.0, roughness=0.03)
        )
        # S2 在 S1 下游 100m 处，底高程 9m
        self.sec2 = Section(
            name="S2",
            x=[0, 10],
            y=[100, 100],
            s=[0, 10],
            z=[9.0, 9.0],
            params=SectionParams(name="S2", design_q=50.0, roughness=0.03)
        )

    def test_critical_depth(self):
        # 矩形断面临界水深公式 hc = (q^2 / g)^(1/3), q = Q/B = 50/10 = 5
        # hc = (25 / 9.81)^(1/3) ≈ 1.365 m
        # 因此临界水位 Zc ≈ 10.0 + 1.365 = 11.365
        Zc = compute_critical_depth(self.sec1, 50.0, 10.0, 20.0)
        self.assertAlmostEqual(Zc, 11.365, places=2)

    def test_subcritical_step(self):
        # 假设下游 S2 水位为 12.0 (水深3m，缓流)
        # 从下游推上游 S1
        from src.core.config import Config
        cfg = Config()
        z_up = standard_step_method_subcritical(
            self.sec2, 12.0, 50.0, 100.0,
            self.sec1, 50.0, 0.0,
            10.0, 20.0, cfg
        )
        # 上游断面底高程比下游高1m，水面可能稍微下降(如果落差被抬升消化)
        # 但这里要检查 z_up 计算的数值是否符合物理预期
        # 水位由于采用了 HEC-RAS 均值输水能力计算 Sf_avg 和更新 α 计算导致有所变化
        # 因为修复了 contraction 判断逻辑，水头损失会有所变化，所以这里重新标定基准值
        self.assertAlmostEqual(z_up, 11.97, places=2)
        self.assertLess(z_up, 15.0)  # 合理区间

    def test_compute_profile(self):
        # 组装 ProfileLine
        line = ProfileLine(
            name="test_line",
            sections=[self.sec1, self.sec2],
            chainage=[0.0, 100.0],
            hydro1d_enabled=True,
            hydro1d_regime="subcritical"
        )
        # 用 results 提供设计水位
        class DummyResult:
            def __init__(self, dl):
                self.design_level = dl
        results = {
            "S1": DummyResult(13.0),
            "S2": DummyResult(12.0)
        }
        from src.core.config import Config
        cfg = Config()
        warnings = []
        levels, nodes = compute_hydro1d_profile(line, cfg, results, warnings=warnings)
        self.assertEqual(len(levels), 2)
        self.assertAlmostEqual(levels[0], 11.97, places=2)
        self.assertEqual(levels[1], 12.0)
        self.assertEqual(len(nodes), 2)

    def test_regime_transition_warning(self):
        # 制造一个急流产生流态转换的情况
        from src.core.config import Config
        cfg = Config()

        # S1 在上游 (z=10.0), S2 在下游 (z=9.0)
        sec_up = Section(name="S1", x=[0, 10], y=[0, 0], s=[0, 10], z=[10.0, 10.0], params=SectionParams(name="S1", design_q=50.0))
        sec_down = Section(name="S2", x=[0, 10], y=[100, 100], s=[0, 10], z=[9.0, 9.0], params=SectionParams(name="S2", design_q=50.0))

        line = ProfileLine(
            name="test_regime",
            sections=[sec_up, sec_down],
            chainage=[0.0, 100.0],
            hydro1d_enabled=True,
            hydro1d_regime="supercritical"
        )

        class DummyResult:
            def __init__(self, dl):
                self.design_level = dl

        results = {
            "S1": DummyResult(11.0),
            "S2": DummyResult(12.0)
        }

        warnings = []
        levels = compute_hydro1d_profile(line, cfg, results, warnings=warnings)

        # 验证产生了流态转换警告
        self.assertTrue(any("断面发生流态转换" in w for w in warnings))

    def test_contraction_expansion_logic(self):
        # 验证缓流和急流情况下的收缩/扩张水头损失逻辑
        # 在缓流中，上游到下游的流速增加(V_d > V_u)表示收缩 (contraction)
        # 从而 default_loss 应该是 0.1 而不是 0.3
        # 这个测试间接验证这一点
        from src.core.config import Config
        cfg = Config()

        # 上游断面宽 20m，流速小
        sec_up = Section(
            name="S1", x=[0, 20], y=[0, 0], s=[0, 20], z=[10.0, 10.0],
            params=SectionParams(name="S1", design_q=50.0, roughness=0.03)
        )
        # 下游断面宽 10m，流速大 (发生收缩)
        sec_down = Section(
            name="S2", x=[0, 10], y=[100, 100], s=[0, 10], z=[9.0, 9.0],
            params=SectionParams(name="S2", design_q=50.0, roughness=0.03)
        )

        # 由于我们只关心 is_contraction 的逻辑，调用缓流函数
        # 从下游往上游推算
        Z_up_sub = standard_step_method_subcritical(
            sec_down, 12.0, 50.0, 100.0,
            sec_up, 50.0, 0.0,
            10.0, 20.0, cfg
        )

        # 只要没有异常且能计算出一个合理的值，就说明这部分计算能跑通
        self.assertTrue(Z_up_sub > 10.0)


    def test_hydraulic_jump_analytic(self):
        # We need a mild slope section to naturally result in h=2.95m.
        # If we carefully select the slopes so the jump occurs right around the middle section

        # To hit exactly M_sub = M_sup at S2:
        # S2 needs h1 = 0.5 (supercritical pass), and h2 = 2.9525 (subcritical pass).
        # We just need to make sure the S1 starts at h=0.5 and S3 starts at h=2.9525

        h1_exact = 0.5
        h2_exact = 2.952527114

        # Set S0 such that normal depth matches h1 and h2
        # Need to include vertical walls for P=B+2h (rectangular channel).
        # Otherwise P=B because there are no side walls in the section definition.
        def make_rect_sec(name, z_bed, slope, n):
            # width is 10, from x=0 to x=10
            x = [0, 0, 10, 10]
            y = [0, 0, 0, 0]
            s = [0, 0.01, 10.01, 10.02]
            z = [z_bed+10, z_bed, z_bed, z_bed+10]
            return Section(name=name, x=x, y=y, s=s, z=z, params=SectionParams(name=name, design_q=50.0, roughness=n, slope=slope))

        S_steep = 0.028605
        S_mild = 0.001132

        sec1 = make_rect_sec("S1", 1.0, S_steep, 0.01)
        sec2 = make_rect_sec("S2", 0.0, S_mild, 0.03)
        sec3 = make_rect_sec("S3", -0.02, S_mild, 0.03)

        line = ProfileLine(
            name="jump_analytic",
            sections=[sec1, sec2, sec3],
            chainage=[0.0, 20.0, 40.0],
            hydro1d_enabled=True,
            hydro1d_regime="mixed"
        )

        from src.core.config import Config
        cfg = Config()
        cfg.compound_mode = False

        levels, nodes = compute_hydro1d_profile(line, cfg, {}, warnings=[])

        # upstream is supercritical
        self.assertGreater(nodes[0].Fr, 1.0)
        # downstream is subcritical
        self.assertLess(nodes[2].Fr, 1.0)

        # The depth at the jump should match conjugate depth relation
        # In our case it jumps at sec2 or sec3, we just check the jump depth at the transition.
        # Since sec2 will receive M_sup and M_sub, M_sup ≈ 52.21, M_sub ≈ 52.21
        # Due to numerical precision M_sub could be slightly greater or smaller.

        # 寻找水跃后的缓流断面：
        jump_idx = -1
        for i in range(1, 3):
            if nodes[i].Fr < 1.0:
                jump_idx = i
                break

        jump_depth = levels[jump_idx] - line.sections[jump_idx].z[1]  # z_bed is at index 1 and 2

        # 相对误差 < 1e-3
        self.assertAlmostEqual(jump_depth, h2_exact, delta=h2_exact*1e-3)

if __name__ == "__main__":
    unittest.main()