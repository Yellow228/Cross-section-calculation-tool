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
        self.assertAlmostEqual(z_up, 11.979, places=2)
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
        levels = compute_hydro1d_profile(line, cfg, results)
        self.assertEqual(len(levels), 2)
        self.assertAlmostEqual(levels[0], 11.979, places=2)
        self.assertEqual(levels[1], 12.0)

if __name__ == "__main__":
    unittest.main()
