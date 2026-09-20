"""断面参数的手动填写与批量修改（Q12）。

当前阶段参数集 xlsx 不提供，改为在界面里手工填写；后期接 Excel 导入后，
同样走这里的 set_one / apply_batch，且导入后仍可单个或批量覆盖。

设计要点：
    1. 所有修改都作用在 Section.params 上，不另存副本，避免两处不同步
    2. apply_batch 支持按纵断面线筛选，满足"整条河统一改糙率"这类常见操作
    3. to_rows / from_rows 提供与 Excel 的双向转换，便于后期导入导出
"""

from __future__ import annotations

from typing import Iterable, Optional

from .model import ProfileLine, Section, SectionParams

# 可在界面上编辑的字段及其中文名
EDITABLE_FIELDS = {
    "slope": "比降",
    "roughness": "糙率",
    "design_q": "设计流量 Qs",
    "roughness_main": "主槽糙率",
    "roughness_left": "左滩糙率",
    "roughness_right": "右滩糙率",
}


def ensure_params(sec: Section) -> SectionParams:
    """保证断面有参数对象（名字为空则补上）。"""
    if sec.params is None:
        sec.params = SectionParams(name=sec.name)
    elif not sec.params.name:
        sec.params.name = sec.name
    return sec.params


def set_one(sec: Section, **fields) -> None:
    """修改单个断面的若干字段。未知字段直接抛错，避免静默写错。"""
    p = ensure_params(sec)
    for k, v in fields.items():
        if k not in EDITABLE_FIELDS:
            raise KeyError(f"不可编辑的字段: {k}（可选 {sorted(EDITABLE_FIELDS)}）")
        setattr(p, k, v)


def apply_batch(sections: Iterable[Section], only_missing: bool = False,
                **fields) -> int:
    """批量修改，返回实际改动的断面数。

    only_missing=True 时只填补尚未填写的字段（NaN 或 None），不覆盖已有值，
    适合"先批量填个默认值，再逐个精调"的用法。
    """
    n = 0
    for sec in sections:
        p = ensure_params(sec)
        changed = False
        for k, v in fields.items():
            if k not in EDITABLE_FIELDS:
                raise KeyError(f"不可编辑的字段: {k}")
            if only_missing:
                cur = getattr(p, k)
                if cur is not None and cur == cur:      # 非 None 且非 NaN
                    continue
            setattr(p, k, v)
            changed = True
        n += 1 if changed else 0
    return n


def apply_batch_to_line(line: ProfileLine, only_missing: bool = False,
                        **fields) -> int:
    """对一条纵断面线上的所有断面批量修改。"""
    return apply_batch(line.sections, only_missing=only_missing, **fields)


def missing_params(sections: Iterable[Section]) -> list[str]:
    """返回参数尚未填全的断面名列表。"""
    out = []
    for sec in sections:
        if sec.validate():
            out.append(sec.name)
    return out


def to_rows(sections: Iterable[Section],
            include_zone_roughness: bool = True) -> list[list]:
    """导出为表格行，供写入 Excel / CSV。

    列顺序与 parse_params 一致，可直接回灌：
        断面编号, 比降, 糙率, 设计流量, 主槽糙率, 左滩糙率, 右滩糙率
    """
    rows = [["断面编号", "比降", "糙率", "设计流量",
             "主槽糙率", "左滩糙率", "右滩糙率"]]
    for sec in sections:
        p = ensure_params(sec)
        row = [sec.name, p.slope, p.roughness, p.design_q]
        if include_zone_roughness:
            row += [p.roughness_main, p.roughness_left, p.roughness_right]
        rows.append(row)
    return rows


def from_rows(rows: list[list]) -> dict[str, SectionParams]:
    """从表格行解析（跳过表头）。列顺序同 to_rows。"""
    from .reader import parse_params
    return parse_params(rows)


def merge(base: dict[str, SectionParams],
          override: dict[str, SectionParams]) -> dict[str, SectionParams]:
    """合并两份参数，override 优先；override 中留空的字段沿用 base。

    用于"Excel 导入后再手工改几个"的场景。
    """
    out = dict(base)
    for name, ov in override.items():
        if name not in out:
            out[name] = ov
            continue
        bs = out[name]
        for f in EDITABLE_FIELDS:
            v = getattr(ov, f)
            if v is None or v != v:      # None 或 NaN 视为未填
                continue
            setattr(bs, f, v)
    return out


def apply_dict(sections: Iterable[Section],
               params: dict[str, SectionParams]) -> list[str]:
    """把参数字典挂到断面上，返回未匹配上的断面名。"""
    missing = []
    for sec in sections:
        p = params.get(sec.name)
        if p is None:
            missing.append(sec.name)
            continue
        sec.params = p
    return missing
