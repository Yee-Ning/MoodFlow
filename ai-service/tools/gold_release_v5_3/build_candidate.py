"""Prepare only the two approved V5.3 row replacements, pending West signoff."""

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "ai-service/evals/datasets/rag_eval_gold_v5_2.frozen.jsonl"
TARGET = SOURCE.with_name("rag_eval_gold_v5_3.frozen.jsonl")
PROJECTION = ROOT / "ai-service/data/reruns/jay-chou-release-freeze-20261019/run01/index/index_projection.jsonl"
SOURCE_SHA = "3f008114b7171831b5ea219f68b3fd6c7a506a943275a2f5e48e7f6c428432d4"
CHANGED = frozenset({"rag-v0-036", "rag-v0-039"})
BACKGROUND_PARENT = "parent-59a19e17c352f9ee3b99208a"
ACTIVITY_PARENT = "parent-a03c56f88de76eda774a6e18"
ALBUM_PARENT = "parent-a9a1ce0be198f68e5eae8ffc"
FACTS_039 = [
    "2008年发行第9张专辑《魔杰座》",
    "2010年发行第10张专辑《跨时代》，并于2010年6月11日在臺北小巨蛋展开《超时代演唱会》",
    "2011年11月11日发行第11张专辑《惊叹号》",
    "2012年12月28日发行第12张专辑《十二新作》；专辑年表同时列有2008至2012年的上述四张作品",
    "这些作品发行与演出活动节点不足以判断2008至2012年的整体风格变化或事业转型",
]
QUOTES_039 = (
    ("v5_3-039-01", ACTIVITY_PARENT, "2008年发行《魔杰座》", "2008年發行第9張專輯《魔杰座》"),
    ("v5_3-039-02", ACTIVITY_PARENT, "2010年发行《跨时代》并展开《超时代演唱会》",
     "2010年發行第10張專輯《跨時代》并于2010年6月11日在臺北小巨蛋開展《超時代演唱会》。"),
    ("v5_3-039-03", ACTIVITY_PARENT, "2011年11月11日发行《惊叹号》",
     "2011年11月11日发行第11张专辑《惊叹号》，专辑同名歌曲为主打单曲。"),
    ("v5_3-039-04", ACTIVITY_PARENT, "2012年演出及12月28日发行《十二新作》",
     "2012年9月22日，周杰倫在新加坡F1（世界一級方程式錦標賽）賽道開唱。12月28日，周杰倫發行個人第12張全新專輯《十二新作》。"),
    ("v5_3-039-05", ALBUM_PARENT, "专辑年表列出2008、2010、2011、2012四个音乐作品节点",
     "2012 十二新作2011 驚嘆號2010 跨時代2008 魔杰座"),
)


def rows(raw):
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def projection():
    result = defaultdict(list)
    with PROJECTION.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["parent_id"] in {ACTIVITY_PARENT, ALBUM_PARENT}:
                result[row["parent_id"]].append(row)
    return result


def anchor(anchor_id, parent_id, claim, quote, parents):
    children = parents[parent_id]
    body = children[0]["payload"]["parent_context"]["content"]
    if body.count(quote) != 1:
        raise ValueError("anchor_parent_exact_quote_missing_or_ambiguous")
    start = body.index(quote)
    quotes = [{"chunk_id": parent_id, "field": "content", "exact_text": quote, "start": start, "end": start + len(quote)}]
    child_ids = []
    for child in children:
        content = child["payload"]["content"]
        if quote in content:
            if content.count(quote) != 1:
                raise ValueError("anchor_child_quote_ambiguous")
            child_ids.append(child["chunk_id"])
            position = content.index(quote)
            quotes.append({"chunk_id": child["chunk_id"], "field": "content", "exact_text": quote,
                           "start": position, "end": position + len(quote)})
    if not child_ids:
        raise ValueError("anchor_has_no_matching_child")
    return {"anchor_id": anchor_id, "status": "matched", "claim": claim, "source_id": children[0]["payload"]["source_id"],
            "parent_id": parent_id, "child_ids": child_ids, "exact_text": quote, "quotes": quotes}


def replacements(original, parents):
    items = rows(original)
    if len(items) != 50 or len({item["case_id"] for item in items}) != 50:
        raise ValueError("expected_fifty_unique_cases")
    selected = {item["case_id"]: deepcopy(item) for item in items if item["case_id"] in CHANGED}
    if set(selected) != CHANGED:
        raise ValueError("approved_case_missing")
    case036 = selected["rag-v0-036"]
    if len(case036["required_facts"]) != 3 or len(case036["anchors"]) != 5:
        raise ValueError("036_source_shape_drift")
    if "2005年11月" not in case036["required_facts"][-1] or case036["anchors"][-1]["parent_id"] != BACKGROUND_PARENT:
        raise ValueError("036_background_target_drift")
    if BACKGROUND_PARENT in case036["relevant_parent_ids"]:
        raise ValueError("036_background_already_gold")
    case036["required_facts"] = case036["required_facts"][:-1]
    case036["anchors"] = case036["anchors"][:-1]
    case039 = selected["rag-v0-039"]
    if case039["relevant_parent_ids"] != [ACTIVITY_PARENT, ALBUM_PARENT]:
        raise ValueError("039_gold_parent_identity_drift")
    case039["required_facts"] = list(FACTS_039)
    case039["anchors"] = [anchor(*spec, parents) for spec in QUOTES_039]
    return {cid: json.dumps(item, ensure_ascii=False) for cid, item in selected.items()}


def candidate_bytes(original, parents):
    updated = replacements(original, parents)
    result = []
    for line in original.splitlines(keepends=True):
        cid = json.loads(line)["case_id"]
        ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
        result.append(updated[cid].encode("utf-8") + ending if cid in updated else line)
    return b"".join(result)


def verify(original, candidate, parents):
    if hashlib.sha256(original).hexdigest() != SOURCE_SHA:
        raise ValueError("v52_frozen_source_sha_drift")
    expected = candidate_bytes(original, parents)
    if candidate != expected:
        raise ValueError("candidate_differs_from_exact_approved_replacements")
    old_lines, new_lines = original.splitlines(keepends=True), candidate.splitlines(keepends=True)
    old, new = rows(original), rows(candidate)
    unchanged, changed = [], []
    for index, (before, after, old_line, new_line) in enumerate(zip(old, new, old_lines, new_lines, strict=True), 1):
        cid = before["case_id"]
        if before["relevant_parent_ids"] != after["relevant_parent_ids"]:
            raise ValueError("gold_parent_list_changed")
        if cid in CHANGED:
            fields = [key for key in before if before[key] != after[key]]
            if set(fields) != {"required_facts", "anchors"} or set(before) != set(after):
                raise ValueError("changed_unapproved_fields")
            changed.append({"line_number": index, "case_id": cid, "changed_fields": fields,
                            "required_fact_count_before": len(before["required_facts"]), "required_fact_count_after": len(after["required_facts"]),
                            "anchor_count_before": len(before["anchors"]), "anchor_count_after": len(after["anchors"])})
        else:
            if old_line != new_line:
                raise ValueError("unapproved_case_bytes_changed")
            unchanged.append({"line_number": index, "case_id": cid,
                              "v52_line_sha256": hashlib.sha256(old_line).hexdigest(),
                              "v53_line_sha256": hashlib.sha256(new_line).hexdigest()})
    if len(unchanged) != 48 or len(changed) != 2:
        raise ValueError("change_population_invalid")
    by_id = {item["case_id"]: item for item in new}
    audits = []
    for item in by_id["rag-v0-039"]["anchors"]:
        parent_id = item["parent_id"]
        if parent_id not in by_id["rag-v0-039"]["relevant_parent_ids"]:
            raise ValueError("039_anchor_outside_gold")
        children = parents[parent_id]
        bodies = {parent_id: children[0]["payload"]["parent_context"]["content"],
                  **{child["chunk_id"]: child["payload"]["content"] for child in children}}
        for quote in item["quotes"]:
            if bodies[quote["chunk_id"]][quote["start"]:quote["end"]] != quote["exact_text"]:
                raise ValueError("039_quote_offset_mismatch")
        audits.append({"anchor_id": item["anchor_id"], "parent_id": parent_id, "child_ids": item["child_ids"],
                       "exact_text": item["exact_text"], "parent_and_child_exact_match": True,
                       "quotes": [{key: quote[key] for key in ("chunk_id", "start", "end")} for quote in item["quotes"]]})
    if {item["parent_id"] for item in audits} != {ACTIVITY_PARENT, ALBUM_PARENT}:
        raise ValueError("039_gold_anchor_coverage_incomplete")
    if any(item["parent_id"] not in by_id["rag-v0-036"]["relevant_parent_ids"] for item in by_id["rag-v0-036"]["anchors"]):
        raise ValueError("036_anchor_outside_gold")
    others = [{"case_id": item["case_id"], "is_gold": BACKGROUND_PARENT in item["relevant_parent_ids"],
               "anchor_ids": [a["anchor_id"] for a in item["anchors"] if a["parent_id"] == BACKGROUND_PARENT]}
              for item in new if item["case_id"] != "rag-v0-036" and (BACKGROUND_PARENT in item["relevant_parent_ids"] or any(a["parent_id"] == BACKGROUND_PARENT for a in item["anchors"]))]
    untouched_old = b"".join(line for item, line in zip(old, old_lines, strict=True) if item["case_id"] not in CHANGED)
    untouched_new = b"".join(line for item, line in zip(new, new_lines, strict=True) if item["case_id"] not in CHANGED)
    return {"status": "validated_candidate_awaiting_West_signature", "schema_version": "v5", "v52_source_sha256": SOURCE_SHA,
            "v53_file_sha256": None, "v53_sha256_status": "not_computed_before_West_signature",
            "v53_signed_by": None, "v53_signed_at": None, "row_freeze_fields": "inherited V5.2 provenance, not V5.3 signoff",
            "rows": 50, "unchanged_rows": unchanged, "changed_rows": changed,
            "unchanged_48_rows_sha256": {"v52": hashlib.sha256(untouched_old).hexdigest(), "v53": hashlib.sha256(untouched_new).hexdigest()},
            "all_parent_lists_identical": True, "all_ground_truths_identical": True,
            "answerability_counts": dict(Counter(item["answerability"] for item in new)),
            "039_anchors": audits, "036_background_other_case_references_preserved": others,
            "new_retrieval_or_model_calls": 0}


def main():
    original, parents = SOURCE.read_bytes(), projection()
    if sys.argv[1:] == ["replacement-lines"]:
        if hashlib.sha256(original).hexdigest() != SOURCE_SHA or TARGET.exists():
            raise ValueError("source_changed_or_candidate_already_exists")
        print(json.dumps(replacements(original, parents), ensure_ascii=False))
    elif sys.argv[1:] == ["verify"]:
        print(json.dumps(verify(original, TARGET.read_bytes(), parents), ensure_ascii=False, indent=2))
    else:
        raise ValueError("only_replacement_lines_or_read_only_verify_supported")


if __name__ == "__main__":
    main()
