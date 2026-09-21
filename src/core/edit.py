"""横断面编辑：改测点高程 / 起点距，增删测点，以及起点距与平面坐标的同步。

为什么单独一个模块
------------------
编辑动作看着简单（改两个数），但它牵动的东西不少，而且**错了不会报错**：
手动覆盖值是测点索引，桩号依赖平面坐标，导出坐标依赖平面坐标。
把这些规则集中在一个不依赖界面的模块里，单元测试才能直接验证。

三条硬规则
----------
1. **改起点距必须同步重算 x/y**
   `s`（起点距）在原始数据中恒从 0 起、严格递增，且实测 31 个断面
   的 `x/y` 折线弧长 ÷ 首末弦长 ≈ 1.0000、各点偏离首末连线 ≤ 0.34 m——
   即**横断面在平面上就是一条直线，`s` 是沿这条直线的累积距离**。
   所以可以由 `s` 反推 `x/y`：`P_i = P_0 + (s_i - s_0)·u`，`u` 为断面走向。
   不同步的后果：`chainage`（桩号取深泓点平面坐标）、`solver` 的水位交点
   平面坐标、`exporter` 的成灾水位坐标导出会全部留在旧位置，
   导出的成果与新断面形态对不上——**图上是新的，导出是旧的**。

2. **起点距必须保持严格递增**
   一旦出现相等或倒序，`interp` 与分区判定会遇到 0 长度 / 负长度区间。
   所以拖动时把目标值**夹在相邻两点之间**，而不是拒绝。

3. **手动索引跟着增删漂移，且不能静默**
   `thalweg_manual` / `zone_left` / `zone_right` / `disaster_idx_manual`
   存的都是**测点索引**。删掉中间的某个点后，后头全部要 -1；
   若删掉的正好是那个被指定的点，只能清空该设定并**明确告知用户**，
   悄悄改成"指向下一个点"是更难发现的错误。
"""

from __future__ import annotations

import math
from typing import Optional

from .model import Section, is_valid_index

#: 存"测点索引"的手动覆盖字段。增删测点时全部要平移 / 清空。
MANUAL_INDEX_FIELDS = ("thalweg_manual", "zone_left", "zone_right",
                       "disaster_idx_manual")

#: 手动字段 -> 中文名。用于"这个设定被清空了"的提示，不能只说"索引失效"。
MANUAL_LABELS = {
    "thalweg_manual": "手动深泓点",
    "zone_left": "手动分区左边界",
    "zone_right": "手动分区右边界",
    "disaster_idx_manual": "手动成灾水位测点",
}

#: 相邻测点起点距的最小间距（m）。用最小间距而不是"必须大于"，
#: 是为了让拖动永远不会把两点拖成重合。
MIN_GAP = 0.01


# ---------------- 断面走向与平面坐标 ----------------
def direction(sec: Section) -> tuple[float, float]:
    """断面首末连线的单位方向向量 (ux, uy)。点数不足或退化时返回 (0, 0)。

    ⚠ 必须在**改动 x/y 之前**取：它就是从当前 x/y 算出来的。
    """
    n = sec.n_points
    if n < 2:
        return (0.0, 0.0)
    dx = sec.x[-1] - sec.x[0]
    dy = sec.y[-1] - sec.y[0]
    L = math.hypot(dx, dy)
    if not (L > 0.0) or L != L:            # 除零与 NaN 都挡掉
        return (0.0, 0.0)
    return (dx / L, dy / L)


def recompute_xy(sec: Section, ux: Optional[float] = None,
                 uy: Optional[float] = None) -> bool:
    """按起点距把 x/y 重新铺到断面直线上（就地修改）。返回是否真的改了。

    不传 ux/uy 时取当前首末连线方向——调用方若刚改过 s 而 s[-1] 也变了，
    务必先把方向取好再传进来，否则会拿已经不一致的 x/y 反算方向。
    """
    n = sec.n_points
    if n < 2:
        return False
    if ux is None or uy is None:
        ux, uy = direction(sec)
    if ux == 0.0 and uy == 0.0:
        return False
    s0, x0, y0 = sec.s[0], sec.x[0], sec.y[0]
    for i in range(n):
        d = sec.s[i] - s0
        sec.x[i] = x0 + d * ux
        sec.y[i] = y0 + d * uy
    return True


# ---------------- 改单点 ----------------
def set_z(sec: Section, i: int, value: float) -> None:
    """改第 i 点的高程。起点距与平面坐标都不受影响，最安全的编辑。"""
    if not is_valid_index(i, sec.n_points):
        return
    sec.z[i] = float(value)


def clamp_s(sec: Section, i: int, value: float) -> float:
    """把想要的起点距夹进"严格递增"允许的范围。返回实际可用的值。

    两端（首点 / 末点）只受一侧约束；若邻点已经挤得比 2×MIN_GAP 还近，
    说明这里本来就动不了，原样返回当前值。
    """
    n = sec.n_points
    if not is_valid_index(i, n):
        return float(value)
    lo = (sec.s[i - 1] + MIN_GAP) if i > 0 else None
    hi = (sec.s[i + 1] - MIN_GAP) if i < n - 1 else None
    if lo is not None and hi is not None and lo > hi:
        return sec.s[i]
    v = float(value)
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


def set_s(sec: Section, i: int, value: float, sync_xy: bool = True) -> float:
    """改第 i 点的起点距并同步平面坐标。返回**实际写入**的值（可能被夹过）。

    调用方要把返回值显示给用户，否则"拖了 5 m 只挪了 0.4 m"会被当成 bug。
    """
    if not is_valid_index(i, sec.n_points):
        return float("nan")
    ux, uy = direction(sec)                 # 改 s 之前先把走向取好
    v = clamp_s(sec, i, value)
    if v != v:                              # NaN：什么都不做
        return sec.s[i]
    sec.s[i] = v
    if sync_xy:
        recompute_xy(sec, ux, uy)
    return v


# ---------------- 增删测点 ----------------
def insert_point(sec: Section, at: int, s: Optional[float] = None,
                 z: Optional[float] = None) -> int:
    """在第 `at` 个位置插入一个测点（at == n 表示追加到末尾）。返回实际索引。

    起点距默认取左右邻点中点（端点按相邻间距外推），高程默认线性插值——
    **插入后断面形态不变，只是多一个点**，这样"插点"不会偷偷改变计算结果。

    ⚠ 必须先取走向：插入前端会让 `s[0]` 变化，之后 `direction()` 就变味了。
    """
    n = sec.n_points
    at = max(0, min(int(at), n))
    ux, uy = direction(sec)
    x0, y0, s0 = (sec.x[0], sec.y[0], sec.s[0]) if n else (0.0, 0.0, 0.0)

    if s is None:
        if n == 0:
            s = 0.0
        elif n == 1:
            s = sec.s[0] + 1.0
        elif at == 0:
            s = sec.s[0] - (sec.s[1] - sec.s[0])
        elif at == n:
            s = sec.s[-1] + (sec.s[-1] - sec.s[-2])
        else:
            s = (sec.s[at - 1] + sec.s[at]) * 0.5

    if z is None:
        if n == 0:
            z = 0.0
        elif at == 0:
            z = sec.z[0]
        elif at == n:
            z = sec.z[-1]
        else:
            ds = sec.s[at] - sec.s[at - 1]
            t = ((s - sec.s[at - 1]) / ds) if ds else 0.5
            z = sec.z[at - 1] + (sec.z[at] - sec.z[at - 1]) * t

    sec.x.insert(at, x0 + (s - s0) * ux)
    sec.y.insert(at, y0 + (s - s0) * uy)
    sec.s.insert(at, float(s))
    sec.z.insert(at, float(z))

    shift_manual_indices(sec, at, +1)
    recompute_xy(sec, ux, uy)
    return at


def delete_point(sec: Section, i: int) -> list[str]:
    """删除第 i 个测点。返回被清空的手动设定名称（空列表 = 没有波及）。

    至少保留 2 个点——少于 2 点断面无法计算，直接拒绝并说明原因。
    """
    n = sec.n_points
    if not is_valid_index(i, n):
        return []
    if n <= 2:
        return ["至少要保留 2 个测点，不能继续删除"]

    cleared: list[str] = []
    for f in MANUAL_INDEX_FIELDS:
        v = getattr(sec, f)
        if v is None:
            continue
        if v == i:
            setattr(sec, f, None)
            cleared.append(MANUAL_LABELS[f])
        elif v > i:
            setattr(sec, f, v - 1)

    for arr in (sec.x, sec.y, sec.s, sec.z):
        del arr[i]
    return cleared


def shift_manual_indices(sec: Section, at: int, delta: int) -> None:
    """把 >= at 的手动索引整体平移 delta（插入 +1、删除 -1）。"""
    for f in MANUAL_INDEX_FIELDS:
        v = getattr(sec, f)
        if v is None:
            continue
        if v >= at:
            setattr(sec, f, v + delta)


# ---------------- 快照 / 撤销 ----------------
#: 快照里要存的全部字段。几何四列 + 手动覆盖，缺一个就还原不干净。
_SNAP_FIELDS = ("x", "y", "s", "z", "thalweg_manual", "zone_manual",
                "zone_left", "zone_right", "disaster_idx_manual")


def snapshot(sec: Section) -> dict:
    """拍一份可完整还原的快照（浅拷贝列表，不共享引用）。"""
    return {f: (list(getattr(sec, f)) if f in ("x", "y", "s", "z")
                else getattr(sec, f))
            for f in _SNAP_FIELDS}


def restore(sec: Section, snap: dict) -> None:
    """把快照写回断面（就地修改）。"""
    for f in _SNAP_FIELDS:
        if f not in snap:
            continue
        v = snap[f]
        setattr(sec, f, list(v) if isinstance(v, list) else v)
