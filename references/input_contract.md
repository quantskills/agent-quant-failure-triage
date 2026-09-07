# 输入契约：给什么做什么，做不到的如实说

## 一、三层输入

| 层 | 内容 | 解锁 | 给不到时报告怎么说 |
|---|---|---|---|
| I1 最小 | 打分表 `[date, symbol, score]` | L1 预测力、L2 风格（top 组代理）、拥挤、L0 | "只能做事后归因，做不了事前预警" |
| I2 账本 | + 策略卡片各层规则；可选真实持仓 `[date, symbol, weight]`、净值 `[date, nav]` | L3 / L4 / L5 反事实剥离、真实超额 | 缺真实账本时用卡片规则模拟持仓 |
| I3 特征 | + 特征矩阵 `[date, symbol, f1, f2, ...]` | 数据分布漂移（唯一能事前预警的层） | 数据层标 `insufficient` |

## 二、打分表能吃的格式（`scripts/adapters.py`）

| 来源 | 识别方式 |
|---|---|
| 量枢院 Alpha 规则 §9 parquet | `trade_date / ts_code / score`（或 `factor_value`） |
| skill-factor-* 长表 | `date / symbol / factor_value` |
| signal.csv 宽表 | 首列日期，其余列为资产代码 |
| PandaAI `get_factor` 面板 | `date / symbol / <因子列>`，用 `signal.score_col` 指定 |
| 任意 DataFrame / records | 列名别名：date=trade_date/signal_date/日期；symbol=ts_code/code/代码；score=factor_value/value/signal/alpha/pred |

裸 6 位代码自动补后缀（6/5 开头 .SH，0/3/1 开头 .SZ，其余 .BJ）。

## 三、策略卡片（`examples/card_strategy.yaml`）

```yaml
strategy_id: <对象名>
object_type: strategy | factor
family: cross_sectional | momentum | reversal | limit_up | timing   # 决定环境刻画看哪些量
signal:    {score_table: <路径>, score_col: <可选>}
position:  {method: top_n|top_pct|threshold, n: 30, weight: equal|score, leverage: 1.0}
timing:    {rule: none|index_above_ma|series, index: 000852.SH, window: 20, file: <series 时>, exposure_when_off: 0.0}
risk:      {stop_loss: -0.08|null, max_drawdown_halt: -0.15|null, halt_days: 5}
execution: {rebalance_days: 5, cost_bps: 15, respect_limits: true}
benchmark: {index: 000852.SH}
ledger:    {positions: <可选>, nav: <可选>}
features:  {table: <可选>}
data:      {index_component: "000852", prices: <离线>, index: <离线>, style: <离线>}
```

只有卡片里声明的层才会做反事实剥离；未声明的层报告标 `insufficient`（"这一层不适用"），不假装能算。

## 四、离线模式

卡片 `data.prices` 存在即自动离线（或 `--offline`）。行情文件最少需要 `date, symbol, close`；缺 `limit_up/limit_down` 时按 ±10% 推断并在报告里标注"非交易所口径"。
