"""Approved H1 checker corrections, without changing prompts or saved text.

Only 031/034 use set coverage. Chinese script conversion applies to title
comparisons only; literal queries, entity resolution and requests stay intact.
Windows NLS is used rather than adding a dependency to the frozen environment.
"""

import ctypes
from ctypes import wintypes
from functools import lru_cache
import re

from pydantic import ValidationError

from evals import rag_h1_bundle_v5 as original


SET_COVERAGE_IDS = frozenset({"rag-v0-031", "rag-v0-034"})


@lru_cache(maxsize=512)
def title_key(value):
    if not value:
        return value
    mapper = ctypes.WinDLL("kernel32", use_last_error=True).LCMapStringEx
    mapper.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR,
                       ctypes.c_int, wintypes.LPWSTR, ctypes.c_int,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.LPARAM]
    mapper.restype = ctypes.c_int
    # NLS length is in UTF-16 code units, including supplementary characters.
    length = len(value.encode("utf-16-le")) // 2
    size = mapper("zh-CN", 0x02000000, value, length, None, 0, None, None, 0)
    if size <= 0:
        raise original.H1Error("h1_title_normalization_failed")
    buffer = ctypes.create_unicode_buffer(size + 1)
    if mapper("zh-CN", 0x02000000, value, length, buffer, size, None, None, 0) != size:
        raise original.H1Error("h1_title_normalization_failed")
    return buffer.value


def title_drift(text, row, *, hyde, entity_resolver, coverage_texts):
    original_titles = set(re.findall(r"《([^》]+)》", row["completed_query"]))
    titles = set(re.findall(r"《([^》]+)》", text))
    works = [e for e in row["entities"] if e["status"] == "resolved"
             and e["entity_type"] in {"song", "album"}]
    allowed = {title_key(t) for t in original_titles}
    for entity in works:
        allowed.update(title_key(name) for name in
                       [entity["surface"], entity["canonical_name"], *entity["aliases"]]
                       if name and name in row["completed_query"])
    if {title_key(t) for t in titles} - allowed:
        return True
    if hyde:
        return False
    # Coverage is checked over separate queries, without concatenating them and
    # inadvertently changing the resolver's local type cues.
    normalized = [title_key(t) for t in coverage_texts]
    if any(not any(title_key(title) in t for t in normalized) for title in original_titles):
        return True
    required_ids = {e["knowledge_entity_id"] for e in works if e["knowledge_entity_id"]
                    and any(title_key(title) in {title_key(name) for name in
                            [e["surface"], e["canonical_name"], *e["aliases"]] if name}
                            for title in original_titles)}
    if entity_resolver is not None and required_ids:
        resolved_ids = {e.knowledge_entity_id for query in coverage_texts
                        for e in entity_resolver.resolve(query, catalog_references=[])
                        if e.status.value == "resolved" and e.knowledge_entity_id}
        if not required_ids <= resolved_ids:
            return True
    return False


def validate_text_constraints(text, row, *, hyde=False, entity_resolver=None, coverage_texts=None):
    if coverage_texts is not None and (hyde or row["case_id"] not in SET_COVERAGE_IDS):
        raise original.H1Error("h1_unapproved_set_coverage")
    coverage_texts = [text] if coverage_texts is None else coverage_texts
    question = row["completed_query"]
    original_years = set(original.YEAR.findall(question))
    years = set(original.YEAR.findall(text))
    if years - original_years or (not hyde and original_years != years):
        raise original.H1Error("h1_year_constraint_drift")
    if title_drift(text, row, hyde=hyde, entity_resolver=entity_resolver, coverage_texts=coverage_texts):
        raise original.H1Error("h1_title_constraint_drift")
    if not hyde:
        for entity in row["entities"]:
            if entity["status"] != "resolved":
                continue
            names = [entity["surface"], entity["canonical_name"], *entity["aliases"]]
            if not any(name and name.casefold() in query.casefold()
                       for name in names for query in coverage_texts):
                raise original.H1Error("h1_entity_constraint_drift")
        for term in row["constraints"]["negative_terms"]:
            if not re.search(r"(?:不要|排除|不包括|不含)\s*" + re.escape(term), text):
                raise original.H1Error("h1_negative_constraint_drift")
        for cue in ("中文资料", "中文資料", "繁体资料", "繁體資料", "英文资料", "英文資料",
                    "采访资料", "訪談資料", "新闻资料", "新聞資料", "乐评资料", "樂評資料"):
            if cue in question and cue not in text:
                raise original.H1Error("h1_document_constraint_drift")
    if entity_resolver is not None:
        original_ids = {e["knowledge_entity_id"] for e in row["entities"] if e["knowledge_entity_id"]}
        new_entities = entity_resolver.resolve(text, catalog_references=[])
        added = {e.knowledge_entity_id for e in new_entities if e.knowledge_entity_id
                 and e.entity_type.value in {"artist", "song", "album", "person"}} - original_ids
        if added:
            raise original.H1Error("h1_added_registry_entity")
    return {"years": sorted(years), "quoted_titles": sorted(re.findall(r"《([^》]+)》", text)),
            "checks": "passed", "coverage": "set" if len(coverage_texts) == 3 else "per_query",
            "semantic_equivalence": "requires_West_review_not_proved_by_rules"}


def validate_expansion(raw, row, entity_resolver=None):
    try:
        parsed = original.ExpansionOutput.model_validate_json(raw)
    except ValidationError as exc:
        raise original.H1Error("h1_expansion_json_schema_invalid") from exc
    queries = [original.literal(text) for text in parsed.queries]
    if any(not q for q in queries) or len({q.casefold() for q in queries}) != 3:
        raise original.H1Error("h1_expansion_blank_or_duplicate")
    coverage = queries if row["case_id"] in SET_COVERAGE_IDS else None
    return queries, [validate_text_constraints(q, row, entity_resolver=entity_resolver,
                                              coverage_texts=coverage) for q in queries]


def validate_hyde(raw, row, entity_resolver=None):
    text = " ".join(raw.split())
    if not text or len(text) > 1000 or "```" in text:
        raise original.H1Error("h1_hyde_blank_long_or_fenced")
    return text, validate_text_constraints(text, row, hyde=True, entity_resolver=entity_resolver)
