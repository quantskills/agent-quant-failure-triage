---
name: agent-quant-failure-triage
description: 策略与因子失效分诊 Agent：对已上线的 A 股因子或策略做急诊分诊，回答它现在还能不能用、坏的是哪一层、是市场变了还是模型坏了、现在该不该重训。策略拆成股票池 / 信号 / 风格暴露 / 择时 / 风控 / 执行六层做反事实剥离，五类死因交叉裁决并输出重训禁令、证伪条件与复核触发；文本 + 单文件 HTML 报告 + parquet。Use when a factor or strategy has started underperforming and the user needs to know which layer failed, whether the cause is internal or external, and whether retraining is allowed right now. Research and education only, not investment advice.
license: GPL-3.0-only
supported-runtimes: [cursor, claude-code, codex, hermes, openclaw]
metadata:
  organization: QuantSkills
  organization_url: https://github.com/quantskills
  repository: agent-quant-failure-triage
  repository_url: https://github.com/quantskills/agent-quant-failure-triage
  project_type: agent
  collection: model-operations
  maintainer: ZLHad
  tags: [a-share, failure-triage, model-drift, counterfactual, retrain-gate, pandadata]
quantSkills:
  schema_version: 2.1.0
  organization: quantskills
  organization_url: https://github.com/quantskills
  repository: agent-quant-failure-triage
  repository_url: https://github.com/quantskills/agent-quant-failure-triage
  project_type: agent
  license: GPL-3.0-only
  maintainer: ZLHad
  collection: model-operations
  catalog:
    category: "09"
    subcategory: 09.monitor-risk-agent
  workflow:
    primary_stage: risk
    workflow_stages: [evaluation, risk, monitoring, reporting]
  tags: [a-share, failure-triage, model-drift, counterfactual, retrain-gate, pandadata]
  requires: [skill-pandadata-api, skill-factor-drift-monitor, skill-factor-decay, agent-crowding-risk-monitor, skill-market-regime-analysis]
  platforms: [cursor, claude-code, codex, hermes, openclaw]
  status: active
  validation_level: runnable
  maintainer_type: community
  summary_zh: "对已上线的 A 股因子或策略做失效分诊：六层反事实剥离定位坏在哪一层，五类死因裁决内因还是外因，输出重训禁令、证伪条件与 HTML 报告。"
  summary_en: "Failure triage for live A-share factors and strategies: six-layer counterfactuals locate the broken layer and separate market change from model breakage, with a retrain gate; research only."
  interface:
    mode: not-applicable
    reason: orchestration-only
---

# 策略与因子失效分诊 Agent

> QuantSkills Community Project，仅供量化研究与教育示例，不构成投资建议，不承诺收益，不自动下单。

## 它只回答一个问题

**这个因子或策略现在还能不能用；如果不能，坏的是哪一层、是市场变了还是模型坏了、现在该不该动它。**

学术说法：在线模型的失效归因与干预闸门（failure attribution and intervention gating）。
人话：策略亏钱了，先别急着改参数。先查清楚是市场换了玩法，还是策略自己的某个零件坏了，再决定是等、是减仓、还是真的要重做。

## 什么时候用

- 一个因子或策略最近跑输了，想知道是暂时的还是坏了。
- 想决定"要不要重训"，但担心在极端行情样本上重训会把噪音学成规律。
- 有一份策略账本，想知道亏损来自信号、择时、止损还是执行。
- 上游 agent（复盘、组合守卫、因子池管理）需要一个"当前能不能用"的结构化判定。

## 因子和策略分开对待

| 对象 | 失效的含义 | 跑哪些线 |
|---|---|---|
| 因子 | 只有一种：预测力失效 | 数据分布、L0 股票池、L1 信号、L2 风格、拥挤 |
| 策略 | 六层任何一层都可能单独坏 | 上述 + L3 择时、L4 风控、L5 执行（反事实剥离） |

策略六层的反事实剥离：去掉择时重算一遍、去掉止损重算一遍、理想成交重算一遍，把每层贡献剥出来。只有这样才能说出"因子没坏，是止损在这种波动里变成了追涨杀跌"。

## 五类死因与裁决顺序

分布漂移 → 风格环境断裂 / 拥挤踩踏 → 组件失效（L0/L3/L4/L5）→ 概念漂移。因果优先、不投票：上游不可信时下游没意义；外因能解释时不动模型；组件坏了重训信号解决不了。只有全部排除后才允许重训。详见 [references/decision_rules.md](references/decision_rules.md)。

## 怎么跑

```bash
pip install -r requirements.txt
# 凭证：PANDA_USERNAME / PANDA_PASSWORD 或 ~/.pandadata/pandadata.env（离线模式不需要）

# 因子对象：只要一张打分表
python scripts/triage.py --scores my_scores.parquet --object-type factor --obs-start 20260701 --obs-end 20260814

# 策略对象：用策略卡片声明各层规则
python scripts/triage.py --card examples/card_strategy.yaml --obs-start 20260701 --obs-end 20260814

# 离线（卡片 data 段给行情文件）
python scripts/triage.py --card card.yaml --obs-start 20260701 --obs-end 20260814 --offline

# 真实行情回放（2026 年 7 月，中证 1000 动量信号，因子 + 策略各跑一次）
python examples/replay_2026_07.py

# 离线自测
python scripts/test.py
```

程序化调用：

```python
import sys; sys.path.insert(0, "scripts")
from triage import run, save_outputs
report = run({"card": "examples/card_strategy.yaml", "obs_start": "20260701", "obs_end": "20260814"})
print(report["plain_text"]); save_outputs(report)   # report.md / report.json / report.html / triage.parquet
```

## 输入

| 层 | 给什么 | 解锁什么 |
|---|---|---|
| I1 | 打分表 `[date, symbol, score]`（Alpha parquet / 长表 / 宽表 csv / PandaAI 因子面板都能吃） | 信号、风格、拥挤、股票池 |
| I2 | 策略卡片（各层规则）；可选真实持仓与净值 | 择时 / 风控 / 执行的反事实剥离 |
| I3 | 特征矩阵 | 分布漂移，唯一能做事前预警的层 |

只给 I1 时报告会明说"只能做事后归因，做不了事前预警"。格式细节见 [references/input_contract.md](references/input_contract.md)。

## 输出

- `report.md`：人话结论 + 分层表（学术读数 → 通俗解读双行）+ 环境刻画 + 置信度。
- `report.html`：单文件暗色看板：结论卡、处置与重训闸门、反事实净值曲线、各层贡献条、滚动 IC、风格主动暴露、环境表、术语表。
- `triage.parquet`：每层一行 + 裁决一行，`build_id / target_id / result_type / result_json / plain_text`，可被上游 agent 直接读。

## Agent 行为约定

1. 先读卡片，确认对象类型；卡片没声明的层一律标"不适用"，不假装能算。
2. 数据取自 PandaData 或用户提供的离线文件；凭证只从环境变量或官方凭证文件读取，绝不写进仓库。
3. 每个结论必须带证伪条件和复核触发；重训闸门是 forbidden / hold / allowed 三态，默认保守。
4. 学术术语必须配一行人话；报告末尾固定免责。
5. 不输出买卖指令、目标价、仓位比例建议之外的任何交易指令。

## 与相邻仓库的边界

| 仓库 | 它管 | 本项目管 |
|---|---|---|
| agent-alpha-portfolio-guardian | 组合里哪些因子该退休（定期体检） | 某一个对象此刻坏在哪一层、能不能动（急诊分诊） |
| skill-factor-drift-monitor | 面板数据的分布漂移 | 把分布漂移作为裁决第一步，再往下判 |
| skill-factor-decay / ic-decay | 因子能用多久 | 现在还能不能用、为什么不能 |
| agent-market-regime-monitor / crowding-risk-monitor | 市场是什么状态 | 你这个模型在这个状态下会不会死 |
| skill-backtest-overfit | 上线前防过拟合 | 上线后失效分诊 |

## 参考文件

- [references/failure_taxonomy.md](references/failure_taxonomy.md) 五类死因 × 六层
- [references/decision_rules.md](references/decision_rules.md) 裁决顺序、阈值、禁令
- [references/input_contract.md](references/input_contract.md) 输入格式与策略卡片
- [references/api_guide.md](references/api_guide.md) PandaData 接口与口径
- [references/source_boundary.md](references/source_boundary.md) 数据来源、假设、参数、限制、风险边界

## 边界与免责

数据来源 PandaData 与用户账本；假设、参数、已知限制、风险边界见 [references/source_boundary.md](references/source_boundary.md)。本项目仅供量化研究与教育示例，不构成投资建议，不承诺收益。
