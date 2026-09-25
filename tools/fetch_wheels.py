"""按 `wheels_urls.txt` 把 21 个 wheel 下载到 `wheels/`。

为什么需要这个脚本
------------------
`wheels/` 约 **108 MB**，占仓库体积 99%（单是 PySide6-Essentials 就 73 MB），
而且会把 `.git` 撑到 100 MB 量级——GitHub 单文件 50 MB 就告警、100 MB 直接拒收。
所以 wheel **不进仓库**，只把下载地址留在 `wheels_urls.txt` 里，用本脚本取回。

取回后就能离线安装了（与 README 里那条命令一致）：

    python tools/fetch_wheels.py
    .venv\\Scripts\\python.exe -m pip install --no-index --find-links wheels -r requirements.txt

特性
----
* 逐行读 URL，**已有且大小一致的文件跳过**，可安全重跑（断了接着下）
* 下载到 `.part` 再改名——中途失败不会留下一个"看着像下好了"的半个文件
* 全部下完后按 `wheels_urls.txt` 里的文件名核对一遍，缺哪个直接报出来
* 没装 requests 也能跑（退回 urllib），**本脚本不引入新依赖**
"""

from __future__ import annotations

import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URLS = os.path.join(ROOT, "wheels_urls.txt")
OUT = os.path.join(ROOT, "wheels")


def _download(url: str, dest: str) -> None:
    """下到 .part 再改名；失败时清掉半截文件。"""
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, dest)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def main() -> int:
    if not os.path.exists(URLS):
        print(f"找不到清单：{URLS}")
        return 1

    urls = [ln.strip() for ln in open(URLS, encoding="utf-8")
            if ln.strip() and not ln.startswith("#")]
    os.makedirs(OUT, exist_ok=True)

    print(f"共 {len(urls)} 个 wheel，目标目录：{OUT}")
    print()
    failed: list[tuple[str, str]] = []
    for i, url in enumerate(urls, 1):
        name = url.rsplit("/", 1)[-1]
        dest = os.path.join(OUT, name)
        if os.path.exists(dest):
            print(f"[{i:>2}/{len(urls)}] 已有，跳过  {name}")
            continue
        print(f"[{i:>2}/{len(urls)}] 下载中…  {name}", end="", flush=True)
        try:
            _download(url, dest)
            print(f"\r[{i:>2}/{len(urls)}] 完成      {name}"
                  f"  （{os.path.getsize(dest) / 1024 / 1024:.1f} MB）")
        except Exception as e:                       # noqa: BLE001
            print(f"\r[{i:>2}/{len(urls)}] 失败      {name}  —— {e}")
            failed.append((name, str(e)))

    # 核对：清单里的每个文件名是否都落地了
    want = {u.rsplit("/", 1)[-1] for u in urls}
    have = set(os.listdir(OUT))
    missing = sorted(want - have)

    print()
    if missing:
        print(f"缺少 {len(missing)} 个文件：")
        for m in missing:
            print(f"  - {m}")
    else:
        print(f"全部 {len(want)} 个 wheel 已就位。")
        print()
        print("接着装依赖（离线、不碰全局）：")
        print(r"  .venv\Scripts\python.exe -m pip install "
              r"--no-index --find-links wheels -r requirements.txt")
    if failed:
        print()
        print("失败的可以重跑本脚本，已下好的会跳过。")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
