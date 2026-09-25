"""校验图标：既看 .ico 文件里有哪些尺寸，也看 exe 里实际嵌入了几个。

为什么需要这个脚本：
    生成 .ico 时如果误用某个小尺寸帧当保存源，Pillow 会静默地只写出那一档尺寸
    （曾经只剩 16×16、656 字节），PyInstaller 也跟着只嵌一档，
    而资源管理器里照样能显示图标，肉眼根本看不出问题。

⚠ 还验一件事：**exe 里嵌的是不是"当前这张"图**，而不只是"档数对不对"。
   换图标后新旧 .ico 的档数通常一样（都是 7 档），只看 `RT_ICON` 项数会报
   "正常"，实际嵌的却是旧图 —— 这是一次真实踩到的假绿灯。
"""

from __future__ import annotations

import os
import struct

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICO = os.path.join(ROOT, "src", "app", "assets", "icon.ico")
EXE = os.path.join(ROOT, "dist", "断面计算工具", "断面计算工具.exe")
WANT = {16, 24, 32, 48, 64, 128, 256}


def check_ico(out: list[str]) -> None:
    out.append("===== .ico 文件 =====")
    out.append(f"路径: {ICO}")
    if not os.path.exists(ICO):
        out.append("!! 不存在")
        return
    out.append(f"大小: {os.path.getsize(ICO)} 字节")
    im = Image.open(ICO)
    sizes = set(im.ico.sizes()) if getattr(im, "ico", None) else {im.size}
    got = {w for w, _h in sizes}
    out.append(f"内含尺寸: {sorted(sizes)}")
    missing = sorted(WANT - got)
    out.append("缺少尺寸: " + (str(missing) if missing else "无"))


def check_exe(out: list[str]) -> None:
    out.append("")
    out.append("===== exe 嵌入的图标资源 =====")
    out.append(f"路径: {EXE}")
    if not os.path.exists(EXE):
        out.append("（未打包，跳过）")
        return
    try:
        import pefile
    except ImportError:
        out.append("（需要 pefile，跳过）")
        return

    pe = pefile.PE(EXE, fast_load=True)
    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])

    n_icon = n_group = 0
    group_counts = []
    if hasattr(pe, "DIRECTORY_ENTRY_RESOURCE"):
        for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            name = pefile.RESOURCE_TYPE.get(entry.struct.Id, str(entry.struct.Id))
            if name == "RT_ICON":
                n_icon = len(entry.directory.entries)
            elif name == "RT_GROUP_ICON":
                n_group = len(entry.directory.entries)
                for res in entry.directory.entries:
                    d = res.directory.entries[0].data.struct
                    raw = pe.get_data(d.OffsetToData, d.Size)
                    group_counts.append(int.from_bytes(raw[4:6], "little"))

    out.append(f"RT_ICON 项数: {n_icon}   RT_GROUP_ICON 项数: {n_group}")
    out.append(f"图标组内含尺寸数: {group_counts}")
    if n_icon >= 2:
        out.append("=> exe 已嵌入多尺寸图标，正常")
    elif n_icon == 1:
        out.append("=> 警告：exe 只嵌入了 1 档图标，小尺寸下会糊。"
                   "请检查 .ico 是否生成了全部尺寸后重新打包")
    else:
        out.append("=> 警告：exe 未嵌入图标")


def ico_frames(path: str) -> dict[int, bytes]:
    """读出 .ico 里每一档的**原始负载**（不重新编码）。

    自己解析 ICO 目录而不用 Pillow 取帧：Pillow 取出再写回会重新编码，
    字节就和 exe 里存的对不上，没法做"是不是同一张图"的比对。
    """
    d = open(path, "rb").read()
    _res, _typ, cnt = struct.unpack_from("<HHH", d, 0)
    out: dict[int, bytes] = {}
    for i in range(cnt):
        w, _h, _cc, _rv, _pl, _bc, size, off = struct.unpack_from(
            "<BBBBHHII", d, 6 + i * 16)
        out[w or 256] = d[off:off + size]      # 256 档的宽高字段写 0
    return out


def check_exe_icon_is_current(out: list[str]) -> None:
    """验 exe 里嵌的是不是**当前这张**图标，而不只是"档数对不对"。

    做法：把当前 .ico 每一档的原始负载拿到 exe 二进制里找。
    PyInstaller 是把 .ico 的各帧原样塞进 RT_ICON 的，所以能这样比。

    ⚠ 16 / 24 这类小帧只有几十~几百字节，理论上存在"恰好撞上"的极小概率；
      真正可靠的是 256 那一档（PNG 压缩、几 KB）。所以**以 256 档为准**，
      小帧只作参考。
    """
    out.append("")
    out.append("===== exe 内嵌的是不是当前这张图 =====")
    if not (os.path.exists(EXE) and os.path.exists(ICO)):
        out.append("（.ico 或 exe 不存在，跳过）")
        return
    frames = ico_frames(ICO)
    blob = open(EXE, "rb").read()
    hit = {sz: (payload in blob) for sz, payload in frames.items()}
    line = "　".join(f"{sz}:{'在' if v else '不在'}" for sz, v in sorted(hit.items()))
    out.append(line)

    big = frames.get(256, b"")
    if big and big in blob:
        out.append("=> exe 内嵌的是当前这张图标 ✓")
    elif big:
        out.append("=> ⚠ 警告：exe 里的 256 档**不是**当前这张图。"
                   "换了图标但没重新打包，或打包用了缓存的旧资源。"
                   "请删掉 build/ 与 dist/ 后重新打包")
    else:
        out.append("=> 无法判定（当前 .ico 里没有 256 档）")


def main() -> None:
    out: list[str] = []
    check_ico(out)
    check_exe(out)
    check_exe_icon_is_current(out)
    txt = "\n".join(out)
    print(txt)
    with open(os.path.join(ROOT, "_icon_check.txt"), "w", encoding="utf-8") as f:
        f.write(txt)


if __name__ == "__main__":
    main()
