# tools/research_memory

A question-answering memory over the repo's own research documents: every
PREREG, REPORT, decision log and README under `research/` and `docs/`, plus
the named extras in [corpus.toml](corpus.toml). Documents only: no price data,
no calendar rows, no results files. Every answer cites the chunks it came from,
or says **"not in the documents"**.

```
retrieve  ->  answer  ->  check
top-k        the owner's      every claim cites a retrieved chunk
chunks       prompt + LLM     (path + heading), else "not in the documents"
```

## Run it

A laptop tool with its own venv: the embedding model needs torch, which never
goes into `requirements/requirements.lock` (CI). Python 3.13.

```
python3.13 -m venv .venv-memory
.venv-memory/bin/pip install --require-hashes -r requirements/requirements-memory.lock
.venv-memory/bin/pip install --no-deps --no-build-isolation -e .

.venv-memory/bin/python -m tools.research_memory fetch-model   # once: the pinned model (network)
.venv-memory/bin/python -m tools.research_memory build         # scan + chunk + embed: the one rebuild command
.venv-memory/bin/python -m tools.research_memory retrieve "What did Study A conclude?"
.venv-memory/bin/python -m tools.research_memory ask "What did Study A conclude?"
.venv-memory/bin/python -m tools.research_memory eval          # retrieval hit rate (no LLM)
.venv-memory/bin/python -m tools.research_memory eval --llm    # + citation correctness (paid calls)
```

- **`ask` and `eval --llm` need the owner's config and prompt.** Copy
  [config.example.toml](config.example.toml) to
  `tools/research_memory/research_memory.toml` (git-ignored) or point
  `$RESEARCH_MEMORY_CONFIG` at it, and fill in every value. Write the prompt at
  `answer.prompt_path`; [prompt.template.md](prompt.template.md) shows what the
  code sends around it. Without either, the graph **abstains** with the reason
  and never uses a default.
- **The key** is `ANTHROPIC_API_KEY`, from the environment or from the `.env`
  file named in the config (default: the repo root's `.env`, git-ignored). It is
  never logged. `answer.provider = "ollama"` uses a local Ollama server instead.
- **LangGraph Studio:** the graph is registered as `research_memory` in
  [tools/langgraph_studio/langgraph.json](../langgraph_studio/langgraph.json).
  Run `langgraph dev` from this venv (its lock carries the same `langgraph-cli`
  as the Studio lock), because the Studio venv has no torch:
  `.venv-memory/bin/langgraph dev --config tools/langgraph_studio/langgraph.json`.
  Input: `{"question": "...", "k": 5}`.
- `requirements/compile_memory_lock.sh` recompiles the lock.

## What it does

**Corpus ([corpus.toml](corpus.toml), owner-approved).** `git ls-files` only,
text read at the build commit, so untracked data and caches can never get in.
Every file has one of eight statuses: draft, registered, amended, post hoc,
result, owner decision, decision log, reference. The status comes from this
map, never from the file name (`research/agents_v1/PREREG.md` is a draft).
Amendment sections of a PREREG are overridden to *amended*, sigma_compare's
post-results amendment to *post hoc*. The build stops on a file with no status,
a stale entry, or an override that matches no heading.

**Scan, on every build.** Secrets (API keys, tokens, private keys, non-local IP
addresses, EC2 hostnames, e-mails), price rows (a timestamp with three or more
price-like decimals; dated numeric CSV lines) and calendar rows (three or more
consecutive dated table rows; a timestamp with a currency and an impact level).
Any hit stops the build before anything is embedded; the previous index stays.
Hits are printed masked. One section is skipped by name: the daily GC dollar
volume table in `gex_feasibility/REPORT.md` (exchange-derived data).

**Chunks.** One per heading, with the full heading path
(`breakout_fade: pre-registration > 6. Amendment 1 (2026-10-28)`). Sections
over ~1,800 characters are split on paragraphs or table rows; each piece keeps
the heading path and repeats a table's header. Metadata per chunk: study, path,
heading path, doc type, status, last-changed commit, index-build commit.

**Index.** `BAAI/bge-small-en-v1.5` (384 dims), pinned to Hugging Face revision
`5c38ec7c` with the weights' SHA-256 checked on every load, loaded offline. One
SQLite file, `database/research_memory.sqlite` (git-ignored), with the
embeddings as float32 blobs; search is cosine over all chunks in memory. A query
with another embedder than the one recorded in the index is refused.

**Answer.** Through backend/agents' LLM layer (`run_call`): response cache, kill
switch, daily budget, retries and one ledger row per attempt in
`database/research_memory_llm.sqlite`. The system prompt is the owner's file,
verbatim; the user message is data only (the question and the chunks with their
path, heading, status, study and doc type). Output is constrained to
`{"claims": [{"text", "citations": [{"path", "heading"}]}]}`.

**Check (code, not the model).** Every claim needs a citation, and every
citation must name a retrieved chunk: same path, heading equal to the chunk's
heading or heading path. One failure turns the whole answer into "not in the
documents". Status labels are printed from the metadata, and claims that cite
only drafts go under **"From drafts (not registered):"**, so a draft is never
shown as a result. Abstains (no config, no prompt, budget, LLM error) print
"abstain: <reason>", never "not in the documents".

## Evaluation

[eval/questions.toml](eval/questions.toml): 25 questions written from the
documents before the first run, each with the file(s) that state the answer.
Paraphrased away from the documents' headings. The eval was run once on the
final code and the numbers below are that run, unchanged.

| Metric | Value | Run |
|---|---|---|
| Retrieval hit rate, k = 3 | **80 %** (20 / 25) | 2026-11-15, index at `a1f6d4d`, 75 files, 643 chunks |
| Retrieval hit rate, k = 5 | **96 %** (24 / 25) | same |
| Citation correctness | not run yet | needs the owner's prompt (`eval --llm`, claude-sonnet-5-5) |

- **Hit at k:** any top-k chunk comes from an expected file.
- **Citation correctness:** the answer passed the check and cites an expected
  file. `eval --llm` also reports the not-in-the-documents and abstain counts
  and the run's list-price cost from the ledger
  (`backend/agents/llm/pricing.py`).
- **Misses.** At k = 3: Study A, its replication, squeeze_oco and sigma_compare.
  Their PREREG chunks, which ask the same question, ranked above the REPORT
  (ranks 4–5). At k = 5: the levels study ("previous-day and weekly highs and
  lows"), where the levels_diag and levels_confirm chunks ranked first.

## Tests

`tests/research_memory/`: a made-up fixture corpus in a throwaway git repo,
a hashing embedder in place of the model, and `FakeLLMClient`. No network, no
model, no torch: they run in CI on the main lock. They also scan the real corpus
at HEAD (the same rules as the build).

```
pytest tests/research_memory
```
