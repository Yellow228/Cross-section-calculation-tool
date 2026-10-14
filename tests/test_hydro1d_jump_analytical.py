
import unittest
import math
from core.model import Section, SectionParams, ProfileLine, ProfileData
from core.hydro1d import compute_hydro1d_profile
from core.config import Config

class TestHydraulicJumpAnalytical(unittest.TestCase):
    def test_hydraulic_jump_conjugate_depth(self):
        # S0: 陡坡, Z=0.5m, s=0m
        sec0 = Section("S0", x=[0, 10], y=[0, 0], s=[0, 10], z=[0.5, 0.5], params=SectionParams(name="S0", design_q=50.0, roughness=0.015))
        # S1: 坡折控制断面, Z=0.0m, s=10m
        sec1 = Section("S1", x=[0, 10], y=[10, 10], s=[0, 10], z=[0.0, 0.0], params=SectionParams(name="S1", design_q=50.0, roughness=0.015))
        # S2: 缓坡段, Z=-0.01m, s=20m
        sec2 = Section("S2", x=[0, 10], y=[20, 20], s=[0, 10], z=[-0.01, -0.01], params=SectionParams(name="S2", design_q=50.0, roughness=0.015))

        sections = [sec0, sec1, sec2]
        s_list = [0.0, 10.0, 20.0]
        z_list = [0.5, 0.0, -0.01]

        line = ProfileLine(
            name="test_jump",
            sections=sections,
            chainage=s_list,
            hydro1d_enabled=True,
            hydro1d_regime="mixed",
            profile=ProfileData(dist=s_list, z=z_list)
        )

        cfg = Config()
        warnings = []

        # S0 h1=0.5m -> Z_initial=1.0m
        # S2 赋予下游理论跃后缓流水深 h2 approx 2.95m -> Z_initial=2.94m
        h1 = 0.5
        Fr1 = 10.0 / math.sqrt(9.81 * 0.5)
        h2 = (h1 / 2.0) * (math.sqrt(1.0 + 8.0 * Fr1**2) - 1.0)

        results = {
            "S0": type("Res", (), {"design_level": 0.5 + h1})(),
            "S2": type("Res", (), {"design_level": -0.01 + h2})()
        }

        levels, nodes = compute_hydro1d_profile(line, cfg, results, warnings=warnings)

        jump_found = False
        for i in range(2):
            if nodes[i] is None or nodes[i+1] is None:
                continue

            Fr_i = nodes[i].Fr
            Fr_next = nodes[i+1].Fr

            if Fr_i > 1.0 and Fr_next < 1.0:
                jump_found = True
                from core.hydro1d import calculate_specific_force
                M_sup = calculate_specific_force(sections[i], levels[i], 50.0, 1.0, cfg)
                M_sub = calculate_specific_force(sections[i+1], levels[i+1], 50.0, 1.0, cfg)
                self.assertTrue(abs(M_sup - M_sub) / M_sup < 1e-2)
                break

        self.assertTrue(jump_found, "Hydraulic jump was not found in the domain.")

if __name__ == "__main__":
    unittest.main()
