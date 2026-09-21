"""程序版本与构建信息。

版本号
------
`VERSION` 手工维护，遵循语义化版本（主版本.次版本.修订号）：

    修 bug / 微调          -> 修订号 +1（1.0.0 -> 1.0.1）
    新增功能（向后兼容）    -> 次版本 +1（1.0.0 -> 1.1.0）
    不兼容的改动           -> 主版本 +1（1.0.0 -> 2.0.0）

构建信息
--------
git 提交短哈希与构建时间，由打包流程烘焙进 `src/core/_build_info.py`。

**为什么必须烘焙**：打包后的 exe 里既没有 git、也没有 `.git` 目录，
运行时无从知道自己是哪一次提交的产物——这正是"拿到一个 exe 看不出它是
哪一版"的根因。所以打包前由 `tools/write_build_info.py`（被 `build_exe.spec`
调用）把当前提交与时间戳写成一个小模块，运行期直接读。

源码运行时该文件可能不存在（被 .gitignore 排除），此时退而实时调 `git` 查；
再不行就显示"未知"。**版本号本身始终可用**，它写在本文件里、不依赖任何外部条件。
"""

from __future__ import annotations

import os
import subprocess
import sys

APP_NAME = "河道断面水位–流量关系计算工具"
APP_NAME_SHORT = "断面计算工具"

VERSION = "1.0.0"

UNKNOWN = "未知"


def _root() -> str:
    """仓库根目录（本文件位于 src/core/ 下）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _baked() -> tuple[str, str]:
    """打包时烘焙进来的构建信息；不存在则返回空串。"""
    try:
        from . import _build_info as b                      # type: ignore
        return (str(getattr(b, "GIT_HASH", "") or ""),
                str(getattr(b, "BUILD_TIME", "") or ""))
    except Exception:
        return ("", "")


def _live_git() -> str:
    """源码运行时实时查 git；打包后或没装 git 时返回空串。"""
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=_root(), capture_output=True,
                           text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def git_hash() -> str:
    """当前构建对应的 git 提交短哈希；查不到返回"未知"。"""
    baked, _ = _baked()
    return baked or _live_git() or UNKNOWN


def build_time() -> str:
    """构建时间；源码运行（无烘焙文件）时返回"未知"。"""
    _, baked = _baked()
    return baked or UNKNOWN


def is_frozen() -> bool:
    """是否以打包程序（exe）方式运行。"""
    return bool(getattr(sys, "frozen", False))


def runtime() -> str:
    return "打包程序（exe）" if is_frozen() else "源码运行"


def title_suffix() -> str:
    """标题栏追加的短串，例：` v1.0.0`。"""
    return f" v{VERSION}"


def full() -> str:
    """「关于」显示的完整信息，可直接整段复制。"""
    return (f"{APP_NAME}\n"
            f"版本：{VERSION}\n"
            f"构建：{git_hash()}\n"
            f"构建时间：{build_time()}\n"
            f"运行方式：{runtime()}")
