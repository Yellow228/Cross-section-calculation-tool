"""用真实 xlsx 验证「按横断面与纵断面相交分组」是否可行。

不需要 openpyxl —— 直接用 zipfile + XML 读表，再喂给 core.spatial。
用法：python tools/check_spatial.py [数据目录]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import zipfile  # noqa: E402

from peek_xlsx import load_shared_strings, read_sheet, sheet_files  # noqa: E402

from core.chainage import rebase_chainage  # noqa: E402
from core.config import Config  # noqa: E402
from core.reader import blocks_to_sections, split_blocks  # noqa: E402
from core.spatial import assign_by_intersection  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_file(path: str, cfg: Config):
    with zipfile.ZipFile(path) as z:
        shared = load_shared_strings(z)
        name, target = sheet_files(z)[0]
        raw = read_sheet(z, target, shared)
    blocks = split_blocks(raw, cfg)
    secs, prof, skipped = blocks_to_sections(blocks, cfg)
    return secs, prof, skipped


def main() -> None:
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data")
    cfg = Config()

    profiles, all_secs, owners = [], [], []
    out = []

    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".xlsx") or fn.startswith("~$"):
            continue
        secs, prof, skipped = load_file(os.path.join(folder, fn), cfg)
        out.append(f"===== {fn} =====")
        out.append(f"  横断面 {len(secs)} 个: {[s.name for s in secs]}")
        if skipped:
            out.append(f"  已排除: {skipped}")
        if prof is None:
            out.append("  !! 无「纵断面」块")
            continue
        prof.chainage = rebase_chainage(prof.dist, prof.z, cfg.chainage_origin)
        out.append(f"  纵断面 {prof.n_points} 点, 里程 {prof.chainage[0]:.1f} ~ "
                   f"{prof.chainage[-1]:.1f} m (origin={cfg.chainage_origin})")
        all_secs.extend(secs)
        profiles.append(prof)
        owners.append(os.path.splitext(fn)[0])

    if not profiles:
        out.append("\n没有纵断面，无法做空间分组")
    else:
        groups, details = assign_by_intersection(all_secs, profiles,
                                                 cfg.intersection_tolerance)
        out.append("\n===== 空间分组结果 =====")
        for pi, prof in enumerate(profiles):
            names = [all_secs[si].name for si in groups[pi]]
            out.append(f"\n纵断面线[{pi}] 来自 {owners[pi]}  共 {len(names)} 个横断面")
            for si in groups[pi]:
                d = details[si]
                out.append(f"    {all_secs[si].name:<10} 交点里程={d['dist']:8.2f} m  "
                           f"间隙={d['gap']:.3f} m  冲突={d['conflicts']}")
        bad = [all_secs[i].name for i, d in enumerate(details) if d["profile"] is None]
        if bad:
            out.append(f"\n!! 未与任何纵断面相交: {bad}")
        else:
            out.append("\n所有横断面都成功归入某条纵断面线")

    with open(os.path.join(ROOT, "_spatial_check.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))


if __name__ == "__main__":
    main()
