from __future__ import annotations

import hashlib
import unittest

from app.rag.knowledge.config import KnowledgeConfig
from app.rag.knowledge.entity_linking.b1_rules import (
    infer_occurrence,
    infer_qi_li_xiang_occurrence,
)
from app.rag.knowledge.entity_linking.b1_scope import (
    B1Occurrence,
    B1Scope,
    B1ScopeEntry,
)
from app.rag.knowledge.entity_linking.grounded import GroundedEntityLinker
from app.rag.knowledge.entity_linking.registry import EntityRegistry, normalize_alias
from tests.test_rag_knowledge import _enriched


def scope_for(chunk, base, surface):
    records = [
        r
        for r in base.rejected
        if normalize_alias(r["quote"]) == normalize_alias(surface)
    ]
    mention = next(
        m
        for m in base.result.unresolved_mentions
        if m.normalized_text == normalize_alias(surface)
    )
    return B1Scope(
        [
            B1ScopeEntry(
                audit_id="test",
                doc_id=chunk.doc_id,
                chunk_id=chunk.chunk_id,
                body_sha256=hashlib.sha256(chunk.content.encode()).hexdigest(),
                normalized_text=mention.normalized_text,
                candidate_entity_ids=tuple(mention.candidate_entity_ids),
                reason=mention.reason,
                occurrences=tuple(
                    B1Occurrence.model_validate(
                        {k: r[k] for k in B1Occurrence.model_fields}
                    )
                    for r in records
                ),
            )
        ]
    )


class B1RuleTests(unittest.TestCase):
    def setUp(self):
        self.registry = EntityRegistry.load()

    def decide(self, text, surface, occurrence=0):
        start = -1
        for _ in range(occurrence + 1):
            start = text.index(surface, start + 1)
        return infer_occurrence(text, start, start + len(surface), self.registry)

    def test_jay_human_predicates(self):
        for text in [
            "私はJayがデビューした時に音楽を紹介していた。",
            "Jayがプロデュース、監督、主演した映画です。",
            "Jayは挑戦する人で俳優として進出しました。",
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.decide(text, "Jay").entity_id, "artist:jay_chou")

    def test_jay_album_ownership_and_unrelated_names_do_not_conflict(self):
        for text in [
            "Since the release of his debut album, Jay, in November 2000.",
            "周杰倫首張個人創作專輯「Jay」發行。Jay-Z是一位歌手。",
            "Jay Chou介绍他的专辑《JAY》。",
            "最佳流行音樂演唱專輯獎 2001年 《Jay》。",
        ]:
            with self.subTest(text=text):
                surface = "JAY" if "《JAY》" in text else "Jay"
                occurrence = (
                    1 if text.startswith("Jay Chou") and surface == "Jay" else 0
                )
                self.assertEqual(
                    self.decide(text, surface, occurrence).entity_id, "album:jay_2000"
                )

    def test_no_default_and_latin_boundaries(self):
        for text, surface in [
            ("Jay来了。", "Jay"),
            ("Jay-Z是歌手。", "Jay"),
            ("bluejay在这里。", "jay"),
            ("http://example/Jay/news", "Jay"),
            ("《Jay的成长故事》是一本书。", "Jay"),
        ]:
            with self.subTest(text=text):
                self.assertIsNone(self.decide(text, surface))

    def test_qi_li_xiang_attached_roles(self):
        cases = [
            ("《七里香》是台灣歌手周杰伦發行第五張國語專輯。", "album:qi_li_xiang"),
            ("专辑《七里香》收录歌曲《七里香》。", "album:qi_li_xiang"),
            ("演唱歌曲《七里香》。", "song:qi_li_xiang"),
            ("「七里香」完整版MV。", "song:qi_li_xiang"),
            ("「七里香」のイントロをギターで弾いた。", "song:qi_li_xiang"),
            ("| 曲目 | 作词 | 时长 |\n| 七里香 | 方文山 | 4:57 |", "song:qi_li_xiang"),
            ("1. 我的地盤\n1. 七里香\n1. 藉口", "song:qi_li_xiang"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(self.decide(text, "七里香").entity_id, expected)
        text = "专辑《七里香》收录歌曲《七里香》。"
        self.assertEqual(self.decide(text, "七里香", 1).entity_id, "song:qi_li_xiang")

    def test_conflicting_and_missing_work_evidence_abstain(self):
        for text in ["歌曲《七里香》是专辑。", "发行七里香。", "七里香的造型。"]:
            with self.subTest(text=text):
                self.assertIsNone(self.decide(text, "七里香"))

    def test_inline_album_and_song_headings_are_local(self):
        text = "**Title of Music Albums** (2004) 七里香\n\n**Song Titles** 七里香"
        self.assertEqual(self.decide(text, "七里香").entity_id, "album:qi_li_xiang")
        self.assertEqual(self.decide(text, "七里香", 1).entity_id, "song:qi_li_xiang")

    def test_yhm_variants_and_people(self):
        for name in ("叶惠美", "葉惠美"):
            for text in [
                f"专辑《{name}》发行。",
                f"《{name}》是歌手周杰伦的第四张专辑。",
            ]:
                with self.subTest(text=text):
                    self.assertEqual(
                        self.decide(text, name).entity_id, "album:ye_hui_mei"
                    )
            for text in [
                f"母亲{name}是一位美术老师。",
                f"{name}女士（妈妈）喜欢音乐。",
                f"美術老師{name}绘画。",
            ]:
                with self.subTest(text=text):
                    self.assertIsNone(self.decide(text, name))

    def test_album_name_origin_does_not_turn_title_into_person(self):
        text = "《葉惠美》是歌手周杰伦的第四张专辑，名称以母亲名字命名。"
        self.assertEqual(self.decide(text, "葉惠美").entity_id, "album:ye_hui_mei")

    def test_genre_specific_grammar_and_fragments(self):
        for name in ("华语流行音乐", "華語流行音樂"):
            for suffix in ("的新時代！", "史的一部分。", "发展。", "暢銷冠軍。"):
                with self.subTest(name=name, suffix=suffix):
                    self.assertEqual(
                        self.decide(name + suffix, name).entity_id, "genre:c_pop"
                    )
            for text in [
                f"《{name}史》一书。",
                f"https://example/{name}发展",
                name + "发展商店。",
            ]:
                with self.subTest(text=text):
                    self.assertIsNone(self.decide(text, name))
        for text, surface, expected in [
            ("这首歌曲是中国风的摇滚乐。", "中国风", "genre:chinese_style"),
            ("歌曲以嘻哈混合埃及音乐。", "嘻哈", "genre:hip_hop"),
            ("周杰伦的歌曲中國風的歌詞。", "中國風", "genre:chinese_style"),
        ]:
            self.assertEqual(self.decide(text, surface).entity_id, expected)
        for text, surface in [("喜欢中国风格。", "中国风"), ("歌曲嘻哈空姐。", "嘻哈")]:
            self.assertIsNone(self.decide(text, surface))

    def test_other_approved_b1_roles_and_wrong_identities(self):
        self.assertEqual(
            self.decide("黑膠唱片 方文山讚譽像時空膠囊。", "方文山").entity_id,
            "person:vincent_fang",
        )
        self.assertEqual(
            self.decide(
                "In the song Huo Yuan Chia (霍元甲), he sang.", "霍元甲"
            ).entity_id,
            "song:fearless",
        )
        for text in [
            "霍元甲 EP（2006年）。",
            "电影《霍元甲》。",
            "民族英雄霍元甲。",
            "电视剧《大侠霍元甲》。",
        ]:
            self.assertIsNone(self.decide(text, "霍元甲"))

    def test_structural_album_reference_and_song_column_regressions(self):
        text = "周杰伦与新力签约后，先后共将《叶惠美》及《七里香》交给新力发行"
        for surface, entity_id in [
            ("叶惠美", "album:ye_hui_mei"),
            ("七里香", "album:qi_li_xiang"),
        ]:
            self.assertEqual(self.decide(text, surface).entity_id, entity_id)
        text = "| 年份 | 歌手 | 歌名 |\n| 2006年 | 周杰倫 | 霍元甲 |\n| 2010 | 跨時代 |"
        self.assertEqual(self.decide(text, "霍元甲").entity_id, "song:fearless")

    def test_cited_record_coreference_requires_independent_record_evidence(self):
        text = "周杰伦:叶惠美(CD).\n1. ^ 葉惠美. 杰威尔音乐."
        self.assertEqual(self.decide(text, "葉惠美").entity_id, "album:ye_hui_mei")
        self.assertIsNone(self.decide("葉惠美. 杰威尔音乐.", "葉惠美"))

    def test_does_not_borrow_parent_or_document_metadata(self):
        self.assertIsNone(self.decide("七里香。", "七里香"))
        with self.assertRaisesRegex(ValueError, "offset"):
            infer_qi_li_xiang_occurrence("七里香", 0, 30, self.registry)


class B1RetryTests(unittest.TestCase):
    def setUp(self):
        self.v6 = GroundedEntityLinker(
            KnowledgeConfig(entity_linking_version="v6", llm_enabled=False)
        )
        self.config = KnowledgeConfig(entity_linking_version="v7", llm_enabled=False)

    def test_requires_explicit_opt_in_and_scope(self):
        with self.assertRaisesRegex(ValueError, "scope"):
            GroundedEntityLinker(self.config)
        with self.assertRaisesRegex(ValueError, "v6_cannot"):
            GroundedEntityLinker(self.v6.config, b1_scope=B1Scope([]))
        with self.assertRaisesRegex(ValueError, "rule_only"):
            GroundedEntityLinker(
                KnowledgeConfig(entity_linking_version="v7"), b1_scope=B1Scope([])
            )

    def test_only_scoped_b1_changes_and_v6_payload_remains_compatible(self):
        chunk = _enriched("私はJayがデビューした時に音楽を紹介していた。歌曲嘻哈空姐。")
        base = self.v6.link_with_evidence(chunk)
        scope = scope_for(chunk, base, "Jay")
        proof = GroundedEntityLinker(self.config, b1_scope=scope).link_with_evidence(
            chunk
        )
        self.assertEqual(
            {e.entity_id for e in proof.result.linked_entities}, {"artist:jay_chou"}
        )
        self.assertEqual(
            [
                m
                for m in proof.result.unresolved_mentions
                if m.normalized_text == "嘻哈"
            ],
            [m for m in base.result.unresolved_mentions if m.normalized_text == "嘻哈"],
        )
        self.assertEqual(self.v6.link_with_evidence(chunk).payload(), base.payload())
        self.assertNotIn("b1_decisions", base.payload())
        self.assertEqual(proof.payload()["scope_sha256"], scope.sha256)
        self.assertEqual(proof.b1_decisions[0]["status"], "resolved_b1")
        for match in proof.body_matches:
            self.assertEqual(
                chunk.content[match["start"] : match["end"]], match["quote"]
            )
            self.assertEqual(
                chunk.content[match["evidence_start"] : match["evidence_end"]],
                match["evidence_quote"],
            )

    def test_empty_scope_does_not_retry(self):
        chunk = _enriched("我是听众，Jayがデビューした。")
        before = self.v6.link_with_evidence(chunk)
        after = GroundedEntityLinker(
            self.config, b1_scope=B1Scope([])
        ).link_with_evidence(chunk)
        self.assertEqual(before.result, after.result)
        self.assertEqual(before.rejected, after.rejected)

    def test_b2_target_already_linked_stays_pending(self):
        chunk = _enriched("Jay Chou。" + "这是正文。" * 12 + "Jayがデビューした。")
        before = self.v6.link_with_evidence(chunk)
        scope = scope_for(chunk, before, "Jay")
        after = GroundedEntityLinker(self.config, b1_scope=scope).link_with_evidence(
            chunk
        )
        self.assertEqual(before.result, after.result)
        self.assertEqual(after.b1_decisions[0]["status"], "kept_unresolved")

    def test_hash_candidate_reason_and_position_bindings_fail_closed(self):
        chunk = _enriched("私はJayがデビューした。")
        before = self.v6.link_with_evidence(chunk)
        original = scope_for(chunk, before, "Jay")
        entry = next(iter(original.entries.values()))
        for update in [
            {"body_sha256": "0" * 64},
            {"candidate_entity_ids": ("song:qi_li_xiang",)},
            {"reason": "wrong"},
            {"doc_id": "other"},
            {"occurrences": (entry.occurrences[0].model_copy(update={"start": 0}),)},
        ]:
            with (
                self.subTest(update=update),
                self.assertRaisesRegex(ValueError, "binding"),
            ):
                scope = B1Scope([entry.model_copy(update=update)])
                GroundedEntityLinker(self.config, b1_scope=scope).link_with_evidence(
                    chunk
                )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            B1Scope([entry, entry])
        with self.assertRaises(Exception):
            B1ScopeEntry.model_validate(
                {**entry.model_dump(), "expected_targets": ["artist:jay_chou"]}
            )

    def test_ambiguous_occurrence_stays_pending(self):
        chunk = _enriched("Jay来了。")
        base = self.v6.link_with_evidence(chunk)
        after = GroundedEntityLinker(
            self.config, b1_scope=scope_for(chunk, base, "Jay")
        ).link_with_evidence(chunk)
        self.assertEqual(base.result, after.result)


if __name__ == "__main__":
    unittest.main()
