# 🩺 策略与因子失效分诊 Agent

**简体中文** | [English](README.en.md)

> 一个因子或策略最近跑输了。**它现在还能不能用？坏的是哪一层？是市场变了还是模型坏了？现在该不该重训？**
> 本 Agent 只回答这四个问题，并且在答不出来的时候明说。

> 项目状态：QuantSkills **社区项目（Community Project）**，未经维护者审核 / 认证 / 背书。仅供量化研究与教育示例，**不构成投资建议，不承诺收益，不自动下单**。

<p align="center">
  <img alt="type" src="https://img.shields.io/badge/type-agent-blueviolet">
  <img alt="data" src="https://img.shields.io/badge/data-PandaData-1f6feb">
  <img alt="runtime" src="https://img.shields.io/badge/runtime-Claude%20Code%20%7C%20Codex%20%7C%20Cursor%20%7C%20Hermes%20%7C%20OpenClaw-10A37F">
  <img alt="license" src="https://img.shields.io/badge/license-GPL--3.0--only-green">
</p>

---

## 为什么需要它

QuantSkills 里一百多个仓库都在**造信号、验信号**：因子挖掘、IC 分析、正交化、回测、防过拟合、组合优化，链路很完整。但这条链路在信号上线那一刻就断了：**没有仓库管信号上线之后怎么死。**

而"失效"不是一件事，是五件事，处置互相矛盾：

| 死因 | 属于 | 正确处置 | 重训 |
|---|---|---|---|
| 分布漂移（输入进入训练集没见过的区域） | 外因 | 冻结，等分布回归 | ❌ |
| 风格环境断裂（市场换了付钱的风格） | 外因 | 对冲 / 降仓 / 等 | ❌ |
| 拥挤踩踏（人太多互相踩） | 外因偏内 | 降仓 | ❌ |
| 组件失效（择时 / 止损 / 执行某一层坏了） | 内因，非信号层 | 只改那一层 | ❌ |
| 概念漂移（因子逻辑真的失效） | 内因，信号层 | 重训或退役 | ✅ |

2026 年 7 月，科创 50 单月峰谷 −28%，上证 50 只 −5%，7/9 和 7/21 出现 +8% 与 +10% 的暴力反弹日。那个月大量量化策略"失效"，但多数没坏。**在 7/30 最低点把"市场变了"误判成"因子坏了"去重训，模型会学到"科技股就是要跌"，然后在 8 月初的反弹里被反杀第二次。误判死因比不诊断更危险。**

## 因子和策略分开对待

因子是一个截面打分，失效只有一种含义：预测力没了。策略不一样，因子只是它的信号层：

```
策略 = L0 股票池 + L1 信号 + L2 风格暴露 + L3 择时开关 + L4 风控 + L5 执行
```

每一层都可能单独坏，而且常常信号没坏、别的层坏了。所以策略对象要做**反事实剥离**：去掉择时重算一遍、去掉止损重算一遍、理想成交重算一遍，把每层贡献剥出来。只有这样才能说出：

> 因子没问题，是 7% 固定止损在这种波动里变成了追涨杀跌。改止损，别动因子。

## 真实行情回放：2026 年 7 月

同一个公开信号（中证 1000 池、20 日动量），分别当因子和当策略分诊，观察窗 2026-07-01 ~ 08-14。完整报告见 [examples/sample_report/](examples/sample_report/)，一条命令可复现：`python examples/replay_2026_07.py`。

| | 因子对象 | 策略对象（top-30 + 站上 20 日线才持仓 + −8% 止损） |
|---|---|---|
| 观察窗超额 | −24.6% | −7.8% |
| 信号层 | Rank IC +0.014 → −0.302，翻符号，变点 07-03 | 同左 |
| 风格层 | 主动暴露 residual_volatility +1.17、liquidity +0.60；风格贡献 −24.1% | momentum +1.21、size +0.82；风格贡献 −22.8% |
| 择时层 | 不适用 | **+13.7%**：去掉开关净值 −33.9% vs −20.2%，开关躲掉了下跌 |
| 风控层 | 不适用 | **+7.8%**：止损 29 次、whipsaw 率 41%，但整体在帮忙 |
| 执行层 | 不适用 | +0.6%：无锁死，成交额未塌缩 |
| 裁决 | 风格环境断裂（外因），❌ 禁止重训 | 风格环境断裂（外因），❌ 禁止重训 |

两个结论的差异正是这个 Agent 的价值：同一个信号，因子层看是"打分反着走了"，策略层看清楚了择时和止损这两个零件其实在帮忙，亏损几乎全来自风格暴露。裁决引擎在这里明确写道：信号层读数同时翻负，但在风格断裂窗口内无法与外因区分，先按外因处置，是否真是概念漂移交给**证伪条件**在风格收敛后复判。

## 快速开始

```bash
pip install -r requirements.txt
# PandaData 凭证：PANDA_USERNAME / PANDA_PASSWORD 环境变量，或 ~/.pandadata/pandadata.env（离线模式不需要）

# 因子对象：只要一张打分表（Alpha parquet / 长表 / 宽表 csv / PandaAI 因子面板都能吃）
python scripts/triage.py --scores my_scores.parquet --object-type factor --obs-start 20260701 --obs-end 20260814

# 策略对象：用策略卡片声明各层规则
python scripts/triage.py --card examples/card_strategy.yaml --obs-start 20260701 --obs-end 20260814

# 离线：卡片 data 段给行情文件，不联网
python scripts/triage.py --card card.yaml --obs-start 20260701 --obs-end 20260814 --offline

# 离线自测（无 panda_data 也全绿）
python scripts/test.py
```

程序化调用（可被复盘 agent、组合守卫 agent 直接调用）：

```python
import sys; sys.path.insert(0, "scripts")
from triage import run, save_outputs
report = run({"card": "examples/card_strategy.yaml", "obs_start": "20260701", "obs_end": "20260814"})
print(report["plain_text"])          # 人话结论 + 处置 + 重训闸门 + 证伪条件
paths = save_outputs(report)         # report.md / report.json / report.html / triage.parquet
```

## 输入：给什么做什么，做不到的明说

| 层 | 给什么 | 解锁什么 | 给不到时报告怎么说 |
|---|---|---|---|
| I1 | 打分表 `[date, symbol, score]` | 信号、风格、拥挤、股票池 | "只能做事后归因，做不了事前预警" |
| I2 | 策略卡片（各层规则）；可选真实持仓与净值 | 择时 / 风控 / 执行的反事实剥离 | 缺账本时按卡片规则模拟持仓 |
| I3 | 特征矩阵 `[date, symbol, f1, ...]` | 分布漂移，唯一能事前预警的层 | 数据层标"证据不足" |

策略卡片示例见 [examples/card_strategy.yaml](examples/card_strategy.yaml)，字段说明见 [references/input_contract.md](references/input_contract.md)。卡片没声明的层一律标"不适用"，不假装能算。

## 输出

- **report.md**：人话结论、分层表（学术读数 → 通俗解读双行）、环境刻画、置信度。
- **report.html**：单文件暗色看板：结论卡与重训闸门、反事实净值曲线、各层贡献条、死因置信度、滚动 Rank IC、风格主动暴露、环境表、术语表。无网络时图表不显示但文字完整。
- **triage.parquet**：每层一行 + 裁决一行（`build_id / target_id / result_type / result_json / plain_text`），上游 agent 直接读。

## 裁决顺序：因果优先，不投票

```
分布漂移 failed  →  冻结                 ❌ 禁止重训   （上游不可信，下游读数没意义）
风格 / 拥挤 warning →  对冲 / 降仓 / 等   ❌ 禁止重训   （外因能解释就不动模型）
组件层贡献 < −1%  →  只改那一层          ❌ 禁止重训   （组件坏了，重训信号解决不了）
信号翻符号 + 变点  →  重训 / 退役         ✅ 允许重训   （全部排除后才允许）
```

每个结论必须附**证伪条件**（"如果出现 X，说明我判错了，改判 Y"）和**复核触发**（到条件自动重跑）。阈值与理由见 [references/decision_rules.md](references/decision_rules.md)。

## 数据与依赖

- 数据源：PandaData `get_stock_daily`（日线 + 涨跌停 + 停牌）、`get_index_daily`（五个宽基指数）、`get_factor`（Barra 风格暴露：beta / momentum / size / liquidity / residual_volatility / growth / leverage / profitability）。接口与口径见 [references/api_guide.md](references/api_guide.md)。
- 复用而非重写：分布漂移、IC 衰减、市场状态、拥挤度这些能力组织里已有仓库，本项目把它们接进裁决链；自己只写策略六层反事实剥离、A 股执行失效线（涨跌停、停牌、成交额塌缩）和裁决引擎。

## 与相邻仓库的边界

| 仓库 | 它管 | 本项目管 |
|---|---|---|
| agent-alpha-portfolio-guardian | 组合里哪些因子该退休（定期体检） | 某一个对象此刻坏在哪一层、能不能动（急诊分诊） |
| skill-factor-drift-monitor | 面板数据的分布漂移 | 把它作为裁决第一步，再往下判 |
| skill-factor-decay / ic-decay | 因子能用多久 | 现在还能不能用、为什么不能 |
| agent-market-regime-monitor / crowding-risk-monitor | 市场是什么状态 | 你这个模型在这个状态下会不会死 |
| skill-backtest-overfit | 上线前防过拟合 | 上线后失效分诊 |

## 运行时入口（Runtime entrypoints）

| 运行时 | 入口 |
|---|---|
| Claude Code | [CLAUDE.md](CLAUDE.md) → [AGENTS.md](AGENTS.md) |
| Codex | [agents/openai.yaml](agents/openai.yaml) |
| Cursor | [.cursor/rules/quantskills-agent.mdc](.cursor/rules/quantskills-agent.mdc)，完整规则 [agents/cursor-rule.mdc](agents/cursor-rule.mdc) |
| Hermes / OpenClaw | 根 [SKILL.md](SKILL.md) 薄包装 + [agents/portable-loader.md](agents/portable-loader.md) |

## 目录

```
AGENTS.md                 声明文件（quantSkills 2.1）
scripts/
  triage.py               入口：run() / validate_input() / CLI / 落盘
  adapters.py             打分表 / 账本 / 特征 / 行情 的格式适配
  market.py               PandaData 在线取数（缓存）与离线加载
  simulator.py            策略日频模拟器（反事实变体）
  layers.py               八条取证线 + 环境刻画
  verdict.py              交叉裁决、重训禁令、证伪条件、人话
  render_html.py          单文件 HTML 报告
  test.py                 离线全流程测试
references/               失效分类学 / 裁决规则 / 输入契约 / 接口口径 / 边界声明
examples/                 卡片模板、2026-07 回放脚本、样例报告
```

## 边界与免责

- **数据来源**：PandaData；策略账本与特征由用户提供。凭证只从环境变量或官方凭证文件读取，仓库不含凭证。
- **假设条件**：反事实剥离假设各层规则可独立替换；风格贡献用日截面线性回归近似；成交按次日开盘、双边一刀切成本。
- **参数**：基准窗 120 交易日、IC 前瞻 5 日、PSI 阈值 0.10 / 0.25、组件失效阈值 −1%、外因解释比例 50%，均可覆盖。
- **已知限制**：只给打分表时做不了事前预警；卡片未声明的层不做剥离；风格暴露依赖 PandaData 因子库覆盖；模拟器不是生产回测引擎；不处理分钟级执行。
- **风险边界**：输出是研究诊断与处置建议，不是交易指令；不自动下单；不承诺任何收益。

**QuantSkills Community Project，仅供量化研究与教育示例，不构成投资建议，不承诺收益。** 完整声明见 [references/source_boundary.md](references/source_boundary.md)。


Pandadata 兼容维护：[abgyjaguo](https://github.com/abgyjaguo)，保留原项目作者署名。
