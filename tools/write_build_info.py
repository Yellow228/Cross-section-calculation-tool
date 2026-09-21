"""打包前把当前 git 提交与时间戳写进 `src/core/_build_info.py`。

由 `build_exe.spec` 在打包时自动调用，也可手工执行：

    .venv\\Scripts\\python.exe tools\\write_build_info.py

生成的文件被 .gitignore 排除——它每次构建都变，不该进仓库；
源码运行且没有该文件时，`core.version` 会退而实时查 git。
"""

from __future__ import annotations

import datetime
import os
import subprocess

TEMPLATE = '''"""构建信息（自动生成，勿手工编辑；被 .gitignore 排除）。

由 tools/write_build_info.py 在打包时生成，供 core.version 读取。
打包后的 exe 里没有 git，只能靠这个烘焙进来的文件知道自己是哪一版。
"""

GIT_HASH = "{hash}"
BUILD_TIME = "{time}"
'''


def git_hash(root: str) -> str:
    """取当前提交的短哈希；没有 git 或不在仓库里则返回空串。"""
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=root, capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def write_build_info(root: str | None = None) -> tuple[str, str]:
    """生成 src/core/_build_info.py，返回 (哈希, 构建时间)。"""
    if root is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    h = git_hash(root) or "未知"
    t = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    dst = os.path.join(root, "src", "core", "_build_info.py")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.format(hash=h, time=t))
    return (h, t)


if __name__ == "__main__":
    _h, _t = write_build_info()
    print(f"已生成 src/core/_build_info.py：GIT_HASH={_h}  BUILD_TIME={_t}")
