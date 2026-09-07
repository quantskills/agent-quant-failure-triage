#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回放式验收：用 2026 年 7 月的真实行情，对同一个公开信号分别做"因子"与"策略"分诊。

- 股票池：中证 1000（PandaData indicator=000852）
- 信号：20 日动量（跳过最近 5 日），每日截面打分；完全公开、可复现
- 策略：top-30 等权，5 日调仓，中证 1000 站上 20 日均线才持仓，单票 -8% 止损，双边 15bp，遵守涨跌停
- 观察窗：2026-07-01 ~ 2026-08-14；基准窗：观察窗前 120 个交易日

运行（需要 PandaData 凭证）
  python examples/replay_2026_07.py            # 生成 examples/data/scores_momentum_zz1000.parquet 并跑两次分诊
输出到 outputs/replay_2026_07/{factor,strategy}/
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))

from common import compact, init_panda  # noqa: E402
from market import fetch_stock_daily  # noqa: E402
from triage import run, save_outputs  # noqa: E402

DATA = HERE / "data"
DATA.mkdir(exist_ok=True)
SCORES = DATA / "scores_momentum_zz1000.parquet"
START, END = "20251201", "20260831"
OBS_START, OBS_END = "20260701", "20260814"


def build_scores() -> pd.DataFrame:
    pd_api = init_panda()
    px = fetch_stock_daily(pd_api, START, END, indicator="000852")
    if px.empty:
        raise RuntimeError("中证1000 日线为空")
    close = px.pivot(index="date", columns="symbol", values="close").sort_index()
    mom = close.shift(5) / close.shift(25) - 1          # 20 日动量，跳过最近 5 日
    score = mom.stack().rename("score").reset_index()
    score.columns = ["date", "symbol", "score"]
    score = score.dropna()
    score = score[score["date"] >= pd.to_datetime("20260101")]
    score.to_parquet(SCORES, index=False)
    print(f"打分表：{len(score)} 行，{score['symbol'].nunique()} 只，{score['date'].min().date()} ~ {score['date'].max().date()} → {SCORES}")
    return score


def main() -> None:
    if not SCORES.is_file():
        build_scores()
    base = {"signal": {"score_table": str(SCORES)}, "position": {"method": "top_n", "n": 30, "weight": "equal"},
            "benchmark": {"index": "000852.SH"}, "data": {"index_component": "000852"}, "family": "momentum"}
    factor_card = {**base, "strategy_id": "momentum-20d-zz1000", "object_type": "factor"}
    strategy_card = {**base, "strategy_id": "momentum-topn-timing-stop", "object_type": "strategy",
                     "timing": {"rule": "index_above_ma", "index": "000852.SH", "window": 20, "exposure_when_off": 0.0},
                     "risk": {"stop_loss": -0.08}, "execution": {"rebalance_days": 5, "cost_bps": 15, "respect_limits": True}}
    out_root = ROOT / "outputs" / "replay_2026_07"
    for name, card in [("factor", factor_card), ("strategy", strategy_card)]:
        print(f"\n===== {name} =====")
        rep = run({"card": card, "obs_start": OBS_START, "obs_end": OBS_END})
        paths = save_outputs(rep, out_root / name)
        print(rep["plain_text"])
        print()
        print(rep["layer_table"])
        print("→", paths["html"])


if __name__ == "__main__":
    main()
