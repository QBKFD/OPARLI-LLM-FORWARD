# feat/research-memory

Research memory (RAG over the research documents), built 2026-11-15 from
`main` @ a1f6d4d. The owner approved the scope (the 52 matched files plus
the named extras: 75 files), the eight statuses and the status map, the
template-only prompt, claude-sonnet-5-5 at default effort, bge-small, SQLite and
a separate memory lock. Below are the choices taken without the owner, each
the conservative option, with its alternative.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| RM1 | **torch 2.11.0**, not the newest release that is two weeks old (2.15.0). 2.12+ ships macOS arm64 wheels for macOS 14+ only; the owner's laptop runs macOS 12.6. torch 2.11 needs `setuptools<82`, so the memory lock overrides the main lock's setuptools 84.0.0 (a build tool), and only there. `requirements/compile_memory_lock.sh` builds the constraints from both locks minus setuptools. | It installs on the machine that runs it; every other shared pin is identical to the main and Studio locks (tested). | torch 2.15 and no local use until macOS 14; or an ONNX runtime instead of sentence-transformers (not what was approved). |
| RM2 | **No `memory` extra in pyproject.toml.** The main lock is compiled with `--all-extras`, so an extra would pull torch into CI on the next recompile. A `requirements-memory.in` and its own lock instead, as for Studio. | The main lock and CI stay unchanged. | A pyproject extra excluded by hand from every recompile. |
| RM3 | **`langgraph-cli[inmem]==0.4.32` is in the memory lock too** (the Studio lock's pin and dependencies). The Studio venv has no torch, and its setuptools pin conflicts with torch 2.11, so `langgraph dev` for both graphs runs from the memory venv. Checked: the server lists `agents` and `research_memory`. | No change to the Studio lock or venv. | A third venv for Studio plus memory. |
| RM4 | **The code lives in `tools/research_memory/`.** CI's default ruff pass already covers `tools/`, and CI edits are the owner's. | Lint coverage without touching `.github/workflows/ci.yml`. | `backend/research_memory/`, which CI's ruff list doesn't name. |
| RM5 | **Text is read at the build commit (`git show <commit>:path`), not the working copy.** Files with uncommitted changes are listed as a warning. | The index matches the commit recorded with it. | Index the working copy (metadata could not name a commit). |
| RM6 | **One section is skipped, not the rule loosened:** `gex_feasibility/REPORT.md` § "D, by trade date" (a dated table of daily GC dollar volume, exchange-derived) hit `calendar_rows_run`. A `[[skip_section]]` entry in corpus.toml, which must match a heading or the build stops. | The rule stays as strict as approved; the skip is visible and reviewed. | Relax the rule for dated tables without prices. |
| RM7 | **The CSV price rule needs four or more numeric fields.** The first version (a date at line start plus four commas) flagged a line of prose in `vol_features/REPORT.md`. | Prose passes; a dated row of numbers still fails (tested). | Keep the loose rule and skip that section too. |
| RM8 | **The statuses are a literal map; an unmapped file stops the build.** CI does not check the map, only the content scan of every selected file. A PR that adds a document (#39's `gex_levels/PREREG.md`) stays green, and the next `build` asks for its status line. | The owner decides each new file's status; CI isn't broken by an unrelated document. | Fail CI on an unmapped file. |
| RM9 | **The CI tests scan the real corpus at HEAD** (the same rules as the build). A committed document with a price row, a calendar dump or a secret fails CI. | The repo is public; that content shouldn't be committed anyway. | Scan only at build time. |
| RM10 | **bge's query instruction** ("Represent this sentence for searching relevant passages: ") on queries, none on passages: the model card's retrieval setup. Passages embed `path`, heading path and text. | The documented use of the model. | No instruction. |
| RM11 | **A citation matches a retrieved chunk by exact path and by the chunk's heading or full heading path,** with whitespace collapsed and a leading `#`/`§` dropped. No fuzzy matching. One failed claim fails the whole answer. | "Every claim cites a retrieved chunk", literally. | Fuzzy heading matching; dropping only the uncited claims. |
| RM12 | **Drafts:** a claim whose citations are all drafts goes under "From drafts (not registered):"; a claim citing a draft and a non-draft stays in the main list with both labels. Post hoc is labelled, not set apart. Labels come from the chunk metadata, never the model. | A draft can't be shown as a result; the labels can't be wrong. | Refuse any claim that cites a draft. |
| RM13 | **`effort = "default"` sends no effort**: an `LLMRequest` subclass drops it from the body (and so from the cache key); the ledger row says `default`. | The owner asked for the model's default effort, and backend/agents always sends one. | Send an explicit effort the owner didn't choose. |
| RM14 | **Abstain is distinct from "not in the documents"**: no config, no prompt, a placeholder, budget, kill switch or an LLM error prints `abstain: <reason>`. | A failure never claims the documents are silent. | One message for both. |
| RM15 | **The ledger is backend/agents' own** (`SqliteLedger` on a `SqliteAgentStore` in `database/research_memory_llm.sqlite`), so the agents' tables are created there unused. | Reuses the tested cache, budget, retry and cost code unchanged. | A second ledger implementation. |
| RM16 | **The config and prompt have no defaults** (as agents.toml): every `<...>` left in, a missing file or an invalid value abstains. The owner's `research_memory.toml` is git-ignored (its kill-switch path is local); the prompt path is the owner's choice and nothing is shipped there. `.env`: only `ANTHROPIC_API_KEY` is read, and never over an existing environment value. | Agent prompt ownership; no key or local path committed. | Ship a default prompt or model. |
| RM17 | **The eval questions were written before the first run, and the eval ran once on the final code.** Expected files include `research/README.md` where its study table states the answer. Nothing was tuned on the results. | The numbers measure the system as built. | Iterate the chunker against the 25 questions. |
| RM18 | **`tests/agents/test_studio.py` now allows the second graph** (`set(graphs) == {"agents", "research_memory"}`, `agents` unchanged). | The only edit to an existing test, and it still pins both entries. | Leave it failing. |
| RM19 | **The Ollama client is tested against a fake HTTP server only.** Ollama isn't installed on this machine. | Not claimed as live-tested. | — |

## Open for the owner

- **Citation correctness:** `eval --llm` waits for the owner's prompt (approved:
  claude-sonnet-5-5, default effort; cost from `pricing.py`).
- **New documents:** when #39 merges, `research/gex_levels/PREREG.md` needs a
  status line in corpus.toml before the next build. #73's
  `news_surprise/DECISIONS.md` is covered by the `*/DECISIONS*.md` glob
  (decision log). Its `PREREG_DRAFT.md` doesn't match the approved name
  patterns, so it is not indexed unless added to `[extra]`.
