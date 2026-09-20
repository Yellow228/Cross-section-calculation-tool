"""Excel 解析：断面数据 + 参数集。对应原 MATLAB transfer.m 开头部分。

⚠ 本模块依赖 openpyxl，需先安装依赖。

块结构（已用真实样本验证，块与块之间**无空行**）：
    行 N    : 断面编号 | yqc6-1          ← 标题行
    行 N+1  : X坐标 | Y坐标 | 起点距 | 高程   ← 列头
    行 N+2 …: 数据
    行 M    : 断面编号 | yqc6-2          ← 下一块紧贴上一块最后一行

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
from dataclasses import dataclass, field
from typing import Optional

from .config import Config
from .model import (ProfileData, ProfileLine, Project, Section, SectionParams)


@dataclass
class Block:
    name: str
    rows: list[list] = field(default_factory=list)
    kind: str = "skip"          # cross / profile / skip


@dataclass
class ParsedBlocks:
    """一个文件的解析结果。

    profiles 是**列表**：一个文件可以有多个「纵断面」块
    （2三凌山.xlsx 有 3 个，铜山溪沟7 有 2 个），每个代表该文件里的一条纵断面线。
    只保留一个会把其余的静默丢掉。

    seq 记录块的原始出现顺序，形如 [("profile", 0), ("cross", 0), ("cross", 1), ...]，
    用于在缺少纵断面块时按顺序回退分组，以及给重名的纵断面线编号。
    """
    sections: list[Section] = field(default_factory=list)
    profiles: list[ProfileData] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    seq: list[tuple[str, int]] = field(default_factory=list)


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
      老文件叫「纵断面」，新文件（2三凌山 / 铜山溪沟7）叫「纵断面1」「纵断面2」
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
        blocks.append(Block(name=name, rows=rows, kind=classify(name, cfg)))

    return blocks


def _rows_to_arrays(rows: list[list], cfg: Config
                    ) -> tuple[list[float], list[float], list[float], list[float]]:
    """把数据行转成 x, y, s, z 四列，跳过非数值行。"""
    xs, ys, ss, zs = [], [], [], []
    for row in rows:
        if len(row) < 4:
            continue
        s_v = _to_float(row[2])
        z_v = _to_float(row[3])
        if s_v is None or z_v is None:
            continue
        if cfg.swap_xy:
            x_v, y_v = _to_float(row[1]), _to_float(row[0])
        else:
            x_v, y_v = _to_float(row[0]), _to_float(row[1])
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


def load_file(path: str, cfg: Config, sheet_name: Optional[str] = None) -> ParsedBlocks:
    """载入单个文件。一个文件可能含多个「纵断面」块，全部保留。"""
    raw = read_sheet_rows(path, sheet_name)
    blocks = split_blocks(raw, cfg)
    return blocks_to_sections(blocks, cfg)


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
                param_path: Optional[str] = None
                ) -> tuple[Project, list[str]]:
    """批量读取整个文件夹下的所有 xlsx。

    一个文件可含**多个「纵断面」块**（如 2三凌山.xlsx 有 3 个），
    每个块代表一条纵断面线，全部作为独立的分组目标参与判定。

    分组策略由 cfg.group_rule 决定：
        "spatial"（默认）—— 按横断面与纵断面在平面上是否相交分组
        其余            —— 按块的出现顺序分组（纵断面块管它后面的横断面）
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
            parsed = load_file(path, cfg)
        except Exception as e:
            warnings.append(f"{fn}: 读取失败 - {e}")
            continue
        if not parsed.sections:
            warnings.append(f"{fn}: 未解析到有效横断面")
            continue
        if parsed.skipped:
            warnings.append(f"{fn}: 已排除非编号块 {parsed.skipped}")
        if len(parsed.profiles) > 1:
            warnings.append(f"{fn}: 含 {len(parsed.profiles)} 个「"
                            f"{cfg.profile_block_name}」块，将分别作为独立纵断面线")
        loaded.append((stem, parsed))

    if not loaded:
        raise ValueError(f"{folder} 下没有可解析的 xlsx 断面文件")

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
    （2三凌山.xlsx 的 11 个断面都叫 sls2-N），必须能区分开。
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
