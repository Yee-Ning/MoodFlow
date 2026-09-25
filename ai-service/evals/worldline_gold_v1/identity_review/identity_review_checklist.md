# WorldLine Gold V1 Identity Review Checklist

> Identity review is independent from relation review. A strong genre source does not prove a catalog mapping.

## 1. Knowledge Artist

- [ ] Canonical name is copied from an identifiable Artist source.
- [ ] `knowledge_entity_id` follows `artist:<slug>`.
- [ ] Every alias is confirmed to refer to the same Artist.
- [ ] Group names and member names are not treated as aliases.
- [ ] Stage name and legal name are linked only when a reliable source makes the link explicit.
- [ ] Chinese simplified/traditional forms are recorded without inventing translations.
- [ ] Japanese/Korean local-script and Romanized names use documented forms.

## 2. Catalog Artist

- [ ] The reviewer opened the catalog Artist page.
- [ ] Provider and raw Artist ID are recorded exactly.
- [ ] Display name matches the candidate or a reviewed alias.
- [ ] Region or market information is consistent.
- [ ] At least one representative work matches the same Artist.
- [ ] A second differentiating field, such as album, group membership or biography, also matches.
- [ ] The page is an Artist page rather than a song, album, playlist, tribute or cover page.
- [ ] Expected node ID equals `<provider>:artist:<entity_id>`.

## 3. Collision and ambiguity checks

- [ ] Search results were checked for same-name Artists.
- [ ] Solo Artist and group identities were compared separately.
- [ ] Alternate provider IDs were recorded when more than one looks plausible.
- [ ] The reviewer did not select an ID solely because it ranked first.
- [ ] No temporary or fabricated catalog ID was created.

## 4. Status decision

Record one preparation status without assigning the Gold outcome:

- `resolved`: one knowledge Artist maps uniquely to one catalog Artist.
- `ambiguous`: multiple identities or provider IDs remain plausible.
- `unresolved`: no verifiable mapping was found.

If the status is `ambiguous` or `unresolved`, keep the candidate pending and describe the exact blocker. Do not choose the most probable ID.

## 5. Reviewer record

- [ ] Identity evidence source IDs exist in the source workspace.
- [ ] Reviewer name and timestamp are recorded.
- [ ] Notes name the fields used to establish or reject the mapping.
- [ ] Identity review remains `pending` until the reviewer completes the checks.
- [ ] Final accepted/rejected outcome is left blank during Batch A preparation.
