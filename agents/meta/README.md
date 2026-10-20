# agents/meta

Votes → one Decision per arm, in plain code, by the rule the owner names in
`agents.toml` (`[meta] rule = "..."`). There is no default rule: none
configured, or an unknown name, gives a `none` decision with the reason.

| Rule | Version | What |
|---|---|---|
| `quorum_v0` | 1 | The skeleton's aggregator v0, unchanged: long or short if ≥ 2 non-abstaining votes agree, else none; confidence ignored |
| `fixed_meta_v1` | 1 | The legacy `meta_decision` with its bugs fixed. Equal weights, exact fractions, and the direction is the argmax (none on a tie). The winning side needs strictly more votes. At least 2 directional votes. High tier: score ≥ 0.7. Low tier: score ≥ 0.6 and agreement ≥ 2/3 |

The same rule decides the `blind` arm (round-1 votes) and the `discussion`
arm (final votes). The rule's name and version are stored on each decision
and enter the epoch. A rule's parameters are part of its definition: new
parameters are a new version.

```
pytest tests/agents_tests/test_meta.py
```
