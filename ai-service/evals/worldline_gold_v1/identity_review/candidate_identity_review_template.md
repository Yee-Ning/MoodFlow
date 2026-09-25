# Candidate Identity Review Template

> Complete this independently from relation review. This template does not assign a Gold outcome.

## Candidate

- Candidate ID:
- Proposed canonical name:
- Region:
- Proposed aliases:
- Identity reviewer:
- Review timestamp:

## Knowledge Entity

- Knowledge entity ID:
- Registry scope: `production_registry | fixture_only_registry | pending`
- Canonical name verified from:
- Alias evidence source IDs:
- Local-script name:
- Romanized/English name:
- Group or solo Artist:

## Catalog Entity

- Provider:
- Raw catalog Artist ID:
- Catalog node ID:
- Catalog display name:
- Catalog Artist page URL:
- Region/market match:
- Representative work used for verification:
- Second differentiating field:
- Identity evidence source IDs:

Expected node format:

```text
<provider>:artist:<entity_id>
```

## Alias Check

- [ ] Every alias refers to the same Artist.
- [ ] Stage name and legal name have an explicit source link.
- [ ] Local-script and Romanized forms are documented forms.
- [ ] Group and member names are not treated as aliases.
- [ ] Simplified/traditional variants were not treated as different Artists.

## Ambiguity Check

- [ ] Same-name catalog Artists were inspected.
- [ ] Solo and group identities were compared separately.
- [ ] Page type is Artist, not song, album, playlist, tribute or cover.
- [ ] More than one plausible catalog ID has been recorded rather than silently discarded.
- [ ] The ID was not selected only because it ranked first.
- [ ] No temporary or fabricated catalog ID was created.

## Preparation Status

- Identity status: `pending | resolved | ambiguous | unresolved`
- Ambiguity reason:
- Reviewer notes:
- Final Gold outcome: **leave blank**

If more than one identity remains plausible, record `ambiguous`. If no mapping can be verified, record `unresolved`. Do not choose an ID by probability.
