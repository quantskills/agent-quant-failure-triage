#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线全流程测试：合成行情 + 合成打分 + 策略卡片，无 panda_data 也全绿。

场景设计（刻意制造"7 月式"断裂）
- 基准窗：正常市场，动量因子有效（打分与前瞻收益正相关）。
- 观察窗：小盘暴跌 + 暴力反转日 + 成交额腰斩 + 部分票跌停锁死；
  动量因子在观察窗内"翻符号"可选（用于概念漂移场景）。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from adapters import load_prices, load_scores  # noqa: E402
from layers import diag_data_drift, ks_stat, page_hinkley, psi  # noqa: E402
from simulator import simulate  # noqa: E402
from triage import run, save_outputs, validate_input  # noqa: E402
from verdict import decide  # noqa: E402

RNG = np.random.default_rng(7)


def make_market(n_sym: int = 120, n_days: int = 260, crash_from: int = 200, concept_drift: bool = False):
    dates = pd.bdate_range("2026-01-05", periods=n_days)
    syms = [f"{600000 + i}.SH" if i % 2 else f"{'%06d' % (300000 + i)}.SZ" for i in range(n_sym)]
    size = RNG.normal(0, 1, n_sym)              # 风格：size 越小越"小盘"
    mom_prev = RNG.normal(0, 1, n_sym)           # 动量特征
    close = np.zeros((n_days, n_sym)); close[0] = 20 + RNG.uniform(0, 30, n_sym)
    style_size_ret = np.zeros(n_days)
    rows_style = []
    for t in range(1, n_days):
        mkt = RNG.normal(0.0002, 0.012)
        crash = t >= crash_from
        # 小盘风格：观察窗小盘被单独处决（size 小 → 负收益）
        s_ret = RNG.normal(0, 0.004) + (-0.006 if (crash and not concept_drift) else 0.0)
        style_size_ret[t] = s_ret
        # 动量因子有效：基准窗内高动量票有正超额；概念漂移场景下观察窗翻符号
        mom_eff = -0.006 if (concept_drift and crash) else 0.0025
        vol = 0.02 * (2.5 if crash else 1.0)
        rev = 0.0
        if crash and t in (crash_from + 6, crash_from + 14):  # 暴力反转日
            rev = 0.06
        r = mkt + (-size) * s_ret + mom_eff * mom_prev + RNG.normal(0, vol, n_sym) + rev - (0.02 if (crash and t in (crash_from + 3, crash_from + 10)) else 0)
        r = np.clip(r, -0.099, 0.099)
        close[t] = close[t - 1] * (1 + r)
        mom_prev = 0.8 * mom_prev + 0.2 * (r / vol)
    recs = []
    for t, d in enumerate(dates):
        amt_scale = 0.45 if t >= crash_from else 1.0
        for i, s in enumerate(syms):
            pc = close[t - 1, i] if t else close[0, i]
            c = close[t, i]
            lu, ld = round(pc * 1.1, 2), round(pc * 0.9, 2)
            o = pc * (1 + RNG.normal(0, 0.004))
            locked_down = t >= crash_from and i % 15 == 0 and t % 4 == 0   # 部分票开盘跌停
            if locked_down:
                o = ld; c = min(c, ld)
            hi, lo = max(o, c) * (1 + abs(RNG.normal(0, 0.005))), min(o, c) * (1 - abs(RNG.normal(0, 0.005)))
            recs.append({"date": d, "symbol": s, "open": round(o, 2), "high": round(hi, 2), "low": round(lo, 2), "close": round(c, 2),
                         "pre_close": round(pc, 2), "volume": 1e6, "amount": 5e7 * amt_scale * (1 + RNG.uniform(0, 1)),
                         "limit_up": lu, "limit_down": ld, "trade_status": 0, "name": f"股{i}"})
        if t % 5 == 0:
            for i, s in enumerate(syms):
                rows_style.append({"date": d, "symbol": s, "size": size[i], "momentum": mom_prev[i], "beta": RNG.normal(0, 1), "liquidity": RNG.normal(0, 1),
                                   "residual_volatility": RNG.normal(0, 1), "growth": RNG.normal(0, 1), "leverage": RNG.normal(0, 1), "profitability": RNG.normal(0, 1)})
    prices = pd.DataFrame(recs)
    # 指数：中证1000 = 小盘票均值，上证50 = 大盘票均值
    cw = prices.pivot(index="date", columns="symbol", values="close")
    small, big = [s for i, s in enumerate(syms) if size[i] < 0], [s for i, s in enumerate(syms) if size[i] >= 0]
    idx = []
    for code, group in [("000852.SH", small), ("000016.SH", big), ("000300.SH", big), ("399006.SZ", small), ("000688.SH", small)]:
        lvl = (cw[group] / cw[group].iloc[0]).mean(axis=1) * 1000
        for d, v in lvl.items():
            idx.append({"date": d, "symbol": code, "open": v, "high": v, "low": v, "close": v, "pre_close": v, "volume": 1e8, "amount": 1e11 * (0.5 if d >= dates[crash_from] else 1.0)})
    index = pd.DataFrame(idx)
    # 打分 = 动量特征（信号日已知）+ 噪声
    sc = []
    mom_path = np.zeros((n_days, n_sym)); m = RNG.normal(0, 1, n_sym)
    for t in range(n_days):
        if t:
            r = close[t] / close[t - 1] - 1
            m = 0.8 * m + 0.2 * (r / 0.02)
        mom_path[t] = m
        for i, s in enumerate(syms):
            sc.append({"date": dates[t], "symbol": s, "score": m[i] + RNG.normal(0, 0.3)})
    scores = pd.DataFrame(sc)
    feats = scores.rename(columns={"score": "f_mom"}).copy()
    feats["f_vol"] = np.repeat(np.where(np.arange(n_days) >= crash_from, 0.05, 0.02), n_sym) + RNG.normal(0, 0.003, len(feats))
    return dates, prices, index, pd.DataFrame(rows_style), scores, feats


def write_case(tmp: Path, concept_drift: bool = False, with_features: bool = True):
    dates, prices, index, style, scores, feats = make_market(concept_drift=concept_drift)
    prices.to_parquet(tmp / "prices.parquet", index=False)
    index.to_parquet(tmp / "index.parquet", index=False)
    style.to_parquet(tmp / "style.parquet", index=False)
    scores.to_parquet(tmp / "scores.parquet", index=False)
    feats.to_parquet(tmp / "features.parquet", index=False)
    card = {
        "strategy_id": "synthetic-momentum-topn", "object_type": "strategy", "family": "momentum",
        "signal": {"score_table": str(tmp / "scores.parquet")},
        "position": {"method": "top_n", "n": 15, "weight": "equal", "leverage": 1.0},
        "timing": {"rule": "index_above_ma", "index": "000852.SH", "window": 20, "exposure_when_off": 0.0},
        "risk": {"stop_loss": -0.06, "max_drawdown_halt": None},
        "execution": {"rebalance_days": 5, "cost_bps": 15, "respect_limits": True},
        "benchmark": {"index": "000852.SH"},
        "data": {"prices": str(tmp / "prices.parquet"), "index": str(tmp / "index.parquet"), "style": str(tmp / "style.parquet")},
    }
    if with_features:
        card["features"] = {"table": str(tmp / "features.parquet")}
    obs_start, obs_end = dates[200], dates[-8]
    return card, obs_start.strftime("%Y%m%d"), obs_end.strftime("%Y%m%d")


def t_adapters(tmp):
    wide = pd.DataFrame({"date": ["2026-01-05", "2026-01-06"], "600000.SH": [1.0, 2.0], "000001": [0.5, 0.1]})
    wide.to_csv(tmp / "wide.csv", index=False)
    s = load_scores(tmp / "wide.csv")
    assert set(s["symbol"]) == {"600000.SH", "000001.SZ"} and len(s) == 4
    alpha = pd.DataFrame({"trade_date": ["20260105"] * 3, "ts_code": ["600000.SH", "000001.SZ", "300001.SZ"], "score": [80.0, 70.0, 60.0], "rank": [1, 2, 3]})
    s2 = load_scores(alpha)
    assert list(s2.columns) == ["date", "symbol", "score"] and s2["score"].max() == 80.0
    p = load_prices(pd.DataFrame({"date": ["20260105", "20260106"], "symbol": ["600000.SH"] * 2, "close": [10.0, 11.0]}))
    assert bool(p["limit_inferred"].all()) and abs(p["limit_up"].iloc[1] - 11.0) < 1e-9
    print("✅ test_adapters（宽表/Alpha parquet/裸代码补后缀/涨跌停推断）")


def t_stats():
    a, b = RNG.normal(0, 1, 2000), RNG.normal(0, 1, 2000)
    assert psi(a, b) < 0.05 and ks_stat(a, b) < 0.08
    c = RNG.normal(1.5, 1, 2000)
    assert psi(a, c) > 0.25 and ks_stat(a, c) > 0.4
    ic = pd.Series(np.r_[RNG.normal(0.05, 0.03, 60), RNG.normal(-0.04, 0.03, 40)], index=pd.bdate_range("2026-01-01", periods=100))
    cp = page_hinkley(ic)
    assert cp is not None and cp >= ic.index[55], cp
    print("✅ test_stats（PSI/KS 对同分布小、对平移大；Page-Hinkley 在均值下降后报警）")


def t_simulator(tmp):
    dates, prices, index, style, scores, feats = make_market(n_sym=40, n_days=80, crash_from=60)
    idx_close = index.pivot(index="date", columns="symbol", values="close")
    card = {"position": {"n": 8}, "risk": {"stop_loss": -0.05}, "execution": {"rebalance_days": 5, "cost_bps": 10, "respect_limits": True}, "timing": {"rule": "none"}}
    full = simulate(scores, prices, card, idx_close, variant="full")
    ideal = simulate(scores, prices, card, idx_close, variant="ideal_fill")
    nostop = simulate(scores, prices, card, idx_close, variant="no_stop")
    assert len(full.nav) == len(dates) and full.nav.iloc[0] > 0
    assert len(full.locked) > 0, "跌停锁死应至少出现一次"
    assert len(ideal.locked) == 0 and len(nostop.stops) == 0
    assert len(full.stops) > 0
    # 同信号同口径两次模拟结果一致（确定性）
    again = simulate(scores, prices, card, idx_close, variant="full")
    assert np.allclose(full.nav.values, again.nav.values)
    print(f"✅ test_simulator（锁死 {len(full.locked)} 笔、止损 {len(full.stops)} 次、变体口径正确、结果确定）")


def t_validate():
    try:
        validate_input({"obs_start": "20260701"})
        raise AssertionError("应报缺 obs_end")
    except ValueError:
        pass
    try:
        validate_input({"obs_start": "20260701", "obs_end": "20260801"})
        raise AssertionError("应报缺 card/scores")
    except ValueError:
        pass
    p = validate_input({"scores": "x.parquet", "obs_start": "20260701", "obs_end": "20260801"})
    assert p["card"]["object_type"] == "factor"
    print("✅ test_validate_input")


def t_pipeline_strategy(tmp):
    card, s, e = write_case(tmp)
    rep = run({"card": card, "obs_start": s, "obs_end": e, "offline": True})
    L, v = rep["layers"], rep["verdict"]
    assert set(L) >= {"data", "L0", "L1", "L2", "crowd", "L3", "L4", "L5"}
    assert L["data"]["status"] in ("warning", "failed"), L["data"]["academic"]          # f_vol 分布跳变
    assert L["L5"]["metrics"]["locked_sells_obs"] >= 1, L["L5"]["metrics"]                # 跌停锁死被记录
    assert L["L5"]["metrics"]["median_amount_ratio"] < 0.7                               # 成交额塌缩
    assert L["L2"]["metrics"].get("method") == "barra_regression"
    assert v["retrain_gate"] in ("forbidden", "hold"), v                                  # 外因/分布场景不得允许重训
    assert "禁止重训" in rep["plain_text"] or "暂不重训" in rep["plain_text"]
    paths = save_outputs(rep, tmp / "out_strategy")
    html = Path(paths["html"]).read_text(encoding="utf-8")
    assert "失效分诊" in html and "waterfall" in html and rep["object_name"] in html
    pq = pd.read_parquet(paths["parquet"])
    assert {"trade_date", "build_id", "target_id", "result_type", "result_json", "plain_text"} <= set(pq.columns)
    assert (pq["result_type"] == "verdict").sum() == 1
    print(f"✅ test_pipeline_strategy（死因={v['cause_label']}，闸门={v['retrain_gate']}，L4 whipsaw={L['L4']['metrics'].get('whipsaw_rate')}，输出四件套齐）")
    return rep


def t_pipeline_strategy_nofeat(tmp):
    card, s, e = write_case(tmp, with_features=False)
    card["strategy_id"] = "synthetic-momentum-nofeat"
    rep = run({"card": card, "obs_start": s, "obs_end": e, "offline": True})
    v, L = rep["verdict"], rep["layers"]
    assert L["data"]["status"] == "insufficient"
    assert v["primary_cause"] in ("style_regime", "component", "crowding"), v["primary_cause"]
    assert v["retrain_gate"] == "forbidden"
    print(f"✅ test_pipeline_strategy_nofeat（无特征时死因={v['cause_label']}；L2 贡献={L['L2']['contribution']}，L4={L['L4']['contribution']}，L5={L['L5']['contribution']}）")


def t_pipeline_factor(tmp):
    card, s, e = write_case(tmp, with_features=False)
    card = {k: card[k] for k in ("strategy_id", "signal", "position", "benchmark", "data")}
    card["strategy_id"] = "synthetic-momentum-factor"
    rep = run({"card": card, "object_type": "factor", "obs_start": s, "obs_end": e, "offline": True})
    L = rep["layers"]
    assert "L3" not in L and "L4" not in L and "L5" not in L
    assert L["data"]["status"] == "insufficient" and "事前预警" in L["data"]["plain"]
    assert rep["object_type"] == "factor"
    save_outputs(rep, tmp / "out_factor")
    print(f"✅ test_pipeline_factor（因子对象只跑 data/L0/L1/L2/crowd；死因={rep['verdict']['cause_label']}）")


def t_concept_drift(tmp):
    card, s, e = write_case(tmp, concept_drift=True, with_features=False)
    card["strategy_id"] = "synthetic-concept-drift"
    rep = run({"card": card, "obs_start": s, "obs_end": e, "offline": True})
    L1 = rep["layers"]["L1"]
    assert L1["metrics"]["sign_flip"] is True, L1["academic"]
    print(f"✅ test_concept_drift（IC 翻符号被检出：{L1['metrics']['ic_mean_base']:+.3f} → {L1['metrics']['ic_mean_obs']:+.3f}，L1={L1['status']}）")


def t_verdict_rules():
    mk = lambda layer, status, c=None, **m: {"layer": layer, "name": layer, "status": status, "contribution": c, "metrics": m, "academic": "", "plain": "", "evidence": []}
    # 数据层失效优先
    v = decide({"data": mk("data", "failed"), "L1": mk("L1", "failed", sign_flip=True, changepoint="2026-07-10")}, "strategy", -0.1, -0.02)
    assert v["primary_cause"] == "distribution_shift" and v["retrain_gate"] == "forbidden"
    # 外因主导
    v = decide({"data": mk("data", "insufficient"), "L2": mk("L2", "warning", -0.06), "L1": mk("L1", "healthy", 0.01)}, "strategy", -0.1, -0.02)
    assert v["primary_cause"] == "style_regime" and v["retrain_gate"] == "forbidden"
    # 组件失效：止损层
    v = decide({"data": mk("data", "insufficient"), "L2": mk("L2", "healthy", 0.0), "L1": mk("L1", "healthy", 0.01), "L4": mk("L4", "warning", -0.05, whipsaw_rate=0.7)}, "strategy", -0.06, -0.01)
    assert v["primary_cause"] == "component" and v["components"][0]["layer"] == "L4" and v["retrain_gate"] == "forbidden"
    # 概念漂移：排除外因和组件后才允许重训
    v = decide({"data": mk("data", "healthy"), "L2": mk("L2", "healthy", 0.0), "L1": mk("L1", "failed", -0.05, changepoint="2026-07-10"), "L4": mk("L4", "healthy", 0.0)}, "strategy", -0.06, -0.01)
    assert v["primary_cause"] == "concept_drift" and v["retrain_gate"] == "allowed"
    print("✅ test_verdict_rules（因果优先顺序：分布 > 外因 > 组件 > 概念漂移）")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="triage_test_"))
    try:
        t_adapters(tmp)
        t_stats()
        t_simulator(tmp)
        t_validate()
        t_verdict_rules()
        t_pipeline_strategy(tmp)
        t_pipeline_strategy_nofeat(tmp)
        t_pipeline_factor(tmp)
        t_concept_drift(tmp)
        print("\n🎉 全部测试通过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
