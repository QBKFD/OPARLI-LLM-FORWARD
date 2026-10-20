"""LLMVoteAgent: a voter built from the owner's prompt files, the owner's
config and an LLM client (docs/AGENTS_ARCHITECTURE.md §5.1, §5.4). It votes in
round 1 and proposes in the discussion.

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
from agents.llm.request import LLMRequest, discussion_request, round1_request
from agents.owner_config import LLM_AGENTS, BudgetSettings, CallSettings, ConfigProblem, LLMSettings, OwnerConfig
from agents.prompting.files import PromptFile, PromptProblem
from agents.prompting.render import RenderedPrompt, RenderProblem, render_discussion, render_round1
from agents.schemas import DiscussionView, Snapshot, Vote

OK_STOP = frozenset({"end_turn"})


@dataclass
class LLMVoteAgent:
    name: str
    prompt: PromptFile | PromptProblem
    owner: OwnerConfig
    deps: CallDeps
    epoch_id: str | None = None
    discussion_prompt: PromptFile | PromptProblem | None = None

    def _abstain(self, reason: str, round_: int = 1, **meta: Any) -> Vote:
        return Vote.abstained(self.name, reason, round=round_, **meta)

    def _settings(self, kind: str) -> tuple[CallSettings, LLMSettings, BudgetSettings] | str:
        """The call, llm and budget settings, or the first problem's reason."""
        if isinstance(self.prompt, PromptProblem):
            return self.prompt.reason
        settings = self.owner.call(self.name, kind)  # type: ignore[arg-type]
        llm, budget = self.owner.llm(), self.owner.budget()
        for section in (settings, llm, budget):
            if isinstance(section, ConfigProblem):
                return section.reason
        assert isinstance(settings, CallSettings) and isinstance(llm, LLMSettings) \
            and isinstance(budget, BudgetSettings)
        return settings, llm, budget

    def _ask(self, request: LLMRequest, settings: CallSettings, llm: LLMSettings, budget: BudgetSettings,
             snapshot: Snapshot, shown: set[tuple[str, int]] | None) -> Vote:
        """Call, then check the served model, the stop reason and the answer."""
        ctx = CallContext(snapshot_hash=snapshot.hash, prompt_file_sha256=getattr(self.prompt, "sha256", None),
                          config_sha256=self.owner.sha256, epoch_id=self.epoch_id)
        out = run_call(request, llm, budget, ctx, self.deps)
        meta: dict[str, Any] = {"prompt_hash": request.prompt_hash, "call_id": out.call_id,
                                "model_id": settings.model}
        r = request.round
        resp = out.response
        if resp is None:
            return self._abstain(out.reason or "api_error: no response", r, **meta)
        meta.update(model_id=resp.model, tokens_in=resp.usage.get("input_tokens"),
                    tokens_out=resp.usage.get("output_tokens"))
        if resp.model != settings.model:
            return self._abstain(f"model_mismatch: requested {settings.model}, served {resp.model}", r, **meta)
        if resp.stop_reason == "refusal":
            return self._abstain(f"refusal:{resp.refusal_category or 'uncategorised'}", r, **meta)
        if resp.stop_reason == "max_tokens":
            return self._abstain(f"truncated: max_tokens {settings.max_tokens} reached", r, **meta)
        if resp.stop_reason not in OK_STOP:
            return self._abstain(f"invalid: stop_reason {resp.stop_reason!r}", r, **meta)
        ans = parse_answer(resp.text, snapshot, shown)
        if isinstance(ans, str):
            return self._abstain(ans, r, **meta)
        return Vote(agent=self.name, round=r, direction=ans.direction,  # type: ignore[arg-type]
                    confidence=ans.confidence, reason=ans.reason, evidence=ans.evidence, **meta)

    def vote(self, snapshot: Snapshot) -> Vote:
        """Round 1: the agent's own prompt and its round1 call settings."""
        got = self._settings("round1")
        if isinstance(got, str):
            return self._abstain(got)
        settings, llm, budget = got
        assert isinstance(self.prompt, PromptFile)
        rendered = render_round1(self.prompt, snapshot, self.owner)
        if isinstance(rendered, RenderProblem):
            return self._abstain(rendered.reason)
        return self._ask(round1_request(rendered, settings), settings, llm, budget, snapshot, None)

    def discuss(self, view: DiscussionView) -> Vote:
        """A discussion proposal for vote-round view.round + 1: its round-1
        content again, then discussion.md, with its discussion call settings.
        Any problem is an abstain, which the change rule turns into the previous
        vote carried forward (AA6)."""
        r = view.round + 1
        got = self._settings("discussion")
        if isinstance(got, str):
            return self._abstain(got, r)
        settings, llm, budget = got
        dp = self.discussion_prompt
        if dp is None or isinstance(dp, PromptProblem):
            return self._abstain(dp.reason if dp is not None else "prompt_invalid: discussion.md: not loaded", r)
        assert isinstance(self.prompt, PromptFile)
        r1 = render_round1(self.prompt, view.snapshot, self.owner)
        rd = render_discussion(dp, view)
        for rendered in (r1, rd):
            if isinstance(rendered, RenderProblem):
                return self._abstain(rendered.reason, r)
        assert isinstance(r1, RenderedPrompt) and isinstance(rd, RenderedPrompt)
        shown = {(v.agent, v.round) for v in (*view.others, view.own_previous)}
        return self._ask(discussion_request(r1, rd, settings, r), settings, llm, budget, view.snapshot, shown)


def make_llm_voters(owner: OwnerConfig, prompts: Mapping[str, PromptFile | PromptProblem], deps: CallDeps,
                    epoch_id: str | None = None) -> dict[str, LLMVoteAgent]:
    """One LLMVoteAgent per LLM agent (visual, technical, sentiment). A
    disabled or misconfigured agent is still built, and abstains with the
    reason, so it shows up in every run. Each shares discussion.md."""
    return {name: LLMVoteAgent(name=name, prompt=prompts[name], owner=owner, deps=deps, epoch_id=epoch_id,
                               discussion_prompt=prompts.get("discussion"))
            for name in LLM_AGENTS}


__all__ = ["LLMVoteAgent", "make_llm_voters"]
