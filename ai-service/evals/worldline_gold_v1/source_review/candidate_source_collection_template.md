# Candidate Source Collection Template

> Copy this template once per source. Source collection does not assign an accepted or rejected outcome.

## Candidate

- Candidate ID:
- Artist canonical name:
- Proposed Genre ID:
- Relation type: `associated_with`
- Collector:
- Collection timestamp:

## Source

- Source ID:
- Fixture source ID:
- Source title:
- Publisher:
- URL:
- Local provenance:
- Source type:
- Language:
- Accessed date:

At least one of URL or local provenance must be present.

## Frozen Content

- Evidence text:

```text

```

- Content SHA256:
- Hash input encoding: `UTF-8`
- Normalization applied: `none`

## Evidence Span

- Exact span:

```text

```

- Span start:
- Span end:
- Check: `evidence_text[span_start:span_end] == exact_span`

## Review Questions

- [ ] Is the source real and reproducible?
- [ ] Does the publisher have identifiable editorial or institutional responsibility?
- [ ] Can the bounded content be frozen for evaluation review?
- [ ] Does the exact span describe the Artist rather than only a song or album?
- [ ] Does it directly support the proposed registered Genre?
- [ ] Are negation, comparison and attribution preserved?
- [ ] Is this source independent from other collected sources?
- [ ] Is the SHA256 calculated from the exact frozen text?

## Preparation Status

- Evidence status: `pending`
- Relation review status: `pending`
- Reviewer notes:
- Final Gold outcome: **leave blank**

Do not use search summaries, model answers, unattributed aggregators or a social-media short sentence as the only evidence.
