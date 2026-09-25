"""数据模型。对应原 MATLAB 的 struct 数组 A。

层级：Project -> ProfileLine(一条纵断面线/河流) -> Section(一个断面) -> SectionResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


def argmin_index(values: list[float]) -> int:
    """**首个**最小值的索引（与 MATLAB min 的取法一致）。空列表返回 -1。

    抽成公用函数是因为它同时被两处需要，而两处**必须给出同一个答案**：
        terrain.find_thalweg_and_peaks —— 定自动深泓点
        Section.thalweg_index         —— 校验手动分区边界时要知道"生效的深泓点在哪"
    若这两处各写一份，一旦取法（首个 / 末个）不一致，
    就会出现"校验说没问题、计算却退回自动"这种看不出来的错位。
    """
    if not values:
        return -1
    best = 0
    for i in range(1, len(values)):
        if values[i] < values[best]:
            best = i
    return best


def is_valid_index(idx, n: int) -> bool:
    """判断 idx 是不是一个可用的测点索引。

    ⚠ 必须排掉 bool：Python 里 `True` 也是 int，`isinstance(True, int)` 为真，
      于是 JSON 里一个 `true` 会悄悄变成"第 2 个测点"。
      这种错不报错、界面上也看不出来，只能靠这里堵住。
    """
    return (isinstance(idx, int) and not isinstance(idx, bool)
            and 0 <= idx < n)


@dataclass
class SectionParams:
    """来自参数集 xlsx。列：断面编号 / 比降 / 糙率 / 设计流量"""
    name: str
    slope: float = float("nan")        # 比降 S，小数（Q1 确认，不做 ‰ 换算）
    roughness: float = float("nan")    # 统一糙率 n
    design_q: float = float("nan")     # 设计流量 Qs

    # Q2 确认：分区糙率可分开填写；留空(None)时回退到 roughness
    roughness_main: Optional[float] = None    # 主槽
    roughness_left: Optional[float] = None    # 左滩
    roughness_right: Optional[float] = None   # 右滩

    def roughness_for_zone(self, zone_index: int, zone_count: int) -> float:
        """按分区取糙率。分区顺序恒为 [左滩, (主槽), 右滩]。

        zone_count == 1 -> 整断面
        zone_count == 2 -> [左或右侧, 另一侧]，此时用左右滩糙率
        zone_count == 3 -> [左滩, 主槽, 右滩]
        留空一律回退到统一糙率。
        """
        if zone_count == 3:
            key = {0: self.roughness_left, 1: self.roughness_main, 2: self.roughness_right}
            v = key.get(zone_index)
        elif zone_count == 2:
            v = self.roughness_left if zone_index == 0 else self.roughness_right
        else:
            v = self.roughness_main
        return self.roughness if v is None else v


@dataclass
class Section:
    """一个断面。x/y 为平面坐标，s 为起点距，z 为高程。四者等长。

    -------------------------- 手动覆盖（Q14）--------------------------
    深泓点 / 分区边界 / 成灾水位 三者默认由 `terrain.analyze_terrain` 自动推算。
    下列字段用于**人工指定**，全部以**实测测点索引**（0-based）表达：
    界面上的"图上拾取"只能落在实测测点上，没有测点之间的位置。

    ⚠ 手动值一律以 `None` 表示"未覆盖、用自动值"，而不是用哨兵数值。
      这样工程文件里 `null` 就有唯一含义：该字段没有人工干预。

    三者相互**耦合**，改一个可能让另一个失效（例如把深泓点挪到左边界右侧），
    所以写入前必须整组校验，见 `override_errors()`。
    """

    name: str
    x: list[float]
    y: list[float]
    s: list[float]
    z: list[float]
    params: SectionParams = field(default_factory=lambda: SectionParams(""))

    # ---- 手动深泓点 ----
    # 一旦指定就**全面顶替**：分区锚点、H~Q 曲线起算水位、左右岸顶的划分点、
    # 水面交点搜索起点，全部以它为准（用户确认的方案）。
    # 注意若它高于 `dmin_auto`（断面真实最低测点），曲线会少掉最低的若干行。
    thalweg_manual: Optional[int] = None

    # ---- 手动分区边界 ----
    # zone_manual=False 时忽略下面两个字段，分区完全按阈值自动扫描。
    # zone_manual=True 时：
    #     两者都 None -> 不分区（1 区）——"这个断面不该分区"也是一种人工选择，
    #                    所以必须能表达，靠 zone_manual 与"两个都是 None"共同区分
    #     仅 zone_left  -> 2 区 [0..left]、[left..n-1]
    #     仅 zone_right -> 2 区 [0..right]、[right..n-1]
    #     两者都有      -> 3 区 左滩 / 主槽 / 右滩
    # ⚠ 用"布尔 + 两个可空索引"而不是一个列表：列表写法里
    #    `null`（全自动）与 `[null, null]`（不分区）在 JSON 里极易混淆，
    #    而两者语义完全相反。显式布尔没有这个歧义。
    zone_manual: bool = False
    zone_left: Optional[int] = None
    zone_right: Optional[int] = None

    # ---- 手动成灾水位 ----
    # 成灾水位只有从实测测点拾取，所以存测点索引，高程由 terrain 取出。
    # 存索引而非高程：避免出现"存了一个不等于任何测点高程的水位"这种
    # 界面上无法还原、也无法再拾取回来的状态。
    disaster_idx_manual: Optional[int] = None

    @property
    def n_points(self) -> int:
        return len(self.z)

    # ---------------- 覆盖字段校验 ----------------
    def thalweg_index(self) -> int:
        """**生效的**深泓点索引：手动优先，否则取高程最低的测点。

        与 `terrain.analyze_terrain` 的取值完全一致（两处都调 `argmin_index`）。
        校验手动分区边界时必须用它，不能只看 `thalweg_manual`——
        因为计算时用的是"生效的那一个"。
        """
        th = self.thalweg_manual
        if is_valid_index(th, self.n_points):
            return th
        return argmin_index(self.z)

    def override_errors(self) -> list[str]:
        """检查手动覆盖值是否自洽；空列表表示没问题。

        任何一条不满足，`analyze_terrain` 都会**退回自动值**而不是硬算，
        以免一个损坏的工程文件把整个断面算成一堆 NaN。

        ⚠ 分区边界的左右判定必须用 `thalweg_index()`（生效值），
          不能用 `thalweg_manual`：没手动设深泓点时后者是 None，
          会导致"校验一律通过、而 terrain 却按自动深泓点把边界否掉"——
          界面上看是"设置没生效"，实际上两边判断标准不一致。
        """
        errs: list[str] = []
        n = self.n_points

        if self.thalweg_manual is not None and not is_valid_index(
                self.thalweg_manual, n):
            errs.append(f"{self.name}: 手动深泓点索引越界 ({self.thalweg_manual})")

        if self.zone_manual:
            for label, idx in (("左边界", self.zone_left), ("右边界", self.zone_right)):
                if idx is not None and not is_valid_index(idx, n):
                    errs.append(f"{self.name}: 手动{label}索引越界 ({idx})")
            if (is_valid_index(self.zone_left, n)
                    and is_valid_index(self.zone_right, n)
                    and self.zone_left >= self.zone_right):
                errs.append(f"{self.name}: 手动分区左边界({self.zone_left}) "
                            f"未小于右边界({self.zone_right})")
            di = self.thalweg_index()
            if 0 <= di < n:
                if (is_valid_index(self.zone_left, n) and self.zone_left >= di):
                    errs.append(f"{self.name}: 手动分区左边界({self.zone_left}) "
                                f"未落在深泓点({di})左侧")
                if (is_valid_index(self.zone_right, n) and self.zone_right <= di):
                    errs.append(f"{self.name}: 手动分区右边界({self.zone_right}) "
                                f"未落在深泓点({di})右侧")

        if (self.disaster_idx_manual is not None
                and not is_valid_index(self.disaster_idx_manual, n)):
            errs.append(f"{self.name}: 手动成灾水位测点索引越界 "
                        f"({self.disaster_idx_manual})")

        return errs

    @property
    def has_manual(self) -> bool:
        """是否有任何人工覆盖。用于界面上标「手动」与工程文件瘦身。"""
        return (self.thalweg_manual is not None or self.zone_manual
                or self.disaster_idx_manual is not None)

    def duanmian_xy(self) -> str:
        """对应原 A.duanmianXY：'起点X,起点Y;终点X,终点Y'，三位小数"""
        return f"{self.x[0]:.3f},{self.y[0]:.3f};{self.x[-1]:.3f},{self.y[-1]:.3f}"

    def validate(self, check_params: bool = True) -> list[str]:
        """返回问题描述列表；空列表表示通过。对应缺陷 D6。

        check_params=False 时只校验几何，用于"参数还没填"的阶段——
        否则会在参数随后被补齐后留下一堆过时的假告警。
        """
        errs: list[str] = []
        if not (len(self.x) == len(self.y) == len(self.s) == len(self.z)):
            errs.append(f"{self.name}: 四列长度不一致 "
                        f"(x={len(self.x)}, y={len(self.y)}, s={len(self.s)}, z={len(self.z)})")
        if self.n_points < 2:
            errs.append(f"{self.name}: 测点不足 2 个 (实际 {self.n_points})")
        if not check_params:
            return errs
        p = self.params
        if p.roughness is None or p.roughness != p.roughness or p.roughness <= 0:
            errs.append(f"{self.name}: 糙率缺失或非正 ({p.roughness})")
        if p.slope != p.slope or p.slope <= 0:
            errs.append(f"{self.name}: 比降缺失或非正 ({p.slope})")
        if p.design_q != p.design_q:
            errs.append(f"{self.name}: 设计流量缺失")
        return errs


@dataclass
class TerrainInfo:
    """地形特征提取结果（对应原 MATLAB 阶段 ② ③ 的输出）

    ---------- 「转折点」与「分区边界」是两个概念（Q15）----------
    原 MATLAB 里这两个是同一对索引（分区直接由转折点拼出来），本项目一开始
    也沿用，但那是图省事。实际上：

        left/right_turn_idx   转折点 —— 扫描算法的产物，**永远自动**，
                              人工改不了；成灾水位由它决定
        zone_left/right_idx   分区边界 —— 决定 `zones` 与各分区糙率取值，
                              **默认等于转折点**，可人工拾取覆盖

    所以「手动改分区」不再连带改动转折点与成灾水位。
    沿用时要注意：**改分区边界不会让成灾水位变**，那是设计如此。

    ---------- 其余字段 ----------
    除上述两者外，`dmin / dmin_idx / disaster_level / disaster_idx / zones`
    都是**套用人工覆盖之后**的有效值，下游（曲线、成灾流量、导出、三张图）
    直接使用，不需要知道有没有手动干预。
    同时保留 `*_auto` 系列字段，仅供界面显示"自动值是多少、你覆盖掉了什么"。
    """
    dmin: float = float("nan")          # 深泓点高程
    dmin_idx: int = -1                  # 深泓点索引（0-based）
    zmax: float = float("nan")          # 左岸最高点
    zmax_idx: int = -1
    ymax: float = float("nan")          # 右岸最高点
    ymax_idx: int = -1
    zymin: float = float("nan")         # 水位计算上限 min(Zmax, Ymax)

    # 转折点：永远自动，决定成灾水位
    left_turn_idx: Optional[int] = None
    right_turn_idx: Optional[int] = None

    # 分区边界：默认 = 转折点，可人工覆盖
    zone_left_idx: Optional[int] = None
    zone_right_idx: Optional[int] = None

    disaster_level: float = float("nan")   # 成灾水位
    disaster_idx: int = -1

    zones: list[tuple[int, int]] = field(default_factory=list)  # [(start, stop_exclusive)] 0-based

    # ---------------- 未套用人工覆盖时的自动值（仅供界面对照） ----------------
    dmin_auto: float = float("nan")        # 断面真实最低测点高程
    dmin_auto_idx: int = -1
    zymin_auto: float = float("nan")       # 深泓点未被手动改时对应的水位上限
    disaster_level_auto: float = float("nan")


@dataclass
class SectionResult:
    """单个断面的完整计算结果"""
    hvec: list[float] = field(default_factory=list)   # 水位
    qvec: list[float] = field(default_factory=list)   # 流量
    avec: list[float] = field(default_factory=list)   # 过水面积
    pvec: list[float] = field(default_factory=list)   # 湿周
    bvec: list[float] = field(default_factory=list)   # 顶宽

    design_level: float = float("nan")       # Hs：设计流量对应水位
    design_level_plus: float = float("nan")  # Hs1 = Hs + 加高幅度（Config.raise_level，默认 +1）
    disaster_flow: float = float("nan")      # ChengZaiLiuLiang

    # 加高水位 Hs1 处的左右岸水面交点（原 MATLAB 的输出即基于此，决定 Zbs/Ybs）
    left_point: Optional[tuple[float, float]] = None   # 左岸水面交点 (X, Y)
    right_point: Optional[tuple[float, float]] = None  # 右岸水面交点 (X, Y)
    left_status: int = 0                                # Zbs: 156/172
    right_status: int = 0                               # Ybs: 156/172

    # 设计水位 Hs 处的左右岸水面交点（新增，供「设计水位淹没范围坐标」导出）
    left_point_hs: Optional[tuple[float, float]] = None
    right_point_hs: Optional[tuple[float, float]] = None
    left_status_hs: int = 0
    right_status_hs: int = 0

    warnings: list[str] = field(default_factory=list)


@dataclass
class ProfileData:
    """「纵断面」块的实测数据：起点距即沿河里程，高程即河底高程。

    有了它就不需要从横断面深泓点推算里程。
    """
    x: list[float] = field(default_factory=list)   # 平面坐标
    y: list[float] = field(default_factory=list)
    dist: list[float] = field(default_factory=list)  # 起点距（原始）
    z: list[float] = field(default_factory=list)     # 高程
    chainage: list[float] = field(default_factory=list)  # 按 chainage_origin 重定基后的里程
    origin: str = "lowest"

    @property
    def n_points(self) -> int:
        return len(self.dist)


@dataclass
class ProfileLine:
    """一条纵断面线（一条河流 / 一段河道）。Q4 确认：一个工程含多条。"""
    name: str
    sections: list[Section] = field(default_factory=list)
    chainage: list[float] = field(default_factory=list)   # 各断面里程 (m)，见 chainage.py
    order_source: str = "file"   # file / auto / manual
    profile: Optional[ProfileData] = None   # 来自文件末尾「纵断面」块
    # 各横断面在纵断面上的**原始起点距**（与 profile.dist 同坐标系），
    # 由空间相交分组时算出。chainage 是重定基后的桩号，两者坐标系不同：
    # 算比降要用原始起点距（slope.py），显示桩号用 chainage。
    profile_dist: list[float] = field(default_factory=list)

    # 一维水动力模拟配置与结果
    hydro1d_enabled: bool = False
    hydro1d_regime: str = "subcritical"  # 'subcritical', 'supercritical', 'mixed', 'auto'
    hydro1d_levels: list[float] = field(default_factory=list)  # 与 sections 等长的推算水位结果


@dataclass
class Project:
    profile_lines: list[ProfileLine] = field(default_factory=list)

    def all_sections(self) -> list[Section]:
        return [s for line in self.profile_lines for s in line.sections]
