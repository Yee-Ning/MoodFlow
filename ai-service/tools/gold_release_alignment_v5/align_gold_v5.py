"""Offline, text-first Gold V5 proposal; never edits the frozen Gold or a release."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys


GOLD_SHA256 = "1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e"
RELEASE_ID = "knowledge-v2-e40ab37ffcc7bcf0"
ORIGINAL_NEEDS_INPUT = {"rag-v0-046", "rag-v0-047", "rag-v0-048", "rag-v0-068"}
CLARIFY_CASES = {"rag-v0-042", "rag-v0-043", "rag-v0-044"}
RANK = {"insufficient": 0, "partial": 1, "full": 2}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalized(text: str) -> str:
    """Collapse whitespace only: no punctuation, script, or entity substitution."""
    return re.sub(r"\s+", " ", text).strip()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()]


def check_gold(path: Path) -> list[dict]:
    raw = path.read_bytes()
    if digest(raw) != GOLD_SHA256:
        raise ValueError("STOP: frozen Gold SHA256 mismatch")
    rows = load_jsonl(path)
    if len(raw.splitlines()) != 50 or len(rows) != 50:
        raise ValueError("STOP: Gold must contain 50 physical lines / records")
    if len({r["case_id"] for r in rows}) != 50:
        raise ValueError("STOP: duplicate Gold case_id")
    empty = {r["case_id"] for r in rows if r["reference_answer"] in (None, "")}
    needs = {r["case_id"] for r in rows if r["expected_outcome"] == "needs_input"}
    if empty != ORIGINAL_NEEDS_INPUT or needs != empty:
        raise ValueError("STOP: original needs_input contract changed")
    return rows


class Corpus:
    def __init__(self, rows: list[dict], quarantine: list[dict]):
        self.rows = rows
        self.quarantine = quarantine
        self.sources = {r["metadata"]["source_id"] for r in rows}
        self.texts = {r["chunk_id"]: normalized(r["content"]) for r in rows}
        self.by_id = {r["chunk_id"]: r for r in rows}
        self.claims = [
            (row, claim["claim"]["statement"])
            for row in rows for claim in row.get("knowledge", {}).get("claims", [])
        ]

    def exact_locations(self, quote: str) -> list[dict]:
        needle = normalized(quote)
        if not needle:
            raise ValueError("Empty evidence quote is forbidden")
        groups = {}
        for row in self.rows:
            start = self.texts[row["chunk_id"]].find(needle)
            if start < 0:
                continue
            source = row["metadata"]["source_id"]
            parent = row["parent_id"] or row["chunk_id"]
            key = (source, parent)
            location = groups.setdefault(key, {
                "source_id": source, "parent_id": parent, "child_ids": [],
                "parent_text_matched": False, "matches": [],
            })
            if row["parent_id"] is None:
                location["parent_text_matched"] = True
            else:
                location["child_ids"].append(row["chunk_id"])
            # Retain an actual new-text quote, not the old chunk locator.
            pattern = r"\s+".join(re.escape(part) for part in quote.strip().split())
            raw_match = re.search(pattern, row["content"])
            if raw_match is None:
                raise ValueError("Normalized match cannot be traced to raw text")
            location["matches"].append({
                "chunk_id": row["chunk_id"], "field": "content",
                "quote": raw_match.group(), "raw_start": raw_match.start(),
                "raw_end": raw_match.end(),
            })
        for location in groups.values():
            location["child_ids"].sort()
        return [groups[key] for key in sorted(groups)]

    def suggestions(self, claim: str, source: str | None) -> list[dict]:
        """Lexical hints only. They can never become accepted evidence."""
        def grams(text):
            text = normalized(text).lower()
            return {text[i:i + 2] for i in range(max(0, len(text) - 1))}
        target = grams(claim)
        scores = []
        for row in self.rows:
            if row["parent_id"] is not None:
                continue
            if source in self.sources and row["metadata"]["source_id"] != source:
                continue
            shared = target & grams(row["content"])
            score = len(shared) / max(1, len(target))
            if score > 0:
                scores.append((score, row["chunk_id"], row, "content"))
        for row, statement in self.claims:
            if source in self.sources and row["metadata"]["source_id"] != source:
                continue
            shared = target & grams(statement)
            score = len(shared) / max(1, len(target))
            if score > 0:
                scores.append((score, row["chunk_id"], row, "knowledge.claim.statement"))
        output, seen = [], set()
        for score, _, row, field in sorted(scores, key=lambda x: (-x[0], x[1], x[3])):
            key = (row["metadata"]["source_id"], row["parent_id"] or row["chunk_id"])
            if key in seen:
                continue
            seen.add(key)
            output.append({"source_id": key[0], "parent_id": key[1],
                           "score": round(score, 4), "field": field,
                           "preview": row["content"][:240], "accepted_as_evidence": False})
            if len(output) == 3:
                break
        return output

    def locate(self, anchor: dict) -> dict:
        quote = anchor["exact_text"]
        if not isinstance(quote, str) or not normalized(quote):
            raise ValueError("Evidence anchors require nonempty exact_text")
        source = anchor.get("source_id")
        all_hits = self.exact_locations(quote)
        context = anchor.get("required_context")
        if context:
            all_hits = [hit for hit in all_hits
                        if normalized(context) in self.texts[hit["parent_id"]]]
        preferred = [hit for hit in all_hits if hit["source_id"] == source]
        eligible = preferred or all_hits
        result = {"anchor_id": anchor["anchor_id"], "source_id": source,
                  "claim": anchor["claim"], "exact_text": quote,
                  "origin": anchor.get("origin", "old_supporting_span"),
                  "all_exact_candidates": all_hits, "accepted_locations": [],
                  "quarantine_matches": [], "suggestions": []}
        if context:
            result["required_context"] = context
        if len(eligible) == 1:
            result.update(status="matched", accepted_locations=eligible,
                          source_changed=eligible[0]["source_id"] != source)
        elif len(eligible) > 1:
            result.update(status="ambiguous", reason="multiple_parent_candidates")
        elif source not in self.sources:
            result.update(status="source_absent", reason="source_not_in_release")
        else:
            result.update(status="text_changed", reason="source_present_quote_not_located")
        if result["status"] == "source_absent":
            needle = normalized(quote)
            for document in self.quarantine:
                haystack = normalized(document["markdown"])
                if needle in haystack:
                    result["quarantine_matches"].append({
                        "source_id": document["source_id"], "document_id": document["document_id"],
                        "path": document["path"], "field": "markdown",
                        "normalized_start": haystack.index(needle), "quote": quote,
                        "same_source": document["source_id"] == source,
                        "parent_id": None, "child_ids": [], "usable_in_release": False,
                    })
            result["recovery"] = ("recoverable_by_unquarantine" if result["quarantine_matches"]
                                  else "题超纲候选")
        if result["status"] != "matched":
            result["suggestions"] = self.suggestions(anchor["claim"], source)
        return result


def fact_alignment(fact: dict, anchors: dict[str, dict]) -> dict:
    output = {**fact, "proof_anchor_ids": [], "partial_anchor_ids": []}
    if fact["kind"] == "scope_limit":
        output["status"] = "scope_limit_not_a_positive_evidence_claim"
        return output
    options = fact["any_of"]
    completed = [option for option in options
                 if option and all(anchors[key]["status"] == "matched" for key in option)]
    if completed:
        output.update(status="matched", proof_anchor_ids=sorted(set(completed[0])))
    elif any(anchors[key]["status"] == "matched" for option in options for key in option):
        output.update(status="partial", partial_anchor_ids=sorted({
            key for option in options for key in option if anchors[key]["status"] == "matched"}))
    elif not options:
        output["status"] = "unresolved_no_anchor"
    else:
        states = {anchors[key]["status"] for option in options for key in option}
        output["status"] = next(s for s in ["ambiguous", "source_absent", "text_changed"] if s in states)
    return output


def old_label(case: dict) -> str:
    if case["expected_outcome"] == "needs_input":
        return "needs_input"
    return {"fully_answerable": "full", "partially_answerable": "partial",
            "insufficient_evidence": "insufficient"}[case["answerability_level"]]


def suggest_label(previous: str, facts: list[dict], clarify: bool = False) -> str:
    if previous == "needs_input" or clarify:
        return "needs_input"
    positive = [fact for fact in facts if fact["kind"] == "evidence"]
    observed = ("full" if positive and all(f["status"] == "matched" for f in positive)
                else "partial" if any(f["status"] in {"matched", "partial"} for f in positive)
                else "insufficient")
    return min((previous, observed), key=lambda label: RANK[label])


def validate_plan(cases: list[dict], plan: dict) -> None:
    if plan["review_status"] != "pending_west" or plan["release_id"] != RELEASE_ID:
        raise ValueError("Review plan is not a pending proposal for this release")
    if set(plan["cases"]) != {c["case_id"] for c in cases}:
        raise ValueError("Review plan must cover every case exactly once")
    for case in cases:
        entry = plan["cases"][case["case_id"]]
        if entry["old_required_facts"] != case["required_facts"]:
            raise ValueError("Review plan no longer matches original required_facts")
        anchors = {f"span-{i}" for i in range(1, len(case["supporting_spans"]) + 1)}
        for anchor in entry["extra_anchors"]:
            if anchor["anchor_id"] in anchors:
                raise ValueError("Duplicate review anchor_id")
            anchors.add(anchor["anchor_id"])
        for fact in entry["facts"]:
            if fact["kind"] not in {"evidence", "scope_limit"}:
                raise ValueError("Invalid fact kind")
            for option in fact["any_of"]:
                if not option or not set(option) <= anchors:
                    raise ValueError("Fact references a missing or empty evidence option")
        for group in entry["groups"]:
            if not set(group["anchor_ids"]) <= anchors:
                raise ValueError("Evidence group references a missing review anchor")
        if len(entry["groups"]) != len(case["evidence_groups"]):
            raise ValueError("Every evidence_group must be accounted for")
        if case["case_id"] in ORIGINAL_NEEDS_INPUT and (entry["facts"] or entry["extra_anchors"]):
            raise ValueError("Original needs_input cases must be carried over")


def align_case(case: dict, entry: dict, corpus: Corpus, number: int) -> dict:
    anchors = [dict(anchor_id=f"span-{i}", origin="old_supporting_span",
                    **{k: span[k] for k in ("source_id", "claim", "exact_text")})
               for i, span in enumerate(case["supporting_spans"], 1)]
    anchors += entry["extra_anchors"]
    located = {anchor["anchor_id"]: corpus.locate(anchor) for anchor in anchors}
    facts = [fact_alignment(fact, located) for fact in entry["facts"]]
    unanchored_searches = []
    if old_label(case) != "needs_input":
        for fact in facts:
            if fact["status"] == "unresolved_no_anchor":
                unanchored_searches.append({"claim": fact["text"],
                                            "candidates": corpus.suggestions(fact["text"], None),
                                            "accepted_as_evidence": False})
        if not anchors:
            unanchored_searches.append({"claim": case["query"],
                                        "candidates": corpus.suggestions(case["query"], None),
                                        "accepted_as_evidence": False})
    label = suggest_label(old_label(case), facts, case["case_id"] in CLARIFY_CASES)
    positive = [f for f in facts if f["kind"] == "evidence"]
    unresolved_states = {
        located[key]["status"] for fact in positive if fact["status"] != "matched"
        for option in fact["any_of"] for key in option if located[key]["status"] != "matched"
    }
    if old_label(case) == "needs_input":
        status = "needs_input_carry_over"
    elif positive and all(f["status"] == "matched" for f in positive):
        status = "matched"
    elif "source_absent" in unresolved_states:
        status = "source_absent"
    elif "ambiguous" in unresolved_states or any(f["status"] == "unresolved_no_anchor" for f in positive) or not positive:
        status = "ambiguous"
    else:
        status = "text_changed"
    status_reason = {
        "needs_input_carry_over": "original_needs_input_assets_preserved",
        "matched": "all_proposed_positive_facts_located_not_a_full_answerability_claim",
        "source_absent": "a_required_fact_depends_on_a_source_absent_from_release",
        "ambiguous": "non_unique_or_missing_query_evidence_anchor_requires_human_review",
        "text_changed": "source_present_but_a_required_quote_is_not_located",
    }[status]
    recovery = Counter(a.get("recovery") for a in located.values() if a.get("recovery"))
    proposed_facts = [f["text"] for f in facts if f["kind"] == "evidence" and f["status"] == "matched"]
    proposed_rubric = [f["text"] for f in facts
                       if f["status"] == "matched" or f["kind"] == "scope_limit"]
    if label == "needs_input":
        proposed_rubric = []
    if label == "needs_input":
        ground_truth = ""
    elif entry.get("draft_answer"):
        if any(f["status"] != "matched" for f in positive):
            raise ValueError("Draft answer requires all its positive facts to be grounded")
        ground_truth = entry["draft_answer"]
    elif positive and all(f["status"] == "matched" for f in positive):
        ground_truth = case["reference_answer"] or ""
    elif proposed_facts:
        ground_truth = ("本次新 release 可核实的要点是：" + "；".join(proposed_facts)
                        + "。其余原标注要点尚未在该 release 完成证据对齐，不能据此完整回答问题。")
    else:
        ground_truth = "本次对齐未在新 release 中定位到足以回答该问题的证据；不能据此给出可靠结论。"
    groups = []
    for original, mapping in zip(case["evidence_groups"], entry["groups"]):
        groups.append({"evidence_group_id": original["evidence_group_id"],
                       "claim": original["claim"], "relation": original["relation"],
                       "anchor_ids": mapping["anchor_ids"],
                       "status": "matched" if any(located[a]["status"] == "matched"
                                                   for a in mapping["anchor_ids"]) else "unresolved",
                       "new_locations": [p for a in mapping["anchor_ids"]
                                         for p in located[a]["accepted_locations"]],
                       "all_candidate_locations": [p for a in mapping["anchor_ids"]
                                                   for p in located[a]["all_exact_candidates"]],
                       "note": "按 claim/文本重建；未使用 acceptable_parent_ids 匹配"})
    return {"number": number, "case_id": case["case_id"], "question": case["query"],
            "old_expected_outcome": case["expected_outcome"],
            "old_answerability_level": case["answerability_level"],
            "old_label": old_label(case), "evidence_status": status,
            "evidence_status_reason": status_reason,
            "suggested_label": label, "review_status": "pending_west",
            "original_reference_answer": case["reference_answer"],
            "original_required_facts": case["required_facts"],
            "proposed_ground_truth": ground_truth,
            "proposed_required_facts": proposed_rubric,
            "scope_limits": [f["text"] for f in facts if f["kind"] == "scope_limit"],
            "fact_alignment": facts, "anchors": list(located.values()),
            "unanchored_claim_searches": unanchored_searches,
            "evidence_groups": groups, "recovery_counts": dict(recovery),
            "notes": entry["notes"], "alternatives": entry.get("alternatives", []),
            "clarification_prompt": entry.get("clarification_prompt"),
            "changes": {"reference_answer_changed": ground_truth != (case["reference_answer"] or ""),
                        "required_facts_changed": proposed_rubric != case["required_facts"],
                        "original_needs_input_carried_over": case["case_id"] in ORIGINAL_NEEDS_INPUT}}


def markdown_report(audit: dict) -> str:
    def cell(text):
        return str(text).replace("|", "\\|").replace("\n", " ")
    summary = audit["summary"]
    lines = ["# Gold V5 对齐审计表（Gate 1，待 West 终审）", "",
             f'- 输入 release：`{RELEASE_ID}`；50 题按冻结 Gold 原顺序排列。',
             f'- 冻结 Gold 任务前后 SHA256：`{GOLD_SHA256}`，均通过。',
             '- West 已在当前会话授权“进 Gate 1”。本产物是建议稿，不是冻结集或签字结果。',
             '- candidate 严格为 question / ground_truth / contexts / answer 四列；contexts=[]、answer=""。',
             '- case_id、题型、required_facts、新 Parent/Child 和证据全文见本表逐题明细及 JSON sidecar；不塞入 Ragas 四列。',
             f'- 建议标签分布：`{json.dumps(summary["labels"], ensure_ascii=False)}`。',
             f'- 证据状态分布：`{json.dumps(summary["evidence_statuses"], ensure_ascii=False)}`。',
             '- 4 道原 needs_input 保留题干和空答案；042/043/044 为新增 needs_input 建议，未签字，candidate 的 ground_truth 同样留空。',
             '', '## 方法与解释边界', '',
             '- 只折叠空白后比对原文；不以旧 Parent/Child ID 关联新 release。claim/source_id/evidence_groups 用于建立逐 fact 的人工建议映射。',
             '- 同一 Parent 内的 Parent/Child 重复表示合并为一个证据位置；不同 Parent 的多个同文候选保留 ambiguous。锚定 source_id 的唯一位置优先，跨 source 的同文副本仍列出。',
             '- matched 表示正向 fact 已定位，不等于问题 fully answerable；原来缺少问题证据锚的题也记 ambiguous，并在 JSON 中区分原因。',
             '- 词面/claim 相似候选只是检索线索，不计为 matched。表格事实同时检查表头与具体行。证据组按 claim 重建，旧 acceptable_parent_ids 不参与匹配。',
             '- 标签按完整 fact 的覆盖计算；原标签为上限，不自动升级原 insufficient。范围限制/“未定位到证据”不冒充正向证据，也不构成全语料事实不存在的证明。',
             '- source_absent 逐锚在 34 篇隔离 normalized 文本查找；命中只表示 recoverable_by_unquarantine，仍不计入当前 release。未命中标“题超纲候选”，不是终审结论。',
             '- 隔离文档没有本次 chunk/enrichment，不编造 Parent/Child；未解除隔离、重跑流水线或修改生产配置。',
             '', '## 50 题总表', '',
             '| case_id | 旧标签 | 证据状态 | 新建议标签 | 备注 |',
             '| --- | --- | --- | --- | --- |']
    for case in audit["cases"]:
        found = sum(f["status"] == "matched" for f in case["fact_alignment"] if f["kind"] == "evidence")
        total = sum(f["kind"] == "evidence" for f in case["fact_alignment"])
        recovery = '; '.join(f'{key}×{value}' for key, value in case["recovery_counts"].items())
        note = f'{found}/{total} 个正向 fact；{recovery}；' + '；'.join(case["notes"])
        lines.append(f'| [{case["case_id"]}](#case-{case["case_id"]}) | '
                     f'{case["old_expected_outcome"]} / {case["old_answerability_level"]} | '
                     f'{case["evidence_status"]} | {case["suggested_label"]} | {cell(note)} |')
    lines += ['', '## 逐题证据与修正记录', '']
    for case in audit["cases"]:
        lines += [f'<a id="case-{case["case_id"]}"></a>',
                  f'### {case["number"]}. {case["case_id"]}', '',
                  f'**问题：** {case["question"]}', '',
                  f'**建议：** {case["old_label"]} → {case["suggested_label"]}；证据状态 {case["evidence_status"]}。', '',
                  '**候选 ground_truth：**', '', case["proposed_ground_truth"] or '（空；needs_input）', '',
                  '**候选 required_facts：**', '']
        lines += [f'- {fact}' for fact in case["proposed_required_facts"]] or ['（无已完全对齐的正向 fact）']
        lines += ['', '**逐 fact 覆盖：**', '']
        for fact in case["fact_alignment"]:
            lines.append(f'- `{fact["status"]}` {fact["text"]}；依据：'
                         + (', '.join(fact["proof_anchor_ids"]) or '无已接受证据 / 范围限制'))
        lines += ['', '**证据锚与新位置：**', '']
        for anchor in case["anchors"]:
            lines.append(f'- `{anchor["anchor_id"]}` / `{anchor["status"]}` / `{anchor["source_id"]}`：{anchor["claim"]}')
            for hit in anchor["accepted_locations"]:
                lines.append(f'  - 新 Parent `{hit["parent_id"]}`；Child：'
                             + (', '.join(f'`{kid}`' for kid in hit["child_ids"]) or '原文跨 Child，仅 Parent 命中')
                             + f'；source `{hit["source_id"]}`。')
                lines.append('  - 新文本：' + cell(hit["matches"][0]["quote"]))
            if anchor["status"] == "ambiguous":
                lines.append('  - 待人工选择：' + ', '.join(f'`{h["parent_id"]}`' for h in anchor["all_exact_candidates"]))
            for hit in anchor["quarantine_matches"]:
                lines.append(f'  - `recoverable_by_unquarantine`：`{hit["source_id"]}`，`{hit["path"]}`；没有 release Parent/Child。')
            if anchor.get("recovery") == "题超纲候选":
                lines.append('  - 34 篇隔离文本也未定位到该原文：题超纲候选，West 终审。')
        if not case["anchors"]:
            lines.append('- 原题无 supporting_spans；未将空锚当作已匹配。')
        for search in case["unanchored_claim_searches"]:
            lines.append('- 无原文锚的 claim 检索线索（未采作证据）：' + cell(search["claim"]))
            for hint in search["candidates"]:
                lines.append(f'  - `{hint["source_id"]}` / `{hint["parent_id"]}`；'
                             f'词面覆盖 {hint["score"]}，需人工判断。')
        lines += ['', '**修正/终审事项：**', '']
        lines += [f'- {note}' for note in case["notes"]]
        lines += [f'- 备选：{alternative}' for alternative in case["alternatives"]]
        if case["clarification_prompt"]:
            lines.append(f'- 待澄清：{case["clarification_prompt"]}')
        if case["changes"]["reference_answer_changed"]:
            lines += ['- 原参考答案（仅供比较，未修改冻结文件）：' + (case["original_reference_answer"] or '（空）')]
        lines += ['']
    lines += ['## 验证与交付边界', '',
              f'- 已验证 Gate 0 清单中 {audit["inputs"]["gate0_artifact_count"]} 个输入产物的 SHA256；开始和结束均复核冻结 Gold。',
              f'- 运行脚本、--check、单元测试与语法检查命令见[工具说明]({Path(__file__).resolve().with_name("README.md").as_posix()})；执行结果随最终交付报告。',
              '- 逐 fact 映射及替代原文是 Codex 建议，仍需 West 人工确认；相似候选与源文档评论不等于客观因果结论。',
              '- candidate、审计及 review_plan 均保持 pending_west；未生成 frozen V5，Gate 2 另行派发。', '']
    return '\n'.join(lines)


def build_outputs(repo: Path, inventory_path: Path, plan_path: Path) -> dict[Path, bytes]:
    gold_path = repo / "ai-service/evals/datasets/rag_eval_gold_v4.frozen.jsonl"
    cases = check_gold(gold_path)
    inventory_raw = inventory_path.read_bytes()
    inventory = json.loads(inventory_raw)
    run_root = Path(inventory["run_root"])
    if inventory["release_id"] != RELEASE_ID:
        raise ValueError("STOP: wrong release in Gate 0 inventory")
    snapshots = {}
    for artifact in inventory["artifacts"]:
        path = Path(artifact["path"])
        if path != gold_path and not path.is_relative_to(run_root):
            raise ValueError("Unexpected input outside frozen Gold / run root")
        raw = path.read_bytes()
        if digest(raw) != artifact["sha256"]:
            raise ValueError("STOP: Gate 0 input changed: " + str(path))
        snapshots[path] = digest(raw)
    projection = load_jsonl(run_root / f"data/indexing/v1/{RELEASE_ID}/v4/index_projection.jsonl")
    knowledge = load_jsonl(run_root / "data/knowledge/v2/knowledge_enriched_chunks.jsonl")
    claim_rows = [c for r in knowledge for c in r["knowledge"]["claims"]]
    counts = (len(projection), len({r["parent_id"] for r in projection}),
              len({r["payload"]["source_id"] for r in projection}),
              sum(c["verification"]["status"] == "SUPPORTED" for c in claim_rows))
    if counts != (691, 413, 22, 57):
        raise ValueError("STOP: release counts changed")
    if {r["payload"]["source_release_id"] for r in projection} != {RELEASE_ID}:
        raise ValueError("STOP: mixed projection releases")
    child_ids = {r["chunk_id"] for r in knowledge if r["parent_id"] is not None}
    parent_ids = {r["chunk_id"] for r in knowledge if r["parent_id"] is None}
    if child_ids != {r["chunk_id"] for r in projection} or parent_ids != {r["parent_id"] for r in projection}:
        raise ValueError("STOP: projection/knowledge identity mismatch")
    quarantine = []
    for entry in inventory["quarantine"]["details"]:
        doc = json.loads(Path(entry["normalized_path"]).read_bytes())
        doc["path"] = entry["normalized_path"]
        quarantine.append(doc)
    plan_raw = plan_path.read_bytes()
    plan = json.loads(plan_raw)
    validate_plan(cases, plan)
    corpus = Corpus(knowledge, quarantine)
    aligned = [align_case(case, plan["cases"][case["case_id"]], corpus, i)
               for i, case in enumerate(cases, 1)]
    audit = {"schema_version": "gold-v5-alignment-v1", "release_id": RELEASE_ID,
             "gate": "Gate 1", "review_status": "pending_west",
             "gate0_authorization": "West explicitly authorized 进 Gate 1 in the current chat",
             "inputs": {"gold_sha256_before": GOLD_SHA256, "gold_sha256_after": digest(gold_path.read_bytes()),
                        "gate0_inventory_sha256": digest(inventory_raw),
                        "gate0_artifact_count": len(snapshots),
                        "review_plan_sha256": digest(plan_raw), "run_root": str(run_root),
                        "script_sha256": digest(Path(__file__).read_bytes())},
             "summary": {"cases": len(aligned),
                         "labels": dict(Counter(c["suggested_label"] for c in aligned)),
                         "evidence_statuses": dict(Counter(c["evidence_status"] for c in aligned)),
                         "anchor_statuses": dict(Counter(a["status"] for c in aligned for a in c["anchors"])),
                         "recovery": dict(Counter(a["recovery"] for c in aligned for a in c["anchors"]
                                                  if a.get("recovery"))),
                         "cases_with_quarantine_matches": sum(any(a["quarantine_matches"] for a in c["anchors"])
                                                               for c in aligned),
                         "original_needs_input_carried_over": sorted(ORIGINAL_NEEDS_INPUT)},
             "cases": aligned}
    candidate = [dict(question=c["question"], ground_truth=c["proposed_ground_truth"], contexts=[], answer="")
                 for c in aligned]
    outputs = {
        repo / "ai-service/evals/datasets/rag_eval_gold_v5.candidate.jsonl":
            ''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in candidate).encode('utf-8'),
        repo / "workspace/moodflow-review/gold-v5-alignment-audit.json": json_bytes(audit),
        repo / "workspace/moodflow-review/gold-v5-对齐审计表.md": markdown_report(audit).encode('utf-8'),
    }
    for path, checksum in snapshots.items():
        if digest(path.read_bytes()) != checksum:
            raise ValueError("STOP: input changed while aligning: " + str(path))
    if plan_path.read_bytes() != plan_raw or inventory_path.read_bytes() != inventory_raw:
        raise ValueError("STOP: plan or inventory changed while aligning")
    check_gold(gold_path)
    return outputs


def publish_outputs(outputs: dict[Path, bytes], check_only: bool = False) -> None:
    # Preflight every path before creating anything; reruns only verify identical bytes.
    for path, content in outputs.items():
        if path.exists() and path.read_bytes() != content:
            raise ValueError("Refusing to overwrite a different existing output: " + str(path))
        if check_only and not path.exists():
            raise ValueError("Missing output during --check: " + str(path))
    if not check_only:
        for path, content in outputs.items():
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as stream:
                    stream.write(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--check", action="store_true", help="recompute and compare; write nothing")
    args = parser.parse_args(argv)
    repo = args.repository_root.resolve()
    gold = repo / "ai-service/evals/datasets/rag_eval_gold_v4.frozen.jsonl"
    try:
        outputs = build_outputs(repo, repo / "workspace/moodflow-review/gold-v5-Gate0-inventory.json",
                                Path(__file__).with_name("review_plan.json"))
        publish_outputs(outputs, args.check)
        check_gold(gold)
        print(json.dumps({"result": "PASS", "mode": "check" if args.check else "create_or_verify_identical",
                          "gold_sha256_after": digest(gold.read_bytes()),
                          "outputs": {str(path): digest(data) for path, data in outputs.items()}}, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, OSError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
