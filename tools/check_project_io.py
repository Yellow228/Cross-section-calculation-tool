"""核对：工程文件是否完整保存了「计算设置」与「每个断面的参数」。

逐字段比对（不是抽样），并检查打开后用恢复的设置重算，结果是否与保存前一致。
只读，临时文件写在系统临时目录，不碰 data/ 与 output/。
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from dataclasses import fields

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from core import project_io                              # noqa: E402
from core.config import Config                           # noqa: E402
from core.model import SectionParams                     # noqa: E402
from core.reader import load_folder                      # noqa: E402
from core.solver import solve_section                    # noqa: E402

L: list[str] = []


def log(s: str = "") -> None:
    L.append(s)


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
    return a == b


def main() -> None:
    data = os.path.join(ROOT, "data")
    cfg1 = Config()
    proj1, _ = load_folder(data, cfg1)

    # 与界面 `_load` 的行为保持一致：空参数先用默认值填补。
    # 否则报告里全显示 nan，看起来像「工程文件没保存参数」——
    # 实际只是直接调 load_folder 时跳过了界面的自动填补。
    from core.params import apply_batch, missing_params
    if missing_params(proj1.all_sections()):
        apply_batch(proj1.all_sections(), only_missing=True,
                    roughness=0.03, slope=0.005, design_q=50.0)

    with tempfile.TemporaryDirectory() as td:
        dst = os.path.join(td, "verify.dmprj")
        project_io.save_project(dst, proj1, cfg1,
                                source={"dir": data, "files": []})
        proj2, cfg2, meta = project_io.load_project(dst)
        size = os.path.getsize(dst)
        with open(dst, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)

    secs1 = proj1.all_sections()
    secs2 = proj2.all_sections()

    # ---------------- ① 计算设置 ----------------
    cfg_fields = [f.name for f in fields(Config)]
    log("=" * 96)
    log("① 计算设置（Config）逐字段比对")
    log("=" * 96)
    log(f"  工程文件里的 config 段共 {len(raw['config'])} 个字段")
    bad = []
    for name in cfg_fields:
        v1, v2 = getattr(cfg1, name), getattr(cfg2, name)
        if not _same(v1, v2):
            bad.append(name)
    for i in range(0, len(cfg_fields), 3):
        row = []
        for name in cfg_fields[i:i + 3]:
            mark = "✗" if name in bad else "✓"
            row.append(f"{mark} {name}={getattr(cfg2, name)!r}")
        log("  " + "　".join(f"{c:<38}" for c in row))
    log(f"  → 共 {len(cfg_fields)} 项，未恢复 {len(bad)} 项"
        + (f"：{bad}" if bad else "（全部一致）"))

    # ---------------- ② 断面参数 ----------------
    pf = [f.name for f in fields(SectionParams)]
    log("")
    log("=" * 96)
    log("② 每个断面的参数（SectionParams）逐字段比对")
    log("=" * 96)
    log(f"  字段：{', '.join(pf)}")
    log(f"  断面数：{len(secs1)}")
    bad2 = []
    for a, b in zip(secs1, secs2):
        for name in pf:
            v1, v2 = getattr(a.params, name), getattr(b.params, name)
            if not _same(v1, v2):
                bad2.append(f"{a.name}.{name}: {v1!r} -> {v2!r}")
    log(f"  → 共 {len(secs1)} × {len(pf)} = {len(secs1) * len(pf)} 个值，"
        f"不一致 {len(bad2)} 处" + ("" if not bad2 else "："))
    for x in bad2[:15]:
        log("     " + x)

    # 打印几个真实值，便于肉眼确认
    log("")
    log("  抽样（前 3 个断面 + 一个参数为空的断面）：")
    for s in list(secs2)[:3]:
        p = s.params
        log(f"    {s.name:<10} 比降={p.slope:<10g} 糙率={p.roughness:<8g} "
            f"Qs={p.design_q:<8g} 主槽={p.roughness_main} "
            f"左滩={p.roughness_left} 右滩={p.roughness_right}")
    empties = [s.name for s in secs2 if math.isnan(s.params.slope)][:3]
    log(f"    比降为空的断面：{empties or '（无）'}")

    # ---------------- ③ 恢复后重算是否一致 ----------------
    log("")
    log("=" * 96)
    log("③ 用恢复的设置重算，结果是否与保存前一致")
    log("=" * 96)
    bad3 = []
    for a, b in zip(secs1, secs2):
        r1, i1 = solve_section(a, cfg1)
        r2, i2 = solve_section(b, cfg2)
        if not _same(r1.design_level, r2.design_level):
            bad3.append(f"{a.name}.Hs {r1.design_level} -> {r2.design_level}")
        if not _same(r1.disaster_flow, r2.disaster_flow):
            bad3.append(f"{a.name}.成灾流量 {r1.disaster_flow} -> {r2.disaster_flow}")
        if len(r1.hvec) != len(r2.hvec):
            bad3.append(f"{a.name}.曲线点数 {len(r1.hvec)} -> {len(r2.hvec)}")
        if not _same(i1.disaster_level, i2.disaster_level):
            bad3.append(f"{a.name}.成灾水位 {i1.disaster_level} -> {i2.disaster_level}")
    log(f"  → {len(secs1)} 个断面比对 Hs / 成灾流量 / 成灾水位 / 曲线点数，"
        f"不一致 {len(bad3)} 处")
    for x in bad3[:10]:
        log("     " + x)

    log("")
    log(f"  工程文件体积：{size / 1024:.1f} KB")
    log(f"  format={meta['format']}  version={meta['version']}")

    text = "\n".join(L)
    out = os.path.join(ROOT, "output", "工程文件核对报告.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
