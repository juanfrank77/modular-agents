# SKILL: ingest-resource

## Trigger
Ingest resource, distill knowledge, summarize PDF, transcribe audio, save article, process document

## Steps
1. Read the full resource content provided in the <resource> block
2. Identify what the resource actually claims or teaches — not just its topic
3. Extract only the non-obvious, usable ideas; skip filler and marketing
4. Write next actions as concrete first steps ("Draft a pricing page section using the anchoring technique"), not vague intentions ("think about pricing")
5. Match the resource against the user's active projects and name the ones it serves

## Output Format
- TITLE line first, max 8 words, specific not generic
- Summary: 2-3 sentences maximum
- Key ideas: 3-6 bullets, each one standalone and specific
- Next actions: 1-3 checkboxes, each doable in one sitting
- Related projects: exact project names from the user's list, or "none"

## Edge Cases
- If the resource is low-value (pure promotion, no substance): say so in the summary and give zero next actions rather than inventing them
- If the content is in Spanish, keep quotes in Spanish but write the note in the user's preferred language
- If the resource is very long, prioritize ideas that connect to active projects
