import tempfile
import unittest
from pathlib import Path

from app.rag.clean.config import CleanRuleConfig, load_clean_config
from app.rag.clean.corpus import NormalizedCorpusReader
from app.rag.clean.engine import CleanEngine
from app.rag.clean.guard import SemanticGuard
from app.rag.clean.registry import RuleRegistry
from tests.clean_helpers import normalized_document, write_normalized_corpus


CHECKSUM = "sha256:" + "a" * 64


def configured_engine(
    *,
    rules: list[dict[str, object]] | None = None,
    known_sources: list[str] | None = None,
    known_document_types: list[str] | None = None,
) -> CleanEngine:
    config, _ = load_clean_config()
    if rules is not None:
        parsed_rules = [CleanRuleConfig.model_validate(rule) for rule in rules]
    else:
        parsed_rules = config.rules
    config = config.model_copy(
        update={
            "rules": parsed_rules,
            "known_sources": known_sources or [],
            "known_document_types": known_document_types or [],
        }
    )
    registry = RuleRegistry(config)
    return CleanEngine(
        registry,
        SemanticGuard(config.semantic_guard),
        registry_checksum=CHECKSUM,
    )


def rule(
    rule_id: str,
    *,
    scope: dict[str, object],
    action: str,
    pattern: str,
    priority: int,
    replacement: str | None = None,
    flags: list[str] | None = None,
    safe: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": rule_id,
        "name": rule_id.lower(),
        "description": f"Test rule {rule_id}",
        "scope": scope,
        "action": action,
        "pattern": pattern,
        "priority": priority,
        "enabled": True,
        "safe": safe,
        "reason": "Hermetic regression fixture",
        "version": "v0.2",
        "flags": flags or [],
    }
    if replacement is not None:
        payload["replacement"] = replacement
    return payload


class CleanEngineRegressionTests(unittest.TestCase):
    def _context(
        self,
        text: str,
        *,
        source_id: str = "music-document",
        source: str = "Music Source",
        document_type: str | None = "song",
    ):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        root = Path(temporary_directory.name)
        write_normalized_corpus(
            root,
            [
                normalized_document(
                    source_id,
                    text,
                    title="Cross-artist fixture",
                    publisher=source,
                    document_type=document_type,
                )
            ],
        )
        return NormalizedCorpusReader(root).load_all()[0]

    def test_default_safe_rules_preserve_music_domain_facts(self) -> None:
        text = (
            "歌手：王菲  \r\n"
            "歌曲名：红豆\r\n"
            "专辑：唱游\r\n"
            "作词：林夕\r\n"
            "作曲：柳重言\r\n"
            "发行日期：1998-10-02\r\n"
            "排名：1\r\n"
            "BPM：120\r\n"
        )
        result = configured_engine().clean(self._context(text))

        for fact in (
            "王菲",
            "红豆",
            "唱游",
            "林夕",
            "柳重言",
            "1998-10-02",
            "排名：1",
            "BPM：120",
        ):
            self.assertIn(fact, result.after_text)
        self.assertNotIn("\r", result.after_text)
        self.assertIn("GLOBAL_SAFE_002", result.audit.applied_rules)
        self.assertTrue(result.document.needs_review)
        self.assertIn("unknown_source:Music Source", result.document.warnings)

    def test_source_and_document_type_rules_are_strictly_scoped(self) -> None:
        rules = [
            rule(
                "SOURCE_SITE_A_001",
                scope={"type": "SOURCE_SPECIFIC", "sources": ["Site A"]},
                action="DROP_LINE",
                pattern="^导航$",
                priority=100,
            ),
            rule(
                "DOC_SONG_001",
                scope={
                    "type": "DOCUMENT_TYPE_SPECIFIC",
                    "document_types": ["song"],
                },
                action="DROP_LINE",
                pattern="^相关推荐$",
                priority=100,
            ),
        ]
        engine = configured_engine(
            rules=rules,
            known_sources=["Site A", "Site B"],
            known_document_types=["song", "artist"],
        )

        site_a_song = engine.clean(
            self._context("导航\n内容\n相关推荐\n", source="Site A")
        )
        site_b_song = engine.clean(
            self._context(
                "导航\n内容\n相关推荐\n",
                source_id="site-b-song",
                source="Site B",
            )
        )
        site_a_artist = engine.clean(
            self._context(
                "导航\n内容\n相关推荐\n",
                source_id="site-a-artist",
                source="Site A",
                document_type="artist",
            )
        )

        self.assertNotIn("导航", site_a_song.after_text)
        self.assertNotIn("相关推荐", site_a_song.after_text)
        self.assertIn("导航", site_b_song.after_text)
        self.assertNotIn("相关推荐", site_b_song.after_text)
        self.assertNotIn("导航", site_a_artist.after_text)
        self.assertIn("相关推荐", site_a_artist.after_text)

    def test_configured_replace_drop_line_and_drop_block_remove_ui_noise(self) -> None:
        rules = [
            rule(
                "SOURCE_REVIEW_001",
                scope={"type": "SOURCE_SPECIFIC", "sources": ["Review Site"]},
                action="DROP_BLOCK",
                pattern=r"<nav\b[^>]*>.*?</nav>",
                flags=["IGNORECASE", "DOTALL"],
                priority=10,
            ),
            rule(
                "SOURCE_REVIEW_002",
                scope={"type": "SOURCE_SPECIFIC", "sources": ["Review Site"]},
                action="REPLACE",
                pattern=r"<br\s*/?>",
                replacement="\n",
                flags=["IGNORECASE"],
                priority=20,
            ),
            rule(
                "DOC_REVIEW_001",
                scope={
                    "type": "DOCUMENT_TYPE_SPECIFIC",
                    "document_types": ["review"],
                },
                action="DROP_LINE",
                pattern=r"^分享到社交平台$",
                priority=30,
            ),
        ]
        engine = configured_engine(
            rules=rules,
            known_sources=["Review Site"],
            known_document_types=["review"],
        )
        context = self._context(
            "<nav>首页 广告</nav>\nMiles Davis<br>Kind of Blue\n分享到社交平台\n1959\n",
            source="Review Site",
            document_type="review",
        )

        result = engine.clean(context)

        self.assertNotIn("<nav>", result.after_text)
        self.assertNotIn("<br>", result.after_text)
        self.assertNotIn("分享到社交平台", result.after_text)
        self.assertIn("Miles Davis", result.after_text)
        self.assertIn("Kind of Blue", result.after_text)
        self.assertIn("1959", result.after_text)
        self.assertEqual(
            result.audit.applied_rules,
            ["SOURCE_REVIEW_001", "SOURCE_REVIEW_002", "DOC_REVIEW_001"],
        )

    def test_unknown_source_runs_only_global_safe_rules(self) -> None:
        rules = [
            rule(
                "GLOBAL_TEST_001",
                scope={"type": "GLOBAL"},
                action="NORMALIZE",
                pattern="TRAILING_WHITESPACE",
                priority=1,
                safe=True,
            ),
            rule(
                "SOURCE_KNOWN_001",
                scope={"type": "SOURCE_SPECIFIC", "sources": ["Known Site"]},
                action="DROP_LINE",
                pattern="^删除我$",
                priority=1,
            ),
        ]
        engine = configured_engine(
            rules=rules,
            known_sources=["Known Site"],
            known_document_types=["song"],
        )

        result = engine.clean(
            self._context("删除我  \n2004\n", source="New Billboard Source")
        )

        self.assertIn("删除我", result.after_text)
        self.assertNotIn("  \n", result.after_text)
        self.assertEqual(result.audit.applied_rules, ["GLOBAL_TEST_001"])
        self.assertTrue(
            any(value.startswith("unknown_source:") for value in result.audit.warnings)
        )

    def test_unknown_document_type_runs_only_global_safe_rules(self) -> None:
        rules = [
            rule(
                "GLOBAL_TEST_002",
                scope={"type": "GLOBAL"},
                action="NORMALIZE",
                pattern="TRAILING_WHITESPACE",
                priority=1,
                safe=True,
            ),
            rule(
                "SOURCE_KNOWN_002",
                scope={"type": "SOURCE_SPECIFIC", "sources": ["Known Site"]},
                action="DROP_LINE",
                pattern="^删除我$",
                priority=1,
            ),
        ]
        engine = configured_engine(
            rules=rules,
            known_sources=["Known Site"],
            known_document_types=["song"],
        )

        result = engine.clean(
            self._context(
                "删除我  \n1999\n",
                source="Known Site",
                document_type=None,
            )
        )

        self.assertIn("删除我", result.after_text)
        self.assertEqual(result.audit.applied_rules, ["GLOBAL_TEST_002"])
        self.assertIn("missing_document_type", result.audit.warnings)

    def test_semantic_guard_flags_numeric_and_structured_fact_loss(self) -> None:
        aggressive = rule(
            "SOURCE_FACTS_001",
            scope={"type": "SOURCE_SPECIFIC", "sources": ["Fact Source"]},
            action="DROP_LINE",
            pattern=r"^(?:发行：2004|排名：1|BPM：120|作词：方文山|作曲：陶喆)$",
            priority=1,
        )
        engine = configured_engine(
            rules=[aggressive],
            known_sources=["Fact Source"],
            known_document_types=["song"],
        )
        context = self._context(
            "歌手：AURORA\n歌曲：Runaway\n发行：2004\n排名：1\nBPM：120\n作词：方文山\n作曲：陶喆\n",
            source="Fact Source",
        )

        result = engine.clean(context)

        self.assertTrue(
            any(
                value.startswith("numeric_or_date_facts_lost:")
                for value in result.audit.warnings
            )
        )
        self.assertTrue(
            any(
                value.startswith("structured_facts_lost:")
                for value in result.audit.warnings
            )
        )
        self.assertTrue(result.document.needs_review)

    def test_semantic_guard_flags_empty_and_large_text_drop(self) -> None:
        engine = configured_engine(
            rules=[
                rule(
                    "SOURCE_EMPTY_001",
                    scope={"type": "SOURCE_SPECIFIC", "sources": ["Fact Source"]},
                    action="DROP_BLOCK",
                    pattern=r"(?s).*",
                    priority=1,
                )
            ],
            known_sources=["Fact Source"],
            known_document_types=["review"],
        )
        context = self._context(
            ("重要乐评内容。" * 250) + "\n",
            source="Fact Source",
            document_type="review",
        )

        result = engine.clean(context)

        self.assertEqual(result.after_text, "")
        self.assertIn("cleaned_text_empty", result.audit.warnings)
        self.assertTrue(
            any(value.startswith("removed_ratio_high:") for value in result.audit.warnings)
        )
        self.assertTrue(
            any(value.startswith("large_text_drop:") for value in result.audit.warnings)
        )

    def test_same_input_and_registry_are_reproducible(self) -> None:
        engine = configured_engine()
        context = self._context("Artist: Björk  \r\nYear: 1997\r\n")

        self.assertEqual(engine.clean(context), engine.clean(context))


if __name__ == "__main__":
    unittest.main()
