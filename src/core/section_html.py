"""单断面「横断面预览」HTML 生成。

用途
----
给「断面起终点坐标及水位（带横断面预览）.csv」的 Comment 列用：每个断面一行，
第三列放一段**完整、自包含、单行**的 HTML，由支持富文本的查看端（网页、地图
气泡、Excel 富文本插件…）直接渲染。

三条硬约束
----------
1. **单行输出**。CSV 单元格里带换行虽然合法（引号包裹即可），但下游用简单
   `split(',')` 或正则解析的工具会直接崩。所以生成完统一把换行压掉——
   因此模板里的 JS **只能用 `/* */` 注释，不能用 `//`**（压成一行后 `//`
   会把后面整段代码全注释掉）。
2. **零外部依赖**。不引 CDN、不引字体、不引图标，只有一个 `<svg>` + 内联 JS，
   断网也能看。
3. **数字全部来自算法结果**，页面里不再重算曼宁/水位。设计水位、成灾水位、
   成灾流量都直接取 `SectionResult` / `TerrainInfo` 的值，避免"页面口径"
   与"程序口径"两套实现漂移。

与界面 `section_view.py` 的一致性
---------------------------------
配色与图元沿用界面：地面线灰、过水区蓝、设计水位蓝实线、成灾水位红点划线、
分区边界浅蓝虚线；深泓点圆点、岸顶三角、成灾点五角星。
"""

from __future__ import annotations

import html as _html
import json
import math
from typing import Any, Optional

from .config import Config
from .model import Section, SectionResult, TerrainInfo

#: 单行模板。`__XXX__` 为占位符，生成时整体替换。
#: ⚠ JS 里**不许出现 `//` 注释**，原因见模块说明第 1 条。
_TEMPLATE = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<title>断面形态 · __NAME__</title>
<style>
 html,body{margin:0;background:#fff;font:12px/1.5 "Microsoft YaHei",system-ui,sans-serif;color:#222}
 .w{padding:8px 10px}
 h1{font-size:14px;margin:0 0 2px;font-weight:600}
 .sub{color:#777;font-size:11px;margin-bottom:6px}
 svg{width:100%;height:auto;display:block}
 .v{display:flex;flex-direction:column;gap:3px;font-size:12px;color:#444;margin:2px 0 4px}
 .r{display:flex;flex-wrap:wrap;gap:18px;align-items:baseline}
 b{font-size:14px;color:#185FA5}
 .r.d b{color:#A32D2D}
 .lg{display:flex;flex-wrap:wrap;gap:10px;font-size:11px;color:#444;border-top:1px solid #eee;padding-top:6px;margin-top:4px}
 .lg span{display:flex;align-items:center;gap:4px}
 .ln{display:inline-block;flex:0 0 16px;width:16px;height:0;border-top:3px solid}
</style>
<div class="w">
 <h1>__NAME__</h1>
 <div class="sub">__SUB__</div>
 <div class="v"><div class="r" id="r1"></div><div class="r d" id="r2"></div></div>
 <svg id="sv" viewBox="0 0 900 420"></svg>
 <div class="lg">
  <span><i class="ln" style="border-color:#5F5E5A"></i>地面线</span>
  <span><i class="ln" style="border-color:#378ADD"></i>过水断面 / 设计水位</span>
  <span><i class="ln" style="border-color:#A32D2D;border-top-style:dashed"></i>成灾水位</span>
  <span><i class="ln" style="border-color:#85B7EB;border-top-style:dashed"></i>分区边界</span>
 </div>
</div>
<script>
var S=__S__,Z=__Z__,ZONES=__ZONES__,ZB=__ZB__;
var DI=__DI__,DISIDX=__DISIDX__;
var QDES=__QDES__,HDES=__HDES__,DIS=__DIS__,QDIS=__QDIS__;
var PKA=__PKA__,PKB=__PKB__;
var SMIN=__SMIN__,SMAX=__SMAX__,ZMIN=__ZMIN__,ZMAX=__ZMAX__;
var XT=__XT__,ZT=__ZT__;
var W=900,H=420,ML=58,MR=16,MT=12,MB=40;
var X0=ML,X1=W-MR,Y0=MT,Y1=H-MB;
function X(s){return X0+(s-SMIN)/(SMAX-SMIN)*(X1-X0);}
function Y(z){return Y1-(z-ZMIN)/(ZMAX-ZMIN)*(Y1-Y0);}
function F(n){return n.toFixed(1);}
function D(v,d){return (v===null||v!==v||v===undefined)?'—':v.toFixed(d);}
var bed='',i;
for(i=0;i<S.length;i++){bed+=(i?'L':'M')+F(X(S[i]))+','+F(Y(Z[i]));}
var bedFill=bed+'L'+F(X(S[S.length-1]))+','+Y1+'L'+F(X(S[0]))+','+Y1+'Z';
function wet(Hw){
  if(Hw===null||Hw!==Hw)return '';
  var L=null,R=null,j,t;
  for(j=DI;j>0;j--){if(Z[j]<=Hw&&Z[j-1]>Hw){t=(Hw-Z[j])/(Z[j-1]-Z[j]);L=S[j]+t*(S[j-1]-S[j]);break;}}
  for(j=DI;j<S.length-1;j++){if(Z[j]<=Hw&&Z[j+1]>Hw){t=(Hw-Z[j])/(Z[j+1]-Z[j]);R=S[j]+t*(S[j+1]-S[j]);break;}}
  if(L===null||R===null)return '';
  var d='M'+F(X(L))+','+F(Y(Hw));
  for(j=0;j<S.length;j++){if(Z[j]<=Hw&&S[j]>=L&&S[j]<=R)d+='L'+F(X(S[j]))+','+F(Y(Z[j]));}
  return d+'L'+F(X(R))+','+F(Y(Hw))+'Z';
}
function hl(z,c,dash,w){
  if(z===null||z!==z||z===undefined)return '';
  var da=dash?' stroke-dasharray="'+dash+'"':'';
  return '<line x1="'+X0+'" x2="'+X1+'" y1="'+F(Y(z))+'" y2="'+F(Y(z))+'" stroke="'+c+'" stroke-width="'+w+'"'+da+'/>';
}
function lab(s,z,t,c,dy){
  return '<text x="'+F(X(s))+'" y="'+F(Y(z)+dy)+'" fill="'+c+'" font-size="10" text-anchor="middle">'+t+'</text>';
}
function tri(s,z,c){
  return '<path d="M'+F(X(s)-5)+','+F(Y(z)+6)+'L'+F(X(s)+5)+','+F(Y(z)+6)+'L'+F(X(s))+','+F(Y(z)-2)+'Z" fill="'+c+'" stroke="#fff"/>';
}
(function(){
  var g=[];
  for(i=0;i<ZONES.length;i++){
    var a=ZONES[i][0],b=ZONES[i][1];
    g.push('<rect x="'+F(X(S[a]))+'" y="'+Y0+'" width="'+F(X(S[b])-X(S[a]))+'" height="'+(Y1-Y0)+'" fill="#B5D4F4" opacity="'+(i===1?0.10:0.16)+'"/>');
  }
  g.push('<path d="'+bedFill+'" fill="#EFEEEA"/>');
  var wp=wet(HDES);
  if(wp)g.push('<path d="'+wp+'" fill="#378ADD" fill-opacity="0.32"/>');
  g.push('<path d="'+bed+'" fill="none" stroke="#5F5E5A" stroke-width="1.6"/>');
  for(i=0;i<ZB.length;i++){
    g.push('<line x1="'+F(X(S[ZB[i]]))+'" x2="'+F(X(S[ZB[i]]))+'" y1="'+Y0+'" y2="'+Y1+'" stroke="#85B7EB" stroke-dasharray="4 4"/>');
  }
  g.push(hl(HDES,'#378ADD','',1.5));
  g.push(hl(DIS,'#A32D2D','9 4 2 4',1.2));
  for(i=0;i<XT.length;i++){
    g.push('<line x1="'+F(X(XT[i][0]))+'" x2="'+F(X(XT[i][0]))+'" y1="'+Y1+'" y2="'+(Y1+4)+'" stroke="#889"/>');
    g.push('<text x="'+F(X(XT[i][0]))+'" y="'+(Y1+16)+'" fill="#666" font-size="10" text-anchor="middle">'+XT[i][1]+'</text>');
  }
  for(i=0;i<ZT.length;i++){
    g.push('<line x1="'+(X0-4)+'" x2="'+X0+'" y1="'+F(Y(ZT[i][0]))+'" y2="'+F(Y(ZT[i][0]))+'" stroke="#889"/>');
    g.push('<text x="'+(X0-7)+'" y="'+F(Y(ZT[i][0])+3.5)+'" fill="#666" font-size="10" text-anchor="end">'+ZT[i][1]+'</text>');
  }
  g.push('<text x="'+(X0+(X1-X0)/2)+'" y="'+(H-6)+'" fill="#444" font-size="11" text-anchor="middle">起点距 (m)</text>');
  g.push('<text transform="translate(14,'+(Y0+(Y1-Y0)/2)+') rotate(-90)" fill="#444" font-size="11" text-anchor="middle">高程 (m)</text>');
  if(DI>=0&&DI<S.length){
    g.push('<circle cx="'+F(X(S[DI]))+'" cy="'+F(Y(Z[DI]))+'" r="4.5" fill="#993C1D" stroke="#fff"/>');
    g.push(lab(S[DI],Z[DI],'深泓点','#993C1D',16));
  }
  if(PKA){g.push(tri(PKA[0],PKA[1],'#854F0B'));g.push(lab(PKA[0],PKA[1],'左岸顶','#854F0B',-9));}
  if(PKB){g.push(tri(PKB[0],PKB[1],'#854F0B'));g.push(lab(PKB[0],PKB[1],'右岸顶','#854F0B',-9));}
  if(DISIDX>=0&&DISIDX<S.length&&DIS!==null&&DIS===DIS){
    g.push('<text x="'+F(X(S[DISIDX]))+'" y="'+F(Y(DIS)+5)+'" fill="#A32D2D" font-size="15" text-anchor="middle">★</text>');
    g.push(lab(S[DISIDX],DIS,'成灾点','#A32D2D',20));
  }
  g.push('<text x="'+(X1-2)+'" y="'+F(Y(HDES===null?ZMIN:HDES)-3)+'" fill="#185FA5" font-size="10" text-anchor="end">设计 '+D(HDES,3)+'</text>');
  g.push('<text x="'+(X1-2)+'" y="'+F(Y(DIS===null?ZMIN:DIS)-3)+'" fill="#A32D2D" font-size="10" text-anchor="end">成灾 '+D(DIS,3)+'</text>');
  document.getElementById('sv').innerHTML=g.join('');
  document.getElementById('r1').innerHTML='<span>设计水位 <b>'+D(HDES,3)+'</b> m</span><span>设计流量 <b>'+D(QDES,1)+'</b> m³/s</span>';
  document.getElementById('r2').innerHTML='<span>成灾水位 <b>'+D(DIS,3)+'</b> m</span><span>成灾流量 <b>'+D(QDIS,1)+'</b> m³/s</span>';
})();
</script>
</html>"""


def _num(v: Any) -> Optional[float]:
    """转成 float；NaN / None / 非数字一律返回 None（JSON 里写成 null）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                    # NaN
        return None
    return f


def _r(v: Any, nd: int = 6) -> Optional[float]:
    f = _num(v)
    return None if f is None else round(f, nd)


def _js(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _ticks(lo: float, hi: float, target: int = 8) -> tuple[list[float], float]:
    """在 [lo, hi] 里挑一个"好看"的刻度步长（1/2/2.5/5 × 10^n）。"""
    span = hi - lo
    if span <= 0:
        return [lo], 1.0
    raw = span / max(target, 1)
    mag = 10.0 ** math.floor(math.log10(raw))
    step = mag
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if m * mag >= raw:
            step = m * mag
            break
    v = math.ceil(lo / step) * step
    out: list[float] = []
    while v <= hi + 1e-9 and len(out) < 50:
        out.append(round(v, 6))
        v += step
    return out, step


def _tick_labels(vals: list[float], step: float) -> list[list[Any]]:
    """[(值, 文本)]，文本去掉多余小数位。"""
    nd = 0 if step >= 1 else (1 if step >= 0.1 else 2)
    return [[v, f"{v:.{nd}f}".rstrip("0").rstrip(".")] for v in vals]


def _norm_zones(info: TerrainInfo, n: int) -> tuple[list[list[int]], list[int]]:
    """把 `info.zones` 规整成"着色区间（闭区间节点下标）"与"分界线索引"。

    ⚠ 这里的 `stop` 是**节点开区间上界**，不是"最后一段的终点"：
      `_build_zones` 生成的是 `[(0, left+1), (left, right+1), (right, n)]`，
      即相邻分区**共享边界点**（点宽度为零，面积不重复）。
      所以某个分区的**最后一个节点**是 `stop - 1`——
      直接拿 `s[stop]` 当右端会把每一条色带都向右多画一段，
      相邻色带因此重叠、叠出深色（曾经就是这么错的）。

    另外 `stop` 常常等于点数 `n`，拿它索引 `s[stop]` 还会越界。
    """
    raw: list[tuple[int, int]] = []
    for z in (info.zones or []):
        try:
            a, b_excl = int(z[0]), int(z[1])
        except (TypeError, IndexError, ValueError):
            continue
        raw.append((max(0, min(a, n - 1)), b_excl))

    bands: list[list[int]] = []
    for a, b_excl in raw:
        b = max(0, min(b_excl - 1, n - 1))     # 闭区间右端 = stop - 1
        if b > a:
            bands.append([a, b])
    if not bands:
        bands = [[0, n - 1]] if n >= 2 else []

    # 分界线取各分区起点（共享边界点，天然不重复）；首尾两端不画线
    bounds = sorted({a for a, _ in raw[1:] if 0 < a < n - 1})
    return bands, bounds


def section_html(sec: Section, info: TerrainInfo, res: SectionResult,
                 cfg: Optional[Config] = None) -> str:
    """生成该断面的自包含预览 HTML（单行）。

    Parameters
    ----------
    sec : 断面（提供 s / z / name / design_q）
    info : 地形分析结果（深泓点、岸顶、分区、成灾水位）
    res : 计算结果（设计水位、成灾流量）
    cfg : 目前未使用，保留以与其它导出函数签名一致（坐标只影响前两列）
    """
    s = [float(v) for v in sec.s]
    z = [float(v) for v in sec.z]
    n = min(len(s), len(z))
    s, z = s[:n], z[:n]
    if n < 2:
        # 退化断面（少于两个测点）画不出线，返回一句提示而不是让他看到一张空图
        return ("<!doctype html><html><meta charset=\"utf-8\"><body style=\"font:12px "
                "'Microsoft YaHei',sans-serif\">断面「" + _html.escape(sec.name) +
                "」测点不足 2 个，无法绘制断面图。</body></html>")

    dmin_idx = info.dmin_idx if 0 <= info.dmin_idx < n else z.index(min(z))
    dis_idx = info.disaster_idx if 0 <= info.disaster_idx < n else -1

    zones, zone_bounds = _norm_zones(info, n)

    def peak(idx: int) -> Optional[list[float]]:
        return [s[idx], z[idx]] if 0 <= idx < n else None

    hdes = _r(res.design_level)
    dis = _r(info.disaster_level)
    qdes = _r(sec.params.design_q)
    qdis = _r(res.disaster_flow)

    smin, smax = min(s), max(s)
    zlo, zhi = min(z), max(z)
    for extra in (hdes, dis):
        if extra is not None:
            zlo, zhi = min(zlo, extra), max(zhi, extra)
    pad = max((zhi - zlo) * 0.08, 0.05)
    zmin, zmax = round(zlo - pad, 6), round(zhi + pad, 6)

    xv, xs_ = _ticks(smin, smax, 8)
    zv, zs_ = _ticks(zmin, zmax, 6)

    title = _html.escape(sec.name)
    sub = f"{n} 个测点 ｜ 起点距 {smin:g} ~ {smax:g} m"

    out = _TEMPLATE
    for token, val in (
        ("__NAME__", title),
        ("__SUB__", sub),
        ("__S__", _js([_r(v) for v in s])),
        ("__Z__", _js([_r(v) for v in z])),
        ("__ZONES__", _js(zones)),
        ("__ZB__", _js(zone_bounds)),
        ("__DI__", str(dmin_idx)),
        ("__DISIDX__", str(dis_idx)),
        ("__QDES__", _js(qdes)),
        ("__HDES__", _js(hdes)),
        ("__DIS__", _js(dis)),
        ("__QDIS__", _js(qdis)),
        ("__PKA__", _js(peak(info.zmax_idx))),
        ("__PKB__", _js(peak(info.ymax_idx))),
        ("__SMIN__", _js(round(smin, 6))),
        ("__SMAX__", _js(round(smax, 6))),
        ("__ZMIN__", _js(zmin)),
        ("__ZMAX__", _js(zmax)),
        ("__XT__", _js(_tick_labels(xv, xs_))),
        ("__ZT__", _js(_tick_labels(zv, zs_))),
    ):
        out = out.replace(token, val)
    # 压成单行：下游用简单解析的工具（按 , 切分）才不会被单元格里的换行坑到
    return out.replace("\r", "").replace("\n", "")
