import unittest
import math
from src.core.model import Section, SectionParams, ProfileLine, ProfileData
from src.core.hydro1d import compute_hydro1d_profile
from src.core.config import Config

class TestHydroExtras(unittest.TestCase):
    def test_mixed_flow(self):
        sec1 = Section(name="S1", x=[0, 10], y=[0, 0], s=[0, 10], z=[10.0, 10.0], params=SectionParams(name="S1", design_q=50.0, slope=0.05))
        sec2 = Section(name="S2", x=[0, 10], y=[10, 10], s=[0, 10], z=[9.5, 9.5], params=SectionParams(name="S2", design_q=50.0))
        sec3 = Section(name="S3", x=[0, 10], y=[20, 20], s=[0, 10], z=[9.0, 9.0], params=SectionParams(name="S3", design_q=50.0))
        sec4 = Section(name="S4", x=[0, 10], y=[120, 120], s=[0, 10], z=[8.9, 8.9], params=SectionParams(name="S4", design_q=50.0, slope=0.001)) # mild slope

        line = ProfileLine(
            name="test_mixed",
            sections=[sec1, sec2, sec3, sec4],
            chainage=[0.0, 10.0, 20.0, 120.0],
            hydro1d_enabled=True,
            hydro1d_regime="mixed",
            profile=ProfileData(dist=[0.0, 10.0, 20.0, 120.0], z=[10.0, 9.5, 9.0, 8.9])
        )

        cfg = Config()
        warnings = []
        levels, nodes = compute_hydro1d_profile(line, cfg, {}, warnings=warnings)

        self.assertEqual(len(levels), 4)

        # Ensure no NaNs are returned (physical consistency)
        for i, lvl in enumerate(levels):
            self.assertFalse(math.isnan(lvl), f"Level at index {i} is NaN")

        # The first parts are steep, should be supercritical
        self.assertGreater(nodes[0].Fr, 1.0)
        # Because we only set slope on S1 to be steep, the transition could happen anywhere
        # depending on S2 and S3 parameters. S2 and S3 don't have steep slopes (they use fallback or default).
        # We just need to check that a jump to subcritical occurred, meaning the last part is mild.

        # The last part is mild, should be subcritical
        self.assertLess(nodes[-1].Fr, 1.0)


    def test_spacing_warnings(self):
        sec1 = Section(name="S1", x=[0, 10], y=[0, 0], s=[0, 10], z=[10.0, 10.0], params=SectionParams(name="S1", design_q=50.0, slope=0.05))
        sec2 = Section(name="S2", x=[0, 10], y=[1000, 1000], s=[0, 10], z=[9.0, 9.0], params=SectionParams(name="S2", design_q=50.0))

        line = ProfileLine(
            name="test_spacing",
            sections=[sec1, sec2],
            chainage=[0.0, 1000.0],
            hydro1d_enabled=True,
            hydro1d_regime="subcritical",
            profile=ProfileData(dist=[0.0, 1000.0], z=[10.0, 9.0])
        )

        cfg = Config()
        warnings = []
        levels, nodes = compute_hydro1d_profile(line, cfg, {}, warnings=warnings)

        self.assertTrue(any("断面间距可能过大" in w for w in warnings))

if __name__ == "__main__":
    unittest.main()
