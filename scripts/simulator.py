#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略日频模拟器：用于反事实剥离（去择时 / 去风控 / 理想成交）。

账本口径（A 股）
- 信号日 t 计算目标持仓，t+1 开盘成交；同一开盘先卖后买，卖出所得当日可买。
- respect_limits=True 时：开盘 ≤ 跌停价或停牌 → 卖不掉（记 locked）；开盘 ≥ 涨停价或停牌 → 买不进（记 missed）。
- 止损按收盘价相对入场价判定，次日开盘执行。
- 回撤熔断：净值回撤触及阈值 → 次日开盘清仓，空仓 halt_days 个交易日。
- 成本：双边 cost_bps（默认 15bp，一刀切，报告里写明）。

这不是生产回测引擎，只服务"同一信号、不同层规则"的相对比较；变体之间成交口径完全一致，
所以差值可以解释为该层的贡献。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_POSITION = {"method": "top_n", "n": 30, "weight": "equal", "leverage": 1.0}
DEFAULT_TIMING = {"rule": "none", "index": "000852.SH", "window": 20, "exposure_when_off": 0.0}
DEFAULT_RISK = {"stop_loss": None, "max_drawdown_halt": None, "halt_days": 5}
DEFAULT_EXECUTION = {"rebalance_days": 5, "cost_bps": 15, "respect_limits": True}


@dataclass
class SimResult:
    nav: pd.Series
    exposure: pd.Series
    trades: pd.DataFrame
    stops: list[dict] = field(default_factory=list)
    locked: list[dict] = field(default_factory=list)
    missed: list[dict] = field(default_factory=list)
    halts: list[dict] = field(default_factory=list)
    timing_on: pd.Series | None = None
    holdings: dict = field(default_factory=dict)  # date -> {symbol: weight}

    def ret(self, start=None, end=None) -> float:
        s = self.nav
        if start is not None:
            s = s[s.index >= pd.to_datetime(start)]
        if end is not None:
            s = s[s.index <= pd.to_datetime(end)]
        if len(s) < 2:
            return 0.0
        return float(s.iloc[-1] / s.iloc[0] - 1)


def _cfg(card: dict, key: str, default: dict) -> dict:
    v = dict(default)
    v.update({k: x for k, x in (card.get(key) or {}).items() if x is not None or k in ("stop_loss", "max_drawdown_halt")})
    return v


def timing_series(card: dict, index_close: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.Series:
    """择时开关序列（1 = 开，0 = 关），按信号日取值，只用当日及以前数据。"""
    t = _cfg(card, "timing", DEFAULT_TIMING)
    rule = str(t.get("rule", "none"))
    if rule in ("none", "", None):
        return pd.Series(1.0, index=dates)
    if rule == "series":
        src = t.get("file")
        df = pd.read_csv(src) if str(src).endswith(".csv") else pd.read_parquet(src)
        dcol = [c for c in df.columns if c.lower() in ("date", "trade_date")][0]
        vcol = [c for c in df.columns if c != dcol][0]
        s = pd.Series(pd.to_numeric(df[vcol], errors="coerce").values, index=pd.to_datetime(df[dcol].astype(str)))
        return s.reindex(dates).ffill().fillna(1.0).clip(0, 1)
    if rule == "index_above_ma":
        code, win = t.get("index", "000852.SH"), int(t.get("window", 20))
        if index_close.empty or code not in index_close.columns:
            return pd.Series(1.0, index=dates)
        c = index_close[code].dropna()
        on = (c > c.rolling(win).mean()).astype(float)
        return on.reindex(dates).ffill().fillna(1.0)
    raise ValueError(f"未知择时规则: {rule}（支持 none / index_above_ma / series）")


def target_weights(day_scores: pd.Series, pos: dict) -> pd.Series:
    """信号日目标权重（多头，权重和 = 1）。"""
    s = day_scores.dropna()
    if s.empty:
        return pd.Series(dtype=float)
    method = pos.get("method", "top_n")
    if method == "top_n":
        n = int(pos.get("n", 30))
        top = s.sort_values(ascending=False).head(n)
    elif method == "top_pct":
        k = max(1, int(len(s) * float(pos.get("pct", 0.1))))
        top = s.sort_values(ascending=False).head(k)
    elif method == "threshold":
        top = s[s >= float(pos.get("threshold", 0))]
    else:
        raise ValueError(f"未知仓位方法: {method}")
    if top.empty:
        return pd.Series(dtype=float)
    if pos.get("weight", "equal") == "score":
        w = (top - top.min() + 1e-9)
        w = w / w.sum()
    else:
        w = pd.Series(1.0 / len(top), index=top.index)
    return w


def simulate(scores: pd.DataFrame, prices: pd.DataFrame, card: dict, index_close: pd.DataFrame | None = None,
             variant: str = "full", start=None, end=None) -> SimResult:
    """variant: full | no_timing | no_stop | ideal_fill | signal_only。"""
    card = dict(card)
    pos = _cfg(card, "position", DEFAULT_POSITION)
    risk = _cfg(card, "risk", DEFAULT_RISK)
    ex = _cfg(card, "execution", DEFAULT_EXECUTION)
    if variant in ("no_timing", "signal_only"):
        card["timing"] = {"rule": "none"}
    if variant in ("no_stop", "signal_only"):
        risk = {"stop_loss": None, "max_drawdown_halt": None, "halt_days": 0}
    if variant in ("ideal_fill", "signal_only"):
        ex = {**ex, "cost_bps": 0, "respect_limits": False}

    px = prices.copy()
    if start is not None:
        px = px[px["date"] >= pd.to_datetime(start)]
    if end is not None:
        px = px[px["date"] <= pd.to_datetime(end)]
    dates = pd.DatetimeIndex(sorted(px["date"].unique()))
    if len(dates) < 3:
        raise ValueError("模拟区间不足 3 个交易日")
    open_w = px.pivot(index="date", columns="symbol", values="open").reindex(dates)
    close_w = px.pivot(index="date", columns="symbol", values="close").reindex(dates)
    lu_w = px.pivot(index="date", columns="symbol", values="limit_up").reindex(dates)
    ld_w = px.pivot(index="date", columns="symbol", values="limit_down").reindex(dates)
    st_w = px.pivot(index="date", columns="symbol", values="trade_status").reindex(dates).fillna(0)
    open_w = open_w.fillna(close_w)
    sc = scores[(scores["date"] >= dates[0]) & (scores["date"] <= dates[-1])]
    sc_w = sc.pivot(index="date", columns="symbol", values="score").reindex(dates)
    ton = timing_series(card, index_close if index_close is not None else pd.DataFrame(), dates)
    exp_off = float(_cfg(card, "timing", DEFAULT_TIMING).get("exposure_when_off", 0.0))
    lev = float(pos.get("leverage", 1.0))
    cost = float(ex.get("cost_bps", 0)) / 1e4
    respect = bool(ex.get("respect_limits", True))
    reb = max(1, int(ex.get("rebalance_days", 5)))
    stop_loss = risk.get("stop_loss")
    dd_halt = risk.get("max_drawdown_halt")
    halt_days = int(risk.get("halt_days", 5) or 0)

    cash, holdings = 1.0, {}  # symbol -> {"value","entry","entry_date"}
    pending_target: pd.Series | None = None
    pending_exposure = 1.0
    pending_stop_sells: set = set()
    halt_until = -1
    nav, expo, trades, stops, locked, missed, halts, hold_hist = [], [], [], [], [], [], [], {}
    peak = 1.0
    signal_dates = [d for i, d in enumerate(dates) if i % reb == 0 and not sc_w.loc[d].dropna().empty] if not sc_w.dropna(how="all").empty else []
    signal_set = set(signal_dates)
    prev_close = None

    for i, d in enumerate(dates):
        o, c = open_w.loc[d], close_w.loc[d]
        # 1. 按开盘价盯市
        for sym, h in holdings.items():
            pc = prev_close.get(sym) if prev_close is not None else None
            if pc and not np.isnan(pc) and not np.isnan(o.get(sym, np.nan)):
                h["value"] *= float(o[sym]) / float(pc)
        total = cash + sum(h["value"] for h in holdings.values())
        # 2. 执行（前一信号日的目标 / 止损 / 熔断）
        if pending_target is not None or pending_stop_sells or (halt_until >= i and holdings):
            in_halt = halt_until >= i
            tgt = pd.Series(dtype=float) if in_halt else (pending_target if pending_target is not None else None)
            expo_t = 0.0 if in_halt else pending_exposure
            sells = set(pending_stop_sells)
            if tgt is not None:
                sells |= {s for s in holdings if s not in tgt.index}
            for sym in sorted(sells):
                if sym not in holdings:
                    continue
                price = o.get(sym, np.nan)
                blocked = respect and (st_w.loc[d].get(sym, 0) != 0 or (not np.isnan(ld_w.loc[d].get(sym, np.nan)) and price <= ld_w.loc[d][sym] + 1e-9))
                if blocked or np.isnan(price):
                    locked.append({"date": d, "symbol": sym, "reason": "suspended" if st_w.loc[d].get(sym, 0) != 0 else "limit_down"})
                    continue
                v = holdings.pop(sym)["value"]
                cash += v * (1 - cost)
                trades.append({"date": d, "symbol": sym, "side": "sell", "value": v, "stop": sym in pending_stop_sells})
            pending_stop_sells = set()
            if tgt is not None and not in_halt and len(tgt):
                total = cash + sum(h["value"] for h in holdings.values())
                budget = total * lev * expo_t
                cur_val = sum(h["value"] for h in holdings.values())
                # 目标金额
                for sym, w in tgt.items():
                    want = budget * float(w)
                    have = holdings.get(sym, {}).get("value", 0.0)
                    delta = want - have
                    if delta <= total * 1e-4:
                        continue
                    price = o.get(sym, np.nan)
                    blocked = respect and (st_w.loc[d].get(sym, 0) != 0 or (not np.isnan(lu_w.loc[d].get(sym, np.nan)) and price >= lu_w.loc[d][sym] - 1e-9))
                    if blocked or np.isnan(price):
                        missed.append({"date": d, "symbol": sym, "reason": "suspended" if st_w.loc[d].get(sym, 0) != 0 else "limit_up"})
                        continue
                    spend = min(delta, cash)
                    if spend <= 0:
                        break
                    cash -= spend
                    net = spend * (1 - cost)
                    if sym in holdings:
                        holdings[sym]["value"] += net
                    else:
                        holdings[sym] = {"value": net, "entry": float(price), "entry_date": d}
                    trades.append({"date": d, "symbol": sym, "side": "buy", "value": spend, "stop": False})
            pending_target = None
        # 3. 收盘盯市
        for sym, h in holdings.items():
            if not np.isnan(o.get(sym, np.nan)) and not np.isnan(c.get(sym, np.nan)) and o[sym] > 0:
                h["value"] *= float(c[sym]) / float(o[sym])
        total = cash + sum(h["value"] for h in holdings.values())
        nav.append(total)
        expo.append(sum(h["value"] for h in holdings.values()) / total if total > 0 else 0.0)
        hold_hist[d] = {s: h["value"] / total for s, h in holdings.items()}
        # 4. 止损判定（收盘价 vs 入场价）→ 次日开盘卖
        if stop_loss is not None:
            for sym, h in holdings.items():
                cp = c.get(sym, np.nan)
                if not np.isnan(cp) and h["entry"] > 0 and cp / h["entry"] - 1 <= float(stop_loss):
                    if sym not in pending_stop_sells:
                        pending_stop_sells.add(sym)
                        stops.append({"date": d, "symbol": sym, "entry": h["entry"], "stop_close": float(cp),
                                      "loss": float(cp / h["entry"] - 1)})
        # 5. 回撤熔断
        peak = max(peak, total)
        if dd_halt is not None and total / peak - 1 <= float(dd_halt) and halt_until < i:
            halt_until = i + halt_days
            halts.append({"date": d, "drawdown": float(total / peak - 1), "until_idx": halt_until})
        # 6. 信号日 → 生成次日目标
        if d in signal_set and i < len(dates) - 1:
            on = float(ton.get(d, 1.0))
            pending_exposure = on if on > 0 else exp_off
            pending_target = target_weights(sc_w.loc[d], pos)
        prev_close = c

    nav_s = pd.Series(nav, index=dates, name="nav")
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame(columns=["date", "symbol", "side", "value", "stop"])
    return SimResult(nav=nav_s, exposure=pd.Series(expo, index=dates), trades=trades_df, stops=stops,
                     locked=locked, missed=missed, halts=halts, timing_on=ton, holdings=hold_hist)


def run_variants(scores: pd.DataFrame, prices: pd.DataFrame, card: dict, index_close: pd.DataFrame | None,
                 start=None, end=None) -> dict[str, SimResult]:
    out = {}
    for v in ["full", "no_timing", "no_stop", "ideal_fill", "signal_only"]:
        out[v] = simulate(scores, prices, card, index_close, variant=v, start=start, end=end)
    return out


def top_group_weights(scores: pd.DataFrame, card: dict) -> pd.DataFrame:
    """因子对象：每个信号日 top 组等权（无账本时的持仓代理）→ [date, symbol, weight]。"""
    pos = _cfg(card, "position", DEFAULT_POSITION)
    rows = []
    for d, g in scores.groupby("date"):
        w = target_weights(g.set_index("symbol")["score"], pos)
        for sym, x in w.items():
            rows.append({"date": d, "symbol": sym, "weight": float(x)})
    return pd.DataFrame(rows)
