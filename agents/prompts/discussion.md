---
agent: discussion
version: TEMPLATE
---
<!--
TEMPLATE: the discussion turn, shared by all LLM agents.

Infrastructure only. There are no instructions here: the owner writes the
section. While `version` is TEMPLATE the file is refused, and no agent
speaks in the discussion (each keeps its round-1 vote, with the reason
logged).

A discussion request is: the agent's own ## system, then its own round-1
## user exactly as in round 1, then this ## user, rendered for that agent and
round (docs/PROMPT_GUIDE.md section 3.3). So this file has no ## system.

Discussion placeholders (section 3.2), each written as two opening braces,
the name, two closing braces:
  agent, round, max_rounds, own_previous_vote_json, other_votes_json,
  change_rule
Round-1 placeholders are not allowed here: the round-1 content is already
sent again above this message.
-->

## user

<!-- the discussion message, with the placeholders you choose -->
