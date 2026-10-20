"""The JSON schema every LLM agent must answer with, in round 1 and in every
discussion round (docs/PROMPT_GUIDE.md §5).

The LLM client sends it as the structured-output format. Bounds the schema
doesn't express (confidence in [0, 1], text lengths, at most 6 evidence
items, a directional vote needing a resolving ref) are checked in code after
parsing, so they hold whatever the API enforces. Its SHA-256 is part of the
epoch (agents/epoch.py).
"""

from __future__ import annotations

from agents.hashing import sha256_hex

MAX_REASON = 600
MAX_CLAIM = 200
MAX_EVIDENCE = 6

VOTE_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["direction", "confidence", "reason", "evidence"],
    "properties": {
        "direction": {"type": "string", "enum": ["long", "short", "none"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ref", "claim"],
                "properties": {"ref": {"type": "string"}, "claim": {"type": "string"}},
            },
        },
    },
}


def output_schema_sha256() -> str:
    return sha256_hex(VOTE_OUTPUT_SCHEMA)


__all__ = ["MAX_CLAIM", "MAX_EVIDENCE", "MAX_REASON", "VOTE_OUTPUT_SCHEMA", "output_schema_sha256"]
