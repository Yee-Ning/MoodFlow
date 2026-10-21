from __future__ import annotations

import asyncio
import copy
import unittest

from tests.test_rag_knowledge import _enriched, _enriched_child

from app.rag.enrichment.models import EntityTypeHint
from app.rag.knowledge.config import KnowledgeConfig
from app.rag.knowledge.entity_linking.grounded import (
    GroundedEntityLinker,
    boilerplate_reason,
    body_occurrences,
    film_document,
)
from app.rag.knowledge.grounded_qa import (
    GroundedKnowledgeQASampler,
    validate_entity_sample,
)
from app.rag.knowledge.models import (
    KnowledgeEnrichedParentChunk,
    KnowledgeEnrichment,
    KnowledgeEntities,
    KnowledgeStats,
    KnowledgeQuality,
    KnowledgeStatus,
    KnowledgeProvenance,
)


def as_knowledge(base, audit):
    entities = KnowledgeEntities(
        linked_entities=audit.result.linked_entities,
        unresolved_mentions=audit.result.unresolved_mentions,
    )
    value = KnowledgeEnrichment(
        entities=entities,
        claims=[],
        stats=KnowledgeStats.build(entities, []),
        quality=KnowledgeQuality(status=KnowledgeStatus.SUCCESS),
        provenance=KnowledgeProvenance(
            entity_linking_version="v6",
            claim_extraction_version="v10",
            claim_verification_version="v3",
            registry_version="v3",
            prompt_version="v1",
            knowledge_input_hash="sha256:" + "0" * 64,
        ),
    )
    return KnowledgeEnrichedParentChunk(**base.model_dump(), knowledge=value)


class BodyGroundingTests(unittest.TestCase):
    def setUp(self):
        self.linker = GroundedEntityLinker(
            KnowledgeConfig(entity_linking_version="v6", llm_enabled=False)
        )

    def ids(self, text, **kwargs):
        audit = self.linker.link_with_evidence(_enriched(text, **kwargs))
        return {e.entity_id for e in audit.result.linked_entities}, audit

    def test_requires_explicit_rule_only_version(self):
        for config in [KnowledgeConfig(), KnowledgeConfig(entity_linking_version="v6")]:
            with self.assertRaisesRegex(ValueError, "requires_v6_rule_only"):
                GroundedEntityLinker(config)

    def test_artist_metadata_and_title_are_not_body_mentions(self):
        ids, audit = self.ids(
            "这是关于母带制作的技术介绍。",
            primary=("周杰伦", EntityTypeHint.ARTIST),
            metadata={
                "title": "Jay Chou",
                "document_metadata": {"source_metadata": {"album": "范特西"}},
            },
        )
        self.assertEqual(ids, set())
        self.assertTrue(audit.context_only_mentions)

    def test_parent_subject_does_not_ground_unrelated_child(self):
        child = _enriched_child(
            "有实质内容的其他歌手介绍。", mentions=[("周杰伦", EntityTypeHint.ARTIST)]
        )
        self.assertFalse(self.linker.link_with_evidence(child).result.linked_entities)

    def test_nfkc_alias_quotes_keep_original_offsets(self):
        text = "专辑《哎呦，不錯哦》融合了 R＆B 风格。"
        found = body_occurrences(text, ("哎呦,不錯哦", "R&B"))
        self.assertEqual({v["quote"] for v in found}, {"哎呦，不錯哦", "R＆B"})
        self.assertTrue(all(text[v["start"] : v["end"]] == v["quote"] for v in found))

    def test_whitespace_and_casefold_quotes_keep_offsets(self):
        text = "歌手JAY\n  CHOU演唱。"
        found = body_occurrences(text, ("Jay Chou",))
        self.assertEqual(found[0]["quote"], "JAY\n  CHOU")

    def test_longest_alias_owns_its_body_span(self):
        ids, _ = self.ids("专辑《依然范特西》由周杰伦发行。")
        self.assertIn("album:still_fantasy", ids)
        self.assertNotIn("album:fantasy", ids)

    def test_latin_hyphenated_name_does_not_match_short_alias(self):
        self.assertEqual(body_occurrences("Jay-Z met Annie.", ("Jay", "Ann")), [])

    def test_hip_hop_song_substring_is_not_genre(self):
        ids, audit = self.ids("喜欢歌曲嘻哈空姐。")
        self.assertNotIn("genre:hip_hop", ids)
        self.assertIn(
            "genre_is_substring_of_larger_surface",
            {x["reason"] for x in audit.rejected},
        )

    def test_quoted_work_does_not_create_genre_link(self):
        ids, _ = self.ids("歌曲《嘻哈空姐》很好听。")
        self.assertNotIn("genre:hip_hop", ids)

    def test_real_genre_mentions_still_link(self):
        for text in [
            "周杰伦融合嘻哈元素。",
            "周杰伦演唱hip-hop风格歌曲。",
            "周杰伦的嘻哈音乐。",
        ]:
            with self.subTest(text=text):
                self.assertIn("genre:hip_hop", self.ids(text)[0])

    def test_hybrid_genre_is_not_mistaken_for_song_name_substring(self):
        self.assertIn("genre:hip_hop", self.ids("稻香是一首嘻哈民謠搭配饒舌曲風。")[0])

    def test_explicit_music_work_list_resolves_ambiguous_song(self):
        self.assertIn(
            "song:fearless",
            self.ids("《青花瓷》、《霍元甲》、《本草纲目》等均属于中国风作品。")[0],
        )

    def test_film_title_is_not_music_work_even_with_song_metadata(self):
        ids, audit = self.ids(
            "电影《以父之名》由Jim Sheridan导演。",
            metadata={"title": "以父之名（歌曲）"},
        )
        self.assertNotIn("song:in_the_name_of_father", ids)
        self.assertIn(
            "film_music_work_type_conflict", {x["reason"] for x in audit.rejected}
        )

    def test_film_document_context_carries_to_child_link_interface(self):
        film = "| 导演 | Jim |\n| 主演 | Gerry |\n| 片长 | 133 |\n上映日期1993"
        self.assertTrue(film_document(film))
        linker = GroundedEntityLinker(
            self.linker.config, film_documents=frozenset({"doc-1"})
        )
        result = asyncio.run(linker.link(_enriched("以父之名讲述电影主角的故事。")))
        self.assertNotIn(
            "song:in_the_name_of_father", {e.entity_id for e in result.linked_entities}
        )

    def test_actual_song_title_still_links(self):
        self.assertIn(
            "song:in_the_name_of_father", self.ids("歌曲《以父之名》由周杰伦演唱。")[0]
        )

    def test_explicit_work_type_cannot_be_overridden_by_unique_registry_candidate(self):
        ids, audit = self.ids("专辑《青花瓷》发行了。")
        self.assertNotIn("song:blue_and_white_porcelain", ids)
        self.assertIn(
            "explicit_work_type_conflict", {x["reason"] for x in audit.rejected}
        )

    def test_shared_jay_stays_unresolved_without_body_context(self):
        ids, audit = self.ids("Jay来了。", primary=("周杰伦", EntityTypeHint.ARTIST))
        self.assertFalse(ids)
        self.assertTrue(audit.result.unresolved_mentions)

    def test_definite_boilerplate_is_excluded(self):
        examples = [
            ("| | |", []),
            ("[编辑]", []),
            ("2021", ["Graduation Year"]),
            ("Masters", ["Thesis Type"]),
            ("1. Track 1\n1. Track 2", []),
            ("跳到主要內容區塊\n- 首頁", []),
            ("首页 分类索引 创建账号 登录", []),
            ("© Copyright All Rights Reserved", []),
            ("分类信息 招商 年赚百万 利润", []),
            ("| 查论编周杰倫歌曲 |", []),
            ("| 獲獎及榮譽 | 其他歌手", ["外部連結"]),
        ]
        for text, path in examples:
            with self.subTest(text=text):
                self.assertIsNotNone(boilerplate_reason(text, path))

    def test_short_real_track_list_and_prose_are_not_length_filtered(self):
        self.assertIsNone(
            boilerplate_reason("1. 逆鱗\n1. 麥芽糖\n1. 珊瑚海", ["Side C"])
        )
        self.assertIsNone(boilerplate_reason("周杰伦演唱了歌曲。", []))

    def test_mixed_footer_and_metadata_header_are_not_samples(self):
        for text in [
            "1. 困獸之鬥\n集團徵才 關於我們 法律條款 隱私權政策\n© 台灣索尼音樂娛樂",
            "取自https://example.test\n分类：\n金曲獎作品\n隐藏分类：\n关于维基百科",
            "| 周杰伦访谈 |\n| |\n| http://example.test 2004年10月24日07:49 报纸 |\n| |",
        ]:
            with self.subTest(text=text):
                self.assertIsNotNone(boilerplate_reason(text, []))

    def test_advertisement_topic_menu_is_not_a_genre_mention(self):
        self.assertEqual(
            boilerplate_reason(
                "Advertisement\n\nK-pop, Mandopop, other Asian pop\n\nLifestyleEntertainment",
                [],
            ),
            "advertisement_topic_navigation",
        )


class GroundedSamplingTests(unittest.TestCase):
    def setUp(self):
        linker = GroundedEntityLinker(
            KnowledgeConfig(entity_linking_version="v6", llm_enabled=False)
        )
        self.base = _enriched("周杰倫融合R＆B风格。")
        self.audit = linker.link_with_evidence(self.base)
        self.chunk = as_knowledge(self.base, self.audit)
        self.sampler = GroundedKnowledgeQASampler({self.chunk.chunk_id: self.audit})

    def test_sample_body_quotes_are_complete_valid_and_deterministic(self):
        rows = self.sampler.entity_rows([self.chunk])
        self.assertEqual(rows, self.sampler.entity_rows([self.chunk]))
        for row in rows:
            validate_entity_sample(row, self.chunk)
            self.assertTrue(
                all(s in self.chunk.content for s in row["matched_surfaces"])
            )
        self.assertIn("R＆B", {s for row in rows for s in row["matched_surfaces"]})

    def test_empty_population_remains_empty(self):
        self.assertEqual(GroundedKnowledgeQASampler({}).entity_rows([]), [])

    def test_empty_surfaces_are_invalid(self):
        row = copy.deepcopy(self.sampler.entity_rows([self.chunk])[0])
        row["matched_surfaces"] = []
        with self.assertRaisesRegex(ValueError, "missing_body_matches"):
            validate_entity_sample(row, self.chunk)

    def test_wrong_quotes_and_offsets_are_invalid(self):
        row = copy.deepcopy(self.sampler.entity_rows([self.chunk])[0])
        row["body_matches"][0]["start"] += 1
        with self.assertRaisesRegex(ValueError, "quote_mismatch|offset_invalid"):
            validate_entity_sample(row, self.chunk)

    def test_wrong_chunk_and_body_hash_fail_closed(self):
        row = copy.deepcopy(self.sampler.entity_rows([self.chunk])[0])
        row["chunk_id"] = "wrong"
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            validate_entity_sample(row, self.chunk)
        changed = self.chunk.model_copy(
            update={"content": self.chunk.content + "changed"}
        )
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            self.sampler.entity_rows([changed])

    def test_resolution_proof_cannot_be_reused_with_different_entity_set(self):
        entities = self.chunk.knowledge.entities.model_copy(
            update={"linked_entities": []}
        )
        changed = self.chunk.model_copy(
            update={
                "knowledge": self.chunk.knowledge.model_copy(
                    update={"entities": entities}
                )
            }
        )
        with self.assertRaisesRegex(ValueError, "entity_set_mismatch"):
            self.sampler.entity_rows([changed])


if __name__ == "__main__":
    unittest.main()
