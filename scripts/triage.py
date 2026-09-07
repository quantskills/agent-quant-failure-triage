#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent-quant-failure-triage · 主入口。

用法
  python scripts/triage.py --card examples/card_strategy.yaml --obs-start 20260701 --obs-end 20260814
  python scripts/triage.py --scores my_scores.parquet --object-type factor --obs-start 20260701 --obs-end 20260814
  python scripts/triage.py --card card.yaml --offline        # 卡片 data 段提供行情文件，不联网

程序化调用（BUILD 规则）
  from triage import run, validate_input
  report = run({"card": card_dict_or_path, "obs_start": "20260701", "obs_end": "20260814"})

输出（--out 目录，默认 outputs/<object_id>_<obs_end>/）
  report.md / report.json / report.html / triage.parquet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapters import load_features, load_nav, load_positions, load_scores  # noqa: E402
from common import BUILD_ID, DATA_VERSION, LAYERS, OUTPUT_DIR, compact, now_iso  # noqa: E402
from layers import (diag_crowding, diag_data_drift, diag_execution, diag_risk, diag_signal, diag_style,  # noqa: E402
                    diag_timing, diag_universe, environment)
from market import load_market  # noqa: E402
from render_html import render_html  # noqa: E402
from simulator import run_variants, top_group_weights  # noqa: E402
from verdict import decide, layer_table, plain_text  # noqa: E402

DEFAULT_CONFIG = {
    "baseline_days": 120,      # 基准窗长度（交易日）
    "horizon": 5,              # IC 前瞻期
    "warmup_days": 30,         # 模拟预热
    "psi_warn": 0.10, "psi_fail": 0.25,
    "component_min_loss": 0.01, "external_share": 0.5,
}


# ----------------------------------------------------------------------------
# 输入
# ----------------------------------------------------------------------------
def load_card(src: Any) -> dict:
    if isinstance(src, dict):
        return dict(src)
    p = Path(src)
    if not p.is_file():
        raise FileNotFoundError(f"策略卡片不存在: {p}")
    card = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    base = p.parent
    # 相对路径按卡片所在目录解析
    for sect in ("signal", "ledger", "features", "data", "timing"):
        d = card.get(sect)
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, str) and (v.endswith((".parquet", ".csv", ".json", ".xlsx", ".pq"))) and not Path(v).is_absolute():
                    cand = base / v
                    if cand.exists():
                        d[k] = str(cand)
    return card


def validate_input(input_data: Any) -> dict:
    """校验并标准化调用参数。接受 dict{card|scores, obs_start, obs_end, [object_type, offline, config]}。"""
    if not isinstance(input_data, dict):
        raise ValueError("input_data 必须是 dict")
    if not input_data.get("obs_start") or not input_data.get("obs_end"):
        raise ValueError("必须提供 obs_start / obs_end（观察窗）")
    if input_data.get("card") is None and input_data.get("scores") is None:
        raise ValueError("必须提供 card（策略卡片）或 scores（打分表）之一")
    card = load_card(input_data["card"]) if input_data.get("card") is not None else {}
    if input_data.get("scores") is not None:
        card.setdefault("signal", {})["score_table"] = input_data["scores"]
    if input_data.get("object_type"):
        card["object_type"] = input_data["object_type"]
    card.setdefault("object_type", "strategy" if any(card.get(k) for k in ("timing", "risk", "ledger")) else "factor")
    card.setdefault("strategy_id", input_data.get("object_id") or card.get("name") or "unnamed-object")
    if not (card.get("signal") or {}).get("score_table"):
        raise ValueError("卡片 signal.score_table 缺失：至少需要因子打分表（I1 最小契约）")
    obs_start, obs_end = compact(input_data["obs_start"]), compact(input_data["obs_end"])
    if obs_start >= obs_end:
        raise ValueError("obs_start 必须早于 obs_end")
    return {"card": card, "obs_start": obs_start, "obs_end": obs_end, "offline": bool(input_data.get("offline", False)),
            "config": {**DEFAULT_CONFIG, **(input_data.get("config") or {})}, "out": input_data.get("out")}


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def run(input_data: Any) -> dict:
    p = validate_input(input_data)
    card, cfg = p["card"], p["config"]
    obs_start, obs_end = pd.to_datetime(p["obs_start"]), pd.to_datetime(p["obs_end"])
    object_type = card["object_type"]
    scores = load_scores(card["signal"]["score_table"], score_col=(card["signal"].get("score_col")))
    symbols = sorted(scores["symbol"].unique())
    # 窗口：基准窗 = 观察窗之前 baseline_days 个交易日；再往前留预热
    base_days = int(cfg["baseline_days"])
    fetch_start = obs_start - pd.Timedelta(days=int((base_days + cfg["warmup_days"]) * 1.6))
    fetch_end = obs_end + pd.Timedelta(days=int(cfg["horizon"] * 1.6) + 3)
    market = load_market(card, compact(fetch_start), compact(fetch_end), symbols, offline=p["offline"])
    prices = market.prices
    if prices.empty:
        raise RuntimeError("行情为空：检查凭证 / 股票池 / 离线文件")
    tdays = pd.DatetimeIndex(sorted(prices["date"].unique()))
    before = tdays[tdays < obs_start]
    if len(before) < 20:
        raise RuntimeError("观察窗之前的可用交易日不足 20 天，无法构建基准窗")
    base_end = before[-1]
    base_start = before[max(0, len(before) - base_days)]
    sim_start = before[max(0, len(before) - base_days - int(cfg["warmup_days"]))]
    scores = scores[(scores["date"] >= sim_start) & (scores["date"] <= obs_end)]
    idx_close = market.index_close()

    layers: dict[str, dict] = {}
    # 特征（I3）
    feats = None
    if (card.get("features") or {}).get("table"):
        feats = load_features(card["features"]["table"])
    layers["data"] = diag_data_drift(feats, base_start, base_end, obs_start, obs_end, cfg["psi_warn"], cfg["psi_fail"])
    layers["L0"] = diag_universe(scores, prices, base_start, base_end, obs_start, obs_end)

    # 持仓：账本优先，否则模拟；因子对象用 top 组代理
    sims: dict = {}
    holdings: dict = {}
    nav_curves = None
    strategy_ret = bench_ret = None
    if object_type == "strategy":
        sims = run_variants(scores, prices, card, idx_close, start=sim_start, end=obs_end)
        full = sims["full"]
        holdings = {d: w for d, w in full.holdings.items()}
        ledger = card.get("ledger") or {}
        if ledger.get("positions"):
            pos = load_positions(ledger["positions"])
            holdings = {d: g.set_index("symbol")["weight"].to_dict() for d, g in pos.groupby("date")}
        strategy_ret = full.ret(obs_start, obs_end)
        if ledger.get("nav"):
            nav_real = load_nav(ledger["nav"])
            w = nav_real[(nav_real.index >= obs_start) & (nav_real.index <= obs_end)]
            if len(w) > 1:
                strategy_ret = float(w.iloc[-1] / w.iloc[0] - 1)
        labels = [str(d.date()) for d in full.nav.index if obs_start <= d <= obs_end]
        def norm(s):
            s = s[(s.index >= obs_start) & (s.index <= obs_end)]
            return [round(float(x / s.iloc[0]), 4) for x in s.values] if len(s) else []
        nav_curves = {"labels": labels, "series": {k: norm(v.nav) for k, v in sims.items()}}
    else:
        tg = top_group_weights(scores, card)
        holdings = {d: g.set_index("symbol")["weight"].to_dict() for d, g in tg.groupby("date")}
        sims = {"signal_only": None}
        # 因子对象的"收益"：top 组等权、按调仓周期换仓、理想成交
        from simulator import simulate
        so = simulate(scores, prices, {**card, "timing": {"rule": "none"}, "risk": {}}, idx_close, variant="signal_only", start=sim_start, end=obs_end)
        sims = {"signal_only": so}
        strategy_ret = so.ret(obs_start, obs_end)
        labels = [str(d.date()) for d in so.nav.index if obs_start <= d <= obs_end]
        s = so.nav[(so.nav.index >= obs_start) & (so.nav.index <= obs_end)]
        nav_curves = {"labels": labels, "series": {"signal_only": [round(float(x / s.iloc[0]), 4) for x in s.values]}}
    bench_code = str((card.get("benchmark") or {}).get("index") or "000852.SH")
    if not idx_close.empty and bench_code in idx_close.columns:
        b = idx_close[bench_code][(idx_close.index >= obs_start) & (idx_close.index <= obs_end)].dropna()
        if len(b) > 1:
            bench_ret = float(b.iloc[-1] / b.iloc[0] - 1)
            if nav_curves:
                bb = b.reindex(pd.to_datetime(nav_curves["labels"])).ffill()
                nav_curves["series"]["benchmark"] = [round(float(x / bb.iloc[0]), 4) for x in bb.values]
    excess_loss = (strategy_ret - (bench_ret or 0.0)) if strategy_ret is not None else None

    # L1 信号：贡献 = 纯信号变体（或因子 top 组）超额
    l1_contrib = None
    if sims.get("signal_only") is not None:
        l1_contrib = sims["signal_only"].ret(obs_start, obs_end) - (bench_ret or 0.0)
    layers["L1"] = diag_signal(scores, prices, base_start, base_end, obs_start, obs_end, cfg["horizon"], contribution=l1_contrib)
    layers["L2"] = diag_style(holdings, market, base_start, base_end, obs_start, obs_end, strategy_loss=excess_loss if excess_loss is not None and excess_loss < 0 else None)
    layers["crowd"] = diag_crowding(holdings, market, base_start, base_end, obs_start, obs_end)
    if object_type == "strategy":
        layers["L3"] = diag_timing(sims, card, market, obs_start, obs_end)
        layers["L4"] = diag_risk(sims, card, market, obs_start, obs_end)
        layers["L5"] = diag_execution(sims, card, market, base_start, base_end, obs_start, obs_end)

    env = environment(market, str(card.get("family") or "cross_sectional"), base_start, base_end, obs_start, obs_end)
    v = decide(layers, object_type, strategy_ret, bench_ret, cfg)
    text = plain_text(v, layers, card["strategy_id"], object_type, str(obs_start.date()), str(obs_end.date()), strategy_ret, bench_ret)
    perf = {"观察窗收益": f"{strategy_ret * 100:+.1f}%" if strategy_ret is not None else "n/a",
            f"基准 {bench_code}": f"{bench_ret * 100:+.1f}%" if bench_ret is not None else "n/a",
            "超额": f"{(strategy_ret - bench_ret) * 100:+.1f}%" if strategy_ret is not None and bench_ret is not None else "n/a"}
    if object_type == "strategy" and sims.get("full") is not None:
        perf["观察窗成交笔数"] = str(int(((sims["full"].trades["date"] >= obs_start) & (sims["full"].trades["date"] <= obs_end)).sum())) if len(sims["full"].trades) else "0"
    report = {
        "build_id": BUILD_ID, "data_version": DATA_VERSION, "generated_at": now_iso(),
        "object_name": card["strategy_id"], "object_type": object_type, "family": card.get("family"),
        "obs_start": str(obs_start.date()), "obs_end": str(obs_end.date()), "base_start": str(pd.to_datetime(base_start).date()), "base_end": str(pd.to_datetime(base_end).date()),
        "data_source": market.source, "performance": perf, "strategy_ret": strategy_ret, "bench_ret": bench_ret,
        "layers": layers, "verdict": v, "environment": env, "nav_curves": nav_curves,
        "plain_text": text, "layer_table": layer_table(layers), "config": cfg,
        "inputs": {"score_table": str(card["signal"]["score_table"]), "has_ledger": bool(card.get("ledger")), "has_features": feats is not None},
    }
    return report


# ----------------------------------------------------------------------------
# 落盘
# ----------------------------------------------------------------------------
def to_parquet_rows(report: dict) -> pd.DataFrame:
    rows = []
    base = {"trade_date": report["obs_end"], "build_id": BUILD_ID, "target_id": report["object_name"],
            "data_version": DATA_VERSION, "update_time": report["generated_at"]}
    for k, lay in report["layers"].items():
        rows.append({**base, "result_type": f"layer_{k}", "status": lay["status"], "contribution": lay["contribution"],
                     "result_json": json.dumps({kk: vv for kk, vv in lay["metrics"].items() if kk not in ("ic_series", "exposure_series", "stop_events", "worst")}, ensure_ascii=False),
                     "plain_text": lay["plain"]})
    v = report["verdict"]
    rows.append({**base, "result_type": "verdict", "status": v["primary_cause"], "contribution": v.get("total_excess_loss"),
                 "result_json": json.dumps({k: v[k] for k in ("cause_label", "cause_kind", "actions", "retrain_gate", "retrain_reason", "falsification", "recheck_triggers", "confidence")}, ensure_ascii=False),
                 "plain_text": report["plain_text"]})
    return pd.DataFrame(rows)


def save_outputs(report: dict, out_dir: str | Path | None = None) -> dict:
    out = Path(out_dir) if out_dir else OUTPUT_DIR / f"{report['object_name']}_{report['obs_end'].replace('-', '')}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(_markdown(report), encoding="utf-8")
    slim = {k: v for k, v in report.items() if k != "nav_curves"}
    (out / "report.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    to_parquet_rows(report).to_parquet(out / "triage.parquet", index=False)
    render_html(report, out / "report.html")
    return {"dir": str(out), "md": str(out / "report.md"), "json": str(out / "report.json"), "html": str(out / "report.html"), "parquet": str(out / "triage.parquet")}


def _markdown(r: dict) -> str:
    v = r["verdict"]
    lines = [f"# 失效分诊 · {r['object_name']}（{'因子' if r['object_type'] == 'factor' else '策略'}）", "",
             f"- 观察窗 {r['obs_start']} ~ {r['obs_end']}；基准窗 {r['base_start']} ~ {r['base_end']}；数据源 {r['data_source']}；生成 {r['generated_at']}",
             "- " + "；".join(f"{k} {val}" for k, val in r["performance"].items()), "", "```", r["plain_text"], "```", "", "## 分层剥离", "", "```", r["layer_table"], "```", ""]
    env = r.get("environment", {})
    if env.get("rows"):
        lines += [f"## 环境刻画（策略家族：{env.get('family')}）", "", "| 环境量 | 基准窗 | 观察窗 |", "|---|---:|---:|"]
        lines += [f"| {x['metric']} | {'' if x['base'] is None else round(x['base'], 4)} | {'' if x['obs'] is None else round(x['obs'], 4)} |" for x in env["rows"]]
        lines.append("")
    lines += ["## 死因置信度", ""] + [f"- {k}: {val:.2f}" for k, val in v["confidence"].items()] + ["",
              "> 研究诊断与处置建议，不是交易指令；不构成投资建议，不承诺收益。"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="策略与因子失效分诊")
    ap.add_argument("--card", help="策略卡片 YAML")
    ap.add_argument("--scores", help="因子打分表（parquet/csv），可替代卡片")
    ap.add_argument("--object-type", choices=["factor", "strategy"])
    ap.add_argument("--object-id")
    ap.add_argument("--obs-start", required=True)
    ap.add_argument("--obs-end", required=True)
    ap.add_argument("--baseline-days", type=int)
    ap.add_argument("--horizon", type=int)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    cfg = {k: v for k, v in {"baseline_days": a.baseline_days, "horizon": a.horizon}.items() if v is not None}
    rep = run({"card": a.card, "scores": a.scores, "object_type": a.object_type, "object_id": a.object_id,
               "obs_start": a.obs_start, "obs_end": a.obs_end, "offline": a.offline, "config": cfg})
    paths = save_outputs(rep, a.out)
    print(rep["plain_text"])
    print()
    print(rep["layer_table"])
    print()
    print("输出：", paths["dir"])


if __name__ == "__main__":
    main()
