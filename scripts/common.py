#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent-quant-failure-triage · 公共工具：日期、凭证、缓存、术语。

设计原则
- 凭证只从环境变量或 ~/.pandadata/pandadata.env 读取，绝不落盘到仓库。
- 所有在线取数都可以被"离线文件"替代，保证无 panda_data 也能跑通全流程与测试。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

BUILD_ID = "agent-quant-failure-triage"
BUILD_NAME = "策略与因子失效分诊"
DATA_VERSION = "pandadata-failure-triage-v1"

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / ".cache"
OUTPUT_DIR = REPO_ROOT / "outputs"

# 风格环境用到的五个宽基指数（PandaData get_index_daily 已实测可取）
STYLE_INDEX = {
    "000688.SH": "科创50",
    "399006.SZ": "创业板指",
    "000852.SH": "中证1000",
    "000300.SH": "沪深300",
    "000016.SH": "上证50",
}
# PandaData get_factor 提供的 Barra 风格暴露字段（已实测）
STYLE_FIELDS = ["beta", "momentum", "size", "liquidity", "residual_volatility", "growth", "leverage", "profitability"]

LAYERS = ["data", "L0", "L1", "L2", "L3", "L4", "L5", "crowd"]
LAYER_NAME = {
    "data": "数据分布",
    "L0": "股票池",
    "L1": "信号预测力",
    "L2": "风格暴露",
    "L3": "择时开关",
    "L4": "风控规则",
    "L5": "执行",
    "crowd": "拥挤度",
}


def compact(d: Any) -> str:
    """任意日期 → 'YYYYMMDD'。"""
    if d is None or (isinstance(d, float) and pd.isna(d)):
        return ""
    return pd.to_datetime(str(d)).strftime("%Y%m%d")


def norm_dates(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str.replace("-", "").str.slice(0, 8), format="%Y%m%d", errors="coerce")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ----------------------------------------------------------------------------
# 凭证与 panda_data
# ----------------------------------------------------------------------------
def _load_env_file() -> dict:
    p = Path(os.path.expanduser("~/.pandadata/pandadata.env"))
    out: dict = {}
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def init_panda() -> Any:
    """初始化 panda_data。优先环境变量，其次官方凭证文件；缺失时抛 RuntimeError（由调用方降级到离线）。"""
    try:
        import panda_data  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("未安装 panda_data；离线模式请在策略卡片 data 段提供行情文件") from exc
    env = _load_env_file()
    user = os.getenv("PANDA_USERNAME") or os.getenv("PANDA_DATA_USERNAME") or env.get("DEFAULT_USERNAME")
    pwd = os.getenv("PANDA_PASSWORD") or os.getenv("PANDA_DATA_PASSWORD") or env.get("DEFAULT_PASSWORD")
    base = os.getenv("PANDA_BASE_URL") or env.get("JAVA_SERVICE_BASE_URL")
    if not (user and pwd):
        raise RuntimeError("缺少 PandaData 凭证：设置 PANDA_USERNAME / PANDA_PASSWORD 或 ~/.pandadata/pandadata.env")
    if base:
        panda_data.init_token(username=user, password=pwd, base_url=base)
    else:
        panda_data.init_token(username=user, password=pwd)
    return panda_data


# ----------------------------------------------------------------------------
# 本地缓存（parquet）
# ----------------------------------------------------------------------------
def cache_path(kind: str, **key: Any) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.md5(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    return CACHE_DIR / f"{kind}_{h}.parquet"


def cached(kind: str, loader, **key: Any) -> pd.DataFrame:
    p = cache_path(kind, **key)
    if p.is_file():
        try:
            return pd.read_parquet(p)
        except Exception:
            pass
    df = loader()
    if df is not None and not df.empty:
        try:
            df.to_parquet(p, index=False)
        except Exception:
            pass
    return df if df is not None else pd.DataFrame()


# ----------------------------------------------------------------------------
# 术语表：学术读数 → 人话（报告双行格式共用）
# ----------------------------------------------------------------------------
GLOSSARY = {
    "Rank IC": "因子打分和下一期收益的排序相关性，衡量\"打分还准不准\"",
    "PSI": "总体稳定性指数，衡量输入分布和训练时相比变了多少；大于 0.25 通常算变得厉害",
    "KS": "两组分布最大差距，越大说明现在的输入和过去越不像",
    "变点检测": "在时间序列里找\"从哪天起规律变了\"的统计方法",
    "风格暴露": "你的持仓在小盘、动量、波动等风格上押了多少，哪怕你没打算押",
    "反事实": "把策略某一层拿掉重新算一遍，看那一层到底帮了还是害了",
    "whipsaw": "止损刚打掉就反弹，反复被来回扫，止损变成追涨杀跌",
    "涨跌停裂口": "信号说卖但跌停卖不掉、说买但涨停买不到，回测和实盘之间的差距",
    "拥挤度": "同一个想法有多少人在做，人越多越容易一起踩踏",
    "重训禁令": "当前环境下不允许用最近数据重新训练模型，防止把极端噪音学成规律",
}
