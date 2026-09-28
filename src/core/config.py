"""全局可调参数。

原 MATLAB 中这些值硬编码在 transfer.m 开头，此处全部提出为可配置项。

兼容开关说明（详见DESIGN.md §4.4）：
    compat_drop_last_point  True 时复现 MATLAB 丢掉每个断面末测点的行为，用于回归验证
默认关闭（即采用修复后的新行为）；回归比对时打开它应与 MATLAB 完全一致。

注意 1：分区**必须共享转折点**，没有开关。曾误改成不重叠，导致面积少算最多 19%。
注意 2：单断面 / 复式两种模式各自维护自己的顶宽口径，不是缺陷，不需要兼容开关。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, fields


@dataclass
class Config:
    # ---------------- 解析 ----------------
    section_pattern: str = "断面编号"   # 断面分隔行的首列标识

    # 平面坐标的**列序**。注意这说的是"第 1 列是什么"，不是字母 X/Y：
    #   swap_xy = True  -> 第 1 列是北坐标、第 2 列是东坐标（测量惯例，也是原 xydiandao=1）
    #   swap_xy = False -> 第 1 列是东坐标、第 2 列是北坐标
    # 内部约定固定为 x=东坐标、y=北坐标，所以这个开关只决定"前两列谁是谁"。
    #
    # ⚠ 光看列头字母判不出来：中国测量惯例 X=北、数学/CAD 习惯 X=东，
    #   而"列头写 X坐标、装北坐标"和"列头写 X坐标、装东坐标"两种文件文字上完全一样。
    #   详见 DESIGN.md §4.8。
    swap_xy: bool = True

    # True  = 载入数据时按数据自动判定列序（列头名 + 数值量级互证），判不出来时问用户；
    # False = 强制使用上面的 swap_xy，不自动改（只在日志里提醒判定结果与设置不符）。
    # 用户在「计算设置 → 坐标列序」里显式选"北/东"、或在载入确认框里勾了
    # 「写入工程设置」时会被置为 False。
    swap_xy_auto: bool = True

    # ---------------- 曲线计算 ----------------
    dH: float = 0.1                     # 水位步长 (m)

    # 断面计算模式（原 tempjspd）。两种模式各自维护自己的顶宽口径：
    #   True  = 复式断面：按转折点分区，各分区独立算 A/P/B/Q 后求和，
    #           顶宽 B 取各分区逐段 dx 累加（sectionGeom 口径）
    #   False = 单断面：不分区，整断面一次算完，
    #           顶宽 B 取水面左右交点跨度 x_right - x_left（temp.m 口径）
    compound_mode: bool = True

    # ---------------- 转折点判定阈值 ----------------
    steep_slope: float = 0.4            # 原 apxl：先遇到的"陡坡"阈值
    turn_slope: float = 0.05            # 原 zzdxl：之后遇到的"转缓"阈值

    # ---------------- 加高水位（原 Hs1 = Hs + 1，现可配置）----------------
    # 设计水位之上的安全加高幅度（m）。**始终参与计算**：Hs1 = Hs + raise_level，
    # 直接决定左右岸水面交点（Zbs/Ybs）与"加高水位"这一参考水位本身。
    # raise_enabled 仅控制「加高水位线」是否在断面图 / 纵剖面图上绘制；
    # 关闭它只是隐藏这条线（及其图例），**不改变任何计算结果**。
    raise_level: float = 1.0
    raise_enabled: bool = True

    # ---------------- 输出 ----------------
    output_disaster_level: bool = True  # 原 czswpd：是否输出成灾水位行
    export_pictures: bool = True        # 原 sfsctp：是否批量出图
    picture_dir: str = "RatingCurve_Pic"
    output_dir: str = "output"

    # 导出 CSV 的字符编码。默认 utf-8-sig（UTF-8 **带 BOM**）：
    # 中文 Windows 的 Excel / WPS 打开 CSV 时按系统 ANSI(GBK) 解码，
    # 对无 BOM 的 UTF-8 会误判，表头和断面名全变乱码（"断面" -> "鏂潰"）。
    # 带 BOM 后 Excel/WPS 能正确识别为 UTF-8，中文正常显示。
    # 若下游平台不接受 BOM，可改 "utf-8"（无 BOM）或 "gbk"。
    csv_encoding: str = "utf-8-sig"

    # ---------------- 水动力学参数 ----------------
    kinetic_alpha_auto: bool = True     # 是否根据复式断面分区输水能力自动计算动能修正系数
    kinetic_alpha: float = 1.0          # 动能修正系数固定值 (当 kinetic_alpha_auto 为 False 或单断面时使用)

    # ---------------- 图标码（Q3 确认：保持原样，不开放编辑）----------------
    ICON_FOUND: int = 156               # 找到水面交点
    ICON_NOT_FOUND: int = 172           # 未找到，退化为岸顶最高点
    ICON_DISASTER: int = 152            # 成灾水位点

    # ---------------- 兼容开关（回归验证用）----------------
    # 注意：这里**没有**分区是否重叠的开关。
    # 分区必须"共享转折点"（{1:i1, i1:i2, i2:n}），这是唯一正确写法；
    # 曾提供过改成不重叠的开关，结果每个分区边界漏掉一整条线段、面积少算最多 19%。
    # 详见 terrain._build_zones 的说明。
    compat_drop_last_point: bool = False

    # ---------------- 块识别（Q11：非编号块一律排除）----------------
    # 纵剖面块名：匹配上的块不作为横断面计算，而是作为该纵断面线的剖面实测数据
    profile_block_name: str = "纵断面"
    # 合法横断面名的正则：默认「前缀-序号」，如 secB-1 / secC-1
    cross_section_pattern: str = r"^[^\-_]+[-_]\d+$"
    # True 时，既不是纵剖面块、又不符合上面正则的块（如「桥」）直接跳过并告警
    exclude_non_numbered: bool = True

    # ---------------- 里程（Q10：起始桩号端点可选）----------------
    # "start"  = 以数据首点为桩号 0
    # "end"    = 以数据末点为桩号 0（里程反向）
    # "lowest" = 以高程最低点为桩号 0（Q5/Q9 原答复）
    chainage_origin: str = "lowest"

    # ---------------- 分组规则（Q8 待定，先做成可配置）----------------
    # "none"    : 全部断面归入单条纵断面线
    # "prefix"  : 取断面编号前 N 个字符（group_prefix_len）
    # "split"   : 按分隔符切分，取第 1 段（group_separator）
    # "regex"   : 正则第 1 捕获组（group_regex）
    # "manual"  : 界面手工分组
    # "spatial" 按横断面与纵断面在平面上是否相交分组（默认，最可靠）
    # "split" / "prefix" / "regex" 按断面编号文本分组
    # "none"   全部归入单条纵断面线
    # spatial 需要「纵断面」块；缺则自动回退到 split
    group_rule: str = "spatial"
    # 相交判定的容差（m）。两折线最短距离 <= 此值即视为相交，
    # 用于吸收测量误差导致的"擦肩而过"
    intersection_tolerance: float = 1.0
    group_prefix_len: int = 3
    group_separator: str = "-"
    group_regex: str = r"^([^\d]+)"

    # ---------------- 序列化 ----------------
    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    @classmethod
    def from_dict(cls, data: dict, strict: bool = True) -> "Config":
        """strict=False 时忽略未知字段。

        打开工程文件时用非严格模式：这样「更新版本写出、本版本不认识」
        的设置项不会导致整份文件打不开。
        """
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown and strict:
            raise ValueError(f"配置含未知字段: {sorted(unknown)}")
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def load(cls, path: str) -> "Config":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))
