# agents/prompting

Infrastructure for the owner's prompts: load them, check them, render them
from a snapshot. The prompt **content** is in `agents/prompts/` and belongs
to the owner; nothing here writes it. The contract is
[docs/PROMPT_GUIDE.md](../../docs/PROMPT_GUIDE.md).

| Module | What |
|---|---|
| `files.py` | `load_prompt(name)` / `load_all()`: parse `agents/prompts/<name>.md` (front-matter `agent` + `version`, `## system` / `## user`, comments stripped). Returns a `PromptFile` or a `PromptProblem` whose `reason` is the abstain reason (`prompt_invalid: <file>: <problem>`). Never a fallback text |
| `placeholders.py` | the placeholder names, which template kind may use each (round 1 vs discussion), and the snapshot slot each reads |
| `render.py` | `render_round1(prompt, snapshot, owner_config)`: a `RenderedPrompt` (system text, user parts with the charts as an `ImagesPart`), or a `RenderProblem`. Unavailable slots render `[unavailable: <slot>: <reason>]` |
| `output_schema.py` | the JSON schema every LLM agent answers with, and its SHA-256 (part of the epoch) |
| `__main__.py` | `python -m agents.prompting check`: every prompt and config problem, and the epoch id |

Related modules one level up:
- `agents/owner_config.py`: the owner's `agents.toml`, validated per
  section. A problem is returned, never raised.
- `agents/epoch.py`: `epoch_id` over prompts, config, call settings, schema,
  input code, Meta rule and run context. Also `python -m agents.epoch`.

## Run

```
python -m agents.prompting check --config agents.toml     # exit 0 only if every prompt and the config are usable
python -m agents.epoch --config agents.toml --db agents.sqlite
```

The shipped templates report `unfilled template` until the owner writes them.

## Test

```
pytest tests/agents_tests/test_prompting.py tests/agents_tests/test_prompt_render.py \
       tests/agents_tests/test_owner_config.py tests/agents_tests/test_epoch.py
```

No network, no model calls, no data files (the replay store is synthetic).
