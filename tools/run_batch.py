"""命令行批处理：载入 data/ 下的全部 xlsx -> 计算 -> 导出。

不启动界面，可直接被自动化脚本调用；也是 core 层的端到端验证入口。

用法：
    .venv\\Scripts\\python.exe tools\\run_batch.py
    .venv\\Scripts\\python.exe tools\\run_batch.py --roughness 0.035 --slope 0.006 --design-q 80
    .venv\\Scripts\\python.exe tools\\run_batch.py --params 参数集.xlsx
    .venv\\Scripts\\python.exe tools\\run_batch.py --slope-from-profile endpoints
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from core import slope as S                  # noqa: E402
from core.config import Config              # noqa: E402
from core.exporter import export_all        # noqa: E402
from core.params import apply_batch, missing_params  # noqa: E402
from core.reader import load_folder         # noqa: E402
from core.solver import solve_section       # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="断面水位-流量关系批量计算")
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output"))
    ap.add_argument("--params", default=None, help="参数集 xlsx（可选）")
    ap.add_argument("--roughness", type=float, default=0.03)
    ap.add_argument("--slope", type=float, default=0.005)
    ap.add_argument("--design-q", type=float, default=50.0)
    ap.add_argument("--single", action="store_true", help="单断面模式（默认复式）")
    ap.add_argument("--slope-from-profile", default="keep",
                    choices=["keep"] + list(S.SLOPE_MODES),
                    help="比降来源（Q13）：keep=用 --slope 或参数集的值；"
                         "jc/endpoints/lsq=按纵断面实测数据推算，覆盖上面的默认值。"
                         "jc=约翰斯通-克罗斯法（教材标准，默认口径）")
    ap.add_argument("--compat-matlab", action="store_true",
                    help="打开兼容开关以复现 MATLAB 旧行为（丢掉每个断面的末测点）")
    ap.add_argument("--origin", default="lowest", choices=["start", "end", "lowest"])
    a = ap.parse_args()

    cfg = Config(compound_mode=not a.single, chainage_origin=a.origin)
    if a.compat_matlab:
        cfg.compat_drop_last_point = True

    lines_out: list[str] = []
    project, warnings = load_folder(a.data, cfg, param_path=a.params)

    lines_out.append(f"数据目录: {a.data}")
    lines_out.append(f"纵断面线: {len(project.profile_lines)} 条")
    for ln in project.profile_lines:
        lines_out.append(f"  - {ln.name}: {len(ln.sections)} 个横断面")

    # 未填参数的断面用命令行默认值补齐（Q12 手工填写的批处理等价物）
    all_secs = project.all_sections()
    n_fill = apply_batch(all_secs, only_missing=True,
                         roughness=a.roughness, slope=a.slope, design_q=a.design_q)
    if n_fill:
        lines_out.append(f"\n已用默认值补齐 {n_fill} 个断面的参数 "
                         f"(糙率={a.roughness}, 比降={a.slope}, Qs={a.design_q})")
    still = missing_params(all_secs)
    if still:
        lines_out.append(f"!! 仍缺参数: {still}")

    # 按纵断面推算比降（Q13）。放在默认值补齐**之后**：
    # 这样推算失败的断面（无纵断面 / 倒坡）仍保有默认值兜底，不会留空。
    if a.slope_from_profile != "keep":
        props, ests = S.propose_slopes(project, mode=a.slope_from_profile)
        n_set = S.apply_slopes(project, props)
        lines_out.append(
            f"\n已按纵断面推算比降（口径={a.slope_from_profile}）写入 {n_set} 个断面：")
        for est in ests:
            if est.ok:
                lines_out.append(
                    f"  {est.line:<12} 河段 {est.start:7.2f} ~ {est.end:7.2f} m"
                    f"  长 {est.length:7.1f} m  落差 {est.drop:7.3f} m"
                    f"  → 比降 {est.slope(a.slope_from_profile):.5f}")
            else:
                lines_out.append(f"  {est.line:<12} !! {est.message}")
        bad = [p.section for p in props if p.bad]
        if bad:
            lines_out.append(
                f"  !! 跳过 {len(bad)} 个无效建议值（倒坡/数据不足），"
                f"这些断面保留默认值 {a.slope}: {bad}")

    results: dict[str, object] = {}
    infos: dict[str, object] = {}
    lines_out.append("\n===== 计算结果 =====")
    for ln in project.profile_lines:
        lines_out.append(f"\n【{ln.name}】")
        for sec, ch in zip(ln.sections, ln.chainage or [float('nan')] * len(ln.sections)):
            res, info = solve_section(sec, cfg)
            results[sec.name] = res
            infos[sec.name] = info
            chs = "n/a" if ch != ch else f"{ch:7.1f}"
            lines_out.append(
                f"  {sec.name:<9} 桩号={chs}  深泓={info.dmin:8.2f}  "
                f"分区={len(info.zones)}  曲线点={len(res.hvec):3d}  "
                f"成灾水位={info.disaster_level:8.2f}  "
                f"Hs={res.design_level:8.2f}  Hs1={res.design_level_plus:8.2f}  "
                f"左点={res.left_status} 右点={res.right_status}")
            for w in res.warnings:
                lines_out.append(f"      ! {w}")

    if warnings:
        lines_out.append("\n===== 告警 =====")
        for w in warnings:
            lines_out.append(f"  - {w}")

    written = export_all(project, results, infos, cfg, a.out)
    lines_out.append("\n===== 输出文件 =====")
    for p in written:
        lines_out.append(f"  {p}")

    txt = "\n".join(lines_out)
    print(txt)
    # 与其他核对报告统一放在 output/ 下，不往项目根目录丢文件
    os.makedirs(os.path.join(ROOT, "output"), exist_ok=True)
    with open(os.path.join(ROOT, "output", "批处理报告.txt"),
              "w", encoding="utf-8") as f:
        f.write(txt)


if __name__ == "__main__":
    main()
