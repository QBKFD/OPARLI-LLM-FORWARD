"""python -m agents.llm count-tokens --db REPLAY_DB --t 2025-06-11T14:30:00Z [--config agents.toml] [--prompts DIR]

Renders each LLM agent's round-1 prompt on a replay snapshot and asks the API
how many input tokens it would cost (`messages.count_tokens`, no generation).
Use it on your real prompts before a freeze, to replace the estimates in
docs/AGENTS_ARCHITECTURE.md §9.

It needs API credentials, so it makes one network call per agent, and no
test runs it against the API. An agent whose prompt or config is invalid is
listed with its reason instead.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any, Sequence

from agents.config import SnapshotConfig
from agents.llm.client import LLMCallError
from agents.llm.pricing import PRICES
from agents.llm.request import MissingImage, round1_request
from agents.owner_config import LLM_AGENTS, ConfigProblem, OwnerConfig
from agents.prompting.files import PromptProblem, load_prompt
from agents.prompting.render import RenderProblem, render_round1
from agents.snapshot import Stale, build_snapshot


def _utc(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{s!r} has no timezone")
    return t.astimezone(timezone.utc)


def count_tokens(t: datetime, db: str, owner: OwnerConfig, prompts_dir: str | None, client: Any) -> list[str]:
    """One line per LLM agent: its input tokens and list-price input cost, or why it can't be counted."""
    snap = build_snapshot(t, SnapshotConfig(mode="replay"), db)
    if isinstance(snap, Stale):
        return [f"snapshot: {snap.reason}"]
    lines = []
    for name in LLM_AGENTS:
        prompt = load_prompt(name, prompts_dir)
        if isinstance(prompt, PromptProblem):
            lines.append(f"{name}: {prompt.reason}")
            continue
        settings = owner.call(name, "round1")
        if isinstance(settings, ConfigProblem):
            lines.append(f"{name}: {settings.reason}")
            continue
        rendered = render_round1(prompt, snap, owner)
        if isinstance(rendered, RenderProblem):
            lines.append(f"{name}: {rendered.reason}")
            continue
        req = round1_request(rendered, settings)
        try:
            n = client.count_tokens(req.payload())
        except (LLMCallError, MissingImage) as e:
            lines.append(f"{name}: count failed: {e}")
            continue
        price = PRICES.get(settings.model)
        cost = f", input ≈ ${n * price[0] / 1e6:.4f} at list price" if price else ""
        lines.append(f"{name}: {settings.model} {n} input tokens{cost}"
                     + (f" (unavailable: {', '.join(rendered.unavailable_used)})" if rendered.unavailable_used else ""))
    return lines


def main(argv: Sequence[str] | None = None, client: Any = None) -> int:
    p = argparse.ArgumentParser(prog="python -m agents.llm")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("count-tokens", help="input tokens of each LLM agent's round-1 prompt on a replay snapshot")
    c.add_argument("--db", required=True, help="a replay datafeed store")
    c.add_argument("--t", required=True, type=_utc, help="trigger time, UTC")
    c.add_argument("--config", help="agents.toml (default $AGENTS_CONFIG)")
    c.add_argument("--prompts", help="prompt directory (default agents/prompts)")
    args = p.parse_args(argv)
    if client is None:
        from agents.llm.anthropic_client import AnthropicClient
        client = AnthropicClient()
    for line in count_tokens(args.t, args.db, OwnerConfig.load(args.config), args.prompts, client):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
