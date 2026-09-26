# Source context runtime input contract

Capture 来源为正常文档入库 metadata，不得引用 Gold、review note、evaluation overlay 或生成的解释文本。

`cleaned.metadata.source_context_captures` 通过既有 chunk engine 进入 `chunk.metadata.document_metadata.source_context_captures`，并由 enrichment/knowledge 保留。JSON Schema 见 document_context_capture.schema.json。数组最多 8 条；每个 content 最多 6000 个字符，角色在同一 packet 内唯一。

Capture 必须携带原始 URL、publisher、title、locator、captured_at、capture_method、response_sha256、content、content_sha256、segments。segments 只引用来源中的 article_sentence / interview_question / interview_answer / speaker_binding；不推断缺失的主体或说话人。

每个 segment 的 exact_span 必须等于 content[start:end]。start/end 为 Unicode code-point 半开区间，SHA256 使用 UTF-8。response_sha256 是真实捕获响应的 upstream provenance 标识；不能用生成文本或任意占位 SHA 代替生产 capture。

IndexDocumentProjector v4 将唯一真实 claim span 绑定到已验证 claim，填入 native context/source/parent/chunk/claim IDs、原始 Child SHA 和 anchor offsets。绑定后的形状见 relation_source_context.schema.json。上游已有绑定好的 chunk.metadata.relation_source_contexts 或 document_metadata.relation_source_contexts 也可使用，但不能和 raw capture 重复绑定同一 claim。

Runtime 在 Qdrant payload selector、candidate metadata、ContextPack 和 Worldline adapter 中携带 relation_source_contexts。source_evidence_content 保存原始 Child，compressed context 保存查询视图。Supplement 的文本必须计入原有 item/pack 预算；任何裁剪都不能改写 exact span/offsets 或切断 Q/A 与 speaker binding。

API 的 relation evidence source.metadata.relation_source_context 保存单个完整、通过绑定检查的 packet；generic source metadata 保留 plural packets 和原始 Child。Era/Scene/Influence 共用 provenance contract；未改变各自判定规则。

缺少 capture、无合法 native entity binding、SHA/URL/claim 不一致、重复或超预算时，不编造 context。runtime 根据已有 validator 拒绝不足证据或保留原目录事实，相关 warning 可从 ContextPack/source metadata/trace 查看。

本轮没有实现从任意网页自动推断 interview roles 的新爬虫/模型，也没有为旧生产语料回填 capture。在线启用前，上游必须准备真实 metadata 并通过正常 immutable source/index release 审计。
