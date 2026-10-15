import math
from src.core.rating import compute_rating_curve
from src.core.model import Section, SectionParams, TerrainInfo
from src.core.config import Config

def test_zero_roughness():
    sec = Section(name="Test", x=[0, 10, 20], y=[0, 0, 0], s=[0, 10, 20], z=[10, 0, 10], params=SectionParams(name="Test", roughness=0.0))
    info = TerrainInfo(dmin=0, zymin=10, dmin_auto=0, zymin_auto=10)
    cfg = Config(compound_mode=False)

    # rating curve
    hvec, qvec, _, _, _ = compute_rating_curve(sec, info, cfg)
    assert len(qvec) > 0
    assert not any(math.isnan(q) or math.isinf(q) for q in qvec)
