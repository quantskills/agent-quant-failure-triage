# PandaData 接口与真机口径

以本地《PandaData 接口文档》为准，以下均已真机实测（2026-09-07）。

| 诊断线 | 接口 | 参数 | 用途 | 实测字段 |
|---|---|---|---|---|
| L0 / L5 / L1 | `get_stock_daily` | `symbol=[]`, `indicator="000852"`（股票池）, `st=True` | 日线、涨跌停价、停牌状态 | `symbol, date, open, high, low, close, volume, amount, pre_close, limit_up, limit_down, name, trade_status` |
| L2 环境 | `get_index_daily` | `symbol=["000688.SH"]` 等 | 科创50 / 创业板指 / 中证1000 / 沪深300 / 上证50 | `symbol, date, open, high, low, close, volume, amount, pre_close` |
| L2 暴露 | `get_factor` | `factors=[beta, momentum, size, liquidity, residual_volatility, growth, leverage, profitability]`, `index_component="000852"`, `type="stock"` | Barra 风格暴露（已标准化的 z 值） | 上述字段 + `market_cap, turnover, close` |
| 拥挤（可选） | `get_margin` | `symbol=<单票>` | 融资余额 | `margin_balance, buy_on_margin_value, ...` |

## 口径要点

- `get_stock_daily` 单次窗口 ≤ 5 年；本项目按 45 天分段拉取并缓存到 `.cache/`（gitignored）。
- 风格暴露变化慢，`fetch_style` 也分段拉取，回归时按前一交易日暴露（避免同期偏差），缺失日前向填充。
- `trade_status != 0` 视为停牌；开盘价 ≤ 跌停价视为卖不掉，≥ 涨停价视为买不进。
- ST 股 `st=True` 保留，不在数据层过滤（涨停幅度差异由价格字段本身体现）。
- 流量限额（错误码 500009）：大池长窗口请分段、复用缓存。

## 指数代码

| 代码 | 指数 | 用途 |
|---|---|---|
| 000688.SH | 科创50 | 科技 / 高波动风格；波动率与成交额环境 |
| 399006.SZ | 创业板指 | 成长风格（与沪深300 价差） |
| 000852.SH | 中证1000 | 小盘风格（与上证50 价差）；默认基准与择时指数 |
| 000300.SH | 沪深300 | 大盘 |
| 000016.SH | 上证50 | 超大盘 |
