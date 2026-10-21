"""Apply West's recorded Gate 2 decisions and create immutable Gold V5 artifacts."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import stat
import sys


V4_SHA256 = "1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e"
RELEASE_ID = "knowledge-v2-e40ab37ffcc7bcf0"
DISTRIBUTION = {"full": 18, "partial": 12, "insufficient": 13, "needs_input": 7}
ORIGINAL_NEEDS = {"rag-v0-046", "rag-v0-047", "rag-v0-048", "rag-v0-068"}
CLARIFIED_NEEDS = {"rag-v0-042", "rag-v0-043", "rag-v0-044"}
REWRITES = {"rag-v0-001", "rag-v0-024"}
RAGAS_KEYS = ("question", "ground_truth", "contexts", "answer")
SIDECAR = "workspace/moodflow-review/gold-v5-alignment-audit.json"
FROZEN = "ai-service/evals/datasets/rag_eval_gold_v5.frozen.jsonl"
RAGAS = "ai-service/evals/datasets/rag_eval_gold_v5.ragas.jsonl"
MANIFEST = "workspace/moodflow-review/gold-v5-freeze-manifest.json"
REPORT = "workspace/moodflow-review/gold-v5-冻结报告.md"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def jsonl_bytes(rows: list[dict]) -> bytes:
    return ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows).encode('utf-8')


def read_jsonl(raw: bytes) -> list[dict]:
    return [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]


def check_v4(path: Path) -> list[dict]:
    raw = path.read_bytes()
    if sha(raw) != V4_SHA256:
        raise ValueError("STOP: Gold V4 SHA256 mismatch")
    rows = read_jsonl(raw)
    if len(rows) != 50 or len(raw.splitlines()) != 50 or len({r["case_id"] for r in rows}) != 50:
        raise ValueError("STOP: invalid Gold V4 record / case_id count")
    return rows


def unsigned_audit(current: dict, signoff: dict) -> dict:
    """Restore only the explicitly allowed Gate 2 edits to verify Gate 1's digest."""
    original = deepcopy(current)
    if "gate2_freeze" in original:
        if {case["case_id"]: case["suggested_label"] for case in original["cases"]} != signoff["approved_labels"]:
            raise ValueError("STOP: signed sidecar labels changed after freeze")
        original.pop("gate2_freeze")
        original["review_status"] = "pending_west"
        original["summary"]["labels"] = signoff["gate1_distribution"]
        for case in original["cases"]:
            case["review_status"] = "pending_west"
            case.pop("gate2_resolution", None)
            case["suggested_label"] = signoff["gate1_labels"][case["case_id"]]
    if sha(json_bytes(original)) != signoff["source_sidecar_sha256"]:
        raise ValueError("STOP: sidecar differs from the reviewed Gate 1 snapshot")
    return original


def approve_audit(current: dict, signoff: dict) -> dict:
    source = unsigned_audit(current, signoff)
    if signoff["frozen_by"] != "West" or signoff["release_id"] != RELEASE_ID:
        raise ValueError("STOP: wrong signer / release")
    timestamp = datetime.fromisoformat(signoff["frozen_at"])
    if timestamp.tzinfo is None:
        raise ValueError("STOP: frozen_at must include a timezone")
    if len(signoff["signed_items"]) != 7:
        raise ValueError("STOP: seven signoff items are required")
    labels = {case["case_id"]: case["suggested_label"] for case in source["cases"]}
    if len(labels) != 50 or labels != signoff["gate1_labels"]:
        raise ValueError("STOP: Gate 1 labels changed")
    final_labels = {**labels, "rag-v0-009": "partial"}
    if final_labels != signoff["approved_labels"]:
        raise ValueError("STOP: unapproved label change")
    if dict(Counter(final_labels.values())) != DISTRIBUTION or signoff["final_distribution"] != DISTRIBUTION:
        raise ValueError("STOP: final distribution must be 18 / 12 / 13 / 7")
    if set(signoff["approved_rewrites"]) != REWRITES:
        raise ValueError("STOP: rewrite signoff must cover exactly #1 and #11")
    approved = deepcopy(source)
    approved["review_status"] = "approved_by_west"
    approved["summary"]["labels"] = DISTRIBUTION.copy()
    for case in approved["cases"]:
        key = case["case_id"]
        case["suggested_label"] = final_labels[key]
        case["review_status"] = "approved_by_west"
        if key in REWRITES:
            choice = signoff["approved_rewrites"][key]
            if (case["proposed_ground_truth"] != choice["ground_truth"]
                    or case["proposed_required_facts"] != choice["required_facts"]):
                raise ValueError("STOP: signed rewrite differs from sidecar: " + key)
        if key in CLARIFIED_NEEDS:
            if case["suggested_label"] != "needs_input" or case["proposed_ground_truth"] != "":
                raise ValueError("STOP: clarification case must keep empty ground_truth: " + key)
        if key in REWRITES | CLARIFIED_NEEDS | {"rag-v0-009"}:
            case["gate2_resolution"] = {
                "approved_by": "West", "answerability": final_labels[key],
                "ground_truth_approved": True, "required_facts_approved": True,
                "note": "Gate 2 决定优先；原 notes / alternatives 保留为 Gate 1 历史。",
            }
    needs = {c["case_id"] for c in approved["cases"] if c["suggested_label"] == "needs_input"}
    if needs != ORIGINAL_NEEDS | CLARIFIED_NEEDS:
        raise ValueError("STOP: wrong needs_input cases")
    return approved


def freeze_anchors(case: dict, by_id: dict[str, dict], parents: set[str], children: set[str]) -> list[dict]:
    if case["suggested_label"] == "needs_input":
        return []
    output = []
    for anchor in case["anchors"]:
        if anchor["status"] != "matched":
            continue
        if not anchor["accepted_locations"]:
            raise ValueError("STOP: matched anchor has no accepted location")
        for location in anchor["accepted_locations"]:
            parent = location["parent_id"]
            if parent not in parents or not location["matches"]:
                raise ValueError("STOP: accepted parent is not in the new release")
            for child in location["child_ids"]:
                if child not in children or by_id[child]["parent_id"] != parent:
                    raise ValueError("STOP: accepted Child / Parent mismatch")
            quotes = []
            for match in location["matches"]:
                chunk = match["chunk_id"]
                if chunk != parent and chunk not in location["child_ids"]:
                    raise ValueError("STOP: quote chunk is outside accepted location")
                row = by_id[chunk]
                if row["metadata"]["source_id"] != location["source_id"]:
                    raise ValueError("STOP: evidence source mismatch")
                if match["field"] != "content":
                    raise ValueError("STOP: only raw content quotes may be frozen")
                start, end = match["raw_start"], match["raw_end"]
                quote = match["quote"]
                if not quote or not (0 <= start < end <= len(row["content"])):
                    raise ValueError("STOP: invalid quote offsets")
                if row["content"][start:end] != quote:
                    raise ValueError("STOP: exact quote does not match new chunk text")
                if ' '.join(quote.split()) != ' '.join(anchor["exact_text"].split()):
                    raise ValueError("STOP: anchor / accepted quote mismatch")
                quotes.append({"chunk_id": chunk, "field": "content", "exact_text": quote,
                               "start": start, "end": end})
            output.append({"anchor_id": anchor["anchor_id"], "status": "matched",
                           "claim": anchor["claim"], "source_id": location["source_id"],
                           "parent_id": parent, "child_ids": sorted(set(location["child_ids"])),
                           "exact_text": quotes[0]["exact_text"], "quotes": quotes})
    return output


def changes_for(case: dict, anchors: list[dict]) -> list[dict]:
    key = case["case_id"]
    changes = []
    if key in ORIGINAL_NEEDS:
        return [{"type": "needs_input_carried_over",
                 "reason": "原题干、空答案和空 required_facts 保留；不带证据 ID。"}]
    if key in REWRITES:
        changes.append({"type": "answer_and_required_facts_rewritten",
                        "reason": "West 采用终审版 ground_truth / required_facts。",
                        "details": case["notes"], "previous_ground_truth": case["original_reference_answer"],
                        "previous_required_facts": case["original_required_facts"]})
    if key == "rag-v0-009":
        changes.append({"type": "evidence_realigned_and_label_upgraded",
                        "from": case["old_label"], "to": "partial",
                        "reason": "West 批准局部影响证据；替换与问题错位的快歌灵感/画面感要求。",
                        "details": case["notes"]})
    elif key in CLARIFIED_NEEDS:
        changes.append({"type": "downgraded_to_needs_input", "from": case["old_label"],
                        "to": "needs_input", "reason": "问题范围含糊，West 签字采用 needs_input；ground_truth 为空。",
                        "clarification_prompt": case["clarification_prompt"]})
    elif case["old_label"] != case["suggested_label"]:
        missing = [{"fact": f["text"], "status": f["status"]}
                   for f in case["fact_alignment"] if f["kind"] == "evidence" and f["status"] != "matched"]
        changes.append({"type": "answerability_downgraded", "from": case["old_label"],
                        "to": case["suggested_label"], "reason": "新 release 正向事实覆盖不足；隔离恢复线索不计作当前证据。",
                        "unresolved_facts": missing, "recovery_counts": case["recovery_counts"]})
    if key not in REWRITES | {"rag-v0-009"} and case["changes"]["reference_answer_changed"]:
        changes.append({"type": "reference_answer_revised", "reason": "答案与新 release 已接受的证据及签字后的标签一致。"})
    if key not in REWRITES | {"rag-v0-009"} and case["changes"]["required_facts_changed"]:
        changes.append({"type": "required_facts_narrowed", "reason": "仅保留已完全对齐的正向事实和明确的范围限制。"})
    if anchors:
        changes.append({"type": "evidence_locations_rebound", "release_id": RELEASE_ID,
                        "reason": "仅使用 matched 锚的 accepted_locations；没有按旧 chunk ID 关联。"})
    elif case["suggested_label"] != "needs_input":
        changes.append({"type": "evidence_unresolved", "reason": "没有 matched 锚，relevant_parent_ids 与 anchors 均为空。"})
    return changes


def validate_rows(rows: list[dict], ragas: list[dict], approved: dict) -> None:
    if len(rows) != 50 or len(ragas) != 50 or len({r["case_id"] for r in rows}) != 50:
        raise ValueError("STOP: frozen / Ragas must have 50 rows and 50 unique case_id")
    if dict(Counter(row["answerability"] for row in rows)) != DISTRIBUTION:
        raise ValueError("STOP: frozen distribution mismatch")
    for frozen, view, case in zip(rows, ragas, approved["cases"]):
        if frozen["case_id"] != case["case_id"]:
            raise ValueError("STOP: row / case_id mapping mismatch")
        if set(view) != set(RAGAS_KEYS) or view != {key: frozen[key] for key in RAGAS_KEYS}:
            raise ValueError("STOP: strict Ragas view differs from frozen row")
        if frozen["ground_truth"] != case["proposed_ground_truth"] or frozen["contexts"] != [] or frozen["answer"] != "":
            raise ValueError("STOP: ground_truth / empty inference columns mismatch")
        expected_parents = sorted({a["parent_id"] for a in frozen["anchors"]})
        if frozen["relevant_parent_ids"] != expected_parents:
            raise ValueError("STOP: relevant_parent_ids do not match accepted anchors")
        if frozen["answerability"] == "needs_input" and (frozen["ground_truth"] or frozen["anchors"] or frozen["relevant_parent_ids"]):
            raise ValueError("STOP: needs_input must have empty answer and evidence")
        if frozen["required_facts"] != case["proposed_required_facts"]:
            raise ValueError("STOP: required_facts differ from signed sidecar")


def report_text(manifest: dict, signoff: dict) -> str:
    lines = ["# Gold V5 冻结报告（Gate 2）", "",
             f'- frozen_by：**West**；frozen_at：`{signoff["frozen_at"]}`。',
             f'- release_id：`{RELEASE_ID}`；schema_version：`v5`。',
             '- 签字依据：本次用户指令明确声明 West 已签字；执行下列 7 项，不另行生成或冒充数字签名。',
             '- 状态：冻结产物已生成并完成一致性校验，等待 West 确认冻结完成。', '',
             '## 签字项复核', '', '| 项目 | 已执行决定 | 结果 |', '| --- | --- | --- |']
    for i, item in enumerate(signoff["signed_items"], 1):
        lines.append(f'| {i} | {item} | 通过 |')
    lines += ['', '## 最终数据与格式', '',
              '| full | partial | insufficient | needs_input | 总数 | 唯一 case_id |',
              '| ---: | ---: | ---: | ---: | ---: | ---: |', '| 18 | 12 | 13 | 7 | 50 | 50 |', '',
              '- 只有 #9 的 suggested_label 相比 Gate 1 改为 partial；其他标签保持不变。',
              '- #1/#11 的 ground_truth 与 required_facts 逐字采用签字记录，未再次编辑答案。',
              '- 042/043/044 及原 046/047/048/068 共7题为 needs_input，ground_truth=""、relevant_parent_ids=[]、anchors=[]。',
              '- 每行以 release_id/schema_version/frozen_by/frozen_at 元数据开头，紧随 case_id 和扩展字段；没有额外元数据行，仍为50行。',
              '- anchors 仅收录 matched 锚的 accepted_locations，附新 Parent/Child、精确原文及原文偏移；source_absent/ambiguous 不产生虚构证据。',
              '- source_absent/ambiguous 题若仍有其他 matched 锚，保留这些锚；没有 matched 锚则证据数组为空。',
              '- Ragas 文件严格四列，不含 case_id。通过逐行投影和下表的 line→case_id 映射确认对应，question/ground_truth/contexts/answer 逐行一致。', '',
              '## 路径与 SHA256', '', '| 文件 | 字节数 | SHA256 |', '| --- | ---: | --- |']
    for key, artifact in manifest["artifacts"].items():
        lines.append(f'| `{artifact["path"]}` | {artifact["bytes"]} | `{artifact["sha256"]}` |')
    lines += ['', f'- Gold V4 任务前 SHA256：`{V4_SHA256}`，通过。',
              f'- Gold V4 冻结前再次复核 SHA256：`{V4_SHA256}`，通过；最终交付再复核。',
              f'- Gate 1 原 sidecar SHA256：`{signoff["source_sidecar_sha256"]}`。',
              '- sidecar 已记录 West 签字后的标签、分布、审阅状态及 Gate 2 冻结指纹；notes/alternatives 是保留的 Gate 1 历史，最新决定以 gate2_resolution 和冻结字段为准。', '',
              '## Ragas 逐行 case_id 映射', '', '| 行号 | case_id | answerability | Parent数 | anchor数 |',
              '| ---: | --- | --- | ---: | ---: |']
    for row in manifest["row_map"]:
        lines.append(f'| {row["line"]} | `{row["case_id"]}` | {row["answerability"]} | {row["parent_count"]} | {row["anchor_count"]} |')
    lines += ['', '## 只读规则与验证', '',
              '- v5.frozen.jsonl 创建后设置本机只读属性；工具没有 force/overwrite 入口。同版本任何内容修改都必须走新版本。',
              '- datasets/.gitattributes 仅为这两个新文件固定 LF，避免 Windows checkout 改变冻结文件字节及 SHA256；没有改变 Gold V4 的 Git 属性。',
              '- --check 只读重算全部产物，检查文件哈希、签字项、50行/唯一ID、分布、逐行四列投影、新证据位置及冻结文件只读属性。',
              '- 新工具的测试及复现命令见 `ai-service/tools/gold_release_freeze_v5/README.md`；不修改 Gate 1 生成器。Gate 1 待审稿 --check 不用于验证已签字 sidecar。',
              '- 未进行模型推理或 Ragas 评分；contexts 和 answer 按要求空置。没有前后端或服务配置改动，不启动应用或构建。',
              '- 34篇隔离文档保持原状态，恢复由独立 pipeline track 处理，不阻塞本次冻结；未 push，未运行恢复流水线。', '']
    return '\n'.join(lines)


def build_bundle(repo: Path, signoff_path: Path) -> tuple[dict[Path, bytes], dict]:
    gold_path = repo / "ai-service/evals/datasets/rag_eval_gold_v4.frozen.jsonl"
    gold = check_v4(gold_path)
    signoff_raw = signoff_path.read_bytes()
    signoff = json.loads(signoff_raw)
    sidecar_path = repo / SIDECAR
    sidecar_before = sidecar_path.read_bytes()
    approved = approve_audit(json.loads(sidecar_before), signoff)
    candidate_path = repo / "ai-service/evals/datasets/rag_eval_gold_v5.candidate.jsonl"
    candidate_raw = candidate_path.read_bytes()
    if sha(candidate_raw) != signoff["candidate_sha256"]:
        raise ValueError("STOP: candidate changed after review")
    candidate = read_jsonl(candidate_raw)
    if len(candidate) != 50:
        raise ValueError("STOP: candidate count mismatch")
    inventory_path = repo / "workspace/moodflow-review/gold-v5-Gate0-inventory.json"
    inventory_raw = inventory_path.read_bytes()
    if sha(inventory_raw) != approved["inputs"]["gate0_inventory_sha256"]:
        raise ValueError("STOP: Gate 0 inventory changed")
    inventory = json.loads(inventory_raw)
    snapshots = {gold_path: V4_SHA256, signoff_path: sha(signoff_raw),
                 candidate_path: sha(candidate_raw), inventory_path: sha(inventory_raw)}
    for artifact in inventory["artifacts"]:
        path = Path(artifact["path"])
        if sha(path.read_bytes()) != artifact["sha256"]:
            raise ValueError("STOP: release input changed: " + str(path))
        snapshots[path] = artifact["sha256"]
    knowledge_path = Path(inventory["run_root"]) / "data/knowledge/v2/knowledge_enriched_chunks.jsonl"
    knowledge = read_jsonl(knowledge_path.read_bytes())
    by_id = {row["chunk_id"]: row for row in knowledge}
    parents = {row["chunk_id"] for row in knowledge if row["parent_id"] is None}
    children = set(by_id) - parents
    if len(parents) != 413 or len(children) != 691:
        raise ValueError("STOP: wrong release Parent / Child population")
    frozen, ragas = [], []
    if len(approved["cases"]) != len(gold):
        raise ValueError("STOP: sidecar case count mismatch")
    for original, draft, case in zip(gold, candidate, approved["cases"]):
        if case["case_id"] != original["case_id"] or case["question"] != original["query"]:
            raise ValueError("STOP: sidecar case / query order mismatch")
        if set(draft) != set(RAGAS_KEYS) or draft != {
            "question": case["question"], "ground_truth": case["proposed_ground_truth"], "contexts": [], "answer": ""
        }:
            raise ValueError("STOP: candidate / sidecar answer mismatch")
        anchors = freeze_anchors(case, by_id, parents, children)
        record = {"release_id": RELEASE_ID, "schema_version": "v5", "frozen_by": "West",
                  "frozen_at": signoff["frozen_at"], "case_id": case["case_id"],
                  **{key: draft[key] for key in RAGAS_KEYS}, "answerability": case["suggested_label"],
                  "required_facts": case["proposed_required_facts"],
                  "relevant_parent_ids": sorted({a["parent_id"] for a in anchors}), "anchors": anchors,
                  "old_label": case["old_label"], "changes": changes_for(case, anchors)}
        frozen.append(record)
        ragas.append({key: record[key] for key in RAGAS_KEYS})
    validate_rows(frozen, ragas, approved)
    frozen_raw, ragas_raw = jsonl_bytes(frozen), jsonl_bytes(ragas)
    approved["gate2_freeze"] = {
        "frozen_by": "West", "frozen_at": signoff["frozen_at"], "schema_version": "v5",
        "signoff_sha256": sha(signoff_raw), "source_sidecar_sha256": signoff["source_sidecar_sha256"],
        "frozen_sha256": sha(frozen_raw), "ragas_sha256": sha(ragas_raw),
        "signed_items": signoff["signed_items"], "historical_gate1_notes_retained": True,
    }
    sidecar_raw = json_bytes(approved)
    manifest = {"schema_version": "gold-v5-freeze-manifest-v1", "release_id": RELEASE_ID,
                "frozen_by": "West", "frozen_at": signoff["frozen_at"],
                "status": "frozen_awaiting_west_completion_confirmation", "rows": 50,
                "unique_case_ids": 50, "distribution": DISTRIBUTION,
                "gold_v4_sha256_before": V4_SHA256, "gold_v4_sha256_after": V4_SHA256,
                "signoff": {"path": signoff_path.as_posix(), "sha256": sha(signoff_raw),
                            "signed_items": signoff["signed_items"]},
                "artifacts": {}, "row_map": []}
    for relative, raw in [(FROZEN, frozen_raw), (RAGAS, ragas_raw), (SIDECAR, sidecar_raw)]:
        manifest["artifacts"][relative] = {"path": (repo / relative).as_posix(), "bytes": len(raw), "sha256": sha(raw)}
    for number, (record, view) in enumerate(zip(frozen, ragas), 1):
        manifest["row_map"].append({"line": number, "case_id": record["case_id"],
                                    "answerability": record["answerability"],
                                    "parent_count": len(record["relevant_parent_ids"]),
                                    "anchor_count": len(record["anchors"]),
                                    "question_sha256": sha(view["question"].encode('utf-8')),
                                    "ground_truth_sha256": sha(view["ground_truth"].encode('utf-8'))})
    outputs = {repo / FROZEN: frozen_raw, repo / RAGAS: ragas_raw,
               sidecar_path: sidecar_raw, repo / MANIFEST: json_bytes(manifest),
               repo / REPORT: report_text(manifest, signoff).encode('utf-8')}
    for path, checksum in snapshots.items():
        if sha(path.read_bytes()) != checksum:
            raise ValueError("STOP: input changed while preparing freeze: " + str(path))
    if sidecar_path.read_bytes() != sidecar_before:
        raise ValueError("STOP: sidecar changed while preparing freeze")
    check_v4(gold_path)
    return outputs, {"sidecar_before_sha256": sha(sidecar_before), "frozen_path": repo / FROZEN}


def readonly(path: Path) -> bool:
    return not bool(path.stat().st_mode & stat.S_IWUSR)


def publish_bundle(outputs: dict[Path, bytes], control: dict, check_only: bool = False) -> None:
    frozen_path = control["frozen_path"]
    sidecar_path = next(path for path in outputs if path.name == "gold-v5-alignment-audit.json")
    for path, content in outputs.items():
        if path == sidecar_path and sha(path.read_bytes()) != control["sidecar_before_sha256"]:
            raise ValueError("STOP: sidecar changed before publication")
        if check_only and (not path.exists() or path.read_bytes() != content):
            raise ValueError("STOP: --check output missing or changed: " + str(path))
        if path.exists() and path.read_bytes() != content:
            if path != sidecar_path or sha(path.read_bytes()) != control["sidecar_before_sha256"]:
                raise ValueError("STOP: immutable output already exists with different bytes; create a new version: " + str(path))
    if check_only:
        if not readonly(frozen_path):
            raise ValueError("STOP: frozen file lost its read-only attribute")
        return
    for path, content in outputs.items():
        if path == sidecar_path:
            continue
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(content)
    if sidecar_path.read_bytes() != outputs[sidecar_path]:
        if sha(sidecar_path.read_bytes()) != control["sidecar_before_sha256"]:
            raise ValueError("STOP: sidecar changed during publication")
        sidecar_path.write_bytes(outputs[sidecar_path])
    frozen_path.chmod(stat.S_IREAD)
    if not readonly(frozen_path):
        raise ValueError("STOP: unable to set frozen file read-only")
    for path, content in outputs.items():
        if path.read_bytes() != content:
            raise ValueError("STOP: output failed post-write verification: " + str(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-root', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--check', action='store_true', help='verify only; never write files or attributes')
    args = parser.parse_args(argv)
    repo = args.repository_root.resolve()
    try:
        outputs, control = build_bundle(repo, Path(__file__).with_name('west_signoff.json'))
        publish_bundle(outputs, control, args.check)
        check_v4(repo / 'ai-service/evals/datasets/rag_eval_gold_v4.frozen.jsonl')
        print(json.dumps({'result': 'PASS', 'mode': 'check' if args.check else 'freeze_or_verify_identical',
                          'distribution': DISTRIBUTION, 'frozen_readonly': readonly(control['frozen_path']),
                          'sha256': {str(path): sha(content) for path, content in outputs.items()}}, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
