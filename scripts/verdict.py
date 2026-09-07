#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交叉裁决：五类死因 → 内因/外因 → 处置 → 重训禁令 → 证伪条件 → 复核触发。

裁决顺序（因果优先，不投票）
1. 数据层不可信（分布漂移 failed）→ 一切下游读数不可信 → 冻结，不再往下判。
2. 外因（L2 风格 / 拥挤）能解释大部分损失 → "市场变了" → 只允许 等待 / 对冲 / 降仓。
3. 组件层（L0 / L3 / L4 / L5）反事实贡献显著为负 → "组件失效" → 只改那一层，信号层不动。
4. 以上都排除 → L1 概念漂移（翻符号 + 变点确认）→ "信号坏了" → 允许重训 / 退役。
5. 都不成立 → 健康 或 证据不足。
"""
from __future__ import annotations

from typing import Any

CAUSE_LABEL = {
    "distribution_shift": ("分布漂移", "外因", "输入分布进入训练集没见过的区域，预测不可信"),
    "style_regime": ("风格环境断裂", "外因", "市场换了付钱的风格，模型没坏"),
    "crowding": ("拥挤踩踏", "外因偏内", "同类资金太多互相踩，因子本身仍有效"),
    "component": ("组件失效", "内因·非信号层", "信号没坏，是策略里某一层规则坏了"),
    "concept_drift": ("概念漂移", "内因·信号层", "特征到收益的映射真的变了，因子逻辑失效"),
    "healthy": ("未见失效", "—", "各层读数都在正常范围"),
    "insufficient": ("证据不足", "—", "输入不够，无法给出有把握的判定"),
}
ACTION_LABEL = {
    "freeze": "冻结模型，等分布回归后复核",
    "wait": "等待，不动模型",
    "hedge": "对冲风格暴露",
    "derisk": "降仓 / 降杠杆",
    "fix_component": "只修改失效的那一层规则",
    "retrain": "允许重训（用变点之后的数据）",
    "retire": "退役或重构信号",
    "keep": "维持现状，按复核条件定期复查",
}


def _c(layer: dict | None) -> float:
    return float(layer["contribution"]) if layer and layer.get("contribution") is not None else 0.0


def decide(layers: dict[str, dict], object_type: str, strategy_ret_obs: float | None, bench_ret_obs: float | None,
           cfg: dict | None = None) -> dict:
    cfg = {"component_min_loss": 0.01, "external_share": 0.5, **(cfg or {})}
    L = layers
    total_loss = None
    if strategy_ret_obs is not None:
        excess = strategy_ret_obs - (bench_ret_obs or 0.0)
        total_loss = excess if excess < 0 else 0.0
    confidence: dict[str, float] = {}

    # 1. 数据层
    data = L.get("data")
    if data and data["status"] == "failed":
        confidence["distribution_shift"] = 0.9
        return _pack("distribution_shift", ["freeze", "derisk"], gate=("forbidden", "输入分布已离开训练区域，此时重训只会把外推区域的噪音学进去"),
                     falsify="若特征 PSI 回到 0.10 以下、模型仍持续跑输基准，则改判概念漂移",
                     recheck=["特征 max PSI < 0.10", "连续 10 个交易日 KS < 0.10"], confidence=confidence, layers=L, total_loss=total_loss)

    # 2. 外因
    style, crowd = L.get("L2"), L.get("crowd")
    style_c = _c(style)
    share = None
    if total_loss and total_loss < 0:
        share = min(1.0, max(0.0, style_c / total_loss))
    ext_style = style is not None and style["status"] in ("warning",) and (share is None or share >= cfg["external_share"])
    ext_crowd = crowd is not None and crowd["status"] == "warning"
    if ext_style or (style is not None and style["status"] == "warning" and object_type == "factor"):
        confidence["style_regime"] = 0.6 + 0.3 * (share or 0.5)
        actions = ["hedge", "derisk", "wait"]
        if ext_crowd:
            confidence["crowding"] = 0.6
        comp = _component_findings(L, cfg)
        if comp:
            actions.append("fix_component")
            confidence["component"] = 0.5
        sig = L.get("L1")
        note = ""
        if sig and sig["status"] in ("failed", "warning"):
            confidence["concept_drift"] = 0.4 if sig["status"] == "failed" else 0.25
            note = ("信号层读数同时翻负，但在风格断裂窗口内无法与外因区分（动量类信号本身就会吸收风格趋势）；"
                    "先按外因处置，是否真是概念漂移交给证伪条件在风格收敛后复判")
        return _pack("style_regime", actions, gate=("forbidden", "损失主要由风格环境解释，在极端风格样本上重训会把\"这个风格永远亏\"学成规律" + ("；" + note if note else "")),
                     falsify="若风格价差收敛（|中证1000−上证50 价差| < 5%）后模型仍跑输基准且 L1 IC 不恢复，则改判概念漂移",
                     recheck=["中证1000−上证50 二十日价差收敛到 ±5% 以内", "科创50 二十日年化波动 < 30%", "L1 Rank IC 二十日均值回到基准一半以上"],
                     confidence=confidence, layers=L, total_loss=total_loss, extra_components=comp)
    if ext_crowd:
        confidence["crowding"] = 0.7
        return _pack("crowding", ["derisk", "wait"], gate=("forbidden", "拥挤踩踏样本上重训会学到\"跌\"而不是\"为什么跌\""),
                     falsify="若持仓间相关性回落到基准水平后策略仍跑输，则转查组件层与信号层",
                     recheck=["持仓间平均相关性回到基准 +0.05 以内", "持仓成交额占比回到基准 ×1.1 以内"], confidence=confidence, layers=L, total_loss=total_loss)

    # 3. 组件层
    comp = _component_findings(L, cfg)
    if comp:
        confidence["component"] = 0.7
        worst = comp[0]
        return _pack("component", ["fix_component"], gate=("forbidden", f"失效发生在{worst['name']}层而不是信号层，重训信号解决不了问题"),
                     falsify=f"若修改{worst['name']}规则后，下一个同类环境窗口该层贡献仍为负且信号层 IC 继续下降，则改判概念漂移",
                     recheck=[f"修改后 {worst['name']} 层反事实贡献转正", "信号层 Rank IC 二十日均值不低于基准一半"],
                     confidence=confidence, layers=L, total_loss=total_loss, extra_components=comp)

    # 4. 信号层
    sig = L.get("L1")
    if sig and sig["status"] == "failed":
        confidence["concept_drift"] = 0.75
        cp = sig["metrics"].get("changepoint")
        return _pack("concept_drift", ["retrain", "retire"], gate=("allowed", f"外因与组件层均已排除，IC 翻符号且变点（{cp}）确认，属于信号逻辑本身失效"),
                     falsify="若仅用变点之后样本重训仍无法使样本外 IC 回正，则应退役而非继续重训",
                     recheck=["重训后滚动 Rank IC 连续 20 日同号且均值 > 0.02"], confidence=confidence, layers=L, total_loss=total_loss)
    if sig and sig["status"] == "warning":
        confidence["concept_drift"] = 0.45
        return _pack("concept_drift", ["derisk", "wait"], gate=("hold", "IC 明显变钝但未确认翻符号，先降仓观察，暂不重训"),
                     falsify="若 20 个交易日后 IC 仍未恢复且变点确认，则升级为概念漂移并允许重训",
                     recheck=["L1 Rank IC 二十日均值回到基准一半以上", "变点检测未再报警"], confidence=confidence, layers=L, total_loss=total_loss)

    # 5. 健康 / 不足
    have = [k for k, v in L.items() if v["status"] != "insufficient"]
    if len(have) <= 1:
        return _pack("insufficient", ["keep"], gate=("hold", "输入不足以判定"), falsify="—", recheck=["补充账本或特征后重跑"], confidence={"insufficient": 1.0}, layers=L, total_loss=total_loss)
    confidence["healthy"] = 0.7
    return _pack("healthy", ["keep"], gate=("hold", "未见失效，无需重训"), falsify="若下一观察窗策略跑输基准且任一层转为 warning，则重新分诊",
                 recheck=["每个观察窗结束后重跑分诊"], confidence=confidence, layers=L, total_loss=total_loss)


def _component_findings(L: dict, cfg: dict) -> list[dict]:
    out = []
    for key in ["L4", "L5", "L3", "L0"]:
        lay = L.get(key)
        if not lay or lay["status"] in ("insufficient",):
            continue
        c = _c(lay)
        if (c < -cfg["component_min_loss"]) or (key == "L0" and lay["status"] == "warning"):
            out.append({"layer": key, "name": lay["name"], "contribution": c, "status": lay["status"], "plain": lay["plain"]})
    return sorted(out, key=lambda x: x["contribution"])


def _pack(cause: str, actions: list[str], gate: tuple[str, str], falsify: str, recheck: list[str], confidence: dict,
          layers: dict, total_loss, extra_components: list | None = None) -> dict:
    label, kind, desc = CAUSE_LABEL[cause]
    return {
        "primary_cause": cause, "cause_label": label, "cause_kind": kind, "cause_desc": desc,
        "actions": actions, "action_labels": [ACTION_LABEL[a] for a in actions],
        "retrain_gate": gate[0], "retrain_reason": gate[1],
        "falsification": falsify, "recheck_triggers": recheck,
        "confidence": confidence, "total_excess_loss": total_loss,
        "components": extra_components or [],
    }


def plain_text(v: dict, layers: dict, object_name: str, object_type: str, obs_start: str, obs_end: str,
               strategy_ret: float | None, bench_ret: float | None) -> str:
    gate = {"forbidden": "❌ 禁止重训", "allowed": "✅ 允许重训", "hold": "⏸ 暂不重训"}[v["retrain_gate"]]
    head = f"对象：{object_name}（{'因子' if object_type == 'factor' else '策略'}）  观察窗：{obs_start} ~ {obs_end}"
    perf = ""
    if strategy_ret is not None:
        perf = f"观察窗收益 {strategy_ret * 100:+.1f}%" + (f"，基准 {bench_ret * 100:+.1f}%，超额 {(strategy_ret - bench_ret) * 100:+.1f}%" if bench_ret is not None else "")
    lines = [head, perf, "", f"【结论·人话】{v['cause_label']}（{v['cause_kind']}）：{v['cause_desc']}。"]
    for c in v.get("components", [])[:3]:
        lines.append(f"  · {c['name']}层：{c['plain']}（贡献 {c['contribution'] * 100:+.1f}%）")
    for k in ["L2", "L1", "L4", "L5", "L3"]:
        lay = layers.get(k)
        if lay and lay["status"] in ("warning", "failed") and not any(c["layer"] == k for c in v.get("components", [])):
            lines.append(f"  · {lay['name']}：{lay['plain']}")
    lines += ["", f"【处置】{' → '.join(v['action_labels'])}    {gate}", f"【为什么】{v['retrain_reason']}",
              f"【证伪条件】{v['falsification']}", "【复核触发】" + "；".join(v["recheck_triggers"])]
    return "\n".join(l for l in lines if l is not None)


def layer_table(layers: dict) -> str:
    rows = ["维度            贡献      判定      学术读数 / 通俗解读",
            "（贡献口径：信号 = 纯信号变体的超额；风格 = 其中由风格暴露解释的部分；择时/风控/执行 = 含该层 − 去掉该层的反事实差值；三组不相加）"]
    for k in ["data", "L0", "L1", "L2", "crowd", "L3", "L4", "L5"]:
        lay = layers.get(k)
        if not lay:
            continue
        c = "  n/a  " if lay["contribution"] is None else f"{lay['contribution'] * 100:+6.1f}%"
        rows.append(f"{lay['name']:<14}{c:>8}   {lay['status']:<9} {lay['academic']}")
        rows.append(f"{'':<14}{'':>8}   {'':<9} → {lay['plain']}")
    return "\n".join(rows)
