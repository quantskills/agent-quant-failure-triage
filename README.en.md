# 🩺 Quant Failure Triage Agent

[简体中文](README.md) | **English**

> A factor or strategy has started underperforming. **Can it still be used? Which layer failed? Did the market change, or did the model break? Is retraining allowed right now?**
> This agent answers exactly these four questions, and says so plainly when it cannot.

> Status: QuantSkills **Community Project**, not reviewed, certified or endorsed by the maintainers. Research and educational use only. **Not investment advice, no return promises, no automated order placement.**

<p align="center">
  <img alt="type" src="https://img.shields.io/badge/type-agent-blueviolet">
  <img alt="data" src="https://img.shields.io/badge/data-PandaData-1f6feb">
  <img alt="runtime" src="https://img.shields.io/badge/runtime-Claude%20Code%20%7C%20Codex%20%7C%20Cursor%20%7C%20Hermes%20%7C%20OpenClaw-10A37F">
  <img alt="license" src="https://img.shields.io/badge/license-GPL--3.0--only-green">
</p>

---

## Why

More than a hundred QuantSkills repositories **build and validate signals**: factor mining, IC analysis, orthogonalization, backtesting, overfitting guards, portfolio optimization. The pipeline is complete right up to the moment a signal goes live, and then it stops: **nothing manages how a live signal dies.**

"Failure" is not one thing but five, and the correct responses contradict each other:

| Cause | Kind | Correct response | Retrain |
|---|---|---|---|
| Distribution shift (inputs left the training region) | external | freeze, wait for the distribution to return | ❌ |
| Style regime break (the market stopped paying for this style) | external | hedge / de-risk / wait | ❌ |
| Crowding (too many people in the same trade) | external-ish | de-risk | ❌ |
| Component failure (timing / stop-loss / execution layer broke) | internal, not the signal | fix that layer only | ❌ |
| Concept drift (the factor logic genuinely stopped working) | internal, signal layer | retrain or retire | ✅ |

In July 2026 the STAR 50 index fell 28% peak-to-trough while the SSE 50 fell only 5%, with violent +8% and +10% rebound days on 7/9 and 7/21. Many quant strategies "failed" that month; most were not broken. **Retraining at the 7/30 low because you misread "the market changed" as "the factor broke" teaches the model that tech always falls, and the early-August rebound kills it a second time. Misdiagnosing the cause is more dangerous than not diagnosing at all.**

## Factors and strategies are different objects

A factor is a cross-sectional score; it fails in exactly one way: it stops predicting. A strategy is more than its factor:

```
strategy = L0 universe + L1 signal + L2 style exposure + L3 timing switch + L4 risk rules + L5 execution
```

Any layer can fail on its own, and very often the signal is fine while another layer broke. So strategy objects get **counterfactual decomposition**: re-run without timing, re-run without stops, re-run with ideal fills, and read each layer's contribution. Only then can the report say:

> The factor is fine. The fixed 7% stop-loss turned into chasing tops and selling bottoms in this volatility regime. Fix the stop, leave the factor alone.

## Real-market replay: July 2026

The same public signal (CSI 1000 universe, 20-day momentum) triaged once as a factor and once as a strategy, observation window 2026-07-01 to 08-14. Full reports in [examples/sample_report/](examples/sample_report/); reproduce with `python examples/replay_2026_07.py`.

| | Factor object | Strategy object (top-30, long only above the 20-day MA, −8% stop) |
|---|---|---|
| Excess return in window | −24.6% | −7.8% |
| Signal layer | Rank IC +0.014 → −0.302, sign flip, change point 07-03 | same |
| Style layer | active exposure residual_volatility +1.17, liquidity +0.60; style contribution −24.1% | momentum +1.21, size +0.82; style contribution −22.8% |
| Timing layer | n/a | **+13.7%**: without the switch −33.9% vs −20.2%; the switch dodged the drawdown |
| Risk layer | n/a | **+7.8%**: 29 stops, 41% whipsaw rate, net helpful |
| Execution layer | n/a | +0.6%: no locked limit-down exits, no volume collapse |
| Verdict | style regime break (external), ❌ retrain forbidden | style regime break (external), ❌ retrain forbidden |

The contrast is the point. At the factor level the score "went the wrong way"; at the strategy level the timing switch and the stop-loss turn out to be helping, and nearly all of the loss is style exposure. The adjudicator states explicitly that the signal-layer reading also flipped negative but cannot be separated from the external cause inside a style-break window, so it treats the case as external first and hands the concept-drift question to the **falsification condition** once the style spread converges.

## Quick start

```bash
pip install -r requirements.txt
# PandaData credentials: PANDA_USERNAME / PANDA_PASSWORD, or ~/.pandadata/pandadata.env (not needed offline)

# Factor object: a score table is enough (Alpha parquet / long table / wide csv / PandaAI factor panel)
python scripts/triage.py --scores my_scores.parquet --object-type factor --obs-start 20260701 --obs-end 20260814

# Strategy object: declare each layer in a strategy card
python scripts/triage.py --card examples/card_strategy.yaml --obs-start 20260701 --obs-end 20260814

# Offline: the card's data section points to price files
python scripts/triage.py --card card.yaml --obs-start 20260701 --obs-end 20260814 --offline

# Offline self-test (green without panda_data)
python scripts/test.py
```

Programmatic use (callable from review agents or portfolio guardians):

```python
import sys; sys.path.insert(0, "scripts")
from triage import run, save_outputs
report = run({"card": "examples/card_strategy.yaml", "obs_start": "20260701", "obs_end": "20260814"})
print(report["plain_text"])          # verdict, actions, retrain gate, falsification condition
paths = save_outputs(report)         # report.md / report.json / report.html / triage.parquet
```

## Inputs: what you give decides what you get, and the report says what it cannot do

| Tier | Input | Unlocks | When missing |
|---|---|---|---|
| I1 | score table `[date, symbol, score]` | signal, style, crowding, universe | "post-hoc attribution only, no early warning" |
| I2 | strategy card (layer rules); optional real positions and NAV | counterfactuals for timing / risk / execution | positions simulated from the card |
| I3 | feature matrix `[date, symbol, f1, ...]` | distribution shift, the only layer that warns ahead of P&L | data layer marked insufficient |

Card template: [examples/card_strategy.yaml](examples/card_strategy.yaml); field reference: [references/input_contract.md](references/input_contract.md). Layers not declared in the card are reported as not applicable, never fabricated.

## Outputs

- **report.md**: plain-language verdict, layer table (academic reading → plain gloss), environment metrics, confidence.
- **report.html**: single-file dark dashboard: verdict card and retrain gate, counterfactual NAV curves, layer contribution bars, cause confidence, rolling Rank IC, active style exposure, environment table, glossary. Charts need network for Chart.js; text stays complete offline.
- **triage.parquet**: one row per layer plus one verdict row (`build_id / target_id / result_type / result_json / plain_text`) for upstream agents.

## Adjudication: causal order, not a vote

```
distribution shift failed   → freeze                    ❌ retrain forbidden  (downstream readings meaningless)
style / crowding warning    → hedge / de-risk / wait    ❌ retrain forbidden  (external cause explains it)
component contribution < −1% → fix that layer only      ❌ retrain forbidden  (retraining the signal cannot fix a stop rule)
IC sign flip + change point → retrain / retire          ✅ retrain allowed    (only after everything else is excluded)
```

Every verdict carries a **falsification condition** ("if X happens, I was wrong, reclassify as Y") and **re-check triggers**. Thresholds and rationale: [references/decision_rules.md](references/decision_rules.md).

## Data and dependencies

- PandaData `get_stock_daily` (daily bars, limit prices, suspension), `get_index_daily` (five broad indices), `get_factor` (Barra style exposures: beta / momentum / size / liquidity / residual_volatility / growth / leverage / profitability). Interface notes: [references/api_guide.md](references/api_guide.md).
- Reuse over rewrite: distribution drift, IC decay, market regime and crowding already exist in the organization and are wired into the adjudication chain; this project only adds the six-layer counterfactual decomposition, the A-share execution-failure line (limit locks, suspensions, volume collapse) and the adjudicator.

## Boundaries with neighbouring repositories

| Repository | It answers | This project answers |
|---|---|---|
| agent-alpha-portfolio-guardian | which factors in a portfolio should retire (routine check-up) | which layer of one object broke right now and whether it may be touched (emergency triage) |
| skill-factor-drift-monitor | distribution drift in panel data | uses it as step one, then adjudicates |
| skill-factor-decay / ic-decay | how long a factor lasts | whether it still works now and why not |
| agent-market-regime-monitor / crowding-risk-monitor | what state the market is in | whether this model dies in that state |
| skill-backtest-overfit | overfitting before launch | failure after launch |

## Runtime entrypoints

| Runtime | Entry |
|---|---|
| Claude Code | [CLAUDE.md](CLAUDE.md) → [AGENTS.md](AGENTS.md) |
| Codex | [agents/openai.yaml](agents/openai.yaml) |
| Cursor | [.cursor/rules/quantskills-agent.mdc](.cursor/rules/quantskills-agent.mdc); full rule [agents/cursor-rule.mdc](agents/cursor-rule.mdc) |
| Hermes / OpenClaw | root [SKILL.md](SKILL.md) wrapper + [agents/portable-loader.md](agents/portable-loader.md) |

## Layout

```
AGENTS.md                 declaration (quantSkills 2.1)
scripts/
  triage.py               entry: run() / validate_input() / CLI / outputs
  adapters.py             score / ledger / feature / price format adapters
  market.py               PandaData fetch with cache, offline loading
  simulator.py            daily strategy simulator (counterfactual variants)
  layers.py               eight evidence lines + environment metrics
  verdict.py              adjudication, retrain gate, falsification, plain text
  render_html.py          single-file HTML report
  test.py                 offline end-to-end tests
references/               taxonomy / decision rules / input contract / API notes / boundary
examples/                 card templates, July-2026 replay, sample reports
```

## Boundary and disclaimer

- **Data source**: PandaData; strategy ledgers and features are supplied by the user. Credentials are read only from environment variables or the official credential file; none are stored in the repository.
- **Assumptions**: layers can be replaced independently in counterfactuals; style contribution is a daily cross-sectional linear regression; fills at next open with a flat round-trip cost.
- **Parameters**: 120-day baseline window, 5-day IC horizon, PSI thresholds 0.10 / 0.25, component failure threshold −1%, external-explanation share 50%; all overridable.
- **Known limitations**: no early warning without a feature matrix; undeclared layers are not decomposed; style exposure depends on PandaData factor coverage; the simulator is not a production backtester; no intraday execution modelling.
- **Risk boundary**: output is research diagnosis and intervention guidance, not trade instructions; no order placement; no return promises.

**QuantSkills Community Project, for quantitative research and education only. Not investment advice. No return promises.** Full statement: [references/source_boundary.md](references/source_boundary.md).
