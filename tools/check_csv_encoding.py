"""核验导出 CSV 的编码是否会被 Excel 正确识别。

背景
----
中文 Windows 的 Excel / WPS 打开 CSV 时的真实规则：
    有 UTF-8 BOM (EF BB BF) -> 按 UTF-8 解码，中文正常
    无 BOM                  -> 按系统 ANSI(GBK) 解码
                               UTF-8 文件会被误码，中文全变乱码
                               （"断面" -> "鏂潰"）

本工具按这套规则逐文件判定，并打印 Excel 实际会看到的表头首行。
只读，不修改任何文件。

用法：
    python tools/check_csv_encoding.py [输出目录]
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOM = b"\xef\xbb\xbf"

# 这些词只要有一个能正常读出来，就说明中文没丢
KEYWORDS = ("名称", "断面", "水位", "平面坐标", "流量", "面积", "湿周", "顶宽")


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def judge(path: str) -> tuple[str, str, str]:
    """返回 (结论, 说明, Excel 会看到的首行)。"""
    with open(path, "rb") as f:
        raw = f.read()

    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return "WARN", "UTF-16（Excel 一般也能识别，但非本项目默认）", ""

    has_bom = raw.startswith(BOM)
    body = raw[len(BOM):] if has_bom else raw

    def dec(enc):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            return None

    utf8 = dec("utf-8")
    gbk = dec("gbk")

    # Excel 实际会走哪条路：有 BOM 走 UTF-8，无 BOM 走系统 ANSI(GBK)
    # 预览用宽松解码：严格解码失败时也能看出它在 Excel 里长什么样
    display = (utf8 if has_bom else body.decode("gbk", errors="replace")) or ""
    first_line = display.splitlines()
    first = first_line[0] if first_line else ""

    if has_bom:
        if utf8 is None:
            return "FAIL", "声称有 BOM 但无法按 UTF-8 解码，文件损坏", first
        if any(k in utf8 for k in KEYWORDS) or not _has_cjk(utf8):
            return "OK", "含 BOM -> Excel 按 UTF-8 解码，中文正常", first
        return "WARN", "含 BOM 但未识别出预期中文表头", first

    # ---- 无 BOM ----
    if not _has_cjk(body.decode("utf-8", errors="replace")):
        return "OK", "无 BOM，但内容为纯 ASCII，无乱码风险", first
    if gbk is not None and any(k in gbk for k in KEYWORDS):
        return "OK", "无 BOM 且本身就是 GBK 编码，Excel 能正确解码", first
    if utf8 is not None and any(k in utf8 for k in KEYWORDS):
        return "FAIL", ("无 BOM 的 UTF-8 -> Excel 会按 GBK 误码，中文乱码。"
                        "请把 csv_encoding 设为 utf-8-sig"), first
    return "FAIL", "无 BOM 且编码无法被 Excel 正确识别", first


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "output")
    targets: list[str] = []
    for dp, _dn, fns in os.walk(out_dir):
        # 跳过预览图目录。注意要按**相对路径的分量**判断，
        # 不能用子串匹配——否则 out_dir 本身叫 _selftest 时，其下所有子目录
        # （如 _selftest\SJC4）都会因路径含 "_selftest" 而被误跳过。
        rel = os.path.relpath(dp, out_dir)
        if rel != "." and any(p in ("_preview", "_selftest")
                              for p in rel.split(os.sep)):
            continue
        for fn in sorted(fns):
            if fn.lower().endswith(".csv"):
                targets.append(os.path.join(dp, fn))

    lines = [f"扫描目录：{out_dir}", f"共 {len(targets)} 个 CSV", "-" * 78]
    n_ok = n_fail = n_warn = 0
    for p in targets:
        verdict, why, first = judge(p)
        lines.append(f"[{verdict:4s}] {os.path.relpath(p, out_dir)}")
        lines.append(f"       {why}")
        lines.append(f"       Excel 首行: {first}")
        if verdict == "OK":
            n_ok += 1
        elif verdict == "FAIL":
            n_fail += 1
        else:
            n_warn += 1

    lines.append("-" * 78)
    lines.append(f"通过 {n_ok} / 失败 {n_fail} / 警告 {n_warn} / 总计 {len(targets)}")
    text = "\n".join(lines)

    with open(os.path.join(ROOT, "_csv_enc_check.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
