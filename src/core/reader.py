"""Excel 解析：断面数据 + 参数集。对应原 MATLAB transfer.m 开头部分。

⚠ 本模块依赖 openpyxl，需先安装依赖。

块结构（已用真实样本验证，块与块之间**无空行**）：
    行 N    : 断面编号 | secB-1          ← 标题行
    行 N+1  : X坐标 | Y坐标 | 起点距 | 高程   ← 列头
    行 N+2 …: 数据
    行 M    : 断面编号 | secB-2          ← 下一块紧贴上一块最后一行

块分类（Q11）：
    profile -> 块名 == cfg.profile_block_name（默认「纵断面」），作为该线纵剖面实测数据
    cross   -> 块名匹配 cfg.cross_section_pattern（默认「前缀-序号」），作为横断面参与计算
    skip    -> 其余（如「桥」）直接排除并告警

D9：MATLAB 的切片会让**每个非末尾块丢掉最后一个测点**。
    cfg.compat_drop_last_point=True 时复现该行为（仅用于回归比对），默认 False 保留全部。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from typing import Optional

from .config import Config
from .model import (ProfileData, ProfileLine, Project, Section, SectionParams)


@dataclass
class Block:
    name: str
    rows: list[list] = field(default_factory=list)
    kind: str = "skip"          # cross / profile / skip
    # 列头行（标题行的下一行，如 ["X坐标", "Y坐标", "起点距", "高程"]）。
    # 由 split_blocks 一并取出，供 detect_xy_order 判断列序用；
    # 没有列头行（第一行就是数据）时为空列表。
    header: list = field(default_factory=list)


@dataclass
class ParsedBlocks:
    """一个文件的解析结果。

    profiles 是**列表**：一个文件可以有多个「纵断面」块
    （sample_a.xlsx 有 3 个，sample_e 有 2 个），每个代表该文件里的一条纵断面线。
    只保留一个会把其余的静默丢掉。

    seq 记录块的原始出现顺序，形如 [("profile", 0), ("cross", 0), ("cross", 1), ...]，
    用于在缺少纵断面块时按顺序回退分组，以及给重名的纵断面线编号。
    """
    sections: list[Section] = field(default_factory=list)
    profiles: list[ProfileData] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    seq: list[tuple[str, int]] = field(default_factory=list)
    # X/Y 列序判定结果（见 detect_xy_order）。None = 未做判定。
    xy: Optional["XYDecision"] = None


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _import_openpyxl():
    try:
        import openpyxl
    except ImportError as e:      # pragma: no cover
        raise RuntimeError(
            "读取 xlsx 需要 openpyxl。请先在 .venv 中安装依赖（见 requirements.txt）"
        ) from e
    return openpyxl


def read_sheet_rows(path: str, sheet_name: Optional[str] = None) -> list[list]:
    """读取整个工作表为二维列表（不做类型转换）。"""
    openpyxl = _import_openpyxl()
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.worksheets[0]
    return [[c for c in row] for row in ws.iter_rows(values_only=True)]


def classify(name: str, cfg: Config) -> str:
    """判定块的用途。

    ⚠ 纵剖面块名要按**前缀**匹配，不能精确相等：
      老文件叫「纵断面」，新文件（sample_a / sample_e）叫「纵断面1」「纵断面2」
      「纵断面3」——一个文件里有多条纵断面线，用编号区分。
      用精确相等会把它们全部误判成"非编号块"而排除掉。
    """
    n = (name or "").strip()
    prof_key = (cfg.profile_block_name or "").strip()
    if prof_key and n.startswith(prof_key):
        return "profile"
    try:
        if re.match(cfg.cross_section_pattern, n):
            return "cross"
    except re.error:
        pass
    return "skip"


def split_blocks(raw: list[list], cfg: Config) -> list[Block]:
    """按「断面编号」标题行切块，并分类。

    对应缺陷 D7：改为按索引配对，绝不会像 MATLAB 那样错位。
    """
    nrows = len(raw)
    header_rows: list[int] = []

    for r in range(nrows):
        v = raw[r][0] if len(raw[r]) > 0 else None
        if v is None:
            continue
        if str(v).strip().startswith(cfg.section_pattern):
            header_rows.append(r)

    if not header_rows:
        raise ValueError(f"未找到任何以「{cfg.section_pattern}」开头的行，请检查数据文件")

    bounds = header_rows + [nrows]      # 0-based，末尾用实际行数
    blocks: list[Block] = []

    for j, h0 in enumerate(header_rows):
        name_v = raw[h0][1] if len(raw[h0]) > 1 else None
        name = str(name_v).strip() if name_v is not None else f"块{j+1}"

        a = h0 + 2                       # 列头行之后的第一行
        b_actual = bounds[j + 1]         # 下一块标题行（不含）；末尾块为 nrows
        # MATLAB 只在「非末尾块」少取一行（末尾块因边界补 SRrow+2 而侥幸完整）
        b_matlab = b_actual - 1 if (j + 1 < len(header_rows)) else nrows
        b = b_matlab if cfg.compat_drop_last_point else b_actual

        rows = raw[a:max(b, a)]
        blocks.append(Block(name=name, rows=rows, kind=classify(name, cfg),
                            header=list(raw[h0 + 1]) if h0 + 1 < len(raw) else []))

    return blocks


def _rows_to_arrays(rows: list[list], cfg: Config
                    ) -> tuple[list[float], list[float], list[float], list[float]]:
    """把数据行转成 x, y, s, z 四列，跳过非数值行。

    **x = 北坐标（纵向）、y = 东坐标（横向）** —— 内部的测量坐标系约定。
    输入文件前两列的物理含义由 `cfg.first_col_is_north` 决定（载入前已由
    `detect_xy_order` 判定好），所以这里只做"哪一列进 x"的映射，
    与输入文件的前两列顺序无关。
    """
    xs, ys, ss, zs = [], [], [], []
    n_col = 0 if cfg.first_col_is_north else 1      # 北坐标所在列
    e_col = 1 - n_col                              # 东坐标所在列
    for row in rows:
        if len(row) < 4:
            continue
        s_v = _to_float(row[2])
        z_v = _to_float(row[3])
        if s_v is None or z_v is None:
            continue
        x_v = _to_float(row[n_col])                # x = 北
        y_v = _to_float(row[e_col])                # y = 东
        if x_v is None or y_v is None:
            continue
        xs.append(x_v); ys.append(y_v); ss.append(s_v); zs.append(z_v)
    return xs, ys, ss, zs


def blocks_to_sections(blocks: list[Block], cfg: Config) -> ParsedBlocks:
    """把块转成断面列表 + **纵剖面列表** + 被排除的块名 + 原始顺序。"""
    out = ParsedBlocks()

    for b in blocks:
        xs, ys, ss, zs = _rows_to_arrays(b.rows, cfg)

        if b.kind == "profile":
            if len(zs) >= 2:
                out.profiles.append(ProfileData(x=xs, y=ys, dist=ss, z=zs))
                out.seq.append(("profile", len(out.profiles) - 1))
            else:
                out.skipped.append(f"{b.name}(测点不足)")
            continue

        if b.kind == "skip":
            if cfg.exclude_non_numbered:
                out.skipped.append(b.name)
                continue
            # 显式关闭排除开关时，非编号块也当横断面处理
            if len(zs) >= 2:
                out.sections.append(Section(name=b.name, x=xs, y=ys, s=ss, z=zs,
                                            params=SectionParams(name=b.name)))
                out.seq.append(("cross", len(out.sections) - 1))
            continue

        if len(zs) < 2:
            out.skipped.append(f"{b.name}(测点不足)")
            continue
        out.sections.append(Section(name=b.name, x=xs, y=ys, s=ss, z=zs,
                                    params=SectionParams(name=b.name)))
        out.seq.append(("cross", len(out.sections) - 1))

    return out


def parse_params(raw: list[list]) -> dict[str, SectionParams]:
    """解析参数集：第1列=断面编号, 第2列=比降, 第3列=糙率, 第4列=设计流量。

    支持可选的第 5~7 列：主槽 / 左滩 / 右滩糙率（Q2）。
    """
    out: dict[str, SectionParams] = {}
    for row in raw:
        if not row or row[0] is None:
            continue
        name = str(row[0]).strip()
        if name == "" or name == "断面编号":
            continue
        slope = _to_float(row[1]) if len(row) > 1 else None
        rough = _to_float(row[2]) if len(row) > 2 else None
        q = _to_float(row[3]) if len(row) > 3 else None
        if slope is None and rough is None and q is None:
            continue
        out[name] = SectionParams(
            name=name,
            slope=slope if slope is not None else float("nan"),
            roughness=rough if rough is not None else float("nan"),
            design_q=q if q is not None else float("nan"),
            roughness_main=_to_float(row[4]) if len(row) > 4 else None,
            roughness_left=_to_float(row[5]) if len(row) > 5 else None,
            roughness_right=_to_float(row[6]) if len(row) > 6 else None,
        )
    return out


def attach_params(sections: list[Section],
                  params: dict[str, SectionParams]) -> list[str]:
    """把参数集挂到断面上，返回未匹配上的断面名列表（对应缺陷 D6）。"""
    missing = []
    for sec in sections:
        p = params.get(sec.name)
        if p is None:
            missing.append(sec.name)
            continue
        sec.params = p
    return missing


def group_into_profile_lines(sections: list[Section],
                             cfg: Config) -> list[ProfileLine]:
    """按 cfg.group_rule 把断面分组成纵断面线。

    Q8 已确认命名规则为「前缀-序号」，故默认用 split（取 '-' 前一段）。
    """
    if cfg.group_rule == "none":
        return [ProfileLine(name="默认", sections=list(sections))]

    def key(name: str) -> str:
        if cfg.group_rule == "prefix":
            return name[:cfg.group_prefix_len] or "默认"
        if cfg.group_rule == "split":
            return name.split(cfg.group_separator)[0] or "默认"
        if cfg.group_rule == "regex":
            m = re.match(cfg.group_regex, name)
            return m.group(1) if m else "默认"
        return "默认"

    buckets: dict[str, list[Section]] = {}
    for sec in sections:
        buckets.setdefault(key(sec.name), []).append(sec)

    return [ProfileLine(name=k, sections=v) for k, v in buckets.items()]


# =====================================================================
# X/Y 列序判定（详见 DESIGN.md §4.8）
# =====================================================================
# 要判定的其实只有一句话：**输入的第 1 列是北坐标还是东坐标**。
# 判出来之后，北列进 x、东列进 y —— 内部一律是测量坐标系（x=北、y=东），
# 与输入文件的列序无关（见 config.Config.first_col_is_north 的说明）。
#
# 只用两层证据，都不做几何推断：
#   层 1 列头名：强信号（含「北/纵/N」「东/横/E」等）可定论。
#                裸 X / Y **只算弱信号**——中国测量惯例 X=北，而数学/CAD 习惯
#                里 X 常指东，"列头 X坐标装北坐标"与"列头 X坐标装东坐标"
#                两种文件在文字层面完全一样，字母本身不构成证据。
#   层 2 数值量级：中国境内高斯投影的三档区间互不重叠，是很硬的证据。
#
# 两层都拿不准就报 confident=False，由界面问用户，**不猜**。

#: 列头里表示"北坐标"的强关键词
_HEADER_NORTH = ("北", "纵", "northing")
#: 列头里表示"东坐标"的强关键词
_HEADER_EAST = ("东", "横", "easting")

#: 数值量级区间（中国境内 CGCS2000 / 西安80 高斯投影）。三档互不重叠是判据成立的前提。
NORTH_RANGE = (2.0e6, 6.0e6)        # 北坐标：7 位，约 2.0~6.0 百万
EAST_ZONE_RANGE = (1.0e7, 4.6e7)    # 东坐标**含带号**：8 位（3° 带 25~45 带、6° 带 13~23 带）
EAST_PLAIN_RANGE = (1.0e5, 1.0e6)   # 东坐标**不含带号**：6 位，多在 5.0e5 附近

_XY_MIN_SAMPLES = 3      # 每列至少要有这么多有效数值才判定
_XY_HIT_RATIO = 0.8      # 区间命中率阈值（个别跳点不影响）
_XY_MAX_SAMPLE = 50      # 每块取样行数上限


@dataclass
class XYDecision:
    """一个文件的 X/Y 列序判定结果。

    `first_is_north` 说的就是"输入文件的第 1 列是不是北坐标"。
    判不出来时为 None，由调用方套 `Config.first_col_is_north` 兜底。
    """
    first_is_north: Optional[bool]   # None = 判不出来
    confident: bool             # 能否定论；False 时界面应当询问用户
    source: str                 # 依据：列头+量级 / 量级 / 列头 / 指定 / 未判定
    note: str                   # 一行说明（含证据），用于状态栏与日志
    conflict: bool = False      # 证据之间矛盾（列头 vs 量级、块与块之间）

    def describe(self) -> str:
        """人间可读的一句话，如「输入第 1 列=北坐标（列头+量级）」。"""
        if self.first_is_north is None:
            return f"输入列序未确定（{self.source}）"
        return (f"输入第 1 列={'北' if self.first_is_north else '东'}坐标"
                f"（{self.source}）")


def _looks_like_header(cells: list) -> bool:
    """列头行的判据：前两列至少有一个是**非数值文字**。

    第一行就是数据的文件会被判为 False，直接走量级层。
    """
    for v in (cells or [])[:2]:
        if v is None:
            continue
        if _to_float(v) is None and str(v).strip():
            return True
    return False


def _header_kind(text) -> Optional[bool]:
    """一列列头文字指向哪种坐标：True=北 / False=东 / None=没有强信号。"""
    s = str(text or "").strip().lower()
    if not s:
        return None
    for k in _HEADER_NORTH:
        if k in s:
            return True
    for k in _HEADER_EAST:
        if k in s:
            return False
    return None


def _header_order(cells: list) -> tuple[Optional[bool], bool]:
    """按列头文字判断列序。返回 (第 1 列是否北, 是否矛盾)。

    第一项为 None 表示"没有强信号"（裸 X/Y、无列头、或两列同类）。
    """
    if not _looks_like_header(cells):
        return None, False
    first = _header_kind(cells[0] if len(cells) > 0 else None)
    second = _header_kind(cells[1] if len(cells) > 1 else None)
    if first is None and second is None:
        return None, False
    if first is None:
        return (not second), False          # 第 2 列是北 -> 第 1 列是东
    if second is None:
        return first, False
    if first == second:
        return None, True                   # 两列都指同一边，列头自相矛盾
    return first, False


def _classify_column(values: list[float]) -> Optional[str]:
    """把一列数值分类成 north / east / east_weak / None。"""
    vals = [v for v in values[:_XY_MAX_SAMPLE] if v is not None]
    if len(vals) < _XY_MIN_SAMPLES:
        return None

    def hit(lo: float, hi: float) -> bool:
        return sum(1 for v in vals if lo <= v <= hi) / len(vals) >= _XY_HIT_RATIO

    if hit(*NORTH_RANGE):
        return "north"
    if hit(*EAST_ZONE_RANGE):
        return "east"
    if hit(*EAST_PLAIN_RANGE):
        return "east_weak"      # 1e5~1e6 排他性弱，需另一列辅证
    return None


def _order_from_pair(c1: Optional[str], c2: Optional[str]) -> Optional[bool]:
    """由两列的量级分类推出列序：True=第 1 列北 / False=第 1 列东 / None=判不出。"""
    if c1 == "north":
        return True                 # 北坐标区间排他性最强，单独即可定论
    if c1 == "east":                # 含带号的东坐标同样很强
        return False
    if c1 == "east_weak":
        return False if c2 == "north" else None     # 弱东必须配一个明确的北
    if c2 == "north":               # 第 2 列是北坐标 -> 第 1 列只能是东
        return False
    return None


def _column_values(rows: list[list], col: int) -> list[float]:
    out: list[float] = []
    for row in rows:
        if len(row) <= col:
            continue
        v = _to_float(row[col])
        if v is not None:
            out.append(v)
    return out


def _aggregate(orders: list[bool]) -> tuple[Optional[bool], bool]:
    """把各块的结论合并成文件级结论。返回 (结论, 是否矛盾)。"""
    if not orders:
        return None, False
    if all(o == orders[0] for o in orders):
        return orders[0], False
    return None, True               # 块与块之间不一致


def detect_xy_order(blocks: list[Block]) -> XYDecision:
    """判定「输入的第 1 列是北坐标还是东坐标」。只做列头名与数值量级两层。

    判不出来时 `first_is_north=None`、`confident=False`，由调用方套
    `Config.first_col_is_north` 兜底，并由界面弹窗问用户。
    """
    # ---- 层 1：列头 ----
    h_orders: list[bool] = []
    h_conflict = False
    for b in blocks:
        o, c = _header_order(b.header)
        h_conflict = h_conflict or c
        if o is not None:
            h_orders.append(o)
    h_order, h_mixed = _aggregate(h_orders)

    # ---- 层 2：数值量级 ----
    m_orders: list[bool] = []
    m_detail: list[str] = []
    for b in blocks:
        c1 = _classify_column(_column_values(b.rows, 0))
        c2 = _classify_column(_column_values(b.rows, 1))
        if not m_detail and (c1 or c2):
            m_detail.append(f"{b.name}: 第1列={c1 or '未知'}、第2列={c2 or '未知'}")
        o = _order_from_pair(c1, c2)
        if o is not None:
            m_orders.append(o)
    m_order, m_mixed = _aggregate(m_orders)

    ambiguous = h_conflict or h_mixed or m_mixed

    # ---- 合并 ----
    if h_order is not None and m_order is not None and h_order == m_order:
        return XYDecision(m_order, True, "列头+量级",
                          f"输入第 1 列={'北' if m_order else '东'}坐标"
                          f"（列头与数值量级一致{'；' + m_detail[0] if m_detail else ''}）",
                          conflict=ambiguous)
    if m_order is not None:
        # 量级是硬证据（区间互不重叠），列头文字可能是前人填错的 -> 量级优先
        n = (f"输入第 1 列={'北' if m_order else '东'}坐标（按数值量级判定"
             f"{'：' + m_detail[0] if m_detail else ''}）")
        if h_order is not None:
            n += "；⚠ 列头文字与数值量级不一致，已按量级处理"
        return XYDecision(m_order, True, "量级", n, conflict=ambiguous or h_order is not None)
    if h_order is not None:
        return XYDecision(h_order, True, "列头",
                          f"输入第 1 列={'北' if h_order else '东'}坐标（按列头文字判定）",
                          conflict=ambiguous)
    # 都没判出来
    why = "列头与数值量级互相矛盾" if ambiguous else "列头未明示、数值也不在可判定的量级区间"
    return XYDecision(None, False, "未判定",
                      f"无法自动判定输入列序（{why}）", conflict=ambiguous)


def load_file(path: str, cfg: Config, sheet_name: Optional[str] = None,
              xy_override: Optional[bool] = None) -> ParsedBlocks:
    """载入单个文件。一个文件可能含多个「纵断面」块，全部保留。

    内部一律是测量坐标系（x=北、y=东），所以这里只判定"输入第 1 列是不是北"，
    再把北列放进 x —— 与输入文件的前两列顺序无关。

    xy_override 给定时直接采用它（=输入第 1 列是否北坐标），不再做判定。
    用于用户刚在确认框里选过的这一次载入，避免"问了又判、判了又问"的死循环。
    """
    raw = read_sheet_rows(path, sheet_name)
    blocks = split_blocks(raw, cfg)

    if xy_override is not None:
        first_north = bool(xy_override)
        decision = XYDecision(first_north, True, "指定",
                              f"按你的选择读取：输入第 1 列是"
                              f"{'北' if first_north else '东'}坐标")
    else:
        detected = detect_xy_order(blocks)
        if detected.confident:
            first_north, decision = detected.first_is_north, detected
        else:
            # 判不出来 -> 用工程里的兜底值，并保持 confident=False，
            # 界面据此弹窗问用户（core 不认识界面，只负责把信号带上去）
            first_north = cfg.first_col_is_north
            decision = XYDecision(
                first_north, False, "未判定",
                f"{detected.note}；暂按当前设置：输入第 1 列是"
                f"{'北' if first_north else '东'}坐标",
                conflict=detected.conflict)

    parsed = blocks_to_sections(blocks, replace(cfg, first_col_is_north=first_north))
    parsed.xy = decision
    return parsed


def load_project(section_path: str,
                 param_path: Optional[str] = None,
                 cfg: Optional[Config] = None,
                 sheet_name: Optional[str] = None) -> tuple[Project, list[str]]:
    """载入单个文件构成工程。返回 (Project, 警告信息列表)。"""
    cfg = cfg or Config()
    warnings: list[str] = []
    stem = os.path.splitext(os.path.basename(section_path))[0]

    parsed = load_file(section_path, cfg, sheet_name)
    if not parsed.sections:
        raise ValueError(f"{section_path} 未解析到任何有效横断面")

    if parsed.skipped:
        warnings.append(f"{os.path.basename(section_path)}: 已排除非编号块 {parsed.skipped}")
    if not parsed.profiles:
        warnings.append(f"{os.path.basename(section_path)}: 未找到「{cfg.profile_block_name}」块，"
                        f"纵剖面里程将改用横断面深泓点推算")

    lines = _build_lines([(stem, parsed)], cfg, warnings)

    if param_path:
        params = parse_params(read_sheet_rows(param_path))
        missing = attach_params(parsed.sections, params)
        if missing:
            warnings.append(f"参数集中缺少以下断面的参数：{missing}")
        for line in lines:
            for sec in line.sections:
                warnings.extend(sec.validate())

    return Project(profile_lines=lines), warnings


def load_folder(folder: str, cfg: Optional[Config] = None,
                param_path: Optional[str] = None,
                xy_override: Optional[bool] = None,
                xy_report: Optional[list] = None
                ) -> tuple[Project, list[str]]:
    """批量读取整个文件夹下的所有 xlsx。

    一个文件可含**多个「纵断面」块**（如 sample_a.xlsx 有 3 个），
    每个块代表一条纵断面线，全部作为独立的分组目标参与判定。

    分组策略由 cfg.group_rule 决定：
        "spatial"（默认）—— 按横断面与纵断面在平面上是否相交分组
        其余            —— 按块的出现顺序分组（纵断面块管它后面的横断面）

    xy_override：直接指定列序（True=第 1 列是北坐标），跳过自动判定。
                 用于"用户刚在确认框里选过"的那一次重载。
    xy_report  ：出参。传入一个 list 时，会按文件追加
                 `(文件名, XYDecision)`，供界面汇总展示或弹窗询问。
                 core 层不认识界面，所以用出参而不是回调。
    """
    cfg = cfg or Config()
    warnings: list[str] = []
    loaded: list[tuple[str, ParsedBlocks]] = []

    for fn in sorted(os.listdir(folder)):
        if fn.lower().startswith("~$") or not fn.lower().endswith(".xlsx"):
            continue
        path = os.path.join(folder, fn)
        stem = os.path.splitext(fn)[0]
        try:
            parsed = load_file(path, cfg, xy_override=xy_override)
        except Exception as e:
            warnings.append(f"{fn}: 读取失败 - {e}")
            continue
        if not parsed.sections:
            warnings.append(f"{fn}: 未解析到有效横断面")
            continue
        if parsed.xy is not None:
            if xy_report is not None:
                xy_report.append((fn, parsed.xy))
            # 判不出来 / 证据矛盾都是需要用户过目的，进告警；
            # 判定明确的只由界面写一行汇总，不占用告警位
            if not parsed.xy.confident or parsed.xy.conflict:
                warnings.append(f"{fn}: {parsed.xy.note}")
        if parsed.skipped:
            warnings.append(f"{fn}: 已排除非编号块 {parsed.skipped}")
        if len(parsed.profiles) > 1:
            warnings.append(f"{fn}: 含 {len(parsed.profiles)} 个「"
                            f"{cfg.profile_block_name}」块，将分别作为独立纵断面线")
        loaded.append((stem, parsed))

    if not loaded:
        raise ValueError(f"{folder} 下没有可解析的 xlsx 断面文件")

    # 断面重名必须在**分组之前**解决，原因有两个：
    #   1. 分组、桩号、attach_params 全都按名字办事，重名会一路错到底；
    #   2. 结果字典 `MainWindow.results` 就是 `{断面名: 结果}`，
    #      重名会让后载入的**静默覆盖**先载入的——导出 CSV 里两个断面
    #      引用同一份结果、一维推算取错设计水位，且不报错、图上看不出来。
    # 纵断面线名早就有去重（`_dedupe_names`），断面名一直没有，这是补上那一半。
    _dedupe_section_names(loaded, warnings)

    lines = _build_lines(loaded, cfg, warnings)

    if param_path:
        params = parse_params(read_sheet_rows(param_path))
        all_secs = [s for ln in lines for s in ln.sections]
        missing = attach_params(all_secs, params)
        if missing:
            warnings.append(f"参数集中缺少以下断面的参数：{missing}")

    # 没给参数集时不做参数校验——参数稍后会在界面/命令行补齐，
    # 那时再校验才不会留下过时的假告警
    if param_path is not None:
        for line in lines:
            for sec in line.sections:
                warnings.extend(sec.validate())

    return Project(profile_lines=lines), warnings


def _dedupe_section_names(loaded: list[tuple[str, "ParsedBlocks"]],
                          warnings: list[str]) -> list[tuple[str, str]]:
    """跨文件重名的横断面自动改名，返回 [(原名, 新名), ...]。

    **只在真的重名时改名**（方案 A）：不重名的数据一个字符都不动，
    所以绝大多数工程完全无感，也不会把下游按断面名对账的脚本弄断链。

    后缀用 `@文件名`（如 `secA-1@b`）而不是 `-段2`：
    断面重名基本都发生在**不同文件各有一块同名断面**的场景，
    带上文件来源才能让用户看出"这两个 secA-1 分别来自哪"。

    ⚠ 必须同时改 `sec.params.name`。两者在 `params.ensure_params` 里
      有"保持一致"的约定，且工程文件里各存一份——只改一边会让
      参数面板/导出按旧名去找，改名反而制造出"参数丢失"。
    """
    counts: dict[str, int] = {}
    for _stem, parsed in loaded:
        for sec in parsed.sections:
            counts[sec.name] = counts.get(sec.name, 0) + 1
    dupes = {n for n, c in counts.items() if c > 1}
    if not dupes:
        return []

    renamed: list[tuple[str, str]] = []
    taken: set[str] = set()
    for stem, parsed in loaded:
        for sec in parsed.sections:
            if sec.name not in dupes:
                taken.add(sec.name)
    for stem, parsed in loaded:
        for sec in parsed.sections:
            if sec.name not in dupes:
                continue
            base = f"{sec.name}@{stem}"
            new_name, k = base, 2
            while new_name in taken:            # 同文件内原名重复时补序号
                new_name = f"{base}-{k}"
                k += 1
            taken.add(new_name)
            renamed.append((sec.name, new_name))
            sec.name = new_name
            if sec.params is not None:
                sec.params.name = new_name

    if renamed:
        detail = "、".join(f"{a} → {b}" for a, b in renamed[:5])
        more = f"（共 {len(renamed)} 个）" if len(renamed) > 5 else ""
        warnings.append(
            f"检测到重名横断面，已自动改名以免互相覆盖：{detail}{more}")
    return renamed


def _build_lines(loaded: list[tuple[str, ParsedBlocks]], cfg: Config,
                 warnings: list[str]) -> list[ProfileLine]:
    """把若干文件、若干纵断面块汇总成纵断面线列表。"""
    all_sections: list[Section] = []
    profiles: list[ProfileData] = []
    meta: list[tuple[str, int]] = []        # (文件名, 文件内第几个纵断面块)

    for stem, parsed in loaded:
        all_sections.extend(parsed.sections)
        for k, prof in enumerate(parsed.profiles):
            metrics = prof
            metrics.chainage = _chainage_of(prof, cfg)
            metrics.origin = cfg.chainage_origin
            profiles.append(metrics)
            meta.append((stem, k + 1))

    if cfg.group_rule == "spatial" and profiles:
        return _group_spatial(all_sections, profiles, meta, cfg, warnings)

    if cfg.group_rule == "spatial" and not profiles:
        warnings.append("未找到任何「纵断面」块，空间分组不可用，"
                        "已回退为按块出现顺序分组")

    return _group_by_order(loaded, profiles, meta, cfg, warnings)


def _chainage_of(prof: ProfileData, cfg: Config) -> list[float]:
    from .chainage import rebase_chainage
    return rebase_chainage(prof.dist, prof.z, cfg.chainage_origin)


def _group_spatial(all_sections: list[Section], profiles: list[ProfileData],
                   meta: list[tuple[str, int]], cfg: Config,
                   warnings: list[str]) -> list[ProfileLine]:
    """按横断面与纵断面相交关系分组（默认策略，不依赖块的出现顺序）。"""
    from .chainage import chainage_at_distance
    from .spatial import assign_by_intersection

    groups, details = assign_by_intersection(all_sections, profiles,
                                             cfg.intersection_tolerance)

    bases: list[str] = []
    entries: list[tuple[ProfileData, list[Section], list[float], list[float], str, int]] = []
    for pi, prof in enumerate(profiles):
        secs = [all_sections[si] for si in groups[pi]]
        ch = []
        dlist = []
        for si in groups[pi]:
            d = details[si]["dist"]
            dlist.append(d if d is not None else float("nan"))
            ch.append(chainage_at_distance(prof, d) if d is not None else float("nan"))
        # 按里程排序，得到沿河的真实顺序（chainage 与 profile_dist 一起排）
        order = sorted(range(len(secs)), key=lambda k: (ch[k] != ch[k], ch[k]))
        secs = [secs[k] for k in order]
        ch = [ch[k] for k in order]
        dlist = [dlist[k] for k in order]
        stem, k_in_file = meta[pi]
        bases.append(_base_name(secs, f"{stem}"))
        entries.append((prof, secs, ch, dlist, stem, k_in_file))

    names = _dedupe_names(bases)
    lines = [ProfileLine(name=names[i], sections=e[1], chainage=e[2],
                         order_source="spatial", profile=e[0],
                         profile_dist=e[3])
             for i, e in enumerate(entries)]

    unassigned = [all_sections[i].name for i, d in enumerate(details)
                  if d["profile"] is None]
    if unassigned:
        warnings.append(f"以下横断面与任何纵断面都不相交，未归入任何组：{unassigned}")
    conflicted = [(all_sections[i].name, d["conflicts"])
                  for i, d in enumerate(details) if d["conflicts"]]
    if conflicted:
        warnings.append(f"以下横断面同时与多条纵断面相交，已取最近的一条：{conflicted}")
    empty = [lines[i].name for i, g in enumerate(groups) if not g]
    if empty:
        warnings.append(f"以下纵断面线没有匹配到任何横断面（可能是多余的剖面块）：{empty}")

    return lines


def _group_by_order(loaded: list[tuple[str, ParsedBlocks]],
                    profiles: list[ProfileData], meta: list[tuple[str, int]],
                    cfg: Config, warnings: list[str]) -> list[ProfileLine]:
    """回退方案：按块的出现顺序关联——纵断面块管它后面出现的横断面，直到下一个纵断面块。"""
    from .chainage import compute_chainage, rebase_chainage
    from .terrain import analyze_terrain

    pairs: list[tuple[ProfileData | None, list[Section], str, int]] = []
    profile_seq = 0

    for stem, parsed in loaded:
        buckets: dict[int | None, list[Section]] = {}
        cur: int | None = None
        for kind, idx in parsed.seq:
            if kind == "profile":
                cur = idx
                buckets.setdefault(cur, [])
            else:
                buckets.setdefault(cur, []).append(parsed.sections[idx])

        for key in sorted(buckets, key=lambda k: (k is None, k if k is not None else -1)):
            secs = buckets[key]
            if not secs:
                continue
            prof = profiles[profile_seq] if key is not None else None
            if key is not None:
                profile_seq += 1
            pairs.append((prof, secs, stem, (key or 0) + 1))

    bases, entries = [], []
    for prof, secs, stem, k_in_file in pairs:
        ch: list[float] = []
        if prof is not None and prof.chainage:
            infos = [analyze_terrain(s, cfg) for s in secs]
            ch = compute_chainage(secs, infos, origin=cfg.chainage_origin)
            order = sorted(range(len(secs)), key=lambda k: ch[k])
            secs = [secs[k] for k in order]
            ch = [ch[k] for k in order]
        elif prof is not None:
            infos = [analyze_terrain(s, cfg) for s in secs]
            ch = compute_chainage(secs, infos, origin=cfg.chainage_origin)
        if prof is None:
            warnings.append(f"{stem}: 有一组横断面之前没有「{cfg.profile_block_name}」块，"
                            f"里程改用深泓点推算")
        bases.append(_base_name(secs, f"{stem}"))
        entries.append((prof, secs, ch))

    names = _dedupe_names(bases)
    return [ProfileLine(name=names[i], sections=e[1], chainage=e[2],
                        order_source="order", profile=e[0])
            for i, e in enumerate(entries)]


def _base_name(secs: list[Section], fallback: str) -> str:
    """纵断面线命名：优先取组内断面的公共前缀，否则用文件名。"""
    if not secs:
        return fallback
    prefixes = set()
    for s in secs:
        p = s.name.split("-")[0].split("_")[0]
        if p:
            prefixes.add(p)
    if len(prefixes) == 1:
        return prefixes.pop()
    return fallback


def _dedupe_names(bases: list[str]) -> list[str]:
    """同名的纵断面线加 -段N 后缀。

    一个文件里的多个「纵断面」块常常共用同一个断面编号前缀
    （sample_a.xlsx 的 11 个断面都叫 secA-N），必须能区分开。
    """
    counts: dict[str, int] = {}
    for b in bases:
        counts[b] = counts.get(b, 0) + 1
    seen: dict[str, int] = {}
    out: list[str] = []
    for b in bases:
        if counts[b] > 1:
            seen[b] = seen.get(b, 0) + 1
            out.append(f"{b}-段{seen[b]}")
        else:
            out.append(b)
    return out


def _group_pairs(sections: list[Section], cfg: Config) -> list[tuple[str, list[Section]]]:
    """复用 group_into_profile_lines 的分组逻辑，但返回 (线名, 断面列表) 对。"""
    return [(ln.name, ln.sections) for ln in group_into_profile_lines(sections, cfg)]
