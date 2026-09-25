"""生成 wheel 下载清单（文件名 + URL），结果写入 `wheels_urls.txt`。

不做控制台打印，避免 Windows 控制台编码问题；出错信息写 `_wheelgen.log`。

**只产出 `wheels_urls.txt`**（`tools/fetch_wheels.py` 直接读它）。
原先还会产出 `wheels_report.txt`——那只是给人看的明细，包名/版本在
`requirements.txt` 里、文件名在 URL 末尾，信息完全重复，且它里面会写进
本机绝对路径。已删，别再把它加回来。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 包名 -> 版本（None 表示取最新版）
PKGS = {
    "numpy": "2.5.3",
    "matplotlib": "3.11.2",
    "contourpy": "1.4.0",
    "cycler": "0.12.1",
    "fonttools": "4.65.0",
    "kiwisolver": "1.5.1",
    "pillow": "12.3.0",
    "packaging": "26.3",
    "pyparsing": "3.3.2",
    "python-dateutil": "2.9.0.post0",
    "six": None,
    "openpyxl": "3.1.5",
    "et-xmlfile": "2.0.0",
    # 只装 Essentials（QtCore/Gui/Widgets 等，足够跑界面 + matplotlib 嵌入）。
    # PySide6 是空壳元包；PySide6-Addons 含 QtWebEngine/Qt3D/QtCharts 等，
    # 本工具一个都用不到，却要 160 MB，因此不列。
    "PySide6-Essentials": "6.11.2",
    "shiboken6": "6.11.2",
    "PyInstaller": "6.22.3",
    "altgraph": "0.17.5",
    "pefile": "2024.8.26",
    "pywin32-ctypes": "0.2.3",
    "pyinstaller-hooks-contrib": "2026.7",
    "setuptools": "84.0.0",
}


def pick(files: list[dict]) -> dict | None:
    whls = [f for f in files if f["filename"].endswith(".whl")]
    win = [f for f in whls if "win_amd64" in f["filename"]]
    anypy = [f for f in whls if "none-any" in f["filename"]]
    for pool in (win, anypy):
        for f in pool:
            if "-cp313-" in f["filename"]:
                return f
    for f in win:
        if re.search(r"-cp\d+-abi3-", f["filename"]):
            return f
    # 平台专用但无 ABI 标签的（如 pyinstaller-...-py3-none-win_amd64.whl）
    for f in win:
        if "-none-" in f["filename"]:
            return f
    return win[0] if win else (anypy[0] if anypy else None)


def main() -> None:
    rows = []
    for pkg, ver in PKGS.items():
        url = f"https://pypi.org/pypi/{pkg}/json" if ver is None \
            else f"https://pypi.org/pypi/{pkg}/{ver}/json"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                d = json.load(r)
        except Exception as e:
            rows.append((pkg, "ERROR", str(e), ""))
            continue
        v = d["info"]["version"]
        # /pypi/<pkg>/json 返回 releases；/pypi/<pkg>/<ver>/json 返回 urls
        files = d.get("releases", {}).get(v) or d.get("urls") or []
        f = pick(files)
        if f is None:
            rows.append((pkg, v, "(无适用 wheel)", ""))
            continue
        rows.append((pkg, v, f["filename"], f["url"]))

    urls = [u for _pkg, _v, _fn, u in rows if u]

    # ⚠ 不完整的清单**绝不写出**：PyPI 抽风时若照常覆盖，
    #   仓库里那份好清单会变成残缺版，下次 fetch_wheels.py 就静默漏装包。
    #   宁可报错、保持原文件不动（错误会进 _wheelgen.log）。
    if len(urls) != len(PKGS):
        missing = "、".join(p for p, _v, _f, u in rows if not u)
        raise RuntimeError(
            f"只拿到 {len(urls)}/{len(PKGS)} 个 wheel 的 URL，"
            f"未写出清单（原 wheels_urls.txt 保持不动）。缺的包：{missing}"
        )

    with open(os.path.join(ROOT, "wheels_urls.txt"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(urls) + "\n")


if __name__ == "__main__":
    import traceback
    try:
        main()
        with open(os.path.join(ROOT, "_wheelgen.log"), "w", encoding="utf-8") as fp:
            fp.write("OK\n")
    except Exception:
        with open(os.path.join(ROOT, "_wheelgen.log"), "w", encoding="utf-8") as fp:
            fp.write(traceback.format_exc())
