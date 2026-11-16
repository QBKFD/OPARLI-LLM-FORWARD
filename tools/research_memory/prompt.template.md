<!--
Template for the research-memory answer prompt. The owner writes the prompt;
this file holds only the placeholder below. Copy it to the path set in
research_memory.toml (answer.prompt_path) and replace the placeholder. Until
then the graph abstains ("config_invalid: prompt: ...").

What the code does with the file:
- HTML comments (like this one) are removed; the rest is sent verbatim as the
  system prompt.
- The user message holds data only: "Question: ...", then each retrieved chunk
  as "[n] path: ...", "heading: ...", "heading path: ...", "status: ...",
  "study: ...", "doc type: ...", "text:" and the chunk's text.
- The answer is constrained to this JSON schema (structured output):
  {"claims": [{"text": "...", "citations": [{"path": "...", "heading": "..."}]}]}
- The citation check accepts a citation whose path is a retrieved chunk's path
  and whose heading equals that chunk's "heading" or "heading path".
  Any uncited claim, or a citation of a chunk that was not retrieved, turns the
  whole answer into "not in the documents". No claims gives the same.
- Status labels and the "From drafts (not registered)" grouping are added by
  the code from the chunk metadata.
-->
<owner: the system prompt for the research-memory answer step>
