"""用标准库（zipfile + xml）窥探 xlsx 结构，无需 openpyxl。

用法：python tools/peek_xlsx.py <文件路径> [最大行数]
"""

from __future__ import annotations

import sys
import zipfile
import xml.etree.ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def col_to_idx(ref: str) -> int:
    """'C7' -> 2 (0-based 列号)"""
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return n - 1


def load_shared_strings(z: zipfile.ZipFile) -> list[str]:
    try:
        data = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    out = []
    for si in root.findall(f"{NS}si"):
        out.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
    return out


def sheet_files(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid2target = {}
    for r in rels.findall(f"{REL}Relationship"):
        rid2target[r.get("Id")] = r.get("Target")
    out = []
    for sh in wb.find(f"{NS}sheets").findall(f"{NS}sheet"):
        rid = sh.get(f"{RNS}id")
        tgt = rid2target.get(rid, "")
        if not tgt.startswith("xl/"):
            tgt = "xl/" + tgt.lstrip("/")
        out.append((sh.get("name"), tgt))
    return out


def read_sheet(z: zipfile.ZipFile, path: str, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(z.read(path))
    rows: list[list[str]] = []
    for row in root.iter(f"{NS}row"):
        cells: list[str] = []
        for c in row.findall(f"{NS}c"):
            ref = c.get("r") or ""
            ci = col_to_idx(ref) if ref else len(cells)
            while len(cells) <= ci:
                cells.append("")
            t = c.get("t")
            v = c.find(f"{NS}v")
            if t == "s":
                val = shared[int(v.text)] if v is not None and v.text else ""
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{NS}t"))
            else:
                val = v.text if v is not None else ""
            cells[ci] = val or ""
        rows.append(cells)
    return rows


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python tools/peek_xlsx.py <文件> [最大行数]")
        sys.exit(1)
    path = sys.argv[1]
    max_rows = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    start_row = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    end_row = int(sys.argv[4]) if len(sys.argv) > 4 else None

    with zipfile.ZipFile(path) as z:
        shared = load_shared_strings(z)
        sheets = sheet_files(z)
        print(f"文件: {path}")
        print(f"工作表: {[n for n, _ in sheets]}")
        for name, target in sheets:
            rows = read_sheet(z, target, shared)
            print(f"\n===== sheet『{name}』共 {len(rows)} 行 =====")
            lo = start_row - 1
            hi = (end_row if end_row else len(rows))
            sel = list(enumerate(rows[lo:hi], start=start_row))
            shown = sel[:max_rows]
            for i, r in shown:
                cells = [c if c != "" else "·" for c in r]
                print(f"{i:4d} | " + " | ".join(cells))
            if len(sel) > len(shown):
                print(f"     ... 省略 {len(sel) - len(shown)} 行")


if __name__ == "__main__":
    main()
