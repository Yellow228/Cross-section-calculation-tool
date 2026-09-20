"""按纵断面推算各横断面比降，输出建议值表供核对。

对应界面上的「按纵断面推算比降」对话框，命令行可用它先看结果。
只读，不修改任何数据。

默认口径为**约翰斯通-克罗斯法**（Johnstone & Cross, 1949），
同时并列显示两端点法与最小二乘，便于判断该信哪个。

用法：
    python tools/check_slope.py [数据目录]
"""

from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from core import slope as S                                    # noqa: E402
from core.config import Config                                 # noqa: E402
from core.reader import load_folder                            # noqa: E402


def main() -> None:
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data")
    project, _ = load_folder(folder, Config())

    props, ests = S.propose_slopes(project, mode="jc")
    by_line: dict[str, list] = {}
    for p in props:
        by_line.setdefault(p.line, []).append(p)

    out: list[str] = []
    out.append("=" * 104)
    out.append("比降推算核对表")
    out.append(f"默认口径：【{S.MODE_LABELS['jc']}】"
               f"　另两种口径并列显示便于对比")
    out.append("河段范围：最上游横断面 ~ 最下游横断面之间（不含纵断面两端的延伸段）")
    out.append("=" * 104)

    for est in ests:
        out.append(f"\n▌{est.line}")
        if not est.ok:
            out.append(f"   ✗ {est.message}")
            continue

        out.append(f"   河段 {est.start:.2f} ~ {est.end:.2f} m"
                   f"　长 {est.length:.2f} m　落差 {est.drop:.3f} m"
                   f"　纵断面测点 {est.n_points} 个")
        out.append(f"   ┌ 约翰斯通-克罗斯 {est.slope_jc:10.6f}  ← 采用")
        out.append(f"   │ 两端点法       {est.slope_endpoints:10.6f}"
                   f"   （相对 JC {100 * (est.slope_endpoints / est.slope_jc - 1):+.1f}%）")
        out.append(f"   └ 最小二乘       {est.slope_lsq:10.6f}"
                   f"   （相对 JC {100 * (est.slope_lsq / est.slope_jc - 1):+.1f}%）"
                   f"   R²={est.r2:.4f}")
        if est.jc_skipped:
            pct = 100 * est.jc_skipped_len / est.length
            out.append(f"   ⚠ JC 法跳过了 {est.jc_skipped} 个倒坡子河段"
                       f"（占长度 {pct:.1f}%），会使结果略偏高，建议核查该段纵断面")

        out.append(f"   {'断面':<12}{'起点距m':>10}{'桩号m':>10}"
                   f"{'纵断面高程':>12}{'当前比降':>12}{'建议比降':>12}")
        for p in by_line.get(est.line, []):
            cur = "（空）" if not math.isfinite(p.current) else f"{p.current:.5f}"
            prop = "—" if p.bad else f"{p.proposed:.5f}"
            flag = "  ⚠倒坡/无效" if p.bad else ""
            out.append(f"   {p.section:<12}{p.profile_dist:>10.2f}"
                       f"{p.chainage:>10.2f}{p.z_profile:>12.3f}"
                       f"{cur:>12}{prop:>12}{flag}")

    text = "\n".join(out)
    dst = os.path.join(ROOT, "output", "比降推算核对表.txt")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
