"""Offline diagnosis of four fixed H1 Parents; never construct a search backend."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import unicodedata

from evals.ragas_gate0 import (
    AI_ROOT, GOLD, GOLD_SHA, GOLD_V4, PROJECTION, PROJECTION_SHA,
    RELEASE_ROOT, REPO_ROOT, STEP2, STEP2_SHA, Gate0Guard, configure_offline,
)

H1 = REPO_ROOT / "workspace/moodflow-review/h1-retrieval-25-v5-20261018/attempt-03"
OUTPUT = REPO_ROOT / "workspace/moodflow-review/h2-parent-diagnosis-v5-20261018"
PARENTS = (
    "parent-bb32b222016ed83b4eccabaa", "parent-4da6252d2b838d7851185536",
    "parent-705e51bf8082844c72dbf857", "parent-74225d7e77f81f8051ea39c7",
)
CASE_IDS = ("rag-v0-036", "rag-v0-037", "rag-v0-039", "rag-v0-040")
WHEEL_SHA = "4e2caeba4db207c6eec6d41ce2f760c32dae36c78761c3640a0e45eadc6d1ec1"
WHEEL_NAME = "qdrant_edge_py-0.8.0-cp310-abi3-win_amd64.whl"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8", newline="\n")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path):
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]


def cosine(a, b):
    if len(a) != len(b) or not a or not all(math.isfinite(x) for x in [*a, *b]):
        raise ValueError("invalid_dense_vector")
    norm = math.sqrt(math.fsum(x*x for x in a) * math.fsum(x*x for x in b))
    if not norm:
        raise ValueError("zero_dense_vector")
    return math.fsum(x*y for x, y in zip(a, b, strict=True)) / norm


def overlap(query_ids, document_ids):
    """Native sparse token dimensions, without IDF or a new retrieval ranking."""
    q, d = set(query_ids), set(document_ids)
    shared = sorted(q & d)
    return {"query_terms": len(q), "document_terms": len(d), "shared_ids": shared,
            "shared_count": len(shared), "query_coverage": len(shared)/len(q) if q else None,
            "jaccard": len(shared)/len(q | d) if q | d else None,
            "missing_ids": sorted(q-d)}


def anchor_check(anchor, parent_text, children):
    quote = anchor["exact_text"]
    containing = [c["chunk_id"] for c in children if quote in c["payload"]["content"]]
    return {"anchor_id": anchor["anchor_id"], "claim": anchor["claim"], "exact_text": quote,
            "parent_contains": quote in parent_text, "containing_children": containing,
            "gold_child_ids": anchor["child_ids"],
            "gold_child_contains": bool(set(containing) & set(anchor["child_ids"]))}


def old_stage(case, parent):
    hits = [{"leg_id": leg["leg_id"], **h} for leg in case["leg_results"]
            for h in leg["hits"] if h["parent_id"] == parent]
    stages = {k: [h for h in case[k] if h["parent_id"] == parent]
              for k in ("rrf_top40", "selection_input_rrf", "selection", "rerank_top12")}
    return {"raw_hits": hits, "stages": stages,
            "classification": "not_in_any_leg" if not hits else
            "raw_retrieved_below_rrf80" if not stages["selection_input_rrf"] else
            "rerank_top12_hit" if stages["rerank_top12"] else "downstream_drop"}


def protection():
    paths = [GOLD, GOLD_V4, PROJECTION, STEP2 / "cases.jsonl", H1 / "cases.jsonl",
             H1 / "inputs.json", H1 / "completion.json", H1 / "pre_call_manifest.json",
             RELEASE_ROOT / "data/indexing/embedding_cache/v1/embeddings.sqlite3"]
    paths += sorted((AI_ROOT / "app").rglob("*.py"))
    paths += sorted((AI_ROOT / "app").rglob("*.yaml"))
    paths += [RELEASE_ROOT / f"data/chunked/v1/{s}/chunks.json"
              for s in ("jc-v1-030", "jc-v1-034", "jc-v1-037")]
    return {str(p.relative_to(REPO_ROOT)): digest(p) for p in paths}


def native_bm25(tool_dir):
    if digest(tool_dir / WHEEL_NAME) != WHEEL_SHA:
        raise ValueError("h2_tool_wheel_hash_mismatch")
    sys.path.insert(0, str(tool_dir / "lib"))
    from qdrant_edge import Bm25, Bm25Config, DisabledStemmer, StopwordsSet, TokenizerType
    return Bm25(Bm25Config(k=1.2, b=0.75, avg_len=256, lowercase=True,
                          tokenizer=TokenizerType.Multilingual, stemmer=DisabledStemmer(),
                          stopwords=StopwordsSet()))


def token_labels(text, ids, native=None):
    """Decode hashed IDs using literal substrings; numerical overlap stays native.

    Unresolved IDs are kept, never guessed. Native normalization can produce a
    lemma absent from the literal input, so these labels are only a reading aid.
    """
    from sklearn.utils import murmurhash3_32
    needed, labels = set(ids), defaultdict(set)
    normalized = unicodedata.normalize("NFKC", text).lower()
    for start in range(len(normalized)):
        if not normalized[start].isalnum():
            continue
        for end in range(start+1, min(len(normalized), start+64)+1):
            s = normalized[start:end]
            if not s[-1].isalnum():
                break
            token_id = abs(murmurhash3_32(s, seed=0))
            if token_id in needed:
                labels[str(token_id)].add(s)
    # Charabia's Chinese variant normalization need not preserve the literal
    # Unicode spelling. Resolve remaining IDs with the native pipeline itself.
    # This is embedding a substring, never searching a corpus.
    unresolved = needed-{int(k) for k in labels}
    if native is not None and unresolved:
        for length in range(1, min(16, len(normalized))+1):
            for start in range(len(normalized)-length+1):
                s = normalized[start:start+length]
                if not all(c.isalnum() for c in s):
                    continue
                output = list(native.embed_query(s).indices)
                if len(output) == 1 and output[0] in unresolved:
                    labels[str(output[0])].add(s)
            unresolved -= {int(k) for k in labels}
            if not unresolved:
                break
    return {"labels": {k: sorted(v) for k, v in labels.items()},
            "unresolved_ids": sorted(needed-{int(k) for k in labels})}


def run(tool_dir):
    if (OUTPUT / "report.md").exists() or (OUTPUT / "completion.json").exists():
        raise ValueError("h2_refuse_overwriting_reviewable_or_sealed_result")
    configure_offline()
    # Initialize the unchanged production CUDA path before evaluator imports.
    import torch
    if torch.cuda.is_available():
        torch.cuda.init()
    guard = Gate0Guard()
    sys.addaudithook(guard.audit)
    from app.rag.indexing.dense import QwenDenseEncoder
    from app.rag.retrieval.config import load_retrieval_config
    from app.rag.retrieval.encoder import QwenQueryEncoder
    before = protection()
    for path, expected in [(GOLD, GOLD_SHA),
                           (GOLD_V4, "1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e"),
                           (PROJECTION, PROJECTION_SHA),
                           (STEP2 / "cases.jsonl", STEP2_SHA)]:
        if digest(path) != expected:
            raise ValueError("h2_frozen_input_hash_mismatch")
    completion = read(H1 / "completion.json")
    for name, expected in completion["artifact_sha256"].items():
        if digest(H1 / name) != expected:
            raise ValueError(f"h2_h1_artifact_changed:{name}")
    projection = jsonl(PROJECTION)
    by_child = {r["chunk_id"]: r for r in projection}
    by_parent = defaultdict(list)
    for row in projection:
        by_parent[row["parent_id"]].append(row)
    assert len(by_parent) == 413 and len(by_child) == 691
    gold = {r["case_id"]: r for r in jsonl(GOLD)}
    all_cases = jsonl(H1 / "cases.jsonl")
    cases = [c for c in all_cases if c["case_id"] in CASE_IDS]
    assert [c["case_id"] for c in cases] == list(CASE_IDS)
    config = load_retrieval_config()
    assert config.model_dump(mode="json") == read(H1 / "pre_call_manifest.json")["retrieval_config"]
    bm25 = native_bm25(tool_dir)
    assert not bm25.embed_query("2003").indices
    target_docs = {by_parent[p][0]["doc_id"] for p in PARENTS}
    parents = []
    for pid in PARENTS:
        rows = by_parent[pid]
        parent = rows[0]["payload"]["parent_context"]
        assert all(c["payload"]["parent_context"] == parent for c in rows)
        parents.append({"parent_id": pid, "doc_id": rows[0]["doc_id"],
                        "source_id": rows[0]["payload"]["source_id"],
                        "parent_context": parent, "first500": parent["content"][:500],
                        "children": [{k: c[k] for k in ("chunk_id", "point_id", "dense_text", "bm25_text", "input_hash")}
                                     | {"content": c["payload"]["content"]} for c in rows]})
    # Keep adjacent Parent context for a reviewable chunk-boundary diagnosis.
    neighbors = []
    for source in ("jc-v1-030", "jc-v1-034", "jc-v1-037"):
        chunked = read(RELEASE_ROOT / f"data/chunked/v1/{source}/chunks.json")
        ordered = chunked["parents"]
        for i, p in enumerate(ordered):
            if p["chunk_id"] in PARENTS:
                neighbors.append({"parent_id": p["chunk_id"], "source_id": source,
                                  "previous": ordered[max(0, i-1):i], "next": ordered[i+1:i+2]})
    assert len(neighbors) == 4 and len(target_docs) == 3
    query_texts = list(dict.fromkeys([c["question"] for c in cases] +
                                    [leg["text"] for c in cases for leg in c["leg_results"]]))
    doc_texts = list(dict.fromkeys([p["parent_context"]["content"] for p in parents] +
                                  [c["dense_text"] for p in parents for c in p["children"]]))
    encoder = QwenDenseEncoder(config.dense)
    query_encoder = QwenQueryEncoder(config.dense, encoder=encoder)
    vectors = {"queries": {}, "documents": {}}
    query_batches = []
    for case in cases:
        batch = [leg["text"] for leg in case["leg_results"] if leg["channel"] == "dense"]
        assert len(batch) == 4
        query_batches.append(batch)
    query_batches += [[c["question"]] for c in cases]
    for batch in query_batches:
        vectors["queries"].update(zip(batch, query_encoder.encode_queries(batch), strict=True))
    for i in range(0, len(doc_texts), config.dense.batch_size):
        batch = doc_texts[i:i+config.dense.batch_size]
        vectors["documents"].update(zip(batch, encoder.encode_documents(batch), strict=True))
    print(json.dumps({"offline_queries": len(vectors["queries"]),
                      "offline_documents": len(vectors["documents"])}), flush=True)
    cache_path = RELEASE_ROOT / "data/indexing/embedding_cache/v1/embeddings.sqlite3"
    with sqlite3.connect(cache_path.resolve().as_uri()+"?mode=ro&immutable=1", uri=True) as db:
        cached = {h: json.loads(v) for h, v in db.execute("SELECT input_hash, dense_vector FROM dense_embeddings_v1")}
    sparse = {}
    def terms(text):
        if text not in sparse:
            sparse[text] = list(bm25.embed_query(text).indices)
        return sparse[text]
    comparisons = []
    checks = []
    for p in parents:
        rows = by_parent[p["parent_id"]]
        for row in rows:
            fresh = vectors["documents"][row["dense_text"]]
            cached_vector = cached[row["input_hash"]]
            checks.append({"child_id": row["chunk_id"], "fresh_vs_index_cache_cosine": cosine(fresh, cached_vector),
                           "maximum_component_difference": max(abs(a-b) for a, b in zip(fresh, cached_vector, strict=True))})
        for case in cases:
            if p["parent_id"] not in case["relevant_parent_ids"]:
                continue
            anchors = [anchor_check(a, p["parent_context"]["content"], rows)
                       for a in gold[case["case_id"]]["anchors"] if a["parent_id"] == p["parent_id"]]
            assert anchors and all(a["parent_contains"] and a["gold_child_contains"] for a in anchors)
            leg_rows = []
            for leg in [{"leg_id": "original:diagnostic_only", "text": case["question"], "channel": "none", "hits": []}] + case["leg_results"]:
                q = vectors["queries"][leg["text"]]
                child_scores = [{"child_id": row["chunk_id"],
                                 "dense_cosine": cosine(q, vectors["documents"][row["dense_text"]]),
                                 "indexed_cache_dense_cosine": cosine(q, cached[row["input_hash"]]),
                                 "bm25_overlap": overlap(terms(leg["text"]), terms(row["bm25_text"]))}
                                for row in rows]
                calibration = []
                if leg["channel"] == "dense":
                    # Recompute only three already-returned controls. No corpus ranking.
                    for hit in [leg["hits"][0], leg["hits"][39], leg["hits"][-1]]:
                        row = by_child[hit["child_id"]]
                        score = cosine(q, cached[row["input_hash"]])
                        calibration.append({"child_id": hit["child_id"], "rank": hit["rank"],
                                            "recorded_score": hit["raw_score"], "offline_cache_score": score,
                                            "absolute_error": abs(score-hit["raw_score"])})
                leg_rows.append({"leg_id": leg["leg_id"], "channel": leg["channel"], "text": leg["text"],
                                 "bm25_was_executed_in_H1": leg["channel"] == "lexical",
                                 "parent_overlap": overlap(terms(leg["text"]), terms(p["parent_context"]["content"])),
                                 "query_term_labels": token_labels(leg["text"], terms(leg["text"]), bm25),
                                 "parent_dense_cosine": cosine(q, vectors["documents"][p["parent_context"]["content"]]),
                                 "children": child_scores, "existing_dense_controls": calibration,
                                 "existing_rank80_score": leg["hits"][-1]["raw_score"] if leg["hits"] else None,
                                 "existing_target_hits": [h for h in leg["hits"] if h["parent_id"] == p["parent_id"]]})
            comparisons.append({"parent_id": p["parent_id"], "case_id": case["case_id"],
                                "question": case["question"], "answerability": case["answerability"],
                                "gold_anchors": anchors, "existing_H1_stage": old_stage(case, p["parent_id"]),
                                "queries": leg_rows})
    controls = [{"case_id": c["case_id"], "question": c["question"], "parent_id": p,
                 **old_stage(c, p)} for c in all_cases if c["case_id"] not in CASE_IDS
                for p in PARENTS if any(h["parent_id"] == p for leg in c["leg_results"] for h in leg["hits"])]
    after = protection()
    assert before == after and not guard.denied
    assert len(comparisons) == 6 and len(checks) == 7
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8", newline="\n")
    write(OUTPUT / "parents.json", parents)
    write(OUTPUT / "neighbors.json", neighbors)
    write(OUTPUT / "comparisons.json", comparisons)
    write(OUTPUT / "existing_other_case_controls.json", controls)
    write(OUTPUT / "embedding_vectors.json", vectors)
    write(OUTPUT / "native_term_ids.json", {s: {"ids": ids, **token_labels(s, ids, bm25)} for s, ids in sparse.items()})
    write(OUTPUT / "verification.json", {
        "new_qdrant_requests": 0, "new_retrieval_calls": 0, "new_deepseek_calls": 0, "judge_calls": 0,
        "secret_reads": 0, "network_guard_denied": guard.denied, "protected_unchanged": before == after,
        "protected_sha256": before, "target_parent_count": 4, "parent_case_pairs": 6,
        "actual_leg_rows": 42, "original_query_rows": 6, "unique_query_embeddings": len(query_texts),
        "unique_document_embeddings": len(doc_texts), "index_cache_checks": checks,
        "query_batch_policy": "Four actual dense variant texts per case, as in HybridRetrievalService; original query encoded alone for diagnosis only.",
        "query_batches": query_batches, "resolved_device": encoder._device,
        "document_token_lengths": {s: len(encoder._tokenizer(s)["input_ids"]) for s in doc_texts},
        "runtime": {"torch": torch.__version__, "cuda": torch.cuda.is_available(),
                    "model_dtype": str(next(encoder._model.parameters()).dtype)},
        "index_cache_read_only": True, "dense_config": config.dense.model_dump(mode="json"),
        "bm25_options": config.bm25.query_options(), "bm25_tool": {
            "package": "qdrant-edge-py==0.8.0", "wheel": WHEEL_NAME, "sha256": WHEEL_SHA,
            "isolated_temporary_directory": str(tool_dir), "production_environment_changed": False,
            "only_used_embed_query": True, "no_shard_or_search_created": True},
        "H1_runtime_audit_still_pending": completion["runtime_audit_accepted_by_West"] is False,
        "source_code_sha256": digest(Path(__file__)),
        "scope": "fixed paired similarities/token overlap only; no corpus ranking or retrieval rerun",
    })
    print(json.dumps({"done": True, "pairs": len(comparisons), "qdrant_calls": 0}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-dir", type=Path, default=Path(os.environ["TEMP"])/"MoodFlow-h2-qdrant-edge-0.8.0")
    run(parser.parse_args().tool_dir)
