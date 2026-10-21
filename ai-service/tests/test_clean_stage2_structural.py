"""Unit tests for Stage 2: Structural Data Cleaning with Whitelist Protections."""

from __future__ import annotations

from app.rag.clean.config import StructuralCleanConfig
from app.rag.clean.structural import StructuralCleaner


def test_table_whitelist_preserves_markdown_tables():
    cleaner = StructuralCleaner(StructuralCleanConfig(whitelist_tables=True))

    md_table = """
# 专辑曲目列表

| 序号 | 歌名 | 作词 | 作曲 | 时长 |
|:---|:---|:---|:---|:---|
| 1 | 夜曲 | 方文山 | 周杰伦 | 03:48 |
| 2 | 蓝色风暴 | 方文山 | 周杰伦 | 04:46 |
| 3 | 发如雪 | 方文山 | 周杰伦 | 05:01 |

正文介绍内容。
""".strip()

    result = cleaner.clean(md_table)
    assert "| 序号 | 歌名 |" in result.cleaned_text
    assert "| 1 | 夜曲 |" in result.cleaned_text
    assert result.protected_tables >= 1
    assert result.stage_metrics.details["protected_tables"] >= 1


def test_table_whitelist_preserves_html_tables():
    cleaner = StructuralCleaner(StructuralCleanConfig(whitelist_tables=True))

    html_table = """
<table>
  <tr><th>奖项</th><th>年份</th><th>结果</th></tr>
  <tr><td>最佳国语男歌手</td><td>2004</td><td>获奖</td></tr>
</table>
""".strip()

    result = cleaner.clean(html_table)
    assert "<table>" in result.cleaned_text
    assert "最佳国语男歌手" in result.cleaned_text
    assert result.protected_tables >= 1


def test_list_whitelist_preserves_bullet_and_numbered_lists():
    cleaner = StructuralCleaner(StructuralCleanConfig(whitelist_lists=True))

    content = """
以下为演唱会曲目列表：

- 开场曲：黄金甲
- 第二首：青花瓷
- 第三首：稻香
- 压轴曲：七里香

巡演城市安排：

1. 台北小巨蛋
2. 北京鸟巢
3. 香港红磡体育馆
""".strip()

    result = cleaner.clean(content)
    assert "- 开场曲：黄金甲" in result.cleaned_text
    assert "1. 台北小巨蛋" in result.cleaned_text
    assert result.protected_lists >= 2
    assert result.stage_metrics.details["protected_lists"] >= 2


def test_removes_structural_noise_empty_tags_and_redundant_hr():
    cleaner = StructuralCleaner(StructuralCleanConfig())

    noisy_doc = """
# 标题

<div class="header-placeholder"></div>

正文第一段真实内容。

---
***
___

正文第二段真实内容。

<!-- redundant empty template note -->
""".strip()

    result = cleaner.clean(noisy_doc)
    assert "正文第一段真实内容。" in result.cleaned_text
    assert "正文第二段真实内容。" in result.cleaned_text
    assert '<div class="header-placeholder"></div>' not in result.cleaned_text
    assert "<!-- redundant empty template note -->" not in result.cleaned_text
    assert result.removed_blocks >= 1
    assert result.stage_metrics.removed_blocks >= 1


def test_disabled_structural_cleaner_passes_through():
    cleaner = StructuralCleaner(StructuralCleanConfig(enabled=False))
    raw = "<div></div>\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    result = cleaner.clean(raw)
    assert result.cleaned_text == raw
    assert result.removed_blocks == 0
    assert result.stage_metrics.details == {"enabled": False}
