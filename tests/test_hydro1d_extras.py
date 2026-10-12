import unittest
import math
from src.core.model import Section, SectionParams, ProfileLine, ProfileData
from src.core.hydro1d import compute_hydro1d_profile
from src.core.config import Config

class TestHydroExtras(unittest.TestCase):
    def test_mixed_flow(self):
        sec1 = Section(name="S1", x=[0, 10], y=[0, 0], s=[0, 10], z=[10.0, 10.0], params=SectionParams(name="S1", design_q=50.0))
        sec2 = Section(name="S2", x=[0, 10], y=[10, 10], s=[0, 10], z=[9.5, 9.5], params=SectionParams(name="S2", design_q=50.0))
        sec3 = Section(name="S3", x=[0, 10], y=[20, 20], s=[0, 10], z=[9.0, 9.0], params=SectionParams(name="S3", design_q=50.0))
        sec4 = Section(name="S4", x=[0, 10], y=[120, 120], s=[0, 10], z=[8.9, 8.9], params=SectionParams(name="S4", design_q=50.0)) # mild slope

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
        for lv in levels:
            self.assertFalse(math.isnan(lv))

    def test_spacing_warnings(self):
        sec1 = Section(name="S1", x=[0, 10], y=[0, 0], s=[0, 10], z=[10.0, 10.0], params=SectionParams(name="S1", design_q=50.0))
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
