import unittest
import tempfile
from pathlib import Path

from app.rag.clean.config import load_clean_config
from app.rag.clean.corpus import NormalizedCorpusReader
from app.rag.clean.engine import CleanEngine
from app.rag.clean.guard import SemanticGuard
from app.rag.clean.registry import RuleRegistry
from tests.clean_helpers import normalized_document, write_normalized_corpus


WIKIPEDIA_HEADER = """- 首页
- 分类索引
- 特色内容
- 新闻动态
- 随机条目
- 联络我们
- 关于维基百科

- 帮助
- 维基社群
- 编辑入门
- 互助客栈
- IRC即时聊天
- 最近更改
- 特殊页面

![](https://zh.wikipedia.org/static/images/icons/wikipedia.png)

![维基百科](https://zh.wikipedia.org/static/images/mobile/copyright/wikipedia-wordmark-zh.svg)

![自由的百科全书](https://zh.wikipedia.org/static/images/mobile/copyright/wikipedia-tagline-zh.svg)

- 资助维基百科
- 创建账号
- 登录

- 资助维基百科
- 创建账号
- 登录

"""

WIKIPEDIA_ACTIONS = """- 条目
- 讨论

- 不转换
- 简体
- 繁體
- 大陆简体
- 香港繁體
- 澳門繁體
- 大马简体
- 新加坡简体
- 臺灣正體

- 阅读
- 编辑
- 查看历史

- 阅读
- 编辑
- 查看历史

- 链入页面
- 相关更改
- 上传文件
- 固定链接
- 页面信息
- 引用此页
- 获取短链接

- 下载为PDF
- 打印页面

- 维基数据项目

"""


class CleanV02RegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config, self.checksum = load_clean_config()
        self.assertEqual(self.config.clean_version, "v0.2")

    def _clean(self, root: Path, source_id: str):
        context = NormalizedCorpusReader(
            root,
            document_type_sidecar=self.config.document_type_sidecar,
            document_type_sidecar_checksum=(
                self.config.document_type_sidecar_checksum
            ),
        ).load_all()[0]
        self.assertEqual(context.document.source_id, source_id)
        return CleanEngine(
            RuleRegistry(self.config),
            SemanticGuard(self.config.semantic_guard),
            registry_checksum=self.checksum,
        ).clean(context)

    def test_sony_rule_removes_only_reviewed_icons_and_preserves_album_facts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = (
                "![Sony Music](https://sonymusic.com.tw/wp-content/themes/smtw-45p/dist/img/sony-music-logo.png)\n\n"
                "- 華語\n- 西洋\n- 東洋\n- 韓樂\n- 古典\n- 新聞\n- 商店\n\n"
                "![Adele / 25](https://cdn.example.test/album/25-cover.jpg)\n\n"
                "歌手：Adele\n专辑：25\n发行日期：2015-11-20\n排名：1\nBPM：120\n"
                "![Spotify](https://cdn.example.test/2025/01/Spotify_Primary_Logo_RGB_White.png)\n"
            )
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "jc-v1-004",
                        text,
                        title="25",
                        publisher="台灣索尼音樂娛樂",
                        document_type=None,
                    )
                ],
            )

            result = self._clean(root, "jc-v1-004")

            self.assertEqual(result.document.document_type, "album")
            self.assertEqual(result.document.document_type_origin, "sidecar")
            self.assertIsNotNone(result.document.document_type_sidecar_checksum)
            self.assertNotIn("sony-music-logo", result.after_text)
            self.assertNotIn("Spotify_Primary_Logo", result.after_text)
            self.assertIn("25-cover.jpg", result.after_text)
            self.assertNotIn("\n\n\n", result.after_text)
            for fact in ("Adele", "专辑：25", "2015-11-20", "排名：1", "BPM：120"):
                self.assertIn(fact, result.after_text)
            self.assertFalse(
                any(
                    warning.startswith("numeric_or_date_facts_lost:")
                    for warning in result.audit.warnings
                )
            )

    def test_jvr_rule_preserves_company_contact_and_music_facts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = (
                "- 繁\n- 簡\n- EN\n- NEWS 最新消息\n- FB\n\n"
                "歌手：宇多田光\n歌曲：First Love\n发行：1999\n\n"
                "- 版權及隱私保護⁄\n- 杰威爾音樂有限公司\n"
                "- © Copyright 2019\n- 台北市松山區長春路451號11樓\n"
                "- 886-2-2547-5588\n"
            )
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "jc-v1-001",
                        text,
                        title="Artist profile",
                        publisher="杰威爾音樂",
                        document_type=None,
                    )
                ],
            )

            result = self._clean(root, "jc-v1-001")

            self.assertNotIn("- NEWS 最新消息", result.after_text)
            self.assertNotIn("- FB", result.after_text)
            for fact in (
                "宇多田光",
                "First Love",
                "1999",
                "杰威爾音樂有限公司",
                "2019",
                "台北市松山區長春路451號11樓",
                "886-2-2547-5588",
            ):
                self.assertIn(fact, result.after_text)

    def test_wikipedia_rules_require_complete_ui_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = (
                WIKIPEDIA_HEADER
                + "## 目录\n\n- 1 生平\n\n# Nina Simone\n\n"
                + WIKIPEDIA_ACTIONS
                + "歌手：Nina Simone\n发行日期：1958-02-01\n- 统计\n"
            )
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "jc-v1-030",
                        text,
                        title="Nina Simone",
                        publisher="Wikipedia",
                        document_type=None,
                    )
                ],
            )

            result = self._clean(root, "jc-v1-030")

            self.assertNotIn("- 分类索引", result.after_text)
            self.assertNotIn("- 查看历史", result.after_text)
            self.assertIn("## 目录", result.after_text)
            self.assertIn("Nina Simone", result.after_text)
            self.assertIn("1958-02-01", result.after_text)
            self.assertIn("- 统计", result.after_text)

    def test_government_rule_preserves_award_dates_names_and_ranks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = (
                "- 進階搜尋\n- Facebook\n- X\n- line\n- Email\n"
                "- 字級 小\n- 字級 中\n- 字級 大\n"
                "- 關於本局 成立沿革 本局願景 本局首長\n\n"
                "### 音乐奖得奖名单\n- 發佈日期：2024-06-01\n"
                "得奖者：王菲\n得奖作品：红豆\n排名：1\n"
            )
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "jc-v1-011",
                        text,
                        title="音乐奖得奖名单",
                        publisher="文化部影視及流行音樂產業局",
                        document_type=None,
                    )
                ],
            )

            result = self._clean(root, "jc-v1-011")

            self.assertNotIn("- Facebook", result.after_text)
            self.assertNotIn("- 字級 小", result.after_text)
            for fact in ("2024-06-01", "王菲", "红豆", "排名：1"):
                self.assertIn(fact, result.after_text)

    def test_thepaper_rule_keeps_publisher_facts_and_article_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = (
                "![澎湃Logo](https://www.thepaper.cn/_next/static/media/logo_light.4da59bed.png)\n"
                "登录\n- +1\n# Björk interview\n发行日期：2022-09-30\n"
                "#### 扫码下载澎湃新闻客户端\nAndroid版\niPhone版\niPad版\n"
                "- 报料热线: 021-962866\n沪ICP备14003370号\n"
                "© 2014-2026 上海东方报业有限公司\n"
            )
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "jc-v2-007",
                        text,
                        title="Björk interview",
                        publisher="澎湃新闻",
                        document_type=None,
                    )
                ],
            )

            result = self._clean(root, "jc-v2-007")

            self.assertNotIn("logo_light", result.after_text)
            self.assertNotIn("扫码下载", result.after_text)
            for fact in (
                "Björk",
                "2022-09-30",
                "021-962866",
                "沪ICP备14003370号",
                "2014-2026",
                "上海东方报业有限公司",
            ):
                self.assertIn(fact, result.after_text)

    def test_unknown_source_with_matching_text_runs_only_global_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = "- 首页  \n- 分类索引\n歌手：AURORA\n发行：2024\n"
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "new-wiki-source",
                        text,
                        title="AURORA",
                        publisher="New Encyclopedia",
                        document_type="wiki",
                    )
                ],
            )

            result = self._clean(root, "new-wiki-source")

            self.assertIn("- 首页", result.after_text)
            self.assertIn("- 分类索引", result.after_text)
            self.assertNotIn("  \n", result.after_text)
            self.assertEqual(result.audit.applied_rules, ["GLOBAL_SAFE_003"])
            self.assertIn("unknown_source:New Encyclopedia", result.audit.warnings)


if __name__ == "__main__":
    unittest.main()
