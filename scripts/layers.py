#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分层取证：数据分布 / L0 股票池 / L1 信号 / L2 风格 / 拥挤 / L3 择时 / L4 风控 / L5 执行 / 环境。

每条线返回同一结构（dict）：
{
  "layer": "L1", "name": "信号预测力",
  "status": "healthy | watch | warning | failed | insufficient",
  "contribution": float | None,      # 观察窗内对策略收益的贡献（小数），None = 不适用
  "metrics": {...},                  # 学术读数
  "academic": "一句学术读数",
  "plain": "一句人话",
  "evidence": [ "...", ... ],
}
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from common import LAYER_NAME, STYLE_FIELDS, STYLE_INDEX

EPS = 1e-12


def _res(layer: str, status: str, contribution, metrics: dict, academic: str, plain: str, evidence: list) -> dict:
    return {"layer": layer, "name": LAYER_NAME.get(layer, layer), "status": status,
            "contribution": None if contribution is None else float(contribution),
            "metrics": {k: (None if (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else v) for k, v in metrics.items()},
            "academic": academic, "plain": plain, "evidence": evidence}


def _pct(x: float | None) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.1f}%"


def _window(df: pd.DataFrame, start, end) -> pd.DataFrame:
    return df[(df["date"] >= pd.to_datetime(start)) & (df["date"] <= pd.to_datetime(end))]


# ----------------------------------------------------------------------------
# 数据分布漂移（唯一能做事前预警的层）
# ----------------------------------------------------------------------------
def psi(base: np.ndarray, obs: np.ndarray, bins: int = 10) -> float:
    base, obs = base[~np.isnan(base)], obs[~np.isnan(obs)]
    if len(base) < 20 or len(obs) < 20:
        return float("nan")
    qs = np.unique(np.quantile(base, np.linspace(0, 1, bins + 1)))
    if len(qs) < 3:
        return 0.0
    qs[0], qs[-1] = -np.inf, np.inf
    b = np.histogram(base, qs)[0] / len(base)
    o = np.histogram(obs, qs)[0] / len(obs)
    b, o = np.clip(b, 1e-4, None), np.clip(o, 1e-4, None)
    return float(np.sum((o - b) * np.log(o / b)))


def ks_stat(base: np.ndarray, obs: np.ndarray) -> float:
    base, obs = np.sort(base[~np.isnan(base)]), np.sort(obs[~np.isnan(obs)])
    if len(base) < 20 or len(obs) < 20:
        return float("nan")
    allv = np.concatenate([base, obs])
    cb = np.searchsorted(base, allv, side="right") / len(base)
    co = np.searchsorted(obs, allv, side="right") / len(obs)
    return float(np.max(np.abs(cb - co)))


def diag_data_drift(features: pd.DataFrame | None, base_start, base_end, obs_start, obs_end, psi_warn=0.10, psi_fail=0.25) -> dict:
    if features is None or features.empty:
        return _res("data", "insufficient", None, {},
                    "未提供特征矩阵，无法计算 PSI / KS",
                    "没给模型的输入特征，所以做不了\"事前预警\"，只能做事后归因", ["提供 I3 特征矩阵后可解锁本层"])
    cols = [c for c in features.columns if c not in ("date", "symbol")]
    base, obs = _window(features, base_start, base_end), _window(features, obs_start, obs_end)
    rows = []
    for c in cols:
        p, k = psi(base[c].to_numpy(float), obs[c].to_numpy(float)), ks_stat(base[c].to_numpy(float), obs[c].to_numpy(float))
        rows.append({"feature": c, "psi": p, "ks": k})
    tab = pd.DataFrame(rows).dropna()
    if tab.empty:
        return _res("data", "insufficient", None, {}, "样本过少，PSI / KS 不可计算", "特征样本太少，分布比较不可信", [])
    max_psi, share_fail = float(tab["psi"].max()), float((tab["psi"] > psi_fail).mean())
    worst = tab.sort_values("psi", ascending=False).head(5)
    status = "failed" if (max_psi > psi_fail and share_fail >= 0.3) else "warning" if max_psi > psi_fail else "watch" if max_psi > psi_warn else "healthy"
    return _res("data", status, None,
                {"max_psi": max_psi, "share_psi_gt_fail": share_fail, "mean_ks": float(tab["ks"].mean()), "n_features": int(len(tab)),
                 "worst": worst.to_dict("records")},
                f"max PSI={max_psi:.2f}，{share_fail * 100:.0f}% 特征 PSI>{psi_fail}，平均 KS={tab['ks'].mean():.2f}",
                "模型正在处理它训练时没见过的输入" if status in ("failed", "warning") else "输入分布和训练期差不多，模型没有在外推",
                [f"漂移最大的特征：{', '.join(f'{r.feature}({r.psi:.2f})' for r in worst.itertuples())}"])


# ----------------------------------------------------------------------------
# L0 股票池
# ----------------------------------------------------------------------------
def diag_universe(scores: pd.DataFrame, prices: pd.DataFrame, base_start, base_end, obs_start, obs_end) -> dict:
    def stats(s0, s1):
        sc, px = _window(scores, s0, s1), _window(prices, s0, s1)
        if sc.empty or px.empty:
            return None
        pool = set(sc["symbol"])
        pxp = px[px["symbol"].isin(pool)]
        susp = float((pxp["trade_status"] != 0).mean()) if len(pxp) else float("nan")
        st = float(pxp["name"].astype(str).str.contains("ST").mean()) if "name" in pxp.columns and len(pxp) else float("nan")
        locked = float(((pxp["open"] <= pxp["limit_down"] + 1e-9) | (pxp["open"] >= pxp["limit_up"] - 1e-9)).mean()) if len(pxp) else float("nan")
        return {"n_pool": len(pool), "suspend_ratio": susp, "st_ratio": st, "open_locked_ratio": locked}
    b, o = stats(base_start, base_end), stats(obs_start, obs_end)
    if b is None or o is None:
        return _res("L0", "insufficient", None, {}, "基准窗或观察窗无数据", "这段没数据，股票池看不了", [])
    pool_b, pool_o = set(_window(scores, base_start, base_end)["symbol"]), set(_window(scores, obs_start, obs_end)["symbol"])
    churn = len(pool_b ^ pool_o) / max(1, len(pool_b | pool_o))
    locked_delta = (o["open_locked_ratio"] or 0) - (b["open_locked_ratio"] or 0)
    status = "warning" if (churn > 0.3 or locked_delta > 0.03 or (o["suspend_ratio"] or 0) > 0.05) else "watch" if (churn > 0.15 or locked_delta > 0.01) else "healthy"
    return _res("L0", status, None,
                {"base": b, "obs": o, "pool_churn": churn, "open_locked_delta": locked_delta},
                f"池成员变动率 {churn * 100:.0f}%，开盘触板占比 {(b['open_locked_ratio'] or 0) * 100:.1f}%→{(o['open_locked_ratio'] or 0) * 100:.1f}%，停牌占比 {(o['suspend_ratio'] or 0) * 100:.1f}%",
                "分母变了，IC 都不可比" if status == "warning" else "股票池本身基本稳定",
                [f"基准窗池 {b['n_pool']} 只 → 观察窗池 {o['n_pool']} 只"])


# ----------------------------------------------------------------------------
# L1 信号预测力
# ----------------------------------------------------------------------------
def forward_returns(prices: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """信号日 t 的前瞻收益：t+1 开盘买入 → t+1+horizon 收盘（避免用到信号日收盘）。"""
    o = prices.pivot(index="date", columns="symbol", values="open").sort_index()
    c = prices.pivot(index="date", columns="symbol", values="close").sort_index()
    fwd = c.shift(-(1 + horizon)) / o.shift(-1) - 1
    return fwd.stack().rename("fwd").reset_index().rename(columns={"level_0": "date", "level_1": "symbol"})


def daily_rank_ic(scores: pd.DataFrame, prices: pd.DataFrame, horizon: int = 5, min_n: int = 20) -> pd.Series:
    fwd = forward_returns(prices, horizon)
    m = scores.merge(fwd, on=["date", "symbol"], how="inner").dropna()
    out = {}
    for d, g in m.groupby("date"):
        if len(g) >= min_n:
            out[d] = float(g["score"].rank().corr(g["fwd"].rank()))
    return pd.Series(out).sort_index()


def page_hinkley(x: pd.Series, delta_sigma: float = 0.25, lam_sigma: float = 5.0) -> pd.Timestamp | None:
    """Page-Hinkley 变点检测（检测均值下降）。阈值按序列自身标准差缩放，返回首次报警日期。

    U_t = Σ (x_i − x̄_t + δ)，M_t = max U_t，当 M_t − U_t > λ 报警。
    +δ 使稳定期 U_t 缓慢上行，只有持续低于均值超过 δ 的下降才会触发。
    """
    if len(x) < 10:
        return None
    sigma = float(x.std(ddof=1)) or 1e-6
    delta, lam = delta_sigma * sigma, lam_sigma * sigma
    mean, u_t, big_m = 0.0, 0.0, 0.0
    for i, v in enumerate(x.values):
        mean = mean + (v - mean) / (i + 1)
        u_t += v - mean + delta
        big_m = max(big_m, u_t)
        if big_m - u_t > lam:
            return x.index[i]
    return None


def diag_signal(scores: pd.DataFrame, prices: pd.DataFrame, base_start, base_end, obs_start, obs_end,
                horizon: int = 5, contribution: float | None = None) -> dict:
    ic = daily_rank_ic(scores, prices, horizon)
    ic_b, ic_o = ic[(ic.index >= pd.to_datetime(base_start)) & (ic.index <= pd.to_datetime(base_end))], ic[(ic.index >= pd.to_datetime(obs_start)) & (ic.index <= pd.to_datetime(obs_end))]
    if len(ic_b) < 10 or len(ic_o) < 5:
        return _res("L1", "insufficient", contribution, {"n_base": int(len(ic_b)), "n_obs": int(len(ic_o))},
                    "IC 样本不足", "可比的 IC 天数太少，判断不了打分还准不准", [])
    mb, mo = float(ic_b.mean()), float(ic_o.mean())
    sb, so = float(ic_b.std(ddof=1) or 1e-9), float(ic_o.std(ddof=1) or 1e-9)
    icir_b, icir_o = mb / sb, mo / so
    t_o = mo / (so / math.sqrt(len(ic_o)))
    sign_flip = (mb > 0 and mo < 0) or (mb < 0 and mo > 0)
    sgn = np.sign(mb) if mb != 0 else 1.0
    cp = page_hinkley(pd.concat([ic_b, ic_o]) * sgn)                      # 全序列：同号化后检测 |IC| 的均值下降
    cp_near = page_hinkley(pd.concat([ic_b.tail(40), ic_o]) * sgn)        # 近窗：基准窗末 40 日 + 观察窗，避免早期报警后不再重置
    cp_in_obs = cp_near is not None and cp_near >= pd.to_datetime(obs_start)
    if cp_in_obs:
        cp = cp_near
    win_o = float((np.sign(ic_o) == np.sign(mb if mb != 0 else 1)).mean())
    if sign_flip and abs(t_o) > 1.5 and cp_in_obs:
        status = "failed"
    elif sign_flip or (abs(mo) < abs(mb) * 0.3 and cp_in_obs):
        status = "warning"
    elif abs(mo) < abs(mb) * 0.5:
        status = "watch"
    else:
        status = "healthy"
    plain = {"failed": "打分已经反着走了，因子逻辑本身出了问题", "warning": "打分明显变钝，可能在失效边缘",
             "watch": "打分还有效但比以前弱", "healthy": "打分还准，因子逻辑本身还成立"}[status]
    return _res("L1", status, contribution,
                {"ic_mean_base": mb, "ic_mean_obs": mo, "icir_base": icir_b, "icir_obs": icir_o, "t_obs": t_o,
                 "sign_flip": bool(sign_flip), "changepoint": None if cp is None else str(cp.date()), "changepoint_in_obs": bool(cp_in_obs),
                 "win_rate_obs": win_o, "n_obs": int(len(ic_o)), "ic_series": {str(k.date()): float(v) for k, v in ic.items()}},
                f"Rank IC 基准 {mb:+.3f} → 观察 {mo:+.3f}（t={t_o:+.1f}），ICIR {icir_b:+.2f}→{icir_o:+.2f}，{'翻符号' if sign_flip else '未翻符号'}，变点 {cp.date() if cp is not None else '未检出'}",
                plain, [f"观察窗 IC 同号胜率 {win_o * 100:.0f}%"])


# ----------------------------------------------------------------------------
# L2 风格暴露 + 风格环境
# ----------------------------------------------------------------------------
def style_factor_returns(style: pd.DataFrame, rets: pd.DataFrame) -> pd.DataFrame:
    """日截面回归 r_it = a + Σ b_k x_ik(t-1) → 风格收益 b_kt。"""
    cols = [c for c in STYLE_FIELDS if c in style.columns]
    if not cols or style.empty:
        return pd.DataFrame()
    sty = style.set_index(["date", "symbol"])[cols].unstack("symbol").sort_index()
    sty = sty.reindex(rets.index).ffill()  # 风格按周抽样，前向填充
    out = {}
    for d in rets.index[1:]:
        r = rets.loc[d].dropna()
        if len(r) < 50:
            continue
        prev = sty.index[sty.index.get_loc(d) - 1] if sty.index.get_loc(d) > 0 else None
        if prev is None:
            continue
        X = pd.DataFrame({c: sty[c].loc[prev] for c in cols}).reindex(r.index).dropna()
        if len(X) < 50:
            continue
        y = r.loc[X.index].values
        A = np.column_stack([np.ones(len(X)), X.values])
        try:
            beta = np.linalg.lstsq(A, y, rcond=None)[0]
        except Exception:
            continue
        out[d] = dict(zip(["alpha"] + cols, beta))
    return pd.DataFrame(out).T.sort_index()


def active_exposure(holdings: dict, style: pd.DataFrame, universe_mean: pd.DataFrame) -> pd.DataFrame:
    """持仓加权风格暴露 − 池均值 → 主动暴露 [date × style]。"""
    cols = [c for c in STYLE_FIELDS if c in style.columns]
    sty = style.set_index(["date", "symbol"])[cols].unstack("symbol").sort_index()
    rows = {}
    for d, w in holdings.items():
        if not w:
            continue
        idx = sty.index[sty.index <= d]
        if len(idx) == 0:
            continue
        row = sty.loc[idx[-1]]
        ws = pd.Series(w)
        vals = {}
        for c in cols:
            x = row[c].reindex(ws.index)
            ok = x.notna()
            vals[c] = float((x[ok] * ws[ok]).sum() / max(ws[ok].sum(), EPS)) - float(universe_mean.loc[idx[-1], c]) if ok.any() else np.nan
        rows[d] = vals
    return pd.DataFrame(rows).T.sort_index()


def diag_style(holdings: dict, market, base_start, base_end, obs_start, obs_end, strategy_loss: float | None = None) -> dict:
    rets = market.returns_wide()
    idx_close = market.index_close()
    env = {}
    if not idx_close.empty:
        w = idx_close[(idx_close.index >= pd.to_datetime(obs_start)) & (idx_close.index <= pd.to_datetime(obs_end))]
        b = idx_close[(idx_close.index >= pd.to_datetime(base_start)) & (idx_close.index <= pd.to_datetime(base_end))]
        def cum(df, code):
            return float(df[code].iloc[-1] / df[code].iloc[0] - 1) if code in df.columns and len(df) > 1 else float("nan")
        env["size_spread_obs"] = cum(w, "000852.SH") - cum(w, "000016.SH")
        env["growth_spread_obs"] = cum(w, "399006.SZ") - cum(w, "000300.SH")
        env["tech_spread_obs"] = cum(w, "000688.SH") - cum(w, "000016.SH")
        env["size_spread_base"] = cum(b, "000852.SH") - cum(b, "000016.SH")
        r = idx_close.pct_change(fill_method=None)
        for code, nm in [("000688.SH", "vol_kc50"), ("000852.SH", "vol_zz1000")]:
            if code in r.columns:
                env[nm + "_base"] = float(r[code][(r.index >= pd.to_datetime(base_start)) & (r.index <= pd.to_datetime(base_end))].std() * math.sqrt(252))
                env[nm + "_obs"] = float(r[code][(r.index >= pd.to_datetime(obs_start)) & (r.index <= pd.to_datetime(obs_end))].std() * math.sqrt(252))
        amt = market.index_amount()
        if not amt.empty and "000688.SH" in amt.columns:
            ab = amt["000688.SH"][(amt.index >= pd.to_datetime(base_start)) & (amt.index <= pd.to_datetime(base_end))].mean()
            ao = amt["000688.SH"][(amt.index >= pd.to_datetime(obs_start)) & (amt.index <= pd.to_datetime(obs_end))].mean()
            env["amount_ratio_kc50"] = float(ao / ab) if ab else float("nan")
    style = market.style
    if style is None or style.empty or not holdings:
        # 指数代理：持仓超额收益对 (中证1000−上证50) 与 (创业板−沪深300) 回归
        return _style_proxy(holdings, market, env, base_start, base_end, obs_start, obs_end, strategy_loss)
    cols = [c for c in STYLE_FIELDS if c in style.columns]
    fr = style_factor_returns(style, rets)
    if fr.empty:
        return _style_proxy(holdings, market, env, base_start, base_end, obs_start, obs_end, strategy_loss)
    umean = style.groupby("date")[cols].mean()
    ae = active_exposure(holdings, style, umean)
    ae_obs = ae[(ae.index >= pd.to_datetime(obs_start)) & (ae.index <= pd.to_datetime(obs_end))]
    fr_obs = fr[(fr.index >= pd.to_datetime(obs_start)) & (fr.index <= pd.to_datetime(obs_end))]
    if ae_obs.empty or fr_obs.empty:
        return _res("L2", "insufficient", None, env, "风格数据与持仓在观察窗无重叠", "风格暴露算不出来", [])
    # 贡献：Σ_t Σ_k e_k(t-1) × b_k(t)
    ae_lag = ae_obs.reindex(fr_obs.index).ffill().shift(1).bfill()
    contrib_k = (ae_lag[cols] * fr_obs[cols]).sum()
    contrib = float(contrib_k.sum())
    mean_exp = ae_obs[cols].mean()
    big = mean_exp[mean_exp.abs() > 0.5]
    share = None if not strategy_loss or strategy_loss >= 0 else float(min(1.0, max(0.0, contrib / strategy_loss)))
    status = "warning" if (contrib < -0.01 and (share or 0) >= 0.5) else "watch" if (len(big) > 0 or contrib < -0.005) else "healthy"
    worst_k = contrib_k.sort_values().head(3)
    academic = f"主动风格暴露：{', '.join(f'{k}={v:+.2f}' for k, v in mean_exp.round(2).items())}；风格贡献 {_pct(contrib)}（{', '.join(f'{k} {_pct(float(v))}' for k, v in worst_k.items())}）"
    plain = ("你其实一直在赌 " + "、".join({"size": "小盘", "momentum": "动量", "residual_volatility": "高波动", "beta": "高贝塔", "liquidity": "高流动性", "growth": "成长", "leverage": "高杠杆", "profitability": "盈利质量"}.get(k, k) + ("(反向)" if v < 0 and k == "size" else "") for k, v in big.items()) + "，这段时间市场正好不给这个风格付钱") if len(big) and contrib < 0 else ("风格暴露不大，亏损主要不是风格环境造成的" if contrib > -0.005 else "有一些风格拖累，但不是主因")
    metrics = {**env, "active_exposure_mean": mean_exp.round(3).to_dict(), "contribution_by_style": contrib_k.round(4).to_dict(),
               "share_of_loss": share, "method": "barra_regression",
               "exposure_series": {str(k.date()): {c: (None if pd.isna(v) else round(float(v), 3)) for c, v in row.items()} for k, row in ae_obs[cols].iterrows()}}
    return _res("L2", status, contrib, metrics, academic, plain,
                [f"风格环境：中证1000−上证50 价差 {_pct(env.get('size_spread_obs'))}，创业板−沪深300 {_pct(env.get('growth_spread_obs'))}",
                 f"科创50 年化波动 {env.get('vol_kc50_base', float('nan')) * 100:.0f}%→{env.get('vol_kc50_obs', float('nan')) * 100:.0f}%，成交额比 {env.get('amount_ratio_kc50', float('nan')):.2f}" if "vol_kc50_obs" in env else "指数数据缺失"])


def _style_proxy(holdings, market, env, base_start, base_end, obs_start, obs_end, strategy_loss):
    idx_close = market.index_close()
    if idx_close.empty or not holdings or not {"000852.SH", "000016.SH", "399006.SZ", "000300.SH"} <= set(idx_close.columns):
        return _res("L2", "insufficient", None, env, "无风格暴露数据，也无指数代理", "风格这条线没数据", [])
    rets = market.returns_wide()
    r_idx = idx_close.pct_change(fill_method=None)
    port = {}
    for d, w in holdings.items():
        nxt = rets.index[rets.index > d]
        if len(nxt) == 0 or not w:
            continue
        d1 = nxt[0]
        ws = pd.Series(w)
        rr = rets.loc[d1].reindex(ws.index)
        ok = rr.notna()
        port[d1] = float((rr[ok] * ws[ok]).sum() / max(ws[ok].sum(), EPS)) if ok.any() else np.nan
    p = pd.Series(port).dropna()
    df = pd.DataFrame({"p": p, "size": r_idx["000852.SH"] - r_idx["000016.SH"], "growth": r_idx["399006.SZ"] - r_idx["000300.SH"], "mkt": r_idx["000300.SH"]}).dropna()
    win = df[(df.index >= pd.to_datetime(obs_start)) & (df.index <= pd.to_datetime(obs_end))]
    full = df[(df.index >= pd.to_datetime(base_start)) & (df.index <= pd.to_datetime(obs_end))]
    if len(full) < 30:
        return _res("L2", "insufficient", None, env, "样本不足以做指数代理回归", "风格这条线样本太少", [])
    A = np.column_stack([np.ones(len(full)), full[["mkt", "size", "growth"]].values])
    beta = np.linalg.lstsq(A, full["p"].values, rcond=None)[0]
    b_size, b_growth = float(beta[2]), float(beta[3])
    contrib = float(b_size * win["size"].sum() + b_growth * win["growth"].sum()) if len(win) else float("nan")
    share = None if not strategy_loss or strategy_loss >= 0 else float(min(1.0, max(0.0, contrib / strategy_loss)))
    status = "warning" if (contrib < -0.01 and (share or 0) >= 0.5) else "watch" if abs(b_size) > 0.5 or abs(b_growth) > 0.5 else "healthy"
    return _res("L2", status, contrib, {**env, "beta_size": b_size, "beta_growth": b_growth, "share_of_loss": share, "method": "index_proxy"},
                f"指数代理：β_小盘={b_size:+.2f}，β_成长={b_growth:+.2f}，风格贡献 {_pct(contrib)}",
                ("你其实一直在赌小盘/成长，这段时间市场正好不给这个风格付钱" if contrib < -0.01 else "风格拖累不大"),
                [f"风格环境：中证1000−上证50 价差 {_pct(env.get('size_spread_obs'))}，创业板−沪深300 {_pct(env.get('growth_spread_obs'))}"])


# ----------------------------------------------------------------------------
# 拥挤度
# ----------------------------------------------------------------------------
def diag_crowding(holdings: dict, market, base_start, base_end, obs_start, obs_end) -> dict:
    rets = market.returns_wide()
    amt = market.prices.pivot(index="date", columns="symbol", values="amount").sort_index()

    def group_stats(s0, s1):
        days = [d for d in holdings if pd.to_datetime(s0) <= d <= pd.to_datetime(s1) and holdings[d]]
        if len(days) < 3:
            return None
        corr, ashare, skew = [], [], []
        for d in days:
            syms = [s for s in holdings[d] if s in rets.columns]
            nxt = rets.index[(rets.index > d)][:5]
            if len(syms) < 5 or len(nxt) == 0:
                continue
            sub = rets.loc[nxt, syms].dropna(axis=1, how="any")
            if sub.shape[1] >= 5 and sub.shape[0] >= 3:
                c = sub.corr().values
                corr.append(float((c.sum() - np.trace(c)) / (c.shape[0] * (c.shape[0] - 1))))
            if d in amt.index:
                tot = amt.loc[d].sum()
                ashare.append(float(amt.loc[d, [s for s in syms if s in amt.columns]].sum() / tot) if tot else np.nan)
        return {"pair_corr": float(np.nanmean(corr)) if corr else float("nan"), "amount_share": float(np.nanmean(ashare)) if ashare else float("nan"), "n_days": len(days)}
    b, o = group_stats(base_start, base_end), group_stats(obs_start, obs_end)
    if b is None or o is None:
        return _res("crowd", "insufficient", None, {}, "持仓天数不足", "持仓样本太少，看不出拥挤", [])
    d_corr = (o["pair_corr"] or 0) - (b["pair_corr"] or 0)
    ratio_amt = (o["amount_share"] / b["amount_share"]) if b["amount_share"] else float("nan")
    # 负偏尖峰：观察窗内持仓组合单日跌幅超过 2 倍基准窗日波动的天数
    port = []
    for d, w in holdings.items():
        nxt = rets.index[rets.index > d]
        if len(nxt) and w:
            ws = pd.Series(w); rr = rets.loc[nxt[0]].reindex(ws.index); ok = rr.notna()
            if ok.any():
                port.append((nxt[0], float((rr[ok] * ws[ok]).sum() / ws[ok].sum())))
    ps = pd.Series(dict(port)).sort_index()
    pb = ps[(ps.index >= pd.to_datetime(base_start)) & (ps.index <= pd.to_datetime(base_end))]
    po = ps[(ps.index >= pd.to_datetime(obs_start)) & (ps.index <= pd.to_datetime(obs_end))]
    crash_days = int((po < -2 * pb.std()).sum()) if len(pb) > 5 else 0
    score = (1 if d_corr > 0.15 else 0) + (1 if ratio_amt and ratio_amt > 1.3 else 0) + (1 if crash_days >= 2 else 0)
    status = "warning" if score >= 2 else "watch" if score == 1 else "healthy"
    return _res("crowd", status, None,
                {"pair_corr_base": b["pair_corr"], "pair_corr_obs": o["pair_corr"], "amount_share_base": b["amount_share"], "amount_share_obs": o["amount_share"], "crash_days": crash_days, "score": score},
                f"持仓间平均相关性 {b['pair_corr']:.2f}→{o['pair_corr']:.2f}，成交额占比比值 {ratio_amt:.2f}，尖峰下跌日 {crash_days} 天",
                "大家挤在同一个门里，一有风吹草动一起往外跑" if status == "warning" else ("有点拥挤迹象" if status == "watch" else "没有明显拥挤"), [])


# ----------------------------------------------------------------------------
# L3 择时 / L4 风控 / L5 执行（反事实剥离）
# ----------------------------------------------------------------------------
def diag_timing(sims: dict, card: dict, market, obs_start, obs_end) -> dict:
    rule = str((card.get("timing") or {}).get("rule", "none"))
    if rule in ("none", ""):
        return _res("L3", "insufficient", None, {}, "策略未声明择时层", "这个策略没有择时开关，这一层不适用", [])
    full, nt = sims["full"], sims["no_timing"]
    contrib = full.ret(obs_start, obs_end) - nt.ret(obs_start, obs_end)
    ton = full.timing_on
    tw = ton[(ton.index >= pd.to_datetime(obs_start)) & (ton.index <= pd.to_datetime(obs_end))]
    switches = int((tw.diff().abs() > 0).sum())
    off_share = float((tw == 0).mean()) if len(tw) else 0.0
    status = "healthy" if contrib >= 0 else "warning" if contrib < -0.01 else "watch"
    plain = "开关在帮你，这段时间它躲掉了下跌" if contrib > 0.005 else ("开关反而在害你：该空的时候满仓、该满的时候空仓" if contrib < -0.01 else "开关基本没起作用")
    return _res("L3", status, contrib, {"rule": rule, "switches_obs": switches, "off_share_obs": off_share, "ret_full": full.ret(obs_start, obs_end), "ret_no_timing": nt.ret(obs_start, obs_end)},
                f"去掉择时后观察窗收益 {_pct(nt.ret(obs_start, obs_end))} vs 含择时 {_pct(full.ret(obs_start, obs_end))}，切换 {switches} 次，空仓占比 {off_share * 100:.0f}%", plain, [])


def diag_risk(sims: dict, card: dict, market, obs_start, obs_end, rebound_days: int = 5) -> dict:
    risk = card.get("risk") or {}
    if risk.get("stop_loss") is None and risk.get("max_drawdown_halt") is None:
        return _res("L4", "insufficient", None, {}, "策略未声明止损/熔断", "这个策略没有风控规则，这一层不适用", [])
    full, ns = sims["full"], sims["no_stop"]
    contrib = full.ret(obs_start, obs_end) - ns.ret(obs_start, obs_end)
    close = market.close_wide()
    stops = [s for s in full.stops if pd.to_datetime(obs_start) <= s["date"] <= pd.to_datetime(obs_end)]
    whips = 0
    post = []
    for s in stops:
        sym, d = s["symbol"], s["date"]
        after = close.index[close.index > d]
        if sym not in close.columns or len(after) < 2:
            continue
        exit_px = close.loc[after[0], sym]
        fut = close.loc[after[1:1 + rebound_days], sym].dropna()
        if len(fut) and exit_px:
            mx = float(fut.max() / exit_px - 1)
            post.append(mx)
            if mx >= abs(float(risk.get("stop_loss") or 0.05)) / 2:
                whips += 1
    whip_rate = whips / len(stops) if stops else 0.0
    halts = [h for h in full.halts if pd.to_datetime(obs_start) <= h["date"] <= pd.to_datetime(obs_end)]
    status = "healthy" if contrib >= 0 else ("warning" if (contrib < -0.01 and whip_rate >= 0.5) else "watch")
    plain = ("止损变成了追涨杀跌：刚砍就反弹，反复被扫" if whip_rate >= 0.5 and contrib < 0 else
             ("止损在帮你控制损失" if contrib > 0.005 else "风控规则这段时间既没帮多少也没害多少"))
    return _res("L4", status, contrib, {"n_stops_obs": len(stops), "whipsaw_rate": whip_rate, "avg_post_stop_max_rebound": float(np.mean(post)) if post else None,
                                        "n_halts_obs": len(halts), "ret_full": full.ret(obs_start, obs_end), "ret_no_stop": ns.ret(obs_start, obs_end),
                                        "stop_events": [{"date": str(s["date"].date()), "symbol": s["symbol"], "loss": round(s["loss"], 4)} for s in stops[:50]]},
                f"观察窗触发止损 {len(stops)} 次，其中 {whips} 次在 {rebound_days} 日内反弹超过止损幅度一半（whipsaw 率 {whip_rate * 100:.0f}%）；去掉风控后收益 {_pct(ns.ret(obs_start, obs_end))} vs 含风控 {_pct(full.ret(obs_start, obs_end))}",
                plain, [f"熔断触发 {len(halts)} 次"] if halts else [])


def diag_execution(sims: dict, card: dict, market, base_start, base_end, obs_start, obs_end) -> dict:
    full, ideal = sims["full"], sims["ideal_fill"]
    contrib = full.ret(obs_start, obs_end) - ideal.ret(obs_start, obs_end)
    in_obs = lambda e: pd.to_datetime(obs_start) <= e["date"] <= pd.to_datetime(obs_end)
    in_base = lambda e: pd.to_datetime(base_start) <= e["date"] <= pd.to_datetime(base_end)
    locked_o, missed_o = [e for e in full.locked if in_obs(e)], [e for e in full.missed if in_obs(e)]
    locked_b, missed_b = [e for e in full.locked if in_base(e)], [e for e in full.missed if in_base(e)]
    tr = full.trades
    n_tr_o = int(((tr["date"] >= pd.to_datetime(obs_start)) & (tr["date"] <= pd.to_datetime(obs_end))).sum()) if len(tr) else 0
    n_tr_b = int(((tr["date"] >= pd.to_datetime(base_start)) & (tr["date"] <= pd.to_datetime(base_end))).sum()) if len(tr) else 0
    lock_rate_o = len(locked_o) / max(1, n_tr_o + len(locked_o))
    lock_rate_b = len(locked_b) / max(1, n_tr_b + len(locked_b))
    # 成交额塌缩：持仓票的中位成交额 观察/基准
    amt = market.prices.pivot(index="date", columns="symbol", values="amount").sort_index()
    def med_amt(s0, s1):
        vals = []
        for d, w in full.holdings.items():
            if pd.to_datetime(s0) <= d <= pd.to_datetime(s1) and w and d in amt.index:
                vals.append(float(amt.loc[d, [s for s in w if s in amt.columns]].median()))
        return float(np.nanmedian(vals)) if vals else float("nan")
    ab, ao = med_amt(base_start, base_end), med_amt(obs_start, obs_end)
    amt_ratio = ao / ab if ab and not math.isnan(ab) else float("nan")
    impact_mult = (1 / amt_ratio) if amt_ratio and amt_ratio > 0 else float("nan")
    inferred = bool(market.prices["limit_inferred"].any()) if "limit_inferred" in market.prices.columns else False
    status = "warning" if (contrib < -0.01 or lock_rate_o > 0.1 or (amt_ratio and amt_ratio < 0.6)) else "watch" if (contrib < -0.003 or lock_rate_o > lock_rate_b + 0.03) else "healthy"
    plain = ("信号能出，单子出不掉：跌停卖不掉、涨停买不进，成交额还在萎缩" if status == "warning" else ("执行摩擦有所上升" if status == "watch" else "执行层没有明显问题"))
    return _res("L5", status, contrib,
                {"locked_sells_obs": len(locked_o), "missed_buys_obs": len(missed_o), "lock_rate_obs": lock_rate_o, "lock_rate_base": lock_rate_b,
                 "median_amount_ratio": amt_ratio, "implied_impact_multiple": impact_mult, "ret_full": full.ret(obs_start, obs_end), "ret_ideal": ideal.ret(obs_start, obs_end),
                 "limit_inferred": inferred},
                f"观察窗跌停/停牌卖不掉 {len(locked_o)} 笔、涨停买不进 {len(missed_o)} 笔（触板率 {lock_rate_b * 100:.1f}%→{lock_rate_o * 100:.1f}%），持仓票中位成交额比 {amt_ratio:.2f}（隐含冲击成本 ×{impact_mult:.1f}），执行层贡献 {_pct(contrib)}",
                plain, (["涨跌停价为 ±10% 推断值，非交易所口径"] if inferred else []))


# ----------------------------------------------------------------------------
# 环境刻画（策略家族 → 靠什么吃饭 → 该测什么）
# ----------------------------------------------------------------------------
FAMILY_METRICS = {
    "cross_sectional": ["dispersion", "avg_corr"],
    "momentum": ["autocorr_5d", "trend_strength", "dispersion"],
    "reversal": ["reversal_strength", "dispersion"],
    "limit_up": ["limit_up_count", "blow_up_rate", "dispersion"],
    "timing": ["regime_persistence", "autocorr_5d"],
}


def environment(market, family: str, base_start, base_end, obs_start, obs_end) -> dict:
    rets = market.returns_wide()
    px = market.prices
    idx_close = market.index_close()
    def block(s0, s1):
        r = rets[(rets.index >= pd.to_datetime(s0)) & (rets.index <= pd.to_datetime(s1))]
        p = px[(px["date"] >= pd.to_datetime(s0)) & (px["date"] <= pd.to_datetime(s1))]
        out: dict[str, float] = {}
        if len(r) > 2:
            out["dispersion"] = float(r.std(axis=1).mean())
            mv, vm = float(r.mean(axis=1).var()), float(r.var(axis=0).mean())
            out["avg_corr"] = float(mv / vm) if vm else float("nan")
            nxt = r.shift(-1)
            out["reversal_strength"] = float(-np.nanmean([r.loc[d].corr(nxt.loc[d]) for d in r.index[:-1] if r.loc[d].notna().sum() > 30]))
        if not idx_close.empty and "000852.SH" in idx_close.columns:
            ir = idx_close["000852.SH"].pct_change(fill_method=None)
            ir = ir[(ir.index >= pd.to_datetime(s0)) & (ir.index <= pd.to_datetime(s1))]
            if len(ir) > 10:
                out["autocorr_5d"] = float(ir.autocorr(5)) if len(ir) > 6 else float("nan")
                c = idx_close["000852.SH"][(idx_close.index >= pd.to_datetime(s0)) & (idx_close.index <= pd.to_datetime(s1))]
                out["trend_strength"] = float(abs(c.iloc[-1] / c.iloc[0] - 1) / max(ir.abs().sum(), EPS))
                on = (c > c.rolling(20).mean()).astype(int)
                out["regime_persistence"] = float(len(on) / max(1, int((on.diff().abs() > 0).sum())))
        if len(p):
            lu = (p["close"] >= p["limit_up"] * 0.9999) & (p["trade_status"] == 0)
            touched = (p["high"] >= p["limit_up"] * 0.9999) & (p["trade_status"] == 0)
            out["limit_up_count"] = float(lu.groupby(p["date"]).sum().mean())
            out["blow_up_rate"] = float(1 - lu.sum() / touched.sum()) if touched.sum() else float("nan")
        return out
    b, o = block(base_start, base_end), block(obs_start, obs_end)
    keys = FAMILY_METRICS.get(family, FAMILY_METRICS["cross_sectional"])
    rows = [{"metric": k, "base": b.get(k), "obs": o.get(k)} for k in keys if k in b or k in o]
    return {"family": family, "rows": rows, "all_base": b, "all_obs": o}
