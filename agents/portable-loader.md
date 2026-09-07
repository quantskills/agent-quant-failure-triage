# Portable loader / 便携加载提示

Load and follow the canonical root `AGENTS.md`. This Hermes / OpenClaw adapter adds no policy.

```text
You have access to a local agent named agent-quant-failure-triage at:
<AGENT_QUANT_FAILURE_TRIAGE_ROOT>

Use it when a factor or strategy has started underperforming and the user needs to know
which layer failed, whether the cause is market change or model breakage, and whether
retraining is allowed right now.

1. Read <ROOT>/AGENTS.md first; it is the canonical declaration.
2. Read <ROOT>/references/input_contract.md to build or check the strategy card.
3. Run from <ROOT>:
   python scripts/triage.py --card <card.yaml> --obs-start YYYYMMDD --obs-end YYYYMMDD
   python scripts/triage.py --scores <scores.parquet> --object-type factor --obs-start ... --obs-end ...
4. Report plain_text verdict, the layer table, the retrain gate and the falsification condition.
5. Layers not declared in the card are "not applicable"; never fabricate a reading.
6. Credentials come only from PANDA_USERNAME / PANDA_PASSWORD or ~/.pandadata/pandadata.env.
7. Research and education only: no trade instructions, no return promises, no official-endorsement claims.
```
