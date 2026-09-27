# Worldline Production Native Positive Preparation

日期：2026-09-27。范围仅 B2；没有执行 Semantic Relations Eval、最终 acceptance、restart 或部署，没有修改 merge/persistence、validator 或 evaluator。

**本轮未达到正式发布条件。真实来源和正确主体的 SUPPORTED Scene child candidate 已取得，但最新 working dataset 的明确 Influence 只保留为 parent claim，且有 7 条 claim 未获全量支持，正式 release gate 拒绝发布。** 已停止新增来源和继续尝试，旧 approved index/runtime 保持原值。

本文 native_positive_ready 按本轮完整要求判定，包含正式 v4 索引及真实 Hybrid/Expansion smoke；SUPPORTED staging candidate 不等于生产 readiness。

## 1. Production extraction contract

v13：entity linking v4、claim extraction v10、verification v3、prompt v1、registry v3；LLM 开启，batch=16、concurrency=4、timeout=45 秒、retry=2。

| 类型 | 严格 structured output schema 允许且 runtime 支持的 predicate |
| --- | --- |
| Scene | belongs_to_scene, representative_of_scene, scene_association, style_scene_association, emerged_from_scene |
| Influence | influenced, influenced_by, inspired, inspired_by, shaped_style, cited_as_influence |

scene_membership / associated_with_scene 不是现有 enum，应使用已有规范 predicate。Extractor 保留 provider predicate，没有将这些 predicate 自动归为 other 的 normalization；LLM verification 没有额外白名单将它们排除。Rule extractor 没有相关 cue 不影响 LLM 路径。

通用 prompt 要求明确、客观、原子事实及连续原文 evidence span；验证仍检查 source chunk、span、时间和 source-local 支持情况。**Schema/prompt 准入已支持，因此按要求没有修改 extraction contract。** 六个原 config/schema/prompt/extractor/verifier 文件的 SHA 在交付前复核一致。

production_extraction_contract_ready=true 表示准入审计通过，不表示本次所有生成结果正确或可发布。

审计：[extraction_contract_audit.json](D:/code/ProjectsAll/MoodFlow/ai-service/data/source_ingestion/worldline-native-positive-v2-20260927/extraction_contract_audit.json)。

## 2. 两篇最小真实来源

两篇均重新由 SourceIngestionService 取得原网页 HTTP 200，保留原 HTTP/robots/pacing 流程。不使用搜索摘要、Gold、review pack 或审核说明。

| source ID | 来源身份及原网页 | 明确关系 |
| --- | --- | --- |
| wl-native-v2-001 | World Socialist Web Site / [Assessing an American pop icon](https://www.wsws.org/en/articles/2012/12/05/nirv-d05.html)，Nick Barrickman，2012-12-05 | Nirvana 的 Grunge scene emergence；另一原段落明确描述 Cobain 的 punk scene emergence。仅以事实段落作为依据，不使用评论观点作为正例。 |
| wl-native-v2-002 | ABC News / [Kurt Cobain's legacy is evident in the music from the year he died](https://www.abc.net.au/news/2024-04-05/30-years-kurt-cobain-died-alternative-music-1994/103652402)，Matt Neal，2024-04-05 | 明确的 Pixies → Nirvana Influence。 |

raw response SHA256：

```text
wl-native-v2-001: dda83b56f08bad394c250dfb419d917f58445d7cf8bb3180adb04ed7e137626c
wl-native-v2-002: 82b98e20c5a14ccac700e4981651af641db8d15ea7427778c19951b30fcf33d6
```

[raw_sources.jsonl](D:/code/ProjectsAll/MoodFlow/ai-service/data/source_ingestion/worldline-native-positive-v2-20260927/raw_sources.jsonl) 保存实际 raw_path、时间和 SHA。raw bytes 未修改；第二次 run 复用同一 immutable snapshot，没有新增网页。

## 3. 两次正式 production run

实际链路：

SourceIngestionService → raw immutable snapshot → HtmlParser / normalize → source context capture → CleanPipelineService → ChunkPipelineService → metadata enrichment → EntityLinker → ClaimExtractor → ClaimVerifier → KnowledgePipelineService → KnowledgeReleaseBuilder gate。

metadata enrichment 使用现有 rule-only 模式，claim extraction/verification 使用真实 deepseek-flash provider。Trace wrapper 只记录实际返回值、请求 hash、model、latency，不提供候选、不修 predicate、不增加生产策略之外的重试。没有手工修改 claim JSON。

两次均是相同的 2 documents、35 parent、41 child，共 76 chunks；clean 未删改原文。2 个 document_type 缺失/suspicious 提示和 18 个 chunk warning 原样保留。enrichment 76/76 index_ready，内容和结构 IDs 保留。

| 项目 | 第一次：v14 / registry v4 | 第二次：v15 / registry v5 |
| --- | --- | --- |
| registry 数据 | 保留 v3 全部 62 个实体，增加 Nirvana、Pixies | 保留 v4 全部 64 个实体，补原文实际出现的 Kurt Cobain、NWA、REM、Metallica |
| config | 与 v13 仅 registry_version 不同 | 与 v13 仅 registry_version 不同 |
| linked occurrences / unresolved | 82 / 0 | 167 / 0 |
| knowledge success / partial / failed | 67 / 9 / 0 | 62 / 14 / 0 |
| owned claims | 79 | 81 |
| SUPPORTED | 75 | 74 |
| PARTIALLY_SUPPORTED / CONTRADICTED / INSUFFICIENT_EVIDENCE | 1 / 1 / 2 | 6 / 0 / 1 |
| recorded extraction / verification calls | 104 / 89 | 111 / 99 |
| recorded completed / failed requests | 163 / 30 | 189 / 21 |
| elapsed | 211.73 秒 | 242.25 秒 |
| latency median / max | 3791.59 / 9138.83 ms | 3883.545 / 8523.9 ms |

recorded calls 为 trace 覆盖的 extraction/verification 请求，不冒充其他未记录阶段的总调用数。没有输出 API Key。

第一次发现原文主语 Cobain 等不在 registry，部分结果误绑 Nirvana。仅补真实实体后，第二次依相同来源重新经过 parse/clean/chunk/enrich/extract/verify，未混用两次结果。Cobain 的 surname alias 保持歧义控制。

第二次最终 warnings：6 个 chunk 的 claim_extraction_llm_incomplete:max_output_tokens、7 个 claim_extraction_invalid_output、1 个 claim_unknown_subject_entity。失败不是最终 timeout；未修改原 provider 的 1024 output-token budget。

## 4. Native 正例与索引资格

第二次已有真实、SUPPORTED Scene child candidate：

```text
claim_id: claim:e79e6a255766ebd163888f47
source_id: wl-native-v2-001
document_id: doc-wl-native-v2-001-3b9acc738bff7ee9b2ae
chunk_id: child-e8ab7103e1e2816914615785
subject_entity_id: artist:kurt_cobain
predicate: emerged_from_scene
object_value: West Coast American punk scene
verification: SUPPORTED (confidence=1.0)
```

主体现在与原文 Cobain 对齐，不再将成员经历冒充 Nirvana 的关系；原段落完整进入 article_sentence capture。exact span、source/chunk/entity/claim IDs、SHA 和 provenance 均保留。此为 staging candidate，没有宣称 SceneExpansionService 在线验证通过。

明确 Nirvana/Grunge 原段落第二次也在 provider raw output 中产生 Scene predicate，但该 child 最终是 partial、无 owned claims。原始返回存在不等于正式 extractor 接受；未把 raw candidate 手工写回数据。

第一次已有正确 ABC Influence child：claim:bf6ae99ce653aa66777d454c，child-9a17f2d459cd03b4eb930f4a，artist:pixies → artist:nirvana，influenced，SUPPORTED。

第二次同一明确关系只保留为 parent native claim：

```text
claim_id: claim:1e1e2cf3cd55ef4241a6cb99
source_id: wl-native-v2-002
document_id: doc-wl-native-v2-002-dd868528eddc49706ffe
chunk_id: parent-c4c46393864635097c469c47
subject_entity_id: artist:pixies
predicate: influenced
object_entity_id: artist:nirvana
verification: SUPPORTED (confidence=0.99)
```

对应 child 的 production extraction 重试最终仍是 partial、没有 owned claims。当前 IndexDocumentProjector v4 的 supported_claims 取正式 child claims，不自动继承 parent claims。因此最新统一版本 working dataset 中明确、有两个 artist ID 和方向的 Influence child candidate 数为 0。面向 genre、industry 或不明 kids 的 influenced 结果未用于替代 artist → artist 正例。

未跨 registry/version 手工拼接第一次的 Influence 与第二次的 Scene，未绕过 extraction/verification。

## 5. Source context 与 metadata

只从唯一 HTML 原段落逐字 capture，保留 URL、publisher、title、locator、raw response/content SHA、DOM offset 和 document/source IDs。

| run / source | decoded DOM offset | capture content SHA256 |
| --- | --- | --- |
| 第一次 / WSWS | [1328:1754]，article#article p | 49096284635d82d82740ade3ea004d0884e9dbf001628128c49e37c954a44a9c |
| 第二次 / WSWS | [3173:3671]，article#article p | 4b7b72f6a0c08c11a92d555053a7efd60757aa27fa7d9a4066971e904b819eca |
| 两次 / ABC | [15652:15815]，main#content p | 464f7c72c464758dc23f75f55365c8035757bcebd7222705957d5357aa4e8e1e |

offset 单位为去除 script/style 后、未清理空白的 decoded DOM Unicode code point。只有 article_sentence，没有可审计 Q/A turn 或 speaker binding 的字段保持缺失。

captures 随 metadata 进入 enriched/knowledge。真实数据审计确认 raw SHA、source ID、claim source_chunk_id、逐字 span 一致。gate 拒绝后，未产生新正式 release 的 v4 native context binding、Qdrant、ContextPack 或 adapter 在线证据。

证据：[第一次完整审计](D:/code/ProjectsAll/MoodFlow/ai-service/data/source_ingestion/worldline-native-positive-v2-20260927/native_preparation_evidence.json)、[第二次完整审计](D:/code/ProjectsAll/MoodFlow/ai-service/data/source_ingestion/worldline-native-positive-v3-20260927/native_preparation_evidence.json)。两个 working datasets、provider trace、原始 raw 和 source capture 中间数据保留。既有 Git ignore 排除的 raw/normalized 文件仍在本地。

## 6. Release gate 和 blocker

两次正式 KnowledgeReleaseBuilder.prepare_review() 均在生成 release QA 文件前拒绝：

```text
knowledge_release_claim_verification_incomplete
```

第二次 7 条阻塞 claim 原样保留：

| claim ID | verification | 原因 |
| --- | --- | --- |
| claim:e331f68b85fa7e7117e30aa3 | INSUFFICIENT_EVIDENCE | 年份没有 evidence span 支持，claim_time_not_grounded |
| claim:17b63efc686880ee5f088653 | PARTIALLY_SUPPORTED | album/year 归属不足，subject 误绑 Nirvana |
| claim:ad329d08f18a7c256c593b64 | PARTIALLY_SUPPORTED | The Offspring 的 chart fact 误绑 Nirvana |
| claim:7dc1c010d685215826715014 | PARTIALLY_SUPPORTED | 同类 child chart subject 误绑 |
| claim:253578f54352fe28af2bde00 | PARTIALLY_SUPPORTED | drumsticks 暗示被抽成确定事实及错误主体 |
| claim:35a781902b3012150fe93ffa | PARTIALLY_SUPPORTED | hypothetical another band 被归为 Nirvana 的确定 Influence |
| claim:d54ad326a5bf38f3430c20d5 | PARTIALLY_SUPPORTED | 同类 counterfactual Influence |

**阻塞位于 production extraction 的 source-to-entity binding、完整 structured-output 接受及非确定事实排除；不是缺少真实关系网页，也不是 enum/whitelist 不支持 predicate。** 未删除失败 claim、改 verification、弱化 gate 或继续找来源。当前不是所有关系一直被降为 other/collaborated_with；报告实际失败，不捏造该模式。

pre_embedding_ready=true 只是结构 gate，不能取代 all-SUPPORTED release gate。未创建 release、collection、index manifest 或新 approval，未直接写 Qdrant。需要这些前提的那一次真实 Hybrid → ContextPack → adapter → Scene/Influence expansion 数据 smoke 未执行，direction/online evidence 消费未标记通过。

## 7. Runtime、修改与验证

旧 runtime 仍为 hybrid；旧 manifest 保持 approved、SHA 不变：

```text
data/indexing/worldline/source-context-20260926/v4/index_build_manifest.json
sha256:a74164c52346dd4050468d2e491dfe5993abec16cb70122d9df7d693769d2f17
```

旧 release/collection 未覆盖。未改 .env、PostgreSQL 或 source identity merge；新 registry/config 未启用到在线 runtime。

新增 v14/v15 config、v4/v5 entity registry 独立数据版本；scripts/worldline_native_source_capture.py、scripts/worldline_native_claim_preparation.py；两个回归测试文件；两批隔离的 source/staging/trace 证据与本报告。仅在本轮四个数据目录添加 .gitattributes，防止 Git 行尾转换改变审计 SHA；复核 staged data bytes 与本地文件一致。用户原有未提交改动未暂存或覆盖。

两次运行命令均使用现有 .venv/Scripts/python.exe -X utf8。TAG 为 worldline-native-positive-v2-20260927 / worldline-native-positive-v3-20260927，CONFIG/REGISTRY 为 v14/v4、v15/v5：

```text
run_clean.py clean --normalized-root data/normalized/TAG --cleaned-output-root data/cleaned/TAG --report-root data/clean/reports/TAG --run-id TAG
run_chunk.py --cleaned-root data/cleaned/TAG --manifest data/cleaned/TAG/cleaned_corpus_manifest.jsonl --output-root data/chunked/TAG
run_enrich.py --chunked-root data/chunked/TAG --manifest data/chunked/TAG/chunked_corpus_manifest.jsonl --output-root data/enriched/TAG --rule-only --sample-size 0
-m scripts.worldline_native_claim_preparation --config app/rag/knowledge/config/CONFIG.yaml --registry app/rag/knowledge/entity_linking/registry/REGISTRY.yaml --enriched-path data/enriched/TAG/enriched_chunks.jsonl --output-root data/knowledge/TAG
-m pytest tests/test_worldline_native_positive_registry.py tests/test_worldline_native_preparation.py tests/test_worldline_source_context_preparation.py tests/test_rag_knowledge.py tests/test_rag_indexing.py tests/test_worldline_influence_source_context.py -q
```

两次 clean/chunk/enrich/knowledge 命令完成，但 semantic verification/release 失败如上，未宣称产品完成。相关测试：122 passed、1 skipped、3 subtests passed。唯一 skip 为既有需显式 QDRANT_INTEGRATION_URL 的独立索引集成测试，未修改跳过条件。新脚本/测试 py_compile、v13 文件 SHA 审计、Git diff check 通过。未改 UI，未额外执行前端构建或最终 acceptance。

已提交 preparation tooling 9e974d5、原文主体 registry 补全 20b2712；未 push。

## 最终状态

生产 readiness 包含正式索引及真实数据 smoke，不能只凭 staging SUPPORTED 标为 ready。

```text
scene_native_positive_ready=false
influence_native_positive_ready=false
production_extraction_contract_ready=true
new_release_ready=false
can_run_final_worldline_acceptance=false
```
