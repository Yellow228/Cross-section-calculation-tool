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

if __name__ == "__main__":
    unittest.main()
