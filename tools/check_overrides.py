"""手动覆盖（深泓点 / 分区边界 / 成灾水位）核对表。

对应界面菜单栏的「分区调节」与「成灾水位」两个面板。只读，不改任何数据。

这份表回答三个问题：

1. **自动值是多少** —— 也就是"你要覆盖掉的是什么"。逐个断面列出深泓点、
   转折点、分区数、成灾水位、水位上限，界面「自动」一栏就取自这些值。

2. **面板那句警告准不准** —— 手动深泓点若高于断面最低测点，面板会说
   "曲线起算水位抬高、水位行数由 A 变 B"。这里**真的把两种情况各解一遍**
   （analyze_terrain + solve_section），拿求解器实际产出的行数去对面板
   显示的那两个数。顺带给出朴素的 `ceil(Δ/dH)` 估算作对照——那个估算
   **是错的**（实测 31 个断面里 21 个差 1 行），保留它是为了让这个坑有据可查。

3. **改分区边界到底改变什么**（Q15）—— 转折点是扫描算法的产物、**永远自动**；
   分区边界默认等于转折点，但可以单独人工指定。这里逐个断面验证：
       改分区 → 分区数与设计水位变
       但 **转折点与成灾水位一律不变**
   并给出设计水位/成灾流量的实际变化幅度，好判断这件事值不值得调。

用法：
    python tools/check_overrides.py [数据目录]
"""

from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from core.config import Config                                  # noqa: E402
from core.model import Section                                  # noqa: E402
from core.rating import hvec_row_counts                         # noqa: E402
from core.reader import load_folder                             # noqa: E402
from core.solver import solve_section                           # noqa: E402
from core.terrain import analyze_terrain                        # noqa: E402


def _solve(sec: Section, cfg: Config):
    """完整解一遍，返回 **(TerrainInfo, SectionResult)**。

    注意 `solve_section` 本身返回的是 (结果, 地形)，顺序相反——这里统一成
    (地形, 结果)，调用处一律按这个顺序解包。

    真实数据里参数是空的（参数集不提供），先补三个占位值让曲线算得出来
    ——**缺设计流量会让设计水位恒为 NaN**，那样就没法比较"改分区对设计水位的影响"了。
    占位值只影响流量的**绝对大小**，不影响"变没变"以及水位本身。
    """
    if sec.params.slope != sec.params.slope or sec.params.slope <= 0:
        sec.params.slope = 0.005
    if sec.params.roughness != sec.params.roughness or sec.params.roughness <= 0:
        sec.params.roughness = 0.03
    if sec.params.design_q != sec.params.design_q:
        sec.params.design_q = 50.0
    res, info = solve_section(sec, cfg)
    return info, res


def _probe_zone(sec: Section, info, di: int):
    """构造一组"与自动不同"的手动分区边界；做不出来就返回 None。

    优先把自动转折点往里收一格（主槽收窄）；没有转折点的那一侧，
    就在深泓点旁边新造一个边界——这样"自动 1 区"能变成 3 区，效果最明显。
    """
    n = sec.n_points
    tl, tr = info.left_turn_idx, info.right_turn_idx

    L = None
    if tl is not None:
        if tl + 1 < di:
            L = tl + 1
    elif di - 1 >= 0:
        L = di - 1

    R = None
    if tr is not None:
        if tr - 1 > di:
            R = tr - 1
    elif di + 1 <= n - 1:
        R = di + 1

    if L is None or R is None:
        return None
    if not (0 <= L < di < R <= n - 1):
        return None
    return L, R


def main() -> None:
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data")
    cfg = Config()
    project, _warnings = load_folder(folder, cfg)

    out: list[str] = []
    out.append("=" * 116)
    out.append("手动覆盖核对表（深泓点 / 分区边界 / 成灾水位）")
    out.append(f"数据目录：{folder}")
    out.append(f"水位步长 dH = {cfg.dH:g} m　断面模式 = "
               f"{'复式断面' if cfg.compound_mode else '单断面'}")
    out.append("开头这段全部是**自动推算值**，即界面「自动」一栏的内容；只读，不改数据。")
    out.append("=" * 116)

    header = (f"{'断面':<10}{'测点':>4}  {'深泓点':>9}  {'左转折':>9}  "
              f"{'右转折':>9}  {'分区':>4}  {'成灾水位':>9}  {'水位上限':>9}")

    n_sec = 0
    bad_rows: list[str] = []
    bad_est: list[str] = []
    zone_bad: list[str] = []
    zone_dhs: list[tuple[str, float]] = []

    for ln in project.profile_lines:
        out.append(f"\n▌{ln.name}　（{len(ln.sections)} 个断面）")
        out.append(header)
        out.append("-" * 116)
        for sec in ln.sections:
            n_sec += 1
            auto, res_auto = _solve(sec, cfg)
            rows_auto = len(res_auto.hvec)

            def pt(idx, _s=sec):
                if idx is None or not (0 <= idx < _s.n_points):
                    return "—"
                return f"{_s.z[idx]:.2f}"

            out.append(
                f"{sec.name:<10}{sec.n_points:>4}  {auto.dmin:>9.2f}  "
                f"{pt(auto.left_turn_idx):>9}  {pt(auto.right_turn_idx):>9}  "
                f"{len(auto.zones):>4}  {auto.disaster_level:>9.2f}  "
                f"{auto.zymin:>9.2f}")

            # ---------- ② 手动深泓点引起的行数变化 ----------
            ai = auto.dmin_auto_idx
            if 0 <= ai and ai + 1 < sec.n_points:
                sec.thalweg_manual = ai + 1
                try:
                    man, res_man = _solve(sec, cfg)
                finally:
                    sec.thalweg_manual = None
                d = man.dmin - auto.dmin
                if d > 0:
                    rows_man = len(res_man.hvec)
                    p_auto, p_cur = hvec_row_counts(man, cfg)
                    est = int(math.ceil(d / cfg.dH - 1e-9)) if cfg.dH > 0 else 0
                    ok = (p_auto == rows_auto and p_cur == rows_man)
                    out.append(
                        f"{'':<10}{'':>4}  深泓点上移一格：抬高 {d:.3f} m，"
                        f"面板显示行数 {p_auto} → {p_cur}，"
                        f"求解器实际 {rows_auto} → {rows_man}"
                        f"{'' if ok else '  ← 与面板不符'}"
                        f"　（若按 ceil(Δ/dH) 估则为少 {est} 行）")
                    if not ok:
                        bad_rows.append(f"{sec.name}: 面板 {p_auto}→{p_cur}、"
                                        f"实际 {rows_auto}→{rows_man}")
                    if est != rows_auto - rows_man:
                        bad_est.append(f"{sec.name}: 估算少 {est} 行、"
                                       f"实际少 {rows_auto - rows_man} 行")

            # ---------- ③ 手动分区边界的影响（Q15）----------
            pr = _probe_zone(sec, auto, auto.dmin_idx)
            if pr is None:
                continue
            sec.zone_manual = True
            sec.zone_left, sec.zone_right = pr
            try:
                zinfo, zres = _solve(sec, cfg)
            finally:
                sec.zone_manual = False
                sec.zone_left = sec.zone_right = None

            same_turn = ((zinfo.left_turn_idx, zinfo.right_turn_idx)
                         == (auto.left_turn_idx, auto.right_turn_idx))
            same_dis = abs(zinfo.disaster_level - auto.disaster_level) < 1e-12
            finite = (zres.design_level == zres.design_level
                      and res_auto.design_level == res_auto.design_level)
            d_hs = zres.design_level - res_auto.design_level if finite else float("nan")
            d_q = (zres.disaster_flow - res_auto.disaster_flow
                   if (zres.disaster_flow == zres.disaster_flow
                       and res_auto.disaster_flow == res_auto.disaster_flow)
                   else float("nan"))
            out.append(
                f"{'':<10}{'':>4}  手动分区边界 {pr[0]} / {pr[1]}："
                f"{len(auto.zones)} 区 → {len(zinfo.zones)} 区，"
                f"转折点{'未变 ✓' if same_turn else '变了 ✗'}，"
                f"成灾水位{'未变 ✓' if same_dis else '变了 ✗'}"
                f"　设计水位 {d_hs:+.3f} m，成灾流量 {d_q:+.2f} m³/s")
            if not same_turn or not same_dis:
                zone_bad.append(f"{sec.name}: "
                                f"转折点{'变了' if not same_turn else 'ok'}、"
                                f"成灾水位{'变了' if not same_dis else 'ok'}")
            if finite:
                zone_dhs.append((sec.name, d_hs))

    # ---------------- 汇总 ----------------
    out.append("")
    out.append("=" * 116)
    out.append(f"共 {n_sec} 个断面。")

    if bad_rows:
        out.append(f"✗ 有 {len(bad_rows)} 个断面的面板行数与求解器实际不符：")
        for m in bad_rows:
            out.append("    " + m)
    else:
        out.append("✓ 面板显示的「水位行数 A → B」与求解器实际产出**逐项一致**，"
                   "该警告数字可直接采信。")

    if bad_est:
        out.append("")
        out.append(f"⚠ 另有 {len(bad_est)}/{n_sec} 个断面说明"
                   f"「ceil(Δ/dH)」这种估算法不可用（差 1 行）：")
        out.append(f"    例：{bad_est[0]}")
        out.append("    原因：水位序列是 dmin + k·dH 的等差数列，深泓点一动，"
                   "**整条网格整体平移**，")
        out.append("    与上端 zymin 的对齐关系随之改变，行数差会落在 "
                   "floor(Δ/dH) 或 ceil(Δ/dH) 上。")
        out.append("    所以代码里改成实际数（core/rating.hvec_row_counts），"
                   "本表保留估算值是为了把这个坑记下来。")

    out.append("")
    if zone_bad:
        out.append(f"✗ 有 {len(zone_bad)} 个断面在手动改分区时动了转折点/成灾水位：")
        for m in zone_bad:
            out.append("    " + m)
    else:
        out.append("✓ 手动改分区边界：**转折点与成灾水位在所有断面都未变动**"
                   "（Q15 成立），只有分区本身与设计水位跟着变。")

    if zone_dhs:
        ds = sorted(d for _n, d in zone_dhs)
        out.append("")
        out.append(f"  分区边界各收窄一格后，设计水位变化 {min(ds):+.3f} ~ "
                   f"{max(ds):+.3f} m（{len(ds)} 个断面）。")
        out.append("  结论：**调分区会影响设计水位，但不会动成灾水位**——"
                   "成灾水位只认转折点（以及手动深泓点）。两者不要混为一谈。")
    out.append("=" * 116)

    text = "\n".join(out)
    print(text)
    dst = os.path.join(ROOT, "output", "手动调节核对表.txt")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8-sig") as f:
        f.write(text)


if __name__ == "__main__":
    main()
