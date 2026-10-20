"""Epochs: one fixed set of everything that can change an agent's answer
(docs/AGENTS_ARCHITECTURE.md §8).

epoch_id = SHA-256 of the canonical JSON of epoch_inputs(). That JSON holds:
- each prompt file's SHA-256 and front-matter version, or the reason it
  couldn't be loaded;
- the SHA-256 of the owner's agents.toml values;
- per LLM agent and per round kind, the model, effort and max_tokens, or the
  config problem;
- the output schema's SHA-256;
- the snapshot schema version and the SHA-256 of the code that builds and
  renders the inputs;
- the Meta rule name (and version, once Meta rules exist);
- the σ model and the scanner journal epoch, once the run knows them.

Any change of any input is a new epoch, automatically: nobody bumps anything.
A prompt whose bytes changed while its `version` didn't is reported by
version_warnings(). The epoch changes either way.

    python -m agents.epoch [--config agents.toml] [--prompts DIR] [--db agents.sqlite]

prints the current epoch id and its inputs. With --db, it also prints what
differs from the last stored run's epoch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.hashing import sha256_hex
from agents.owner_config import LLM_AGENTS, ROUND_KINDS, ConfigProblem, OwnerConfig
from agents.prompting.files import PromptFile, PromptProblem, load_all
from agents.prompting.output_schema import output_schema_sha256
from agents.schemas import Snapshot

log = logging.getLogger(__name__)

EPOCH_SCHEMA = 1
PKG = Path(__file__).resolve().parent
# the code that builds and renders what an agent sees
INPUT_CODE = ("snapshot.py", "resample.py", "schemas.py", "hashing.py", "prompting/files.py",
              "prompting/placeholders.py", "prompting/render.py", "prompting/output_schema.py")


@dataclass(frozen=True)
class Epoch:
    id: str
    inputs: dict[str, Any]
    prompts_sha256: dict[str, str]     # name -> file SHA-256, or "absent"
    owner_config_sha256: str | None


def prompt_entry(p: PromptFile | PromptProblem) -> dict[str, Any]:
    if isinstance(p, PromptFile):
        return {"sha256": p.sha256, "version": p.version}
    return {"sha256": p.sha256, "problem": p.reason}


def calls_entry(cfg: OwnerConfig) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for agent in LLM_AGENTS:
        out[agent] = {}
        for kind in ROUND_KINDS:
            c = cfg.call(agent, kind)
            out[agent][kind] = {"problem": c.reason} if isinstance(c, ConfigProblem) else c.model_dump()
    return out


def input_code_sha256(root: Path = PKG, files: Sequence[str] = INPUT_CODE) -> str:
    """SHA-256 over (path, SHA-256 of the bytes) of every input-building source
    file. A missing file raises: the epoch must never silently skip code."""
    return sha256_hex([[f, hashlib.sha256((root / f).read_bytes()).hexdigest()] for f in files])


def epoch_inputs(prompts: Mapping[str, PromptFile | PromptProblem], cfg: OwnerConfig, *,
                 meta_rule_version: str | None = None, sigma_model_sha256: str | None = None,
                 journal_epoch: int | None = None, code_root: Path = PKG) -> dict[str, Any]:
    meta = cfg.meta()
    return {
        "epoch_schema": EPOCH_SCHEMA,
        "prompts": {name: prompt_entry(p) for name, p in sorted(prompts.items())},
        "owner_config": {"sha256": cfg.sha256} if cfg.load_error is None else {"problem": cfg.load_error},
        "calls": calls_entry(cfg),
        "output_schema_sha256": output_schema_sha256(),
        "snapshot_schema_version": Snapshot.model_fields["schema_version"].default,
        "input_code_sha256": input_code_sha256(code_root),
        "meta": ({"problem": meta.reason} if isinstance(meta, ConfigProblem)
                 else {"rule": meta.rule, "version": meta_rule_version}),
        "sigma_model_sha256": sigma_model_sha256,
        "journal_epoch": journal_epoch,
    }


def compute_epoch(prompts: Mapping[str, PromptFile | PromptProblem], cfg: OwnerConfig, **kw: Any) -> Epoch:
    inputs = epoch_inputs(prompts, cfg, **kw)
    shas = {name: (p.sha256 or "absent") for name, p in sorted(prompts.items())}
    return Epoch(id=sha256_hex(inputs), inputs=inputs, prompts_sha256=shas, owner_config_sha256=cfg.sha256)


def current_epoch(prompts_dir: str | Path | None = None, owner_config: OwnerConfig | None = None,
                  **kw: Any) -> Epoch:
    """Load every prompt and the owner config ($AGENTS_CONFIG unless given)."""
    return compute_epoch(load_all(prompts_dir), owner_config or OwnerConfig.load(), **kw)


def diff_inputs(old: Any, new: Any, prefix: str = "") -> list[str]:
    """Dotted keys whose values differ between two epoch_inputs dicts."""
    if isinstance(old, dict) and isinstance(new, dict):
        out: list[str] = []
        for k in sorted(set(old) | set(new)):
            out += diff_inputs(old.get(k), new.get(k), f"{prefix}.{k}" if prefix else k)
        return out
    return [] if old == new else [prefix or "(root)"]


def version_warnings(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    """Prompts whose bytes changed while their front-matter version didn't."""
    out = []
    for name, n in new.get("prompts", {}).items():
        o = old.get("prompts", {}).get(name, {})
        if o.get("sha256") and n.get("sha256") and o["sha256"] != n["sha256"] \
                and o.get("version") is not None and o.get("version") == n.get("version"):
            out.append(f"{name}.md changed but its version is still {n['version']!r}")
    return out


def last_stored_inputs(db: str) -> dict[str, Any] | None:
    """The epoch inputs of the newest run in an agent store, or None (also
    when the file doesn't exist yet). Opened read-only."""
    if not Path(db).exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT epoch_inputs_json FROM runs WHERE epoch_inputs_json IS NOT NULL"
                          " ORDER BY created_ts DESC, run_id DESC LIMIT 1").fetchone()
    finally:
        con.close()
    return None if row is None else json.loads(row[0])


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m agents.epoch", description="Print the current epoch id.")
    p.add_argument("--config", help="agents.toml (default $AGENTS_CONFIG)")
    p.add_argument("--prompts", help="prompt directory (default agents/prompts)")
    p.add_argument("--db", help="an agent store: also show what changed since its last run")
    args = p.parse_args(argv)
    ep = current_epoch(args.prompts, OwnerConfig.load(args.config))
    print(f"epoch_id {ep.id}")
    print(json.dumps(ep.inputs, indent=2, sort_keys=True))
    if args.db:
        try:
            old = last_stored_inputs(args.db)
        except sqlite3.Error as e:
            print(f"cannot read {args.db}: {e}", file=sys.stderr)
            return 1
        if old is None:
            print("no stored run with an epoch yet")
        else:
            changed = diff_inputs(old, ep.inputs)
            print("unchanged since the last stored run" if not changed else
                  "changed since the last stored run:\n  " + "\n  ".join(changed))
            for w in version_warnings(old, ep.inputs):
                print(f"warning: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
