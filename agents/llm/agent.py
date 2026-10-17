"""LLMVoteAgent: a round-1 voter built from the owner's prompt file, the
owner's config and an LLM client (docs/AGENTS_ARCHITECTURE.md §5.1).

vote(snapshot):
1. the prompt and the round-1 call settings must be valid;
2. render the prompt from the snapshot;
3. call (cache, kill switch, budget, retries: agents/llm/call.py);
4. check the served model, the stop reason and the answer (parse.py);
5. return a Vote with its evidence, tokens and call id.

Any problem at any step is an abstain with the reason. There's no default
prompt, no default model and no guessing from free text. safe_vote (the
graph's wrapper) still bounds the whole call by node_timeout_s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agents.llm.call import CallContext, CallDeps, run_call
from agents.llm.parse import parse_answer
from agents.llm.request import round1_request
from agents.owner_config import LLM_AGENTS, ConfigProblem, OwnerConfig
from agents.prompting.files import PromptFile, PromptProblem
from agents.prompting.render import RenderProblem, render_round1
from agents.schemas import Snapshot, Vote

OK_STOP = frozenset({"end_turn"})


@dataclass
class LLMVoteAgent:
    name: str
    prompt: PromptFile | PromptProblem
    owner: OwnerConfig
    deps: CallDeps
    epoch_id: str | None = None

    def vote(self, snapshot: Snapshot) -> Vote:
        def abstain(reason: str, **meta: Any) -> Vote:
            return Vote.abstained(self.name, reason, **meta)

        if isinstance(self.prompt, PromptProblem):
            return abstain(self.prompt.reason)
        settings = self.owner.call(self.name, "round1")
        if isinstance(settings, ConfigProblem):
            return abstain(settings.reason)
        llm, budget = self.owner.llm(), self.owner.budget()
        for section in (llm, budget):
            if isinstance(section, ConfigProblem):
                return abstain(section.reason)
        assert not isinstance(llm, ConfigProblem) and not isinstance(budget, ConfigProblem)
        rendered = render_round1(self.prompt, snapshot, self.owner)
        if isinstance(rendered, RenderProblem):
            return abstain(rendered.reason)
        request = round1_request(rendered, settings)
        ctx = CallContext(snapshot_hash=snapshot.hash, prompt_file_sha256=self.prompt.sha256,
                          config_sha256=self.owner.sha256, epoch_id=self.epoch_id)
        out = run_call(request, llm, budget, ctx, self.deps)
        meta: dict[str, Any] = {"prompt_hash": request.prompt_hash, "call_id": out.call_id,
                                "model_id": settings.model}
        resp = out.response
        if resp is None:
            return abstain(out.reason or "api_error: no response", **meta)
        meta.update(model_id=resp.model, tokens_in=resp.usage.get("input_tokens"),
                    tokens_out=resp.usage.get("output_tokens"))
        if resp.model != settings.model:
            return abstain(f"model_mismatch: requested {settings.model}, served {resp.model}", **meta)
        if resp.stop_reason == "refusal":
            return abstain(f"refusal:{resp.refusal_category or 'uncategorised'}", **meta)
        if resp.stop_reason == "max_tokens":
            return abstain(f"truncated: max_tokens {settings.max_tokens} reached", **meta)
        if resp.stop_reason not in OK_STOP:
            return abstain(f"invalid: stop_reason {resp.stop_reason!r}", **meta)
        ans = parse_answer(resp.text, snapshot)
        if isinstance(ans, str):
            return abstain(ans, **meta)
        return Vote(agent=self.name, round=1, direction=ans.direction,  # type: ignore[arg-type]
                    confidence=ans.confidence, reason=ans.reason, evidence=ans.evidence, **meta)


def make_llm_voters(owner: OwnerConfig, prompts: Mapping[str, PromptFile | PromptProblem], deps: CallDeps,
                    epoch_id: str | None = None) -> dict[str, LLMVoteAgent]:
    """One LLMVoteAgent per LLM agent (visual, technical, sentiment). A
    disabled or misconfigured agent is still built, and abstains with the
    reason, so it shows up in every run."""
    return {name: LLMVoteAgent(name=name, prompt=prompts[name], owner=owner, deps=deps, epoch_id=epoch_id)
            for name in LLM_AGENTS}


__all__ = ["LLMVoteAgent", "make_llm_voters"]
