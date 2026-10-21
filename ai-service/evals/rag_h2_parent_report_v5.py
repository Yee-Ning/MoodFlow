"""Verify and report saved H2 pairs without model loading or retrieval."""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import sys

from evals import rag_h2_parent_diagnosis_v5 as diag
from evals.ragas_gate0 import Gate0Guard, configure_offline


def fused_positions(case):
    """Accounting over the seven OLD hit lists only, k unchanged at 60."""
    scores, hits = defaultdict(float), {}
    for leg in case["leg_results"]:
        seen = set()
        for h in leg["hits"]:
            if h["point_id"] in seen:
                continue
            seen.add(h["point_id"])
            scores[h["point_id"]] += leg["weight"]/(60+h["rank"])
            hits[h["point_id"]] = h
    return [{"fusion_rank": i, "fusion_score": scores[point], **hits[point]}
            for i, point in enumerate(sorted(scores, key=lambda p: (-scores[p], p)), 1)]


def verify_saved():
    out = diag.OUTPUT
    state, comparisons = diag.read(out / "verification.json"), diag.read(out / "comparisons.json")
    vectors, parents = diag.read(out / "embedding_vectors.json"), diag.read(out / "parents.json")
    terms = diag.read(out / "native_term_ids.json")
    source_cases = {c["case_id"]: c for c in diag.jsonl(diag.H1 / "cases.jsonl")}
    assert state["source_code_sha256"] == diag.digest(Path(diag.__file__))
    assert state["protected_sha256"] == diag.protection() and state["protected_unchanged"]
    assert all(state[k] == 0 for k in ("new_qdrant_requests", "new_retrieval_calls", "new_deepseek_calls", "judge_calls", "secret_reads"))
    assert len(comparisons) == 6 and len(parents) == 4
    parent_map = {p["parent_id"]: p for p in parents}
    calibration = {}
    child_rows = []
    for pair in comparisons:
        source = source_cases[pair["case_id"]]
        parent = parent_map[pair["parent_id"]]
        expected = [("original:diagnostic_only", source["question"])] + [(q["leg_id"], q["text"]) for q in source["leg_results"]]
        assert [(q["leg_id"], q["text"]) for q in pair["queries"]] == expected
        assert pair["existing_H1_stage"] == diag.old_stage(source, pair["parent_id"])
        assert pair["gold_anchors"] and all(a["parent_contains"] and a["gold_child_contains"] for a in pair["gold_anchors"])
        children = {c["chunk_id"]: c for c in parent["children"]}
        for q in pair["queries"]:
            text = q["text"]
            assert q["parent_overlap"] == diag.overlap(terms[text]["ids"], terms[parent["parent_context"]["content"]]["ids"])
            assert math.isclose(q["parent_dense_cosine"], diag.cosine(vectors["queries"][text], vectors["documents"][parent["parent_context"]["content"]]), abs_tol=1e-12)
            for c in q["children"]:
                child = children[c["child_id"]]
                assert c["bm25_overlap"] == diag.overlap(terms[text]["ids"], terms[child["bm25_text"]]["ids"])
                assert math.isclose(c["dense_cosine"], diag.cosine(vectors["queries"][text], vectors["documents"][child["dense_text"]]), abs_tol=1e-12)
                child_rows.append({"case_id": pair["case_id"], "parent_id": pair["parent_id"],
                                   "leg_id": q["leg_id"], "query": text, **c})
            for c in q["existing_dense_controls"]:
                calibration[(pair["case_id"], q["leg_id"], c["rank"])] = c
    original_rows = sum(q["channel"] == "none" for p in comparisons for q in p["queries"])
    assert original_rows == 6 and sum(len(p["queries"])-1 for p in comparisons) == 42 and len(child_rows) == 72
    for source in source_cases.values():
        if source["case_id"] not in diag.CASE_IDS:
            continue
        fused = fused_positions(source)
        assert [h["child_id"] for h in fused[:80]] == [h["child_id"] for h in source["selection_input_rrf"]]
        assert all(math.isclose(a["fusion_score"], b["fusion_score"], abs_tol=1e-12)
                   for a, b in zip(fused[:80], source["selection_input_rrf"], strict=True))
    fused = fused_positions(source_cases["rag-v0-039"])
    hit = next(h for h in fused if h["parent_id"] == diag.PARENTS[3])
    assert hit["fusion_rank"] == 160 and hit["rank"] == 35
    max_error = max(c["absolute_error"] for c in calibration.values())
    assert max_error < 1e-6 and len(calibration) == 48
    assert max(state["document_token_lengths"].values()) < state["dense_config"]["max_length"]
    margins = [q["existing_rank80_score"]-max(c["indexed_cache_dense_cosine"] for c in q["children"])
               for p in comparisons for q in p["queries"] if q["channel"] == "dense"]
    assert min(margins) > 0 and min(margins) > max_error
    return {"verified": True, "actual_leg_rows": 42, "per_child_rows_including_original": 72,
            "unique_existing_dense_calibration_scores": len(calibration),
            "maximum_dense_calibration_absolute_error": max_error,
            "minimum_target_gap_below_existing_dense_rank80": min(margins),
            "all_gold_anchors_preserved_in_expected_parent_and_child": True,
            "rrf_039": {"target": hit, "old_rrf40_cutoff": fused[39]["fusion_score"],
                        "old_rrf80_cutoff": fused[79]["fusion_score"], "old_union_children": len(fused)},
            "all_document_inputs_within_8192_tokens": True, "verification_network_calls": 0}, child_rows


def labels(q, ids):
    return "、".join("/".join(q["query_term_labels"]["labels"].get(str(i), [f"#{i}"])) for i in ids) or "∅"


def render(verification, probes):
    out = diag.OUTPUT
    pairs, parents = diag.read(out / "comparisons.json"), diag.read(out / "parents.json")
    state = diag.read(out / "verification.json")
    cases = {c["case_id"]: c for c in diag.jsonl(diag.H1 / "cases.jsonl")}
    lines = ["# H2：四个 timeline Parent 的只读诊断", "",
        "结论：5 个 Parent×问题组合停在原始召回层；74225×039 已被一条 BM25 leg 召回，停在 RRF 截取前。bb32b222 的三题全 miss 同时有词项失配与语义距离证据；没有发现 Gold 锚点缺失或锚点被切到别的 Parent。", "",
        "本轮仅复用 H1 已保存结果并计算指定文本对。新增检索、Qdrant 请求、DeepSeek、judge、secret 读取均为 **0**；生产参数、配置、代码及冻结文件未改。原 H1 的 advisory probe 审计待审标记仍保留，不在 H2 代为接受。", "",
        "## 诊断归类", "",
        "| Parent | 对应题 | 已证实停点 | 分类及依据 |", "|---|---|---|---|",
        "| bb32b222 | 036 / 037 / 040 | 各 7 条 leg 均无该 Child | **a 主、b 并存**。简体查询与繁体正文的分词不齐；年份数字被过滤。正文是单条发行事实，缺少风格演变/生涯总结语汇；12 个 dense leg 均低于旧第80名。 |",
        "| 4da6252d | 036 | 7 条 leg 均无该 Child | **b 主、a 次**。有周杰伦、音乐等公共词，核心却是《东风破》、二胡、琵琶；子查询没有这些辨识词。HyDE 与正文重叠16词，dense仍低于旧第80名。 |",
        "| 705e51bf | 040 | 7 条 leg 均无该 Parent 的两个 Child | **b 主、a 次**。实际是电影事业分段，查询偏音乐生涯/专辑；Gold 的2007年电影及配乐句确实存在于第二个 Child。 |",
        "| 74225d7e | 039 | BM25单路第35名 → RRF第160名 → 未进80 | **a+b（上游），融合淘汰（直接停点）**。三子查询偏音乐/专辑/个人发展，Gold锚是餐饮投资；命中的是不含两条Gold锚的另一个Child。 |", "",
        "**c（内容缺失）与 d（关键信息切到别的 Parent）均不成立于本轮指定锚点。** 036/039 本来就是 partial：没有整体风格/变化趋势的证据不等于其有限事实锚标错。Gold 未改。", "",
        "## 计算口径与可复核性", "",
        "- 词项来自 Qdrant 同源 Rust multilingual 管线：本地临时解包 `qdrant-edge-py==0.8.0`，只调用 `Bm25.embed_query` 获取唯一 token ID，未创建 shard、未运行 search。生产环境和依赖清单未改。词项覆盖率 = 交集唯一ID数 / 查询唯一ID数；另存 Jaccard、缺失词ID及可读标签。它不是BM25得分，不含IDF，不能单凭覆盖率推出BM25排名。",
        "- 同时计算 **Parent正文** 与实际 **Child bm25_text（含breadcrumb与检索词）** 的重叠。dense复用生产 `QwenQueryEncoder` / `QwenDenseEncoder`，模型与revision、instruction、last-token pooling、归一化均沿用线上；实际模型权重为BF16，归一化及输出为FP32，未强制切换dtype。4条实际dense变体逐题同批编码；原问题单独编码只作诊断，没有新增原问题leg。",
        "- 下表 `P余弦` = 生产模型离线编码的查询与Parent正文；`C余弦` = 与实际Child dense_text新编码的最大值；`索引C` = 同一离线查询与只读本地建库缓存中原索引Child向量的最大值。Parent正文没有被送进线上检索。多Child的逐个分数、原文和ID见JSON，不能把最大分数Child等同于Gold锚Child。",
        f"- 校准：48个旧dense结果（每条实际dense leg的第1/40/80名）重算分数，最大绝对误差 **{verification['maximum_dense_calibration_absolute_error']:.2e}**。所有指定Parent的索引Child分数都低于旧dense第80名，最小差 **{verification['minimum_target_gap_below_existing_dense_rank80']:.6f}**。不支持把这些miss直接归因于HNSW漏搜；未重排全库、未测全库精确rank。",
        f"- 新编码Child与建库缓存余弦为 {min(x['fresh_vs_index_cache_cosine'] for x in state['index_cache_checks']):.6f}–{max(x['fresh_vs_index_cache_cosine'] for x in state['index_cache_checks']):.6f}，存在批次/执行数值差异；历史截点比较以原索引缓存为准。所有11个文档输入均低于8192 tokens，无截断。",
        "- 4个Parent、4个原问题、6个Parent×问题组合、42行实际leg对照；另有6行原问题诊断。去重后编码20个查询、11个文档。BM25标签是native ID的可读映射，数值始终按native ID计算。", "",
        "技术依据：[Qdrant v1.19 multilingual 源码](https://github.com/qdrant/qdrant/blob/v1.19.0/lib/segment/src/index/field_index/full_text_index/tokenizers/multilingual.rs)、[BM25 token ID及权重源码](https://github.com/qdrant/qdrant/blob/v1.19.0/lib/bm25/src/lib.rs)、[Edge 同源管线](https://github.com/qdrant/qdrant/blob/v1.19.0/lib/edge/src/bm25_embed.rs)。本报告没有调用服务器验证tokenizer构建差异，故将native实现同源性与旧检索记录分开陈述。", "",
        "### 原生分词的关键探针", "",
        "纯数字 `2000/2003/2005/2008/2011/2012` 的token集合均为空；`2003年`只留下“年”。这意味着BM25没有将年份范围理解为时间过滤，不能因原文有2003就预期此路必命中。dense输入仍保留完整年份。", "",
        "| 探针文本 | 原生 token ID |", "|---|---|"]
    for p in probes:
        lines.append(f"| {p['text']} | {', '.join(str(i) for i in p['ids']) or '∅'} |")
    lines += ["", "`周杰伦` 与 `周杰倫`、`发行` 与 `發行` 的集合在上述探针中不相交。不能把字面繁简差异一概当成已被处理；bb32b222 的 bm25_text 确实追加了简体艺人别名和部分检索词，因此其索引重叠高于正文，但不等于补足发行/风格/时间线的所有查询词。", "",
              "## 对应原问题与7条实际查询", "",
              "下列Q0/Q1/Q2各自执行dense与BM25，H仅执行dense，合计7条；没有重新改写或生成HyDE。后续对照表沿用完整leg_id。"]
    for cid in diag.CASE_IDS:
        case = cases[cid]
        lines += ["", f"### {cid}（{case['answerability']}）", "", f"原问题：{case['question']}", ""]
        for leg in case["leg_results"]:
            lines += [f"- `{leg['leg_id']}`：{leg['text']}"]
    for p in parents:
        pid = p["parent_id"]
        lines += ["", f"## {pid}", "", f"source=`{p['source_id']}`；doc=`{p['doc_id']}`；Child数={len(p['children'])}。", "",
                  "Parent原文前500字符（不足500则全文）：", ""]
        lines += ["> " + s if s else ">" for s in p["first500"].splitlines()]
        lines += ["", "Child对照：", ""]
        for child in p["children"]:
            lines.append(f"- `{child['chunk_id']}`；dense_text长度={len(child['dense_text'])}；完整内容见 parents.json。")
        for pair in [r for r in pairs if r["parent_id"] == pid]:
            lines += ["", f"### 与 {pair['case_id']} 的逐leg对照", "",
                      "P词=Parent正文覆盖；C词=各Child索引文本覆盖率最大值。旧D80仅对dense行有意义；BM25行的dense分数仅是离线对照，H行的词项重叠也仅作诊断。", "",
                      "| leg | P词交集/查询词 | C词交集/查询词 | P余弦 | C余弦 | 索引C | 旧D80 | 原召回 |",
                      "|---|---:|---:|---:|---:|---:|---:|---|"]
            for q in pair["queries"]:
                po = q["parent_overlap"]
                co = max(q["children"], key=lambda c: c["bm25_overlap"]["query_coverage"])["bm25_overlap"]
                hit = ", ".join(f"{h['child_id']}@{h['rank']}" for h in q["existing_target_hits"]) or ("未执行" if q["channel"] == "none" else "miss")
                cutoff = f"{q['existing_rank80_score']:.6f}" if q["channel"] == "dense" else "—"
                lines.append(f"| {q['leg_id']} | {po['shared_count']}/{po['query_terms']} | {co['shared_count']}/{co['query_terms']} | {q['parent_dense_cosine']:.6f} | {max(c['dense_cosine'] for c in q['children']):.6f} | {max(c['indexed_cache_dense_cosine'] for c in q['children']):.6f} | {cutoff} | {hit} |")
            lines += ["", "查询与Parent正文实际共有的词（只列每个不同文本一次）：", ""]
            for q in pair["queries"]:
                if q["channel"] == "lexical":
                    continue
                lines.append(f"- `{q['leg_id']}`：{labels(q, q['parent_overlap']['shared_ids'])}。")
            lines += ["", "Gold锚点程序核对：", ""]
            for a in pair["gold_anchors"]:
                lines.append(f"- {a['claim']}：Parent精确子串与Gold指定Child均通过；`{' / '.join(a['containing_children'])}`。原句：{a['exact_text']}")
    lines += ["", "## 为什么 bb32b222 在三题21条leg全 miss", "",
        "1. **正文证据真实、位置完整。** 它只有63个字符，唯一Child完整保留正文，dense_text额外前置“葉惠美 > 葉惠美”。2003发行锚没有丢到其他Parent；前一个Parent还保留发行信息表，后一个是命名/预购说明。不能将这次miss判为证据缺失或切分丢证。",
        "2. **三组子查询没有点名《叶惠美》。** 036问风格演变，单句发行简介没有风格论述；037虽有“2003年 音乐作品 发行”，BM25不保留2003，正文是“發行”；040问生涯/专辑梳理，正文也没有“生涯/时间线/梳理”。Parent正文对子查询仅共有“年”或无词，索引追加元数据后仍主要靠艺人/专辑/作品等宽泛词。",
        "3. **dense也不足以进入现有候选。** 三题12个dense比较，原索引Child与旧第80名差距为下表所示；HyDE提供通用风格背景，没有该专辑/发行节点，未弥补这个缺口。", "",
        "| 题号 | 子查询索引Child余弦范围 | HyDE索引Child余弦 | 相对各自旧D80的最小差 |", "|---|---:|---:|---:|"]
    for pair in [p for p in pairs if p["parent_id"] == diag.PARENTS[0]]:
        qs = [q for q in pair["queries"] if q["channel"] == "dense"]
        values = [q["children"][0]["indexed_cache_dense_cosine"] for q in qs]
        lines.append(f"| {pair['case_id']} | {min(values[:3]):.6f}–{max(values[:3]):.6f} | {values[3]:.6f} | {min(q['existing_rank80_score']-v for q,v in zip(qs, values, strict=True)):.6f} |")
    lines += ["", "Parent正文的余弦普遍高于带重复专辑标题的实际Child表示（差值可逐行复算），说明表示文本会影响该短段的分数；本轮没有移除标题重跑检索，也不据此宣称任何修复能恢复召回。H1其他旧题中该Child曾被BM25召回（如004第71名、051第25/11名），排除“此Child完全未入库”的说法；这是旧记录对照，不是新检索。", "",
        "## 74225：必须区分Parent命中、证据Child命中与融合保留", "",
        "039 的 `q2:decomposed:lexical` 在旧记录中命中 `child-5d3961b03be693977fc9330a` 第35名，BM25分数3.318739。它只获得这一条leg的贡献：`1/(60+35)=0.010526315789`。对旧7路列表做RRF账目复算，它在279个去重Child中为第160名；旧第40名分数0.039484191656，第80名0.026500526501。因此它从未进入selection，doc cap、source cap、reranker没有机会处理它。", "",
        "Gold的2011餐厅投资与2012西安KTV句都在 `child-2a8da8dde64454a9d2a2bf07`，并未被切到别的Parent；被召回的5d3961主要是其他餐饮店/2019等内容，不含这两句。Parent recall口径会把同Parent的任意Child视为命中，而生成是否拿到锚点是另一个问题，本轮未改计分规则。", "",
        "这是“锚Child未召回 + 同Parent另一Child弱单路召回后被融合淘汰”，不能概括为七路全miss，也不能归咎于Parent collapse或rerank。", "",
        "## 内容与切分复核", "",
        "| Parent | 证据核对 | 相邻切分检查 | 判断 |", "|---|---|---|---|",
        "| bb32b222 | 三题的2003发行精确锚均在唯一Child | 前一个是发行信息表，后一个是命名/预购；发行句本身完整 | 无c/d；短段与标题表示、查询语汇不匹配 |",
        "| 4da6252d | 收录专辑、二胡/琵琶两锚均在唯一Child | 前为目录，后为参考文献 | 无c/d；风格证据存在，宽泛查询没抓到具体歌曲特征 |",
        "| 705e51bf | 2007电影上映及配乐句完整在第二Child | 前段显式连接电影/音乐生涯；下一Parent是2008以后电影 | 无c/d；音乐时间线Gold包含跨电影节点，查询未覆盖该侧面 |",
        "| 74225d7e | 两条商业活动锚均在第一Child | 前为电竞，后为古董/耳机等；没有锚跨Parent | 无c/d；同Parent有3个Child，真正锚Child与命中Child不同 |", "",
        "相邻Parent全文保留于 neighbors.json；本轮只对指定锚点作缺失/切分判断，不宣称完成全语料或Gold全量复审。", "",
        "## 验证、产物与限制", "",
        "- `python -m pytest tests/test_rag_h2_parent_diagnosis_v5.py tests/test_rag_h2_parent_report_v5.py -q`；结果见 completion.json。覆盖向量异常、唯一词项口径、native标签、Parent/Child锚点区别、raw/RRF停点及重复命中去重。",
        "- `python -m evals.rag_h2_parent_report_v5`：20查询/11文档的保存向量对照、42行leg、72行逐Child、Gold锚与原H1前80的RRF账目离线复核通过。",
        "- Gold V4/V5、projection、Step2、H1受保护产物与所有现存app Python/YAML哈希复核未变。未修改生产逻辑；不涉及后端/前端变更，未做API或UI验证。",
        "- BM25 ID是同源Edge实现的离线结果，没有从服务器导出token流逐项比对；表内覆盖率不等于线上BM25分数。dense校准支持当前分数比较，但本轮没有新检索或全库精确排名。H1原有运行审计限制仍保留。",
        "- 本轮没有调参、重算baseline、改Gold、修生产代码、push、deploy或合入main。停在报告，等West决定后续。", "",
        "产物：parents.json（全文与Child）；comparisons.json（6组逐query明细）；native_term_ids.json；per_child_scores.jsonl（72行）；embedding_vectors.json；neighbors.json；existing_other_case_controls.json；tokenizer_probe.json；verification.json；offline_verification.json；completion.json。", "",
        "来源：Step2 frozen cases；H1 attempt-03 cases/inputs/completion；V5冻结锚；v4 projection；3份原始chunks；只读embedding_cache。完整路径及SHA256保留于 verification.json，所有判断均可下钻到上述文件。", ""]
    return "\n".join(lines)


def main():
    configure_offline()
    guard = Gate0Guard()
    sys.addaudithook(guard.audit)
    verified, child_rows = verify_saved()
    bm25 = diag.native_bm25(Path(diag.os.environ["TEMP"])/"MoodFlow-h2-qdrant-edge-0.8.0")
    probe_texts = ["2000", "2003", "2005", "2008", "2011", "2012", "2003年", "周杰伦", "周杰倫", "发行", "發行"]
    probes = [{"text": s, "ids": list(bm25.embed_query(s).indices)} for s in probe_texts]
    assert not guard.denied
    diag.write(diag.OUTPUT / "offline_verification.json", verified)
    diag.write(diag.OUTPUT / "tokenizer_probe.json", probes)
    (diag.OUTPUT / "per_child_scores.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False, allow_nan=False)+"\n" for c in child_rows), encoding="utf-8", newline="\n")
    (diag.OUTPUT / "report.md").write_text(render(verified, probes), encoding="utf-8", newline="\n")
    print(json.dumps({"verified": verified["verified"], "report": str(diag.OUTPUT / "report.md"), "new_queries": 0}))


if __name__ == "__main__":
    main()
