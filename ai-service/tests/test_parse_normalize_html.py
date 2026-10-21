import unittest
from datetime import UTC, datetime

from app.rag.parse_normalize.models import BlockType, DocumentBlock, ParseInput
from app.rag.parse_normalize.normalizer import MarkdownNormalizer
from app.rag.parse_normalize.parsers.html import HtmlParser
from app.rag.source_ingestion.models import RawSnapshotRecord


RAW_HTML = """<!doctype html>
<html lang="zh-Hant">
  <head>
    <title>Seven Miles</title>
    <meta name="author" content="Jay Chou">
    <meta property="article:published_time" content="2004-08-03T00:00:00+08:00">
    <meta name="description" content="Album profile">
    <link rel="canonical" href="https://example.test/albums/seven-miles">
    <style>.hidden { display: none; }</style>
  </head>
  <body>
    <nav><p>Navigation remains at parse stage.</p></nav>
    <h1>七里香</h1>
    <p>First <a href="/facts">paragraph</a>.</p>
    <h2>Track list</h2>
    <ul><li>我的地盘</li><li>七里香</li></ul>
    <blockquote>窗外的麻雀</blockquote>
    <pre><code class="language-python">print("music")</code></pre>
    <table>
      <tr><th>Song</th><th>Year</th></tr>
      <tr><td>Qi Li Xiang</td><td>2004</td></tr>
    </table>
    <img src="cover.jpg" alt="album cover" title="Cover">
    <noscript>discard noscript</noscript>
    <script>discard script</script>
  </body>
</html>
""".encode("utf-8")


def _parse_input(raw: bytes = RAW_HTML) -> ParseInput:
    snapshot = RawSnapshotRecord(
        source_id="html-source",
        original_url="https://example.test/albums/page.html",
        content_type="text/html; charset=utf-8",
        content_hash="sha256:" + "0" * 64,
        fetched_at=datetime(2026, 9, 9, tzinfo=UTC),
        raw_path="data/raw/html/source.html",
        http_status=200,
        source_quality="high",
        corpus_version="jay-chou-corpus-v1",
    )
    return ParseInput(snapshot=snapshot, raw_bytes=raw, candidate=None)


class HtmlParserTests(unittest.TestCase):
    def test_uncovered_article_text_is_preserved_once_in_dom_order(self) -> None:
        raw = """<html><body><h1>标题</h1><article><div>正文<span>事实</span></div>
        <p>支持的段落<span>完整</span></p><section>后续正文</section></article></body></html>""".encode()
        result = HtmlParser().parse(_parse_input(raw))
        self.assertEqual([b.text for b in result.blocks], ["标题", "正文事实", "支持的段落完整", "后续正文"])
        self.assertEqual(result.blocks[1].parent_block_id, result.blocks[0].block_id)

    def test_fallback_excludes_identifiable_noise_and_hidden_text(self) -> None:
        raw = """<html><body><h1>标题</h1><main>正文事实<br>下一行</main>
        <nav>菜单噪音</nav><footer>页脚噪音</footer><aside>侧栏噪音</aside>
        <div role="navigation">导航噪音</div><div class="ad-banner">广告噪音</div>
        <div id="cookie-consent">弹窗噪音</div><div hidden>隐藏一</div>
        <div aria-hidden="true">隐藏二</div><div style="display: none">隐藏三</div>
        <!-- 注释噪音 --><form>表单噪音</form></body></html>""".encode()
        result = HtmlParser().parse(_parse_input(raw))
        self.assertEqual([b.text for b in result.blocks], ["标题", "正文事实 下一行"])

    def test_existing_semantic_navigation_and_atomic_blocks_are_not_duplicated(self) -> None:
        raw = b"<body><nav><p>Existing navigation</p>new loose menu</nav><ul><li>One</li><li>Two</li></ul></body>"
        result = HtmlParser().parse(_parse_input(raw))
        self.assertEqual([b.text for b in result.blocks], ["Existing navigation", "One\nTwo"])

    def test_metadata_only_spa_falls_back_to_source_title_and_description(self) -> None:
        raw = """<!doctype html>
        <html lang="zh-Hant"><head>
          <title>周杰倫《東風破》Official Music Video</title>
          <meta name="description" content="歌曲與專輯的官方介紹。">
          <script>window.renderClientSide = true;</script>
        </head><body><div id="app"></div></body></html>
        """.encode("utf-8")

        result = HtmlParser().parse(_parse_input(raw))

        self.assertEqual(
            [block.block_type for block in result.blocks],
            [BlockType.HEADING, BlockType.PARAGRAPH],
        )
        self.assertEqual(result.blocks[0].text, "周杰倫《東風破》Official Music Video")
        self.assertEqual(result.blocks[1].text, "歌曲與專輯的官方介紹。")
        self.assertTrue(result.metadata["metadata_only_fallback"])

    def test_html_emits_page_metadata_and_semantic_blocks(self) -> None:
        result = HtmlParser().parse(_parse_input())

        self.assertEqual(result.title, "Seven Miles")
        self.assertEqual(result.author, "Jay Chou")
        self.assertEqual(result.published_at.isoformat(), "2004-08-03")
        self.assertEqual(result.language, "zh-Hant")
        self.assertEqual(
            [block.block_type for block in result.blocks],
            [
                BlockType.PARAGRAPH,
                BlockType.HEADING,
                BlockType.PARAGRAPH,
                BlockType.HEADING,
                BlockType.LIST,
                BlockType.QUOTE,
                BlockType.CODE,
                BlockType.TABLE,
                BlockType.IMAGE,
            ],
        )
        self.assertEqual(result.blocks[2].parent_block_id, result.blocks[1].block_id)
        self.assertEqual(result.blocks[3].parent_block_id, result.blocks[1].block_id)
        self.assertEqual(result.blocks[4].parent_block_id, result.blocks[3].block_id)
        self.assertEqual(result.blocks[4].metadata["items"], ["我的地盘", "七里香"])
        self.assertEqual(
            result.blocks[7].metadata["rows"],
            [["Song", "Year"], ["Qi Li Xiang", "2004"]],
        )
        self.assertTrue(result.blocks[7].metadata["has_header"])
        self.assertEqual(len(result.tables), 1)
        self.assertEqual(
            result.tables[0].rows,
            [["Song", "Year"], ["Qi Li Xiang", "2004"]],
        )
        self.assertEqual(
            result.tables[0].metadata["source_block_id"],
            result.blocks[7].block_id,
        )
        self.assertEqual(
            result.blocks[8].metadata["src"],
            "https://example.test/albums/cover.jpg",
        )
        self.assertEqual(
            result.metadata["canonical_url"],
            "https://example.test/albums/seven-miles",
        )
        all_text = " ".join(block.text for block in result.blocks)
        self.assertNotIn("discard", all_text)
        self.assertIn("Navigation remains", all_text)

    def test_html_parser_does_not_duplicate_atomic_container_children(self) -> None:
        result = HtmlParser().parse(_parse_input())

        self.assertEqual(
            sum(block.text.count("Qi Li Xiang") for block in result.blocks), 1
        )
        self.assertEqual(sum(block.text.count("我的地盘") for block in result.blocks), 1)

    def test_shared_normalizer_renders_canonical_markdown(self) -> None:
        result = HtmlParser().parse(_parse_input())

        markdown = MarkdownNormalizer().render(result.blocks)

        self.assertIn("# 七里香\n\nFirst paragraph.", markdown)
        self.assertIn("## Track list\n\n- 我的地盘\n- 七里香", markdown)
        self.assertIn("> 窗外的麻雀", markdown)
        self.assertIn('```python\nprint("music")\n```', markdown)
        self.assertIn(
            "| Song | Year |\n| --- | --- |\n| Qi Li Xiang | 2004 |", markdown
        )
        self.assertIn(
            "![album cover](https://example.test/albums/cover.jpg)", markdown
        )
        self.assertNotIn("\r", markdown)
        self.assertNotIn("\n\n\n", markdown)
        self.assertTrue(markdown.endswith("\n"))

    def test_headerless_html_table_is_preserved_without_semantic_guess(self) -> None:
        raw = b"<html><body><table><tr><td>Home</td><td>News</td></tr></table></body></html>"

        result = HtmlParser().parse(_parse_input(raw))

        self.assertEqual(result.blocks[0].metadata["table_kind_hint"], "uncertain")
        self.assertFalse(result.blocks[0].metadata["has_header"])
        self.assertEqual(result.tables[0].rows, [["Home", "News"]])
        self.assertEqual(
            MarkdownNormalizer().render(result.blocks),
            "|  |  |\n| --- | --- |\n| Home | News |\n",
        )

    def test_code_fence_is_longer_than_backtick_run_in_content(self) -> None:
        block = DocumentBlock(
            block_id="code-0",
            block_type="code",
            text="literal ``` marker",
            level=None,
            order_index=0,
            parent_block_id=None,
            metadata={"language": "text"},
        )

        markdown = MarkdownNormalizer().render([block])

        self.assertEqual(markdown, "````text\nliteral ``` marker\n````\n")


if __name__ == "__main__":
    unittest.main()
