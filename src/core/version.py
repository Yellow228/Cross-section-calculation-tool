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

VERSION = "1.2.1"

UNKNOWN = "未知"

#: 版本历史：(版本号, 日期, 该版本新增的功能说明)。
#: **最新排在最前**，用户从「帮助 → 关于」往下读就是由新到旧。
#:
#: 三条维护约定
#: ------------
#: 1. **只写用户能感知到的东西**。内部重构、测试补充、注释订正不进这张表——
#:    进来了就是在给用户念 commit log。
#: 2. **表头必须等于 `VERSION`**。 bump 版本号却忘了加条目是最容易发生的疏漏，
#:    由 `tests/test_core.py::TestVersion` 挡住。
#: 3. 日期写**发版那天**，不是写的那天。
CHANGELOG: list[tuple[str, str, list[str]]] = [
    ("1.2.1", "2026-09-22", [
        "修复：鼠标移到靠右侧的测点上时，提示框会把断面图绘图区挤窄（一闪一闪地缩）",
        "修复：主界面切换断面时，断面编辑窗口不跟着切换",
        "断面编辑窗口现在跟随主界面换断面（换行、换纵断面线都会跟）",
        "编辑窗口有未保存改动时，切换断面会先确认一次；选「否」即留在原断面",
    ]),
    ("1.2.0", "2026-09-21", [
        "横断面编辑：表格支持键入两列数值（需先勾选「允许键入 / 粘贴」）",
        "横断面编辑：支持从 Excel 直接 Ctrl+V 粘贴起点距 / 高程整块数据",
        "粘贴为整批校验后一次性写入：有一格不合法就整批取消，断面一个点都不动",
        "横断面编辑：表格默认改为完全只读，避免误敲键盘改坏测点",
    ]),
    ("1.1.0", "2026-09-21", [
        "新增「编辑」菜单：编辑当前横断面的测点（也可双击断面列表）",
        "拖动图上测点改高程，表格与断面图双向联动",
        "支持插入 / 删除测点，配撤销 / 重做 / 还原",
        "改起点距会自动同步平面坐标 x/y，桩号与导出坐标随之更新",
    ]),
    ("1.0.0", "2026-09-20", [
        "程序开始自带版本号：标题栏、帮助 → 关于",
        "「关于」可显示并可复制构建哈希与构建时间，便于定位反馈的版本",
    ]),
    ("1.0.0 之前", "2026-09-20 以前", [
        "载入断面数据、自动分区、逐断面推求水位–流量关系曲线",
        "批量填写参数、按纵断面推算比降（约翰斯通-克罗斯法）",
        "手动指定深泓点 / 分区边界 / 成灾水位（图上拾取）",
        "结果导出为 CSV / Excel，含成灾水位坐标等三类导出",
        "工程文件保存与回放（.dmprj）",
    ]),
]


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


def changelog_text(indent: str = "  ") -> str:
    """版本历史的可读文本。每项一行，缩进后挂在版本号下面。"""
    out: list[str] = []
    for ver, date, items in CHANGELOG:
        # 那一条不是正式版本号（"1.0.0 之前"），前面就不宜加 "v"
        head = f"{ver}（{date}）" if ver.endswith("之前") else f"v{ver}　（{date}）"
        out.append(head)
        for it in items:
            out.append(f"{indent}· {it}")
    return "\n".join(out)


def full() -> str:
    """「关于」显示的完整信息，可直接整段复制。

    版本历史一并带上是刻意的：用户（和未来的自己）手里往往只有一个 exe，
    问"我这版能不能 Ctrl+V 粘贴"时，翻这儿比翻 git log 快。
    """
    return (f"{APP_NAME}\n"
            f"版本：{VERSION}\n"
            f"构建：{git_hash()}\n"
            f"构建时间：{build_time()}\n"
            f"运行方式：{runtime()}\n"
            f"\n版本历史\n{changelog_text()}")
