"""Approved H1 title punctuation correction; keep all other checks identical.

The historical evaluator remains immutable. This validator changes only its
title-format condition; prompt, year/entity/person/exclusion checks are copied
verbatim and checked structurally by unit tests against the historical function.
"""

import re

from evals.rag_h1_bundle_v5 import H1Error, YEAR


def title_format_drift(titles, original_titles, text, row, *, hyde, entity_resolver):
    # A title already present without brackets may acquire brackets, too.
    work_entities = [e for e in row["entities"] if e["status"] == "resolved"
                     and e["entity_type"] in {"song", "album"}]
    allowed = set(original_titles)
    for entity in work_entities:
        allowed.update(name for name in [entity["surface"], entity["canonical_name"], *entity["aliases"]]
                       if name and name in row["completed_query"])
    if titles - allowed:
        return True
    if hyde:
        return False  # Same historical policy: HyDE need not repeat every title.
    if any(title not in text for title in original_titles):
        return True
    # Removing punctuation must still resolve to the same known work, when the
    # original title is registry-backed. Unknown new quoted works already fail.
    required_ids = {e["knowledge_entity_id"] for e in work_entities if e["knowledge_entity_id"]
                    and any(title in {e["surface"], e["canonical_name"], *e["aliases"]}
                            for title in original_titles)}
    if entity_resolver is not None and required_ids:
        resolved_ids = {e.knowledge_entity_id for e in entity_resolver.resolve(text, catalog_references=[])
                        if e.status.value == "resolved" and e.knowledge_entity_id}
        if not required_ids <= resolved_ids:
            return True
    return False


def validate_text_constraints(text, row, *, hyde=False, entity_resolver=None):
    original = row["completed_query"]
    original_years = set(YEAR.findall(original))
    years = set(YEAR.findall(text))
    if years - original_years or (not hyde and original_years != years):
        raise H1Error("h1_year_constraint_drift")
    titles = set(re.findall(r"《([^》]+)》", text))
    original_titles = set(re.findall(r"《([^》]+)》", original))
    if title_format_drift(titles, original_titles, text, row, hyde=hyde, entity_resolver=entity_resolver):
        raise H1Error("h1_title_constraint_drift")
    if not hyde:
        for entity in row["entities"]:
            if entity["status"] != "resolved":
                continue
            names = [entity["surface"], entity["canonical_name"], *entity["aliases"]]
            if not any(name and name.casefold() in text.casefold() for name in names):
                raise H1Error("h1_entity_constraint_drift")
        for term in row["constraints"]["negative_terms"]:
            if not re.search(r"(?:不要|排除|不包括|不含)\s*" + re.escape(term), text):
                raise H1Error("h1_negative_constraint_drift")
        for cue in ("中文资料", "中文資料", "繁体资料", "繁體資料", "英文资料", "英文資料",
                    "采访资料", "訪談資料", "新闻资料", "新聞資料", "乐评资料", "樂評資料"):
            if cue in original and cue not in text:
                raise H1Error("h1_document_constraint_drift")
    if entity_resolver is not None:
        original_ids = {e["knowledge_entity_id"] for e in row["entities"] if e["knowledge_entity_id"]}
        new_entities = entity_resolver.resolve(text, catalog_references=[])
        added = {e.knowledge_entity_id for e in new_entities if e.knowledge_entity_id
                 and e.entity_type.value in {"artist", "song", "album", "person"}} - original_ids
        if added:
            raise H1Error("h1_added_registry_entity")
    return {"years": sorted(years), "quoted_titles": sorted(titles),
            "checks": "passed", "semantic_equivalence": "requires_West_review_not_proved_by_rules"}
