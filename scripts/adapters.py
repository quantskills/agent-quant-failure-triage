#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输入适配：把生态里已有的各种格式统一成分诊需要的三张表。

统一格式
- scores : [date, symbol, score]                 因子打分长表（I1，最小契约）
- ledger : positions [date, symbol, weight]      持仓权重（I2，可选）
           nav       [date, nav]                 净值（I2，可选）
- features: [date, symbol, f1, f2, ...]          特征矩阵（I3，可选，唯一能做事前预警的层）

能识别的来源
1. 量枢院 Alpha 规则 §9 parquet：trade_date / ts_code / score(或 factor_value / rank)
2. skill-factor-* 长表：date / symbol / factor_value
3. signal.csv 宽表：首列日期，其余列为资产
4. PandaAI factor_result JSON：只含 IC 序列时退化为"IC 直接输入"模式（无截面）
5. 任意 DataFrame / records，只要能映射出 date / symbol / score
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from common import norm_dates

DATE_ALIASES = ["date", "trade_date", "signal_date", "datetime", "dt", "日期"]
SYMBOL_ALIASES = ["symbol", "ts_code", "code", "stock", "stock_code", "asset", "证券代码", "代码"]
SCORE_ALIASES = ["score", "factor_value", "value", "signal", "alpha", "factor", "pred", "prediction", "因子值", "得分"]
WEIGHT_ALIASES = ["weight", "w", "position", "pos", "target_weight", "权重", "仓位"]
NAV_ALIASES = ["nav", "net_value", "equity", "净值"]


def _pick(cols: list[str], aliases: list[str]) -> str | None:
    low = {c.lower(): c for c in cols}
    for a in aliases:
        if a.lower() in low:
            return low[a.lower()]
    return None


def _read_any(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"输入文件不存在: {p}")
    suf = p.suffix.lower()
    if suf in (".parquet", ".pq"):
        return pd.read_parquet(p)
    if suf in (".csv", ".txt"):
        return pd.read_csv(p)
    if suf in (".json",):
        obj = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and "factor_analysis" in obj:
            return pd.DataFrame(obj["factor_analysis"])
        return pd.DataFrame(obj)
    if suf in (".xlsx", ".xls"):
        return pd.read_excel(p)
    raise ValueError(f"不支持的文件类型: {suf}")


def _norm_symbol(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip().str.upper()
    # 600000 → 600000.SH / 000001 → 000001.SZ / 430xxx,8xxxxx → .BJ（裸代码兜底）
    def fix(x: str) -> str:
        if "." in x or not x.isdigit() or len(x) != 6:
            return x
        if x.startswith(("6", "5")):
            return x + ".SH"
        if x.startswith(("0", "3", "1")):
            return x + ".SZ"
        return x + ".BJ"
    return s.map(fix)


def load_scores(src: Any, score_col: str | None = None) -> pd.DataFrame:
    """任意来源 → [date, symbol, score]，按 (date, symbol) 去重、排序。"""
    if isinstance(src, (str, Path)):
        df = _read_any(src)
    elif isinstance(src, pd.DataFrame):
        df = src.copy()
    else:
        df = pd.DataFrame(src)
    if df.empty:
        raise ValueError("打分表为空")
    cols = list(df.columns)
    dcol = _pick(cols, DATE_ALIASES)
    scol = _pick(cols, SYMBOL_ALIASES)
    vcol = score_col or _pick(cols, SCORE_ALIASES)
    # 宽表：首列日期，其余列是资产
    if dcol is not None and scol is None and vcol is None and len(cols) > 2:
        long = df.melt(id_vars=[dcol], var_name="symbol", value_name="score")
        long = long.rename(columns={dcol: "date"})
    else:
        if dcol is None or scol is None or vcol is None:
            raise ValueError(f"无法识别 date/symbol/score 列，现有列: {cols}；可用 score_col 指定分数列")
        long = df[[dcol, scol, vcol]].rename(columns={dcol: "date", scol: "symbol", vcol: "score"})
    long["date"] = norm_dates(long["date"])
    long["symbol"] = _norm_symbol(long["symbol"])
    long["score"] = pd.to_numeric(long["score"], errors="coerce")
    long = long.dropna(subset=["date", "symbol", "score"])
    long = long.drop_duplicates(["date", "symbol"], keep="last").sort_values(["date", "symbol"]).reset_index(drop=True)
    if long.empty:
        raise ValueError("打分表清洗后为空（日期或分数无法解析）")
    return long


def load_positions(src: Any) -> pd.DataFrame:
    """持仓/权重表 → [date, symbol, weight]。权重缺失时按等权处理。"""
    df = _read_any(src) if isinstance(src, (str, Path)) else pd.DataFrame(src)
    cols = list(df.columns)
    dcol, scol, wcol = _pick(cols, DATE_ALIASES), _pick(cols, SYMBOL_ALIASES), _pick(cols, WEIGHT_ALIASES)
    if dcol is None or scol is None:
        raise ValueError(f"持仓表需要 date/symbol 列，现有列: {cols}")
    out = df[[dcol, scol] + ([wcol] if wcol else [])].rename(columns={dcol: "date", scol: "symbol"})
    out["date"] = norm_dates(out["date"])
    out["symbol"] = _norm_symbol(out["symbol"])
    if wcol:
        out = out.rename(columns={wcol: "weight"})
        out["weight"] = pd.to_numeric(out["weight"], errors="coerce").fillna(0.0)
    else:
        out["weight"] = 1.0
    out = out.dropna(subset=["date", "symbol"])
    tot = out.groupby("date")["weight"].transform("sum").replace(0, 1.0)
    out["weight"] = out["weight"] / tot
    return out.sort_values(["date", "symbol"]).reset_index(drop=True)


def load_nav(src: Any) -> pd.Series:
    df = _read_any(src) if isinstance(src, (str, Path)) else pd.DataFrame(src)
    cols = list(df.columns)
    dcol, ncol = _pick(cols, DATE_ALIASES), _pick(cols, NAV_ALIASES)
    if dcol is None or ncol is None:
        raise ValueError(f"净值表需要 date/nav 列，现有列: {cols}")
    s = pd.Series(pd.to_numeric(df[ncol], errors="coerce").values, index=norm_dates(df[dcol]))
    s = s.dropna().sort_index()
    return s / s.iloc[0]


def load_features(src: Any) -> pd.DataFrame:
    """特征矩阵 → [date, symbol, f1, f2, ...]，非数值列丢弃。"""
    df = _read_any(src) if isinstance(src, (str, Path)) else pd.DataFrame(src)
    cols = list(df.columns)
    dcol, scol = _pick(cols, DATE_ALIASES), _pick(cols, SYMBOL_ALIASES)
    if dcol is None:
        raise ValueError(f"特征表需要 date 列，现有列: {cols}")
    df = df.rename(columns={dcol: "date"})
    df["date"] = norm_dates(df["date"])
    if scol:
        df = df.rename(columns={scol: "symbol"})
        df["symbol"] = _norm_symbol(df["symbol"])
    keep = ["date"] + (["symbol"] if scol else [])
    num = [c for c in df.columns if c not in keep and pd.api.types.is_numeric_dtype(df[c])]
    return df[keep + num].dropna(subset=["date"]).reset_index(drop=True)


def load_prices(src: Any) -> pd.DataFrame:
    """离线行情 → 与 PandaData get_stock_daily 同口径：
    [date, symbol, open, high, low, close, pre_close, volume, amount, limit_up, limit_down, trade_status]。
    缺失的 limit_up/limit_down 按 ±10% 兜底（并标记 limit_inferred=True）。"""
    df = _read_any(src) if isinstance(src, (str, Path)) else pd.DataFrame(src)
    cols = list(df.columns)
    dcol, scol = _pick(cols, DATE_ALIASES), _pick(cols, SYMBOL_ALIASES)
    if dcol is None or scol is None:
        raise ValueError(f"行情表需要 date/symbol 列，现有列: {cols}")
    df = df.rename(columns={dcol: "date", scol: "symbol"})
    df["date"] = norm_dates(df["date"])
    df["symbol"] = _norm_symbol(df["symbol"])
    for c in ["open", "high", "low", "close", "pre_close", "volume", "amount", "limit_up", "limit_down"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "close" not in df.columns:
        raise ValueError("行情表至少需要 close 列")
    for c in ["open", "high", "low"]:
        if c not in df.columns:
            df[c] = df["close"]
    if "pre_close" not in df.columns:
        df = df.sort_values(["symbol", "date"])
        df["pre_close"] = df.groupby("symbol")["close"].shift(1)
    inferred = False
    if "limit_up" not in df.columns or "limit_down" not in df.columns:
        inferred = True
        df["limit_up"] = (df["pre_close"] * 1.10).round(2)
        df["limit_down"] = (df["pre_close"] * 0.90).round(2)
    if "trade_status" not in df.columns:
        df["trade_status"] = 0
    if "amount" not in df.columns:
        df["amount"] = df.get("volume", 0) * df["close"]
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df["limit_inferred"] = inferred
    return df.dropna(subset=["date", "symbol", "close"]).sort_values(["date", "symbol"]).reset_index(drop=True)
