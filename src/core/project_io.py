"""工程文件（`.dmprj`）的保存与载入。

格式
----
单个 **JSON 文本**，UTF-8 带 BOM。选它的理由：

- **纯标准库**（json），零依赖，不影响 core 层的依赖约束
- **可读**：出问题时能用记事本直接看；也便于版本对比
- 体积可控：31 个断面 + 8 条纵剖面约 100~200 KB

**明确不用 pickle**：工程文件会被转发给同事，而 pickle 反序列化
会执行任意代码，属于真实的安全风险。

保存内容（全量快照）
----------------
- 每条纵断面线：名称、断面顺序、里程、在纵断面上的原始起点距
- 纵剖面实测数据（x / y / 起点距 / 高程）
- 每个横断面的四列几何数据（x / y / 起点距 / 高程）
- 每个断面的参数（比降 / 糙率 / 设计流量 / 分区糙率）
- 每个断面的**手动覆盖**（深泓点 / 分区边界 / 成灾水位，v2 起）
- 全部计算设置（Config）
- 数据来源（目录 + 文件名），仅作记录，**打开时不依赖它**

**刻意不保存计算结果**（TerrainInfo / SectionResult）：它们完全由
数据 + 设置决定，存下来既会让文件膨胀，又可能与重算结果不一致。
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from typing import Optional

from .config import Config
from .model import ProfileData, ProfileLine, Project, Section, SectionParams

FORMAT_ID = "duanmian-project"
# v2：断面新增「手动覆盖」字段（深泓点 / 分区边界 / 成灾水位），见 model.Section。
#     读取时按"缺字段即全自动"处理，所以 v1 文件仍能正常打开（向后兼容）。
#     反向（v1 程序打开 v2 文件）会被版本检查挡下，避免静默丢掉人工设定。
FORMAT_VERSION = 2

SUFFIX = ".dmprj"
FILE_FILTER = "断面工程文件 (*.dmprj);;所有文件 (*)"


# ---------------------------------------------------------------- 辅助转换

def _f(v) -> Optional[float]:
    """浮点 -> JSON 可存的数；NaN / Inf 存成 null（JSON 不支持非有限数）。"""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _nan(v) -> float:
    """JSON 读回：null -> NaN（表示「未填写」）。"""
    if v is None:
        return float("nan")
    return float(v)


def _opt(v) -> Optional[float]:
    """分区糙率用：null 保持 None，语义是「留空、回退统一糙率」。

    注意与 `_nan` 的区别——那两个字段的 null 含义不同：
        比降/糙率/设计流量的 null = 未填写（读回 NaN）
        分区糙率的 null         = 留空（读回 None）
    """
    return None if v is None else float(v)


def _flist(vals) -> list:
    """浮点数组 -> JSON 数组，NaN/Inf 存 null。"""
    return [_f(v) for v in vals]


def _oint(v) -> Optional[int]:
    """整数型覆盖字段（测点索引）：null -> None，语义是「未人工干预」。

    ⚠ 不要用 `_opt`（它 float() 一下），那会把索引 3 变成 3.0，
      而 terrain 里判定合法性用的是 `isinstance(idx, int)`，
      3.0 会被判成非法、静默退回自动 —— 手动设置看起来"存进去了但没生效"。
    """
    if v is None or isinstance(v, bool):
        return None                     # bool 也是 int，但绝不该变成"第 2 个测点"
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _obool(v) -> bool:
    """布尔覆盖字段：只有 JSON 的 true 才算 True。

    不用 `bool(v)`：万一文件里写成字符串 "false"（手工编辑常犯），
    `bool("false")` 是 True，会把"不分区"反过来变成"手动分区"。
    """
    return v is True


def _flist_back(vals) -> list[float]:
    """JSON 数组 -> 浮点数组，null 读回 NaN。"""
    return [_nan(v) for v in (vals or [])]


# ---------------------------------------------------------------- 序列化

def _params_to_dict(p: SectionParams) -> dict:
    return {
        "name": p.name,
        "slope": _f(p.slope),
        "roughness": _f(p.roughness),
        "design_q": _f(p.design_q),
        "roughness_main": _opt(p.roughness_main),
        "roughness_left": _opt(p.roughness_left),
        "roughness_right": _opt(p.roughness_right),
    }


def _params_from_dict(d: dict) -> SectionParams:
    d = d or {}
    return SectionParams(
        name=d.get("name", ""),
        slope=_nan(d.get("slope")),
        roughness=_nan(d.get("roughness")),
        design_q=_nan(d.get("design_q")),
        roughness_main=_opt(d.get("roughness_main")),
        roughness_left=_opt(d.get("roughness_left")),
        roughness_right=_opt(d.get("roughness_right")),
    )


def _section_to_dict(s: Section) -> dict:
    return {
        "name": s.name,
        "x": _flist(s.x),
        "y": _flist(s.y),
        "s": _flist(s.s),
        "z": _flist(s.z),
        "params": _params_to_dict(s.params),
        # ---- 手动覆盖（v2）----
        # 这里每个 null 都只有一个含义：「未人工干预」，读回 None。
        # 注意与另外两种 null 区分：
        #   比降/糙率/设计流量 null = 未填写（读回 NaN）
        #   分区糙率        null = 留空、回退统一糙率（读回 None）
        #   覆盖字段        null = 未人工干预（读回 None）
        "thalweg_manual": _oint(s.thalweg_manual),
        "zone_manual": bool(s.zone_manual),
        "zone_left": _oint(s.zone_left),
        "zone_right": _oint(s.zone_right),
        "disaster_idx_manual": _oint(s.disaster_idx_manual),
    }


def _section_from_dict(d: dict) -> Section:
    return Section(
        name=d.get("name", ""),
        x=_flist_back(d.get("x")),
        y=_flist_back(d.get("y")),
        s=_flist_back(d.get("s")),
        z=_flist_back(d.get("z")),
        params=_params_from_dict(d.get("params")),
        # v1 文件没有这几个键 -> 全部读成 None / False，即"全自动"，符合预期
        thalweg_manual=_oint(d.get("thalweg_manual")),
        zone_manual=_obool(d.get("zone_manual")),
        zone_left=_oint(d.get("zone_left")),
        zone_right=_oint(d.get("zone_right")),
        disaster_idx_manual=_oint(d.get("disaster_idx_manual")),
    )


def _profile_to_dict(p: Optional[ProfileData]) -> Optional[dict]:
    if p is None:
        return None
    return {
        "x": _flist(p.x),
        "y": _flist(p.y),
        "dist": _flist(p.dist),
        "z": _flist(p.z),
        "chainage": _flist(p.chainage),
        "origin": p.origin,
    }


def _profile_from_dict(d: Optional[dict]) -> Optional[ProfileData]:
    if not d:
        return None
    return ProfileData(
        x=_flist_back(d.get("x")),
        y=_flist_back(d.get("y")),
        dist=_flist_back(d.get("dist")),
        z=_flist_back(d.get("z")),
        chainage=_flist_back(d.get("chainage")),
        origin=d.get("origin", "lowest"),
    )


# ---------------------------------------------------------------- 对外接口

def project_to_dict(project: Project, cfg: Config,
                    source: Optional[dict] = None) -> dict:
    """把工程 + 配置拍成可直接 json.dump 的字典。"""
    lines = []
    for ln in project.profile_lines:
        lines.append({
            "name": ln.name,
            "order_source": ln.order_source,
            "chainage": _flist(ln.chainage),
            "profile_dist": _flist(ln.profile_dist),
            "profile": _profile_to_dict(ln.profile),
            "sections": [_section_to_dict(s) for s in ln.sections],
        })

    return {
        "format": FORMAT_ID,
        "version": FORMAT_VERSION,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source or {},
        "config": cfg.to_dict(),
        "profile_lines": lines,
    }


def dict_to_project(data: dict) -> tuple[Project, Config, dict]:
    """从字典还原 (工程, 配置, 元信息)。

    元信息含 format / version / saved_at / source，供界面显示数据来源。
    """
    if not isinstance(data, dict):
        raise ValueError("工程文件内容不是对象")
    fmt = data.get("format")
    if fmt != FORMAT_ID:
        raise ValueError(f"这不像是本程序的工程文件（format={fmt!r}，"
                         f"期望 {FORMAT_ID!r}）")
    version = data.get("version", 0)
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise ValueError(f"工程文件版本 {version} 高于本程序支持的 "
                         f"{FORMAT_VERSION}，请升级程序后再打开")

    lines: list[ProfileLine] = []
    for ld in data.get("profile_lines") or []:
        secs = [_section_from_dict(s) for s in (ld.get("sections") or [])]
        lines.append(ProfileLine(
            name=ld.get("name", ""),
            sections=secs,
            chainage=_flist_back(ld.get("chainage")),
            order_source=ld.get("order_source", "file"),
            profile=_profile_from_dict(ld.get("profile")),
            profile_dist=_flist_back(ld.get("profile_dist")),
        ))

    # strict=False：容忍「更新版本写出、本版本不认识的字段」，
    # 这样旧程序打开新文件不会因为多了个设置项就整份打不开。
    cfg = Config.from_dict(data.get("config") or {}, strict=False)

    meta = {
        "format": fmt,
        "version": version,
        "saved_at": data.get("saved_at", ""),
        "source": data.get("source") or {},
    }
    return Project(profile_lines=lines), cfg, meta


def save_project(path: str, project: Project, cfg: Config,
                 source: Optional[dict] = None) -> None:
    """保存工程到 `path`。

    编码用 utf-8-sig（带 BOM）：与导出的 CSV 保持一致，
    用记事本打开或另存时不会被误判成 GBK 而显示乱码。
    """
    data = project_to_dict(project, cfg, source)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(text)


def load_project(path: str) -> tuple[Project, Config, dict]:
    """载入工程文件，返回 (工程, 配置, 元信息)。"""
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"工程文件不是合法 JSON（第 {e.lineno} 行）：{e.msg}") from e
    return dict_to_project(data)


def describe_source(meta: dict) -> str:
    """把元信息里的数据来源描述成一句话，供状态栏/标题显示。"""
    src = meta.get("source") or {}
    files = src.get("files") or []
    folder = src.get("dir") or ""
    if not files and not folder:
        return ""
    names = "、".join(files[:3]) + ("…" if len(files) > 3 else "")
    when = meta.get("saved_at") or ""
    txt = f"数据来源：{folder}"
    if names:
        txt += f"（{len(files)} 个文件：{names}）"
    if when:
        txt += f"　保存于 {when}"
    return txt


def default_project_name(project: Project) -> str:
    """没指定文件名时，用第一条纵断面线命名，如 `secB.dmprj`。"""
    if project.profile_lines:
        return f"{project.profile_lines[0].name}{SUFFIX}"
    return f"未命名工程{SUFFIX}"


def suggest_path(project: Project, folder: str) -> str:
    return os.path.join(folder, default_project_name(project))
