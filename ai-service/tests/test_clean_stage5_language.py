"""Unit tests for Stage 5: Language Detection and Non-destructive Annotation."""

from __future__ import annotations

from app.rag.clean.config import LanguageDetectionConfig
from app.rag.clean.language import LanguageDetector


def test_language_detector_identifies_chinese_text():
    detector = LanguageDetector(LanguageDetectionConfig())
    text = "周杰伦是华语流行乐男歌手、音乐人，拥有广泛的受众群体。"
    anno, metrics = detector.detect(text)

    assert anno.primary_language == "zh"
    assert anno.confidence > 0.5
    assert not anno.has_english_blocks
    assert anno.policy_action == "retained_unfiltered"
    assert metrics.removed_chars == 0
    assert metrics.removed_blocks == 0


def test_language_detector_identifies_english_blocks_and_retains_them():
    detector = LanguageDetector(LanguageDetectionConfig())
    text = """
周杰伦（Jay Chou），祖籍福建省永春县。

Jay Chou is a Taiwanese musician, singer-songwriter, multi-instrumentalist and actor.

In 2000, he released his debut album Jay under Alfa Music.

收录了《星晴》、《可爱女人》等经典歌曲。
""".strip()

    anno, metrics = detector.detect(text)

    # Identifies English blocks
    assert anno.has_english_blocks
    assert anno.english_block_count >= 2

    # Verifies English blocks are retained and NOT filtered out
    assert anno.policy_action == "retained_unfiltered"
    assert "pending_decision" in metrics.details
    assert metrics.removed_chars == 0
    assert metrics.removed_blocks == 0


def test_language_detection_is_strictly_non_destructive():
    detector = LanguageDetector(LanguageDetectionConfig())
    sample = "English block here.\n\n中文段落在此。\n\nAnother English sentence."
    anno, metrics = detector.detect(sample)

    # The detector does not mutate text
    assert metrics.before_chars == metrics.after_chars
    assert metrics.removed_chars == 0
    assert metrics.removed_blocks == 0
