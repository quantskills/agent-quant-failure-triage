#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单文件 HTML 分诊报告：暗色玻璃拟态 + Chart.js（cdnjs）。

内嵌全部数据，打开即用；无网络时图表不显示但文字与表格完整。
"""
from __future__ import annotations

import json
from pathlib import Path

CHART_CDN = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"

STATUS_COLOR = {"healthy": "#4fd1a6", "watch": "#ffce6b", "warning": "#ff8f5d", "failed": "#ff5d6c", "insufficient": "#8b93a7"}
STATUS_ZH = {"healthy": "健康", "watch": "观察", "warning": "警告", "failed": "失效", "insufficient": "证据不足"}

_TPL = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>失效分诊 · __OBJ__</title>
<script src="__CDN__"></script>
<style>
:root{--bg:#0b0e14;--card:rgba(255,255,255,.045);--line:rgba(255,255,255,.09);--txt:#e6e9ef;--mut:#8b93a7;
--red:#ff5d6c;--org:#ff8f5d;--gold:#ffce6b;--green:#4fd1a6;--blue:#6ba8ff;--purple:#b794ff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1200px 600px at 75% -10%,#1a1424,#0b0e14);color:var(--txt);
font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",Segoe UI,Roboto,sans-serif;padding:28px 22px 60px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:26px 0 10px;color:var(--mut);letter-spacing:.06em;text-transform:uppercase}
.sub{color:var(--mut);font-size:13px}.grid{display:grid;gap:14px}.g2{grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;backdrop-filter:blur(10px)}
.verdict{border-left:4px solid var(--org)}.verdict .cause{font-size:20px;font-weight:600}.kind{display:inline-block;padding:1px 8px;border-radius:999px;
border:1px solid var(--line);font-size:12px;color:var(--mut);margin-left:8px}.gate{display:inline-block;padding:3px 10px;border-radius:8px;font-weight:600;margin-top:8px}
.gate.forbidden{background:rgba(255,93,108,.15);color:var(--red)}.gate.allowed{background:rgba(79,209,166,.15);color:var(--green)}.gate.hold{background:rgba(255,206,107,.15);color:var(--gold)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--mut);font-weight:500}td.num{text-align:right;font-variant-numeric:tabular-nums}.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;color:#0b0e14;font-weight:600}
.plain{color:var(--mut)}.kv{display:flex;flex-wrap:wrap;gap:8px 18px}.kv div{font-size:13px}.kv b{color:var(--txt)}
canvas{max-height:300px}.small{font-size:12px;color:var(--mut)}ul{margin:6px 0 0 18px;padding:0}li{margin:2px 0}
.disc{margin-top:36px;padding:14px 18px;border:1px dashed var(--line);border-radius:12px;color:var(--mut);font-size:12px}
</style></head><body>
<h1>失效分诊 · __OBJ__ <span class="kind">__OTYPE__</span></h1>
<div class="sub">观察窗 __OBS__ ｜ 基准窗 __BASE__ ｜ 数据源 __SRC__ ｜ 生成 __TS__</div>

<div class="grid g2" style="margin-top:18px">
  <div class="card verdict">
    <div class="cause">__CAUSE__<span class="kind">__CKIND__</span></div>
    <div class="plain" style="margin-top:4px">__CDESC__</div>
    <div class="gate __GATE__">__GATETXT__</div>
    <div class="small" style="margin-top:6px">__GATEREASON__</div>
    <h2>处置</h2><div>__ACTIONS__</div>
    <h2>证伪条件</h2><div class="plain">__FALSIFY__</div>
    <h2>复核触发</h2><ul>__RECHECK__</ul>
  </div>
  <div class="card">
    <h2 style="margin-top:0">观察窗表现</h2>
    <div class="kv">__PERF__</div>
    <canvas id="nav"></canvas>
    <div class="small">反事实变体：完整策略 / 去择时 / 去风控 / 理想成交 / 纯信号（因子对象只有纯信号）</div>
  </div>
</div>

<h2>分层剥离 · 各层对观察窗收益的贡献</h2>
<div class="grid g2">
  <div class="card"><canvas id="waterfall"></canvas><div class="small">贡献口径：信号 = 纯信号变体的超额；风格 = 其中由风格暴露解释的部分；择时 / 风控 / 执行 = 含该层 − 去掉该层的反事实差值。三组口径不同，不能相加。</div></div>
  <div class="card"><canvas id="conf"></canvas><div class="small">死因置信度（交叉裁决后）</div></div>
</div>

<h2>逐层读数（学术读数 → 通俗解读）</h2>
<div class="card"><table><thead><tr><th>层</th><th>判定</th><th class="num">贡献</th><th>读数</th></tr></thead><tbody>__ROWS__</tbody></table></div>

<div class="grid g2" style="margin-top:14px">
  <div class="card"><h2 style="margin-top:0">信号预测力 · 滚动 Rank IC</h2><canvas id="ic"></canvas></div>
  <div class="card"><h2 style="margin-top:0">风格主动暴露（观察窗均值）</h2><canvas id="style"></canvas><div class="small">__STYLEENV__</div></div>
</div>

<h2>环境刻画 · 这套打法靠什么吃饭，那个东西还在不在</h2>
<div class="card"><table><thead><tr><th>环境量</th><th class="num">基准窗</th><th class="num">观察窗</th></tr></thead><tbody>__ENVROWS__</tbody></table>
<div class="small" style="margin-top:6px">策略家族：__FAMILY__。先问环境再问策略：事前算是环境刻画，事后算是找借口。</div></div>

<h2>术语表</h2>
<div class="card"><table><tbody>__GLOSS__</tbody></table></div>

<div class="disc">QuantSkills Community Project · agent-quant-failure-triage。本报告是研究诊断与处置建议，不是交易指令；不构成投资建议，不承诺收益。
反事实剥离假设各层规则可独立替换，风格暴露用线性回归近似；成本、涨跌停口径见 references。数据来源：__SRC__。</div>

<script>
const D = __DATA__;
const has = typeof Chart !== 'undefined';
function line(id, labels, series){ if(!has) return; new Chart(document.getElementById(id),{type:'line',data:{labels,datasets:series.map(s=>({label:s.name,data:s.data,borderColor:s.color,borderWidth:s.w||1.6,pointRadius:0,tension:.2,borderDash:s.dash||[]}))},
 options:{plugins:{legend:{labels:{color:'#c9cfdb'}}},scales:{x:{ticks:{color:'#8b93a7',maxTicksLimit:8},grid:{color:'rgba(255,255,255,.05)'}},y:{ticks:{color:'#8b93a7'},grid:{color:'rgba(255,255,255,.05)'}}}}}); }
if(D.nav){ line('nav', D.nav.labels, D.nav.series); }
if(has && D.waterfall){ new Chart(document.getElementById('waterfall'),{type:'bar',data:{labels:D.waterfall.labels,datasets:[{label:'贡献 (%)',data:D.waterfall.values,backgroundColor:D.waterfall.colors,borderRadius:6}]},
 options:{indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8b93a7'},grid:{color:'rgba(255,255,255,.06)'}},y:{ticks:{color:'#c9cfdb'},grid:{display:false}}}}}); }
if(has && D.conf){ new Chart(document.getElementById('conf'),{type:'bar',data:{labels:D.conf.labels,datasets:[{label:'置信度',data:D.conf.values,backgroundColor:'#b794ff',borderRadius:6}]},
 options:{plugins:{legend:{display:false}},scales:{y:{min:0,max:1,ticks:{color:'#8b93a7'},grid:{color:'rgba(255,255,255,.06)'}},x:{ticks:{color:'#c9cfdb'},grid:{display:false}}}}}); }
if(D.ic){ line('ic', D.ic.labels, D.ic.series); }
if(has && D.style){ new Chart(document.getElementById('style'),{type:'bar',data:{labels:D.style.labels,datasets:[{label:'主动暴露',data:D.style.values,backgroundColor:D.style.values.map(v=>v<0?'#6ba8ff':'#ff8f5d'),borderRadius:6}]},
 options:{plugins:{legend:{display:false}},scales:{y:{ticks:{color:'#8b93a7'},grid:{color:'rgba(255,255,255,.06)'}},x:{ticks:{color:'#c9cfdb'},grid:{display:false}}}}}); }
</script></body></html>"""


def _fmt_pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def render_html(report: dict, out_path: str | Path) -> Path:
    from common import GLOSSARY
    L, v = report["layers"], report["verdict"]
    order = ["data", "L0", "L1", "L2", "crowd", "L3", "L4", "L5"]
    rows = []
    for k in order:
        lay = L.get(k)
        if not lay:
            continue
        col = STATUS_COLOR[lay["status"]]
        rows.append(f"<tr><td><b>{lay['name']}</b></td><td><span class='pill' style='background:{col}'>{STATUS_ZH[lay['status']]}</span></td>"
                    f"<td class='num'>{_fmt_pct(lay['contribution'])}</td><td>{lay['academic']}<br><span class='plain'>→ {lay['plain']}</span>"
                    + ("".join(f"<br><span class='small'>· {e}</span>" for e in lay.get('evidence', [])) if lay.get('evidence') else "") + "</td></tr>")
    wf = [(L[k]["name"], L[k]["contribution"]) for k in order if k in L and L[k]["contribution"] is not None]
    data = {
        "waterfall": {"labels": [n for n, _ in wf], "values": [round(c * 100, 2) for _, c in wf],
                      "colors": ["#ff5d6c" if c < 0 else "#4fd1a6" for _, c in wf]} if wf else None,
        "conf": {"labels": [v and __import__("verdict").CAUSE_LABEL[k][0] for k in v["confidence"]], "values": [round(x, 2) for x in v["confidence"].values()]} if v.get("confidence") else None,
    }
    nav = report.get("nav_curves")
    if nav:
        colors = {"full": "#e6e9ef", "no_timing": "#6ba8ff", "no_stop": "#ffce6b", "ideal_fill": "#4fd1a6", "signal_only": "#b794ff", "benchmark": "#8b93a7"}
        names = {"full": "完整策略", "no_timing": "去择时", "no_stop": "去风控", "ideal_fill": "理想成交", "signal_only": "纯信号", "benchmark": "基准"}
        data["nav"] = {"labels": nav["labels"], "series": [{"name": names.get(k, k), "data": s, "color": colors.get(k, "#fff"), "w": 2.2 if k == "full" else 1.4, "dash": [4, 3] if k == "benchmark" else []} for k, s in nav["series"].items()]}
    ic = L.get("L1", {}).get("metrics", {}).get("ic_series")
    if ic:
        import pandas as pd
        s = pd.Series(ic)
        roll = s.rolling(20, min_periods=5).mean()
        data["ic"] = {"labels": list(s.index), "series": [{"name": "日 Rank IC", "data": [round(x, 4) for x in s.values], "color": "rgba(107,168,255,.35)", "w": 1},
                                                          {"name": "20 日均值", "data": [None if pd.isna(x) else round(x, 4) for x in roll.values], "color": "#ffce6b", "w": 2}]}
    sty = L.get("L2", {}).get("metrics", {}).get("active_exposure_mean")
    if sty:
        data["style"] = {"labels": list(sty.keys()), "values": [round(x, 3) for x in sty.values()]}
    env = report.get("environment", {})
    env_rows = "".join(f"<tr><td>{r['metric']}</td><td class='num'>{'' if r['base'] is None else round(r['base'], 4)}</td><td class='num'>{'' if r['obs'] is None else round(r['obs'], 4)}</td></tr>" for r in env.get("rows", [])) or "<tr><td colspan=3 class='small'>无环境数据</td></tr>"
    perf = report.get("performance", {})
    perf_html = "".join(f"<div>{k}<br><b>{val}</b></div>" for k, val in perf.items())
    gate_txt = {"forbidden": "❌ 禁止重训", "allowed": "✅ 允许重训", "hold": "⏸ 暂不重训"}[v["retrain_gate"]]
    style_env = L.get("L2", {}).get("evidence", [])
    html = (_TPL.replace("__CDN__", CHART_CDN).replace("__OBJ__", report["object_name"]).replace("__OTYPE__", "因子" if report["object_type"] == "factor" else "策略")
            .replace("__OBS__", f"{report['obs_start']} ~ {report['obs_end']}").replace("__BASE__", f"{report['base_start']} ~ {report['base_end']}")
            .replace("__SRC__", report.get("data_source", "")).replace("__TS__", report.get("generated_at", ""))
            .replace("__CAUSE__", v["cause_label"]).replace("__CKIND__", v["cause_kind"]).replace("__CDESC__", v["cause_desc"])
            .replace("__GATE__", v["retrain_gate"]).replace("__GATETXT__", gate_txt).replace("__GATEREASON__", v["retrain_reason"])
            .replace("__ACTIONS__", " → ".join(v["action_labels"])).replace("__FALSIFY__", v["falsification"])
            .replace("__RECHECK__", "".join(f"<li>{r}</li>" for r in v["recheck_triggers"]))
            .replace("__PERF__", perf_html).replace("__ROWS__", "".join(rows)).replace("__ENVROWS__", env_rows).replace("__FAMILY__", env.get("family", "cross_sectional"))
            .replace("__STYLEENV__", "<br>".join(style_env)).replace("__GLOSS__", "".join(f"<tr><td><b>{k}</b></td><td class='plain'>{val}</td></tr>" for k, val in GLOSSARY.items()))
            .replace("__DATA__", json.dumps(data, ensure_ascii=False)))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
