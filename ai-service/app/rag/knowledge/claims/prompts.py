"""Versioned grounded claim-extraction prompt."""

CLAIM_EXTRACTION_PROMPT_V1 = """\
Extract only objective, atomic facts explicitly stated in chunk_content.
Each claim must express exactly one independently true-or-false proposition.
Use only supplied linked entity IDs. Prefer an entity ID over a free-text object.
Copy evidence_span exactly and contiguously from chunk_content.
Do not extract opinions, praise, criticism, speculation, or external knowledge.
If there is no supported factual claim, return an empty claims list.
Return only the provided JSON Structured Output schema.
"""

CLAIM_EXTRACTION_PROMPT_V2 = CLAIM_EXTRACTION_PROMPT_V1 + """
The title and document's primary artist are NOT evidence of who performed an action.
Extract only from this small original batch. Every subject and entity object must
be explicitly named by a unique supplied canonical name/alias INSIDE evidence_span.
Copy a complete sentence (including subject, object, qualifications and time).
Never transfer another artist's album, chart position, life event or action to a
linked artist merely because that artist is the article topic. Unknown actors
remain unextracted. A member's actions are not automatically the band's actions.
Classify modality before extracting: factual assertion, hypothetical,
counterfactual, speculation, negation, quotation/opinion. Only explicit affirmative
factual assertions may become definite claims. An if/would/could/another-band
scenario is not history. Metaphors, implications and lyrics are not factual claims.
Preserve semantic direction: A influenced B means A is subject and B is object;
A was influenced by B means A influenced_by B. A scene must be explicitly named
as a scene/movement/community, not inferred from genre or co-occurrence.
Use the most specific existing predicate for an explicitly stated relation.
Do not infer dates from publication date, title, world knowledge or adjacent facts.
Use time=null when the evidence does not explicitly state the claim's date.
Never output an all-null time object. Output no more than four atomic claims per
batch; prefer explicit relational facts over generic 'other' descriptions.
"""
