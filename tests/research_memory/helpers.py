"""A tiny fixture corpus in a throwaway git repo, and config helpers.

Every document is made up. Secret-shaped strings in the scan tests are built
at runtime, so no literal key sits in this repository (gitleaks scans it).
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

RULES = """
[include]
roots = ["research", "docs"]
names = ["PREREG.md", "REPORT.md", "DECISIONS*.md", "README.md"]

[exclude]
globs = ["*/results/*"]

[extra]
paths = ["research/alpha/PROPOSAL.md"]
globs = ["docs/decisions/*.md"]

[status.paths]
"research/alpha/PREREG.md" = "registered"
"research/alpha/REPORT.md" = "result"
"research/alpha/PROPOSAL.md" = "draft"
"research/README.md" = "reference"

[status.globs]
"*/DECISIONS*.md" = "decision log"
"docs/decisions/*.md" = "decision log"

[[section]]
path = "research/alpha/PREREG.md"
heading = '^4\\. Amendment 1\\b'
status = "amended"

[[skip_section]]
path = "research/alpha/REPORT.md"
heading = '^Daily table$'
reason = "fixture: a dated table"
"""

DOCS = {
    "research/README.md": """
        # research/

        Alpha is the only study. Its verdict is in alpha/REPORT.md.
    """,
    "research/alpha/PREREG.md": """
        # alpha: pre-registration

        ## 1. Question

        Does the walrus index forecast copper volatility better than the plug-in?

        ## 2. Rule

        PASS if QLIKE falls with BH p < 0.05 at H = 240.

        ## 4. Amendment 1 (2026-01-02)

        The horizon is H = 240 only.

        ### 4.1 What changes

        H = 60 is dropped.
    """,
    "research/alpha/REPORT.md": """
        # alpha: report

        ## 1. Verdict

        **PASS.** The walrus index lowers QLIKE by 12 % at H = 240.

        ## Daily table

        | Date | value |
        |---|---|
        | 2026-01-05 | 1 |
        | 2026-01-06 | 2 |
        | 2026-01-07 | 3 |

        ## 2. Caveats

        Only one market was tested.
    """,
    "research/alpha/PROPOSAL.md": """
        # Proposal: a penguin index (not run)

        **Status: proposal only.** The penguin index might forecast tin volatility.
    """,
    "research/alpha/DECISIONS.md": """
        # alpha: decisions Claude took

        | # | Decision |
        |---|---|
        | A1 | The walrus index uses a 20-day window. |
    """,
    "docs/decisions/feat-x.md": """
        # feat/x

        The x service restarts at most once per hour.
    """,
    "research/alpha/results/REPORT.md": """
        # not indexed: under results/
    """,
    "research/alpha/notes.txt": "not markdown\n",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                           "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True).stdout


def make_repo(root: Path, docs: dict[str, str] | None = None, rules: str = RULES) -> tuple[Path, Path]:
    """(repo, rules path): a git repo with the fixture documents committed."""
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    for rel, text in (docs or DOCS).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture")
    (repo / "research" / "alpha" / "UNTRACKED.md").write_text("# never indexed\n", encoding="utf-8")
    rules_path = root / "corpus.toml"
    rules_path.write_text(rules, encoding="utf-8")
    return repo, rules_path


CONFIG = """
[answer]
provider = "anthropic"
model = "claude-sonnet-5-5"
effort = "default"
max_tokens = 2000
prompt_path = "prompt.md"

[llm]
mode = "live"
timeout_s = 30.0
max_retries = 1

[budget]
max_calls_per_day = 100
kill_switch_path = "{kill}"
"""


def write_config(root: Path, text: str | None = None, prompt: str | None = "You answer from the chunks.") -> Path:
    cfg = root / "research_memory.toml"
    cfg.write_text((text or CONFIG).replace("{kill}", str(root / "KILL")), encoding="utf-8")
    if prompt is not None:
        (root / "prompt.md").write_text(prompt, encoding="utf-8")
    return cfg
