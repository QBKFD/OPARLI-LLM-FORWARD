"""The `research_memory` LangGraph graph: retrieve -> answer -> check.

    retrieve  embed the question, top-k chunks from the index (k: input, default 5)
    answer    the owner's prompt + the chunks -> claims (llm.py); abstains on a
              missing/invalid config or prompt, a budget/kill-switch stop or an
              LLM error, and never falls back
    check     every claim cites a retrieved chunk by path + heading, else
              "not in the documents"; statuses come from the chunk metadata

Output keys: `verdict` ("answered" | "not_in_documents" | "abstain"),
`answer` (the text shown), `reason`, `claims`, `hits`, `call`. An abstain is
reported as "abstain: <reason>", never as "not in the documents".
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.agents.llm.call import CallDeps
from tools.research_memory.check import NOT_IN_DOCUMENTS, check_claims, render
from tools.research_memory.chunk import Chunk
from tools.research_memory.config import MemoryConfig, Problem, Prompt, load_config, load_prompt
from tools.research_memory.embed import Embedder, ModelError
from tools.research_memory.llm import answer, retrieval_hash
from tools.research_memory.store import Hit, Index, IndexProblem

DEFAULT_K = 5


class State(TypedDict, total=False):
    question: str
    k: int
    hits: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    call: dict[str, Any]
    verdict: str
    reason: str
    answer: str


class Retriever:
    """The index and the embedder, both loaded on first use."""

    def __init__(self, index_path: Path, embedder_factory: Callable[[], Embedder]) -> None:
        self.index_path, self._factory = index_path, embedder_factory
        self._index: Index | None = None
        self._embedder: Embedder | None = None

    @property
    def index(self) -> Index:
        if self._index is None:
            self._index = Index.load(self.index_path)
        return self._index

    def search(self, question: str, k: int) -> list[Hit]:
        if self._embedder is None:
            self._embedder = self._factory()
        index = self.index
        return index.search(self._embedder.embed_query(question), k, self._embedder.name)


Setup = Callable[[], "tuple[MemoryConfig, Prompt, CallDeps] | Problem"]


def default_setup(repo: Path, config_path: Path | None = None) -> Setup:
    """Reads the config and the prompt on every run (edits apply without a
    restart); the client and ledger are built once per config file."""
    cache: dict[str, CallDeps] = {}

    def setup() -> "tuple[MemoryConfig, Prompt, CallDeps] | Problem":
        from tools.research_memory.llm import make_deps   # noqa: PLC0415 - keeps graph import light

        cfg = load_config(config_path)
        if isinstance(cfg, Problem):
            return cfg
        prompt = load_prompt(cfg, repo)
        if isinstance(prompt, Problem):
            return prompt
        if cfg.sha256 not in cache:
            cache[cfg.sha256] = make_deps(cfg, repo)
        return cfg, prompt, cache[cfg.sha256]

    return setup


def _hit_from(d: dict[str, Any]) -> Hit:
    fields = {k: v for k, v in d.items() if k != "score"}
    return Hit(Chunk(**fields), float(d["score"]))


def build_graph(retriever: Retriever, setup: Setup):
    def retrieve(state: State) -> State:
        question = (state.get("question") or "").strip()
        if not question:
            return {"verdict": "abstain", "reason": "no question", "hits": []}
        try:
            hits = retriever.search(question, int(state.get("k") or DEFAULT_K))
        except IndexProblem as e:
            return {"verdict": "abstain", "reason": f"index: {e}", "hits": []}
        except ModelError as e:
            return {"verdict": "abstain", "reason": f"embedder: {e}", "hits": []}
        except ImportError as e:
            return {"verdict": "abstain", "reason": f"embedder: {e.name or e} is not installed "
                                                    "(requirements/requirements-memory.lock)", "hits": []}
        return {"hits": [h.to_dict() for h in hits]}

    def answer_node(state: State) -> State:
        if state.get("verdict") == "abstain":
            return {}
        s = setup()
        if isinstance(s, Problem):
            return {"verdict": "abstain", "reason": s.reason}
        cfg, prompt, deps = s
        hits = [_hit_from(d) for d in state.get("hits", [])]
        snap = retrieval_hash(state["question"], hits, retriever.index.meta)
        out = answer(cfg, prompt, state["question"], hits, deps, snap)
        call = {k: v for k, v in asdict(out).items() if k != "claims"}
        if out.claims is None:
            return {"verdict": "abstain", "reason": out.reason or "no answer", "call": call}
        return {"claims": out.claims, "call": call}

    def check(state: State) -> State:
        if state.get("verdict") == "abstain":
            return {"answer": f"abstain: {state.get('reason')}"}
        hits = [_hit_from(d) for d in state.get("hits", [])]
        result = check_claims(state.get("claims", []), hits)
        if not result.ok:
            return {"verdict": "not_in_documents", "reason": result.reason or "", "answer": NOT_IN_DOCUMENTS}
        return {"verdict": "answered", "answer": render(result),
                "claims": [asdict(c) for c in result.claims]}

    g = StateGraph(State)
    g.add_node("retrieve", retrieve)
    g.add_node("answer", answer_node)
    g.add_node("check", check)
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "answer")
    g.add_edge("answer", "check")
    g.add_edge("check", END)
    return g.compile()


def make_default_graph(repo: Path, index_path: Path | None = None, config_path: Path | None = None):
    from tools.research_memory.build import DEFAULT_INDEX                 # noqa: PLC0415
    from tools.research_memory.embed import SentenceTransformerEmbedder   # noqa: PLC0415

    return build_graph(Retriever(index_path or DEFAULT_INDEX, SentenceTransformerEmbedder), default_setup(
        repo, config_path))


__all__ = ["DEFAULT_K", "Retriever", "State", "build_graph", "default_setup", "make_default_graph"]
