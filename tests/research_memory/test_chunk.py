"""chunk.py: heading paths, fences, the size cap, table headers, section statuses and skips."""

import re

import pytest

from tools.research_memory.chunk import (SEP, TOP, apply_skips, chunk_document, clean_heading, sections, split_body,
                                         strip_sections, unmatched_overrides)
from tools.research_memory.corpus import Document, SectionRule, SkipRule


def doc(text, path="research/s/PREREG.md", status="registered"):
    return Document(path=path, text=text, status=status, study="s", doc_type="PREREG", last_commit="c" * 40)


def test_heading_paths_and_top_of_file():
    text = "intro line\n\n# Title\n\nbody\n\n## A\n\na\n\n### A.1\n\na1\n\n## B\n\nb\n"
    got = [(SEP.join(p), b) for p, b in sections(text)]
    assert got == [(TOP, "intro line"), ("Title", "body"), ("Title > A", "a"), ("Title > A > A.1", "a1"),
                   ("Title > B", "b")]


def test_empty_sections_are_dropped_and_headings_cleaned():
    text = "# **Bold** `code` title\n\n## Empty\n\n## Full\n\nx\n"
    assert [SEP.join(p) for p, _ in sections(text)] == ["Bold code title > Full"]
    assert clean_heading("  **H2**   statistic `x` ") == "H2 statistic x"


def test_headings_inside_code_fences_are_text():
    text = "# T\n\n```\n# not a heading\n```\n\nafter\n"
    (path, body), = sections(text)
    assert path == ["T"] and "# not a heading" in body and "after" in body


def test_long_section_split_keeps_heading_and_cap():
    paras = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(30))
    chunks = chunk_document(doc(f"# T\n\n## Long\n\n{paras}\n"), cap=600)
    assert len(chunks) > 3
    assert {c.heading_path for c in chunks} == {"T > Long"}
    assert [c.part for c in chunks] == list(range(1, len(chunks) + 1))
    assert all(c.n_parts == len(chunks) for c in chunks)
    assert all(len(c.text) <= 600 for c in chunks)
    assert "".join(c.text for c in chunks).count("Paragraph") == 30          # nothing lost


def test_long_table_split_repeats_the_header():
    rows = "\n".join(f"| r{i} | {'x' * 40} |" for i in range(60))
    pieces = split_body(f"| key | value |\n|---|---|\n{rows}", cap=500)
    assert len(pieces) > 2
    for p in pieces:
        lines = p.splitlines()
        assert lines[0] == "| key | value |" and re.match(r"^\|-+\|-+\|$", lines[1])
    assert sum(p.count("| r") for p in pieces) == 60


def test_overlong_single_line_is_cut():
    pieces = split_body("word " * 1000, cap=300)
    assert len(pieces) > 1 and all(len(p) <= 300 for p in pieces)


def test_section_override_applies_to_subsections_only():
    text = "# P\n\n## 1. Q\n\nq\n\n## 4. Amendment 1 (x)\n\na\n\n### 4.1 Changes\n\nc\n\n## 5. After\n\nz\n"
    rule = SectionRule("research/s/PREREG.md", re.compile(r"^4\. Amendment 1\b"), "amended")
    status = {c.heading_path: c.status for c in chunk_document(doc(text), (rule,))}
    assert status == {"P > 1. Q": "registered", "P > 4. Amendment 1 (x)": "amended",
                      "P > 4. Amendment 1 (x) > 4.1 Changes": "amended", "P > 5. After": "registered"}


def test_override_for_another_file_does_not_apply():
    rule = SectionRule("research/other/PREREG.md", re.compile("Q"), "amended")
    assert {c.status for c in chunk_document(doc("# P\n\n## Q\n\nq\n"), (rule,))} == {"registered"}


def test_unmatched_override_is_reported():
    d = doc("# P\n\n## Q\n\nq\n")
    ok = SectionRule(d.path, re.compile("^Q$"), "amended")
    stale = SectionRule(d.path, re.compile("^Amendment 9"), "amended")
    assert unmatched_overrides([d], (ok, stale)) == [stale]


def test_strip_sections_removes_subsections_until_the_next_peer():
    text = "# T\n\n## Keep\n\nk\n\n## Drop\n\nd\n\n### Drop.1\n\nd1\n\n## Keep 2\n\nk2\n"
    out, n = strip_sections(text, [re.compile("^Drop$")])
    assert n == 1 and "d1" not in out and "\nd\n" not in out and "k2" in out and "## Keep\n" in out


def test_apply_skips_and_unmatched_skip():
    d = doc("# T\n\n## Daily table\n\n| a |\n\n## Rest\n\nr\n", path="research/s/REPORT.md")
    kept = apply_skips(d, (SkipRule(d.path, re.compile("^Daily table$"), "fixture"),))
    assert "Daily table" not in kept.text and "Rest" in kept.text
    with pytest.raises(ValueError, match="matches no heading"):
        apply_skips(d, (SkipRule(d.path, re.compile("^Nope$"), "fixture"),))


def test_chunk_ids_are_stable_and_unique():
    text = "# T\n\n## A\n\na\n\n## B\n\nb\n"
    a, b = chunk_document(doc(text)), chunk_document(doc(text))
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert len({c.chunk_id for c in a}) == len(a)
    assert a[0].embed_text().startswith("research/s/PREREG.md\nT > A\n\n")
