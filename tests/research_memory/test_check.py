"""check.py: every claim cites a retrieved chunk, all or nothing; statuses from metadata; drafts apart."""

from tools.research_memory.check import DRAFT_HEADING, NOT_IN_DOCUMENTS, check_claims, render, resolve
from tools.research_memory.chunk import Chunk
from tools.research_memory.store import Hit


def chunk(path, heading_path, status, part=1, n_parts=1):
    return Chunk(chunk_id=f"{path}#{heading_path}-{part}", path=path, study="s", doc_type="X", status=status,
                 heading_path=heading_path, heading=heading_path.split(" > ")[-1], part=part, n_parts=n_parts,
                 text="t", last_commit="c")


REPORT = chunk("research/s/REPORT.md", "s: report > 1. Verdict", "result")
DRAFT = chunk("research/s/PROPOSAL.md", "Proposal > Why", "draft")
SPLIT = [chunk("research/s/PREREG.md", "P > 2. Rule", "registered", i, 2) for i in (1, 2)]
HITS = [Hit(c, 0.5) for c in (REPORT, DRAFT, *SPLIT)]


def claim(text, *refs):
    return {"text": text, "citations": [{"path": p, "heading": h} for p, h in refs]}


def test_leaf_heading_or_full_path_and_normalisation():
    chunks = [h.chunk for h in HITS]
    assert resolve({"path": "research/s/REPORT.md", "heading": "1. Verdict"}, chunks) == [REPORT]
    assert resolve({"path": "./research/s/REPORT.md", "heading": "## s: report >  1. Verdict "}, chunks) == [REPORT]
    assert resolve({"path": "research/s/PREREG.md", "heading": "2. Rule"}, chunks) == SPLIT    # both parts
    assert resolve({"path": "research/s/REPORT.md", "heading": "Verdict"}, chunks) == []        # not exact
    assert resolve({"path": "research/s/OTHER.md", "heading": "1. Verdict"}, chunks) == []


def test_passing_answer_is_rendered_with_statuses():
    r = check_claims([claim("PASS.", ("research/s/REPORT.md", "1. Verdict"))], HITS)
    assert r.ok
    assert render(r) == "- PASS. [research/s/REPORT.md § s: report > 1. Verdict — RESULT]"


def test_any_uncited_claim_fails_everything():
    r = check_claims([claim("PASS.", ("research/s/REPORT.md", "1. Verdict")), claim("Also true.")], HITS)
    assert not r.ok and r.reason == "claim 2 has no citation" and render(r) == NOT_IN_DOCUMENTS


def test_citing_a_chunk_that_was_not_retrieved_fails():
    r = check_claims([claim("X.", ("research/s/REPORT.md", "2. Caveats"))], HITS)
    assert not r.ok and "not a retrieved chunk" in r.reason and render(r) == NOT_IN_DOCUMENTS


def test_no_claims_and_empty_claim_fail():
    assert not check_claims([], HITS).ok
    assert not check_claims([claim("  ", ("research/s/REPORT.md", "1. Verdict"))], HITS).ok


def test_draft_only_claims_are_set_apart():
    r = check_claims([claim("PASS.", ("research/s/REPORT.md", "1. Verdict")),
                      claim("A penguin index is proposed.", ("research/s/PROPOSAL.md", "Why"))], HITS)
    lines = render(r).splitlines()
    assert lines[0].startswith("- PASS.") and lines[1] == "" and lines[2] == DRAFT_HEADING
    assert lines[3] == "- A penguin index is proposed. [research/s/PROPOSAL.md § Proposal > Why — DRAFT]"


def test_a_claim_with_a_draft_and_a_result_stays_in_the_main_list_with_both_labels():
    r = check_claims([claim("Mixed.", ("research/s/PROPOSAL.md", "Why"), ("research/s/REPORT.md", "1. Verdict"))],
                     HITS)
    text = render(r)
    assert DRAFT_HEADING not in text and "— DRAFT]" in text and "— RESULT]" in text


def test_only_drafts_still_get_the_draft_heading():
    r = check_claims([claim("Proposed.", ("research/s/PROPOSAL.md", "Why"))], HITS)
    assert render(r).splitlines()[0] == DRAFT_HEADING
