#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场环境数据：在线（PandaData）或离线（策略卡片 data 段提供的文件）。

在线接口（均已按本地接口文档与真机核对）
- get_stock_daily(symbol=[], indicator=<股票池>, st=True)  → 日线 + 涨跌停价 + 停牌状态
- get_index_daily(symbol=[...])                            → 宽基指数日线
- get_factor(symbol=..., factors=STYLE_FIELDS, index_component=<池>) → Barra 风格暴露
- get_margin(symbol=...)                                     → 融资余额（拥挤旁证，可选）
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd

from adapters import load_prices
from common import STYLE_FIELDS, STYLE_INDEX, cached, compact, norm_dates

CHUNK_DAYS = 45  # 全池日线分段拉取，避免单次超限


class MarketData:
    """把三类市场数据打包：prices（个股日线）、index（指数日线）、style（风格暴露）。"""

    def __init__(self, prices: pd.DataFrame, index: pd.DataFrame, style: pd.DataFrame | None = None,
                 margin: pd.DataFrame | None = None, source: str = "offline"):
        self.prices = prices
        self.index = index
        self.style = style if style is not None else pd.DataFrame()
        self.margin = margin if margin is not None else pd.DataFrame()
        self.source = source

    # ---- 派生 ----
    def close_wide(self) -> pd.DataFrame:
        return self.prices.pivot(index="date", columns="symbol", values="close").sort_index()

    def returns_wide(self) -> pd.DataFrame:
        return self.close_wide().pct_change(fill_method=None)

    def index_close(self) -> pd.DataFrame:
        if self.index.empty:
            return pd.DataFrame()
        return self.index.pivot(index="date", columns="symbol", values="close").sort_index()

    def index_amount(self) -> pd.DataFrame:
        if self.index.empty or "amount" not in self.index.columns:
            return pd.DataFrame()
        return self.index.pivot(index="date", columns="symbol", values="amount").sort_index()


# ----------------------------------------------------------------------------
# 在线
# ----------------------------------------------------------------------------
def _norm_daily(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["date"] = norm_dates(df["date"])
    for c in ["open", "high", "low", "close", "pre_close", "volume", "amount", "limit_up", "limit_down"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "trade_status" not in df.columns:
        df["trade_status"] = 0
    df["limit_inferred"] = False
    return df.dropna(subset=["date", "symbol", "close"]).sort_values(["date", "symbol"]).reset_index(drop=True)


def fetch_stock_daily(pd_api: Any, start: str, end: str, symbols: list[str] | None = None,
                      indicator: str = "") -> pd.DataFrame:
    """分段拉日线。symbols 为空 → 用 indicator 股票池（如 000852）或全市场。"""
    def _load() -> pd.DataFrame:
        s, e = pd.to_datetime(start), pd.to_datetime(end)
        parts = []
        cur = s
        while cur <= e:
            nxt = min(cur + timedelta(days=CHUNK_DAYS), e)
            df = pd_api.get_stock_daily(symbol=symbols or [], start_date=compact(cur), end_date=compact(nxt),
                                        fields=[], indicator=indicator, st=True)
            if df is not None and not df.empty:
                parts.append(df)
            cur = nxt + timedelta(days=1)
        return _norm_daily(pd.concat(parts, ignore_index=True)) if parts else pd.DataFrame()
    return cached("daily", _load, start=compact(start), end=compact(end), symbols=sorted(symbols or []), indicator=indicator)


def fetch_index_daily(pd_api: Any, start: str, end: str, codes: list[str] | None = None) -> pd.DataFrame:
    codes = codes or list(STYLE_INDEX)

    def _load() -> pd.DataFrame:
        parts = []
        for c in codes:
            df = pd_api.get_index_daily(symbol=[c], start_date=compact(start), end_date=compact(end), fields=[])
            if df is not None and not df.empty:
                parts.append(df)
        if not parts:
            return pd.DataFrame()
        df = pd.concat(parts, ignore_index=True)
        df["date"] = norm_dates(df["date"])
        for col in ["open", "high", "low", "close", "pre_close", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.sort_values(["symbol", "date"]).reset_index(drop=True)
    return cached("index", _load, start=compact(start), end=compact(end), codes=sorted(codes))


def fetch_style(pd_api: Any, start: str, end: str, symbols: list[str] | None = None,
                index_component: str = "") -> pd.DataFrame:
    """Barra 风格暴露。风格变化慢，按周抽样（每 5 个交易日一截面）再前向填充，控制流量。"""
    def _load() -> pd.DataFrame:
        parts = []
        s, e = pd.to_datetime(start), pd.to_datetime(end)
        cur = s
        while cur <= e:
            nxt = min(cur + timedelta(days=CHUNK_DAYS), e)
            kw = dict(start_date=compact(cur), end_date=compact(nxt), factors=STYLE_FIELDS, type="stock")
            if symbols:
                kw["symbol"] = symbols
            if index_component:
                kw["index_component"] = index_component
            try:
                df = pd_api.get_factor(**kw)
            except Exception as exc:  # 风格字段非必需，失败降级
                print(f"  [warn] get_factor 失败，风格暴露降级为指数代理: {str(exc)[:120]}")
                return pd.DataFrame()
            if df is not None and not df.empty:
                parts.append(df)
            cur = nxt + timedelta(days=1)
        if not parts:
            return pd.DataFrame()
        df = pd.concat(parts, ignore_index=True)
        df["date"] = norm_dates(df["date"])
        for c in STYLE_FIELDS:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["date", "symbol"]).sort_values(["date", "symbol"]).reset_index(drop=True)
    return cached("style", _load, start=compact(start), end=compact(end), symbols=sorted(symbols or []), pool=index_component)


def fetch_margin(pd_api: Any, start: str, end: str, symbols: list[str]) -> pd.DataFrame:
    def _load() -> pd.DataFrame:
        parts = []
        for sym in symbols[:200]:  # 拥挤旁证，限流
            try:
                df = pd_api.get_margin(symbol=sym, start_date=compact(start), end_date=compact(end), fields=[])
            except Exception:
                continue
            if df is not None and not df.empty:
                parts.append(df)
        if not parts:
            return pd.DataFrame()
        df = pd.concat(parts, ignore_index=True)
        df["date"] = norm_dates(df["date"])
        df["margin_balance"] = pd.to_numeric(df.get("margin_balance"), errors="coerce")
        return df[["date", "symbol", "margin_balance"]].dropna()
    return cached("margin", _load, start=compact(start), end=compact(end), symbols=sorted(symbols)[:200])


def load_market(card: dict, start: str, end: str, symbols: list[str], offline: bool = False) -> MarketData:
    """按策略卡片 data 段决定在线/离线。离线：prices 必填，index/style/margin 可选。"""
    data = card.get("data") or {}
    if offline or data.get("prices"):
        prices = load_prices(data["prices"]) if data.get("prices") else pd.DataFrame()
        index = pd.DataFrame()
        if data.get("index"):
            index = load_prices(data["index"])[["date", "symbol", "open", "high", "low", "close", "pre_close", "volume", "amount"]]
        style = pd.DataFrame()
        if data.get("style"):
            style = pd.read_parquet(data["style"]) if str(data["style"]).endswith(".parquet") else pd.read_csv(data["style"])
            style["date"] = norm_dates(style["date"])
        if prices.empty:
            raise ValueError("离线模式需要 data.prices（行情文件）")
        return MarketData(prices, index, style, source="offline")
    from common import init_panda
    pd_api = init_panda()
    pool = str(data.get("index_component") or card.get("universe", {}).get("index_component") or "")
    prices = fetch_stock_daily(pd_api, start, end, symbols=symbols if not pool else None, indicator=pool)
    if pool and symbols:
        # 池内可能不含用户票，补齐
        missing = sorted(set(symbols) - set(prices["symbol"]))
        if missing:
            extra = fetch_stock_daily(pd_api, start, end, symbols=missing)
            prices = pd.concat([prices, extra], ignore_index=True).drop_duplicates(["date", "symbol"])
    index = fetch_index_daily(pd_api, start, end)
    style = fetch_style(pd_api, start, end, symbols=None if pool else symbols, index_component=pool)
    return MarketData(prices, index, style, source="pandadata")
