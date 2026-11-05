---
name: agent-quant-failure-triage
description: 策略与因子失效分诊 Agent 的便携安装入口：对已上线的 A 股因子或策略做急诊分诊，定位坏在哪一层、判定内因还是外因、给出重训禁令与证伪条件。Use when a factor or strategy has started underperforming and the user needs to know which layer failed and whether retraining is allowed. Research and education only, not investment advice.
---

# agent-quant-failure-triage

本文件是给 Hermes / OpenClaw 等按 `SKILL.md` 发现技能的运行时用的薄包装。
完整的 Agent 声明、行为约定与用法在 [AGENTS.md](AGENTS.md)，请以它为准。

## Pandadata compatibility

[Pandadata 1.0.6 compatibility](references/pandadata-compatibility.md) — exact SDK baseline, validation scope, and shared guidance for all supported agent runtimes.
