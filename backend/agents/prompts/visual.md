---
agent: visual
version: TEMPLATE
---
<!--
TEMPLATE: Visual agent (LLM; sees the charts where you put the charts placeholder).

Infrastructure only. There are no instructions here: the owner writes both
sections. While `version` is TEMPLATE the file is refused and the agent
abstains ("unfilled template"). Set a version (e.g. 2026-10-20.1) when you
write it.

Round-1 placeholders for ## user (docs/PROMPT_GUIDE.md section 3.1), each
written as two opening braces, the name, two closing braces:
  as_of, alert_json, levels_json, trigger_levels_json, features_json,
  metrics_table, bars_table:1m, bars_table:15m, bars_table:1h, bars_table:4h,
  events_json, charts, snapshot_json
No placeholders in ## system. Comments like this one are never sent to the
model; delete this one or keep it.
-->

## system

<!-- the owner's instructions -->

## user

<!-- the per-alert message, with the placeholders you choose -->
