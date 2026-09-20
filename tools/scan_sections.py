"""扫描 xlsx 中的断面切块情况：列出每个「断面编号」行的行号与名称，
并对比 MATLAB 切片规则(iidx(j)+2 : iidx(j+1)-2)与实际数据行范围。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from peek_xlsx import load_shared_strings, sheet_files, read_sheet, col_to_idx  # noqa: E402

import zipfile  # noqa: E402


def scan(path: str, pattern: str = "断面编号") -> None:
    print(f"\n########## {os.path.basename(path)} ##########")
    with zipfile.ZipFile(path) as z:
        shared = load_shared_strings(z)
        for name, target in sheet_files(z):
            rows = read_sheet(z, target, shared)
            nrows = len(rows)
            heads = []
            for i, r in enumerate(rows, start=1):       # 1-based 行号
                if r and str(r[0]).strip().startswith(pattern):
                    heads.append((i, r[1].strip() if len(r) > 1 else ""))
            print(f"sheet『{name}』总行数={nrows}，断面数={len(heads)}")

            bounds = [h[0] for h in heads] + [nrows + 2]
            for j, (hrow, sname) in enumerate(heads):
                ml_a = bounds[j] + 2              # MATLAB 1-based 起始行
                ml_b = bounds[j + 1] - 2          # MATLAB 1-based 结束行(含)
                actual_a = hrow + 2               # 表头行之后第一行数据
                actual_b = (heads[j + 1][0] - 1) if j + 1 < len(heads) else nrows
                n_ml = max(0, ml_b - ml_a + 1)
                n_ac = max(0, actual_b - actual_a + 1)
                flag = "" if n_ml == n_ac else f"  <== 差异 {n_ac - n_ml} 行"
                print(f"  [{j+1:2d}] {sname:<12s} 编号行={hrow:4d}  "
                      f"MATLAB行={ml_a}..{ml_b}({n_ml}行)  "
                      f"实际数据行={actual_a}..{actual_b}({n_ac}行){flag}")


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else "data"
    for fn in sorted(os.listdir(folder)):
        if fn.lower().endswith(".xlsx") and not fn.startswith("~$"):
            scan(os.path.join(folder, fn))
