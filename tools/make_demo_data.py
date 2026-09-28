"""生成一份**可公开分发**的示范断面数据（`samples/demo_data/*.xlsx`）。

为什么要它
----------
真实测量数据不随仓库分发（见 `.gitignore` 与 README「目录结构」），
于是新 clone 出来的仓库**没有数据**，`--selftest` / `gui_smoke.py` 都跑不起来——
等于"别人无法验证这个程序是好的"。本脚本生成一份合成数据补上这个缺口。

**它是合成数据**：河道走向、断面形状、高程都是按规则算出来的，
不含任何真实测量值，可以放心入库。

产出的数据必须满足的隐含条件（都是踩过才知道的）
------------------------------------------------
1. 以第 1 列等于「断面编号」的行切块；列序 `X坐标 / Y坐标 / 起点距 / 高程`
   （前两列是**北坐标在前**，符合中国测量惯例；读取时按"输入第 1 列=北坐标"映射成内部坐标 x=北、y=东）
2. 每个文件要有「纵断面」块——默认 `group_rule="spatial"` 靠它与横断面的
   **平面相交关系**分组，容差 1.0 m。横断面若不真的穿过它，就归不进任何组
3. 起点距从 0 起、严格递增
4. 横断面在平面上必须是**直线**，且 `起点距` 等于沿该直线到首点的距离
   （这样"改起点距同步重算 x/y"才成立，见 CLAUDE.md 坑 #15）
5. `tools/gui_smoke.py` 动态挑"试验断面"，要求存在一个断面满足
   `2 <= 自动深泓点索引 <= n-3` 且深泓点右侧下一点高程抬升
6. 至少要 2 条纵断面线（批量填写用例要"换到第 2 组"）

前 4 条由格式与生成方式保证；第 5 条由 `SHAPE`（见下）保证——那条形状
经实测能让左右两侧都扫出转折点（分区数 3）。

用法
----
    .venv\\Scripts\\python.exe tools\\make_demo_data.py          # 生成到 samples/demo_data
    .venv\\Scripts\\python.exe tools\\make_demo_data.py --outdir 别的目录

生成后可自检：
    .venv\\Scripts\\python.exe src\\app\\main.py --selftest samples\\demo_data
"""

from __future__ import annotations

import argparse
import math
import os

from openpyxl import Workbook

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "samples", "demo_data")

#: 断面形状：`(起点距, 相对河底的高度)`。深泓点在第 4 个点（0-based）。
#:
#: ⚠ 形状是按转折点算法**反推**出来的，别随手改：
#:   从深泓点向外扫，先要有一段陡坡（slope > 0.4，这里 2.0m/4m = 0.50），
#:   之后要有一段平缓（slope < 0.05，这里 0.05m/8m ≈ 0.006）——
#:   满足这两条才扫得出转折点，也才有 3 个分区。
#:   把中间那段陡坡拉平或把外侧那段抬陡，分区就会退化成 2 区或 1 区。
SHAPE: list[tuple[float, float]] = [
    (0.0, 4.00),
    (14.0, 3.95),
    (22.0, 3.90),
    (26.0, 2.00),          # 陡坡下沿（相对河底 2.0 m，跨 4 m -> slope 0.50）
    (30.0, 0.00),          # 深泓点
    (34.0, 2.00),
    (38.0, 3.90),
    (46.0, 3.95),
    (60.0, 4.00),
]

#: 每条纵断面线的定义：名字前缀 / 所属文件 / 起点平面坐标 / 走向(度，正北顺时针) /
#: 长度(m) / 组成员的起点桩号(m)
#:
#: 三条线**刻意隔开几百米**：分组是按平面相交做的，挨太近会互相串组。
LINES: list[dict] = [
    {"prefix": "dm1", "file": "demo_river_a.xlsx",
     "origin": (577000.0, 3325300.0), "bearing": 135.0, "length": 400.0,
     "chainages": [20.0, 140.0, 260.0, 360.0]},
    {"prefix": "dm2", "file": "demo_river_a.xlsx",
     "origin": (577000.0, 3323800.0), "bearing": 150.0, "length": 300.0,
     "chainages": [60.0, 200.0]},
    {"prefix": "dm3", "file": "demo_river_b.xlsx",
     "origin": (578600.0, 3324500.0), "bearing": 120.0, "length": 260.0,
     "chainages": [50.0, 180.0]},
]

PROFILE_STEP = 20.0          # 纵断面实测点的间距(m)
COL_HEADER = ["X坐标", "Y坐标", "起点距", "高程"]


def _unit(bearing_deg: float) -> tuple[float, float]:
    """把「正北顺时针」的方位角化成 (d_east, d_north)。"""
    r = math.radians(bearing_deg)
    return math.sin(r), math.cos(r)


class _Path:
    """一条河道中心线：能按桩号取位置与切向，并输出纵断面实测点。"""

    def __init__(self, origin, bearing, length):
        self.origin = origin
        self.bearing = bearing
        self.length = length
        # 让中心线带一点弯，避免完全是直线（更接近真实，也顺带压一压假设）
        self.bend = 0.00022

    def point(self, c: float) -> tuple[float, float]:
        """桩号 c 处的平面坐标（东, 北）。"""
        e0, n0 = self.origin
        de, dn = _unit(self.bearing)
        # 一个很小的横向偏移，使中心线呈轻微弧形
        off = self.bend * c * c / max(self.length, 1.0) * 40.0
        pe, pn = -dn, de                        # 左法向
        return (e0 + de * c + pe * off, n0 + dn * c + pn * off)

    def tangent(self, c: float) -> tuple[float, float]:
        """桩号 c 处的单位切向（沿河方向）。"""
        d = 2.0
        a = self.point(max(0.0, c - d))
        b = self.point(min(self.length, c + d))
        ve, vn = b[0] - a[0], b[1] - a[1]
        L = math.hypot(ve, vn) or 1.0
        return ve / L, vn / L

    def bed_z(self, c: float) -> float:
        """河底高程：沿程均匀下降（下游更低）。"""
        return 92.30 - 3.60 * (c / max(self.length, 1.0))

    def profile_rows(self) -> list[list]:
        """纵断面实测点。起点距 = 沿河里程，高程 = 河底高程。"""
        rows = []
        c = 0.0
        while c <= self.length + 1e-9:
            e, n = self.point(c)
            rows.append([round(n, 3), round(e, 3), round(c, 3),
                         round(self.bed_z(c), 3)])
            c += PROFILE_STEP
        return rows


def _section_rows(path: _Path, chainage: float, scale: float) -> list[list]:
    """一个横断面的数据行。

    `scale` 同比例放大河深——形状不变，所以转折点与分区数不变。
    """
    e0, n0 = path.point(chainage)
    de, dn = path.tangent(chainage)
    # 横断面垂直于河流走向；取左法向为 s 的增大方向
    pe, pn = -dn, de
    mid_s = SHAPE[len(SHAPE) // 2][0]           # 形状中点（深泓点）的起点距
    bed = path.bed_z(chainage) - 0.15           # 断面河底比纵断面河底略低一点
    rows = []
    for s, dz in SHAPE:
        d = s - mid_s                            # 相对中心点的偏移
        e = e0 + pe * d
        n = n0 + pn * d
        rows.append([round(n, 3), round(e, 3), round(s, 3),
                     round(bed + dz * scale, 3)])
    return rows


def _write_file(path: str, lines: list[dict]) -> None:
    """把若干条纵断面线写进一个 xlsx。

    块顺序模仿真实文件：**每个纵断面块在它自己的横断面之前**。
    块与块之间留一个空行（真实文件就是这样，解析器会跳过非数值行）。
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "断面数据"

    for li, spec in enumerate(lines):
        path_obj = _Path(spec["origin"], spec["bearing"], spec["length"])

        # —— 纵断面块 ——
        prof_name = spec.get("profile_name") or f"纵断面{li + 1}"
        ws.append(["断面编号", prof_name])
        ws.append(COL_HEADER)
        for r in path_obj.profile_rows():
            ws.append(r)
        ws.append([])

        # —— 该线的各个横断面 ——
        for k, c in enumerate(spec["chainages"], 1):
            name = f"{spec['prefix']}-{k}"
            ws.append(["断面编号", name])
            ws.append(COL_HEADER)
            # 每 3 个断面换个深浅，增加一点差异（形状不变，分区数不受影响）
            scale = 1.0 + 0.06 * (k % 3)
            for r in _section_rows(path_obj, c, scale):
                ws.append(r)
            ws.append([])

    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)


def _emit_project(outdir: str, project_path: str) -> None:
    """顺带存一份**基于示范数据**的工程文件示例。

    ⚠⚠ 这一份必须重新生成，绝不能沿用旧的 `samples/demo_project.dmprj`：
    它里面装着**真实测量数据的完整坐标数组**（31 个断面的 x/y/s/z，共约 560 个
    33xxxxx 量级的真实北坐标）、5 个真实文件名，以及 `source.dir` 上的本机绝对路径。
    `.dmprj` 是"数据快照"，把 `data/*.xlsx` 移出仓库并不等于数据没进仓库——
    这个文件本身就是那份数据的另一种写法。

    `source.dir` 特意写成**相对路径**，避免把本机目录结构带进公开仓库。
    """
    import sys
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from core import project_io                       # noqa: PLC0415
    from core.config import Config                    # noqa: PLC0415
    from core.reader import load_folder               # noqa: PLC0415

    cfg = Config()
    project, warns = load_folder(outdir, cfg)
    rel = os.path.relpath(outdir, ROOT).replace(os.sep, "/")
    project_io.save_project(project_path, project, cfg, source={
        "dir": rel,
        "files": sorted(n for n in os.listdir(outdir) if n.endswith(".xlsx")),
    })
    n = len(project.all_sections())
    print(f"  {os.path.relpath(project_path, ROOT)}"
          f"（{len(project.profile_lines)} 条线 / {n} 个断面，来源记为 {rel}）")
    for w in warns:
        print(f"      （告警）{w}")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成可公开分发的示范断面数据")
    ap.add_argument("--outdir", default=DEFAULT_OUT)
    ap.add_argument("--project", default=None,
                    help="顺带生成工程文件示例（默认 samples/demo_project.dmprj）")
    a = ap.parse_args()

    by_file: dict[str, list[dict]] = {}
    for spec in LINES:
        by_file.setdefault(spec["file"], []).append(spec)

    total_sec = 0
    for fname, specs in sorted(by_file.items()):
        out = os.path.join(a.outdir, fname)
        _write_file(out, specs)
        n = sum(len(s["chainages"]) for s in specs)
        total_sec += n
        print(f"  {fname}：{len(specs)} 条纵断面线 / {n} 个横断面")

    project_path = a.project or os.path.join(
        os.path.dirname(a.outdir.rstrip(os.sep)), "demo_project.dmprj")
    _emit_project(a.outdir, project_path)

    print()
    print(f"共 {len(LINES)} 条纵断面线 / {total_sec} 个横断面 -> {a.outdir}")
    print()
    print("接着可以这样验：")
    print(f"  .venv\\Scripts\\python.exe src\\app\\main.py --selftest {a.outdir}")


if __name__ == "__main__":
    main()
