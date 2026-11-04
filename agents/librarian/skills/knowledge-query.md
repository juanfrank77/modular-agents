# SKILL: knowledge-query

## Trigger
Knowledge question, what do I know, search notes, find resource, recall saved

## Steps
1. Check the matching notes provided in <note> blocks first
2. Use the <graph> block (knowledge-graph traversal), when present, to find connections between notes that keyword matching missed
3. Answer from note and graph content only — cite which note the answer comes from
4. If notes don't cover the question, say so and suggest what kind of resource to save

## Output Format
- Direct answer first, then "From: <note title>" attribution
- Keep under 200 words unless the user asks for detail

## Edge Cases
- Multiple notes conflict: present both views and note the dates
- Question is about a project rather than knowledge: suggest asking @projects instead
