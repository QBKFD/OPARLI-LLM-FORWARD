"""python -m agents.prompting check [--prompts DIR] [--config agents.toml]

Checks every prompt file and the owner config without calling any model:
- for each prompt: OK (version, SHA-256, the placeholders it uses) or the
  reason it is invalid;
- every config problem;
- the epoch id these files give.

Exit code 0 if everything is usable, 1 otherwise. The shipped templates are
"unfilled" by design, so they report as invalid until the owner writes them.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from agents.epoch import compute_epoch
from agents.owner_config import OwnerConfig
from agents.prompting.files import PromptFile, load_all


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m agents.prompting")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="validate the prompt files and agents.toml")
    c.add_argument("--prompts", help="prompt directory (default agents/prompts)")
    c.add_argument("--config", help="agents.toml (default $AGENTS_CONFIG)")
    args = p.parse_args(argv)
    prompts = load_all(args.prompts)
    cfg = OwnerConfig.load(args.config)
    ok = True
    for name, pf in prompts.items():
        if isinstance(pf, PromptFile):
            used = ", ".join(pf.placeholders) or "none"
            print(f"OK       {name}.md  version {pf.version}  sha256 {pf.sha256[:12]}  placeholders: {used}")
        else:
            ok = False
            print(f"INVALID  {pf.reason}  ({pf.path})")
    problems = cfg.problems()
    for prob in problems:
        print(f"CONFIG   {prob.reason}" + (f"  ({cfg.path})" if cfg.load_error and cfg.path else ""))
    if not problems:
        print(f"CONFIG   OK  {cfg.path}  sha256 {(cfg.sha256 or '')[:12]}")
    print(f"epoch_id {compute_epoch(prompts, cfg).id}")
    return 0 if ok and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
