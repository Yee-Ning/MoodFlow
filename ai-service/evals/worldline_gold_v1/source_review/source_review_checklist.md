# WorldLine Gold V1 Source Review Checklist

> This checklist prepares evidence for human review. Completing it does not approve a Gold relation.

## 1. Source identity

- [ ] The page or local document can be opened from its recorded URL or provenance.
- [ ] Title and publisher match the actual source.
- [ ] The publisher has identifiable editorial or institutional responsibility.
- [ ] `source_type` accurately describes the source.
- [ ] Language is recorded using the expected language code.
- [ ] The source is not merely a search result snippet.
- [ ] The source is not a model response.
- [ ] The source is not an unattributed aggregation page.
- [ ] A social-media sentence is not being used as the only evidence.

## 2. Freezing eligibility

- [ ] Terms and access permit storing the bounded context needed for evaluation review.
- [ ] Only the minimum context required for the decision will be frozen.
- [ ] URL or local Knowledge release provenance is stable and recorded.
- [ ] Duplicate URL and duplicate content-hash checks have been performed.
- [ ] Reposts of the same original article are not counted as independent corroboration.
- [ ] The bounded `evidence_text` can be reproduced exactly.
- [ ] SHA256 is calculated from the exact UTF-8 `evidence_text` without normalization.

## 3. Direct relation evidence

- [ ] The text identifies the candidate Artist, directly or through unambiguous local context.
- [ ] The text describes the Artist's genre rather than only a song or album genre.
- [ ] The text supports `artist → genre / associated_with`.
- [ ] The genre wording maps unambiguously to a registered Genre V2 ID.
- [ ] Negation, comparison, historical context and attribution have been preserved.
- [ ] “Influenced by”, “contains elements of” and collaborator genres have not been promoted automatically to `associated_with`.
- [ ] `exact_span` remains sufficient when read with the bounded context.

## 4. Exact span

- [ ] `exact_span` is copied verbatim without translation or normalization.
- [ ] `span_start` and `span_end` are calculated against the frozen `evidence_text`.
- [ ] `evidence_text[span_start:span_end] == exact_span`.
- [ ] The selected span is uniquely locatable or its offset disambiguates repeated text.
- [ ] The case source fields and Source Ledger fields are identical.

## 5. Reviewer record

- [ ] Reviewer name is recorded.
- [ ] Review timestamp is recorded.
- [ ] Notes explain why the source is sufficient, insufficient or excluded.
- [ ] Any excluded source uses one of the allowed reasons.
- [ ] Relation review remains `pending` during this preparation phase.

## Disallowed evidence shortcuts

- Search summaries or knowledge panels.
- Model-generated descriptions or citations.
- Pages without identifiable origin or editorial ownership.
- A social-media short sentence as the only source.
- A song/album genre statement used as an Artist genre statement.
- Evidence inferred from the current WorldLine output.
