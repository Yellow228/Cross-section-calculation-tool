import unittest
import math

from src.core.model import Section, SectionParams
from src.core.config import Config
from src.core.terrain import analyze_terrain
from src.core.rating import compute_rating_curve

class TestRatingCurve(unittest.TestCase):
    def test_negative_slope_handled_safely(self):
        sec = Section(
            name="test_neg_slope",
            s=[0, 10, 20],
            z=[5, 0, 5],
            x=[0, 10, 20],
            y=[0, 0, 0],
            params=SectionParams(name="test_neg_slope", slope=-0.001)
        )
        cfg = Config(dH=1.0)
        info = analyze_terrain(sec, cfg)

        # This should not raise a ValueError (math domain error)
        hvec, qvec, avec, pvec, bvec = compute_rating_curve(sec, info, cfg)

        # When slope is effectively 0, Q should be 0 for all points
        self.assertTrue(all(q == 0.0 for q in qvec))

    def test_zero_roughness_handled_safely(self):
        sec = Section(
            name="test_zero_n",
            s=[0, 10, 20],
            z=[5, 0, 5],
            x=[0, 10, 20],
            y=[0, 0, 0],
            params=SectionParams(name="test_zero_n", slope=0.001, roughness=0.0)
        )
        cfg = Config(dH=1.0)
        info = analyze_terrain(sec, cfg)

        # This should not raise a ZeroDivisionError
        hvec, qvec, avec, pvec, bvec = compute_rating_curve(sec, info, cfg)

        # Ensure it computed some Q values > 0 (using fallback 0.03 roughness)
        if len(qvec) > 1: # at least one H > z_min
            self.assertTrue(any(q > 0.0 for q in qvec))

    def test_missing_roughness_handled_safely(self):
        sec = Section(
            name="test_missing_n",
            s=[0, 10, 20],
            z=[5, 0, 5],
            x=[0, 10, 20],
            y=[0, 0, 0],
            params=SectionParams(name="test_missing_n", slope=0.001, roughness=float('nan'))
        )
        cfg = Config(dH=1.0)
        info = analyze_terrain(sec, cfg)

        # This should use fallback 0.03 roughness
        hvec, qvec, avec, pvec, bvec = compute_rating_curve(sec, info, cfg)

        if len(qvec) > 1:
            self.assertTrue(any(q > 0.0 for q in qvec))

    def test_rating_zero_division_safety(self):
        """Test that rating curve avoids division by zero when wetted perimeter evaluates to 0."""
        from src.core.rating import compute_rating_curve
        from src.core.model import Section, SectionParams, TerrainInfo
        from src.core.config import Config
        import src.core.rating

        # Setup standard context
        sec = Section(name="Test", x=[0, 10], y=[0, 0], s=[0, 10], z=[10, 10])
        sec.params = SectionParams(name="Test", slope=0.001)

        info = TerrainInfo(dmin=10, zymin=12.0, zmax=12.0, ymax=12.0, zmax_idx=0, ymax_idx=1, dmin_idx=0)
        info.zones = [(0, 2)]
        info.zymin_auto = 12.0
        info.dmin_auto = 10

        cfg = Config()
        cfg.dH = 0.5
        cfg.compound_mode = False

        # Mock section_geom to simulate the edge case where A > 0 but P == 0
        original_geom = src.core.rating.section_geom
        src.core.rating.section_geom = lambda x, z, H: (1e-6, 0.0, 10.0)

        try:
            # Should not raise ZeroDivisionError
            compute_rating_curve(sec, info, cfg)
        finally:
            # Restore mock
            src.core.rating.section_geom = original_geom

if __name__ == "__main__":
    unittest.main()
