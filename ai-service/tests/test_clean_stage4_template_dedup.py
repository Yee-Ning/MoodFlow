"""Unit tests for Stage 4: Cross-Document Template Deduplication."""

from __future__ import annotations

from app.rag.clean.config import TemplateDeduplicationConfig
from app.rag.clean.template_dedup import TemplateDeduplicator


def test_cross_document_boilerplate_template_deduplication():
    dedup = TemplateDeduplicator(
        TemplateDeduplicationConfig(
            min_doc_frequency=3,
            similarity_threshold=0.85,
            min_block_chars=20,
        )
    )

    boilerplate = "版权所有 杰威尔音乐有限公司 JVR Music All Rights Reserved. 未经许可不得转载。"
    related_links = "相关阅读推荐：周杰伦最新巡演消息、Jay 经典专辑盘点、金曲奖获奖回顾。"

    doc1 = f"# 歌曲介绍：夜曲\n\n《夜曲》是周杰伦演唱的一首歌曲，收录于十一月的萧邦。\n\n{boilerplate}\n\n{related_links}"
    doc2 = f"# 歌曲介绍：发如雪\n\n《发如雪》是周杰伦演唱的一首歌曲，由方文山作词。\n\n{boilerplate}\n\n{related_links}"
    doc3 = f"# 歌曲介绍：青花瓷\n\n《青花瓷》是周杰伦演唱的一首具有浓郁中国风的歌曲。\n\n{boilerplate}\n\n{related_links}"

    corpus = [
        ("doc-1", "jvr_site", doc1),
        ("doc-2", "jvr_site", doc2),
        ("doc-3", "jvr_site", doc3),
    ]

    # Index and train boilerplate templates across documents from jvr_site
    trained = dedup.train_site_templates(corpus)
    assert "jvr_site" in trained
    assert len(trained["jvr_site"]) >= 1

    # Deduplicate doc1
    res1 = dedup.deduplicate(doc1, doc_id="doc-1", site="jvr_site")
    assert "《夜曲》是周杰伦演唱的一首歌曲" in res1.cleaned_text
    assert boilerplate not in res1.cleaned_text
    assert related_links not in res1.cleaned_text
    assert res1.removed_blocks >= 1
    assert res1.stage_metrics.removed_blocks >= 1


def test_template_deduplication_preserves_unique_content_and_tables():
    dedup = TemplateDeduplicator(
        TemplateDeduplicationConfig(min_doc_frequency=2, similarity_threshold=0.85)
    )

    table_block = "| 序号 | 歌名 |\n|---|---|\n| 1 | 晴天 |\n| 2 | 简单爱 |"
    repeated_footer = "本站所有音乐资讯均来源于官方新闻通告，版权归原作者所有。"

    doc1 = f"文章 1 正文内容独特介绍。\n\n{table_block}\n\n{repeated_footer}"
    doc2 = f"文章 2 正文内容独特介绍。\n\n{table_block}\n\n{repeated_footer}"

    corpus = [
        ("doc-1", "siteA", doc1),
        ("doc-2", "siteA", doc2),
    ]

    dedup.train_site_templates(corpus)

    res = dedup.deduplicate(doc1, doc_id="doc-1", site="siteA")
    # Table should NEVER be deleted by template deduplication
    assert "| 序号 | 歌名 |" in res.cleaned_text
    assert "| 1 | 晴天 |" in res.cleaned_text
    # Boilerplate footer should be removed
    assert repeated_footer not in res.cleaned_text


def test_template_deduplication_does_not_affect_different_sites():
    dedup = TemplateDeduplicator(
        TemplateDeduplicationConfig(min_doc_frequency=2)
    )

    boilerplate = "官方版权通告：索尼音乐独家发行保护声明内容。"
    doc_siteA = f"正文 A\n\n{boilerplate}"
    doc_siteB = f"正文 B\n\n{boilerplate}"

    # Only 1 doc per site -> below min_doc_frequency per site
    corpus = [
        ("doc-A", "siteA", doc_siteA),
        ("doc-B", "siteB", doc_siteB),
    ]
    dedup.train_site_templates(corpus)

    resA = dedup.deduplicate(doc_siteA, doc_id="doc-A", site="siteA")
    assert boilerplate in resA.cleaned_text
    assert resA.removed_blocks == 0
