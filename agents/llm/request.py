"""One model request, independent of the SDK (docs/AGENTS_ARCHITECTURE.md §6).

An LLMRequest holds exactly what is sent: model, effort, max_tokens, the
system text, the user messages and the output schema. Never temperature,
top_p or top_k: current models reject them. Images are referenced by SHA-256,
so `prompt_hash` (the response-cache key) is a pure function of the request.
The bytes are attached only when the payload for the API is built.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Callable

from agents.hashing import canonical_json, sha256_hex
from agents.owner_config import CallSettings
from agents.prompting.output_schema import VOTE_OUTPUT_SCHEMA
from agents.prompting.render import ImagesPart, RenderedPrompt, TextPart


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ImageBlock:
    sha256: str
    media_type: str = "image/png"


Block = TextBlock | ImageBlock


class MissingImage(LookupError):
    """An image block whose bytes can't be found."""


@dataclass(frozen=True)
class LLMRequest:
    agent: str
    round: int
    model: str
    effort: str
    max_tokens: int
    system: str
    messages: tuple[tuple[Block, ...], ...]      # user messages, in order
    output_schema: dict

    def body(self) -> dict[str, Any]:
        """The canonical request body: what the hash covers."""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "messages": [{"role": "user", "content": [_block_body(b) for b in m]} for m in self.messages],
            "output_config": {"effort": self.effort,
                              "format": {"type": "json_schema", "schema": self.output_schema}},
        }

    @property
    def prompt_hash(self) -> str:
        return sha256_hex(self.body())

    def payload(self, image_bytes: Callable[[str], bytes] | None = None) -> dict[str, Any]:
        """Keyword arguments for `messages.create` / `count_tokens`, with the
        image bytes attached. A missing image raises MissingImage."""
        def api_block(b: Block) -> dict[str, Any]:
            if isinstance(b, TextBlock):
                return {"type": "text", "text": b.text}
            if image_bytes is None:
                raise MissingImage(f"image {b.sha256[:12]}: no image store")
            try:
                data = image_bytes(b.sha256)
            except Exception as e:                     # noqa: BLE001 - any lookup failure is "missing"
                raise MissingImage(f"image {b.sha256[:12]}: {type(e).__name__}: {e}") from None
            return {"type": "image", "source": {"type": "base64", "media_type": b.media_type,
                                                "data": base64.b64encode(data).decode("ascii")}}
        body = self.body()
        body["messages"] = [{"role": "user", "content": [api_block(b) for b in m]} for m in self.messages]
        return body

    def describe(self) -> str:
        """The request as canonical JSON (images by hash), for logs."""
        return canonical_json(self.body())


def _block_body(b: Block) -> dict[str, Any]:
    if isinstance(b, TextBlock):
        return {"type": "text", "text": b.text}
    return {"type": "image", "sha256": b.sha256, "media_type": b.media_type}


def blocks_from(rendered: RenderedPrompt) -> tuple[Block, ...]:
    """A rendered user section as content blocks (charts become image blocks)."""
    out: list[Block] = []
    for part in rendered.user:
        if isinstance(part, TextPart):
            out.append(TextBlock(part.text))
        elif isinstance(part, ImagesPart):
            out += [ImageBlock(c.sha256) for c in part.charts]
    return tuple(out)


def round1_request(rendered: RenderedPrompt, settings: CallSettings) -> LLMRequest:
    return LLMRequest(agent=rendered.agent, round=1, model=settings.model, effort=settings.effort,
                      max_tokens=settings.max_tokens, system=rendered.system,
                      messages=(blocks_from(rendered),), output_schema=VOTE_OUTPUT_SCHEMA)


__all__ = ["Block", "ImageBlock", "LLMRequest", "MissingImage", "TextBlock", "blocks_from", "round1_request"]
